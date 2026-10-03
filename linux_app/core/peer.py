"""منطق البروتوكول المشفّر بين الجهازين، مستقل عن وسيلة النقل (LAN: TCP frames — انظر lan.py).

الرسائل الداخلية (JSON داخل AES-GCM) — انظر protocol/PROTOCOL.md:
  list / list_result / get / get_error / offer / done / ack / cancel
وأجزاء الملفات تُرسل كإطارات ثنائية مشفّرة.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, BinaryIO, Callable, Optional

from cryptography.exceptions import InvalidTag

from . import crypto

PAGE_SIZE = 300
MAX_FILE = 1 << 42  # 4 TiB
EMIT_INTERVAL = 0.1

SendText = Callable[[str], Awaitable[None]]
SendBin = Callable[[bytes], Awaitable[None]]
Emit = Callable[[str, dict], None]


class PeerError(Exception):
    pass


def sanitize_name(name: str) -> str:
    name = name.replace("\\", "/").split("/")[-1]
    name = re.sub(r"[\x00-\x1f]", "", name).strip()
    if name in ("", ".", ".."):
        return "file"
    return name[:200]


def unique_path(directory: Path, name: str) -> Path:
    p = directory / name
    if not p.exists() and not Path(str(p) + ".part").exists():
        return p
    stem, suffix = os.path.splitext(name)
    i = 1
    while True:
        p = directory / f"{stem} ({i}){suffix}"
        if not p.exists() and not Path(str(p) + ".part").exists():
            return p
        i += 1


def resolve_under(root: Path, rel: str) -> Optional[Path]:
    root = root.resolve()
    p = (root / rel.lstrip("/")).resolve()
    return p if p == root or root in p.parents else None


@dataclass
class _In:
    tid: bytes
    key: bytes
    name: str
    size: int
    path: Path
    tmp: Path
    fh: BinaryIO
    digest: Any = field(default_factory=hashlib.sha256)
    seq: int = 0
    received: int = 0
    last_emit: float = 0.0


@dataclass
class _Out:
    cancelled: bool = False
    error: str = ""
    ack: Optional["asyncio.Future[dict]"] = None


class PeerSession:
    def __init__(
        self,
        enc_key: bytes,
        send_text: SendText,
        send_binary: SendBin,
        emit: Emit,
        download_dir: Path,
        share_root: Optional[Path] = None,
        writable: bool = True,
        hide_dotfiles: bool = True,
    ) -> None:
        self.enc_key = enc_key
        self._send_text = send_text
        self._send_binary = send_binary
        self._emit = emit
        self.download_dir = download_dir
        self.share_root = share_root  # عند تحديده يخدم list/get/put داخل هذا المجلد فقط
        self.writable = writable      # يسمح للند بالرفع داخل share_root
        self.hide_dotfiles = hide_dotfiles  # إخفاء الملفات التي تبدأ بنقطة (~/.ssh ...)
        self.pending: dict[str, "asyncio.Future[dict]"] = {}
        self.incoming: dict[str, _In] = {}
        self.outgoing: dict[str, _Out] = {}
        self.rejected: dict[str, str] = {}  # tid -> reason (so `done` for a refused offer reports the real reason)

    # ---------------- إرسال ----------------
    async def _send_ctl(self, obj: dict) -> None:
        await self._send_text(crypto.seal_ctl(self.enc_key, obj))

    # ---------------- استقبال ----------------
    async def handle_ctl(self, blob: str) -> None:
        try:
            msg = crypto.open_ctl(self.enc_key, blob)
            t = msg.get("type")
        except Exception:
            self._emit("toast", {"text": "Ignored an undecryptable message"})
            return
        try:
            if t == "list_result":
                fut = self.pending.pop(str(msg.get("id")), None)
                if fut and not fut.done():
                    fut.set_result(msg)
            elif t == "get_error":
                self._emit("get_error", {"error": msg.get("error", "error")})
            elif t == "offer":
                self._on_offer(msg)
            elif t == "done":
                await self._on_done(msg)
            elif t == "ack":
                out = self.outgoing.get(str(msg.get("tid")))
                if out and out.ack and not out.ack.done():
                    out.ack.set_result(msg)
            elif t == "cancel":
                self._on_cancel(msg)
            elif t == "list" and self.share_root:
                await self._serve_list(msg)
            elif t == "get" and self.share_root:
                asyncio.ensure_future(self._serve_get(msg))
        except Exception as e:  # رسالة سيئة من الند لا يجب أن تُسقط الجلسة
            self._emit("toast", {"text": f"Protocol error: {e}"})

    def handle_binary(self, frame: bytes) -> None:
        try:
            tid, seq, ct = crypto.parse_frame(frame)
        except ValueError:
            return
        inc = self.incoming.get(tid.hex())
        if inc is None:
            return
        if seq != inc.seq:
            return self._fail_in(inc, "out_of_order")
        try:
            plain = crypto.open_chunk(inc.key, tid, seq, ct)
            inc.fh.write(plain)
        except InvalidTag:
            return self._fail_in(inc, "tampered")
        except OSError as e:
            return self._fail_in(inc, f"disk: {e.strerror}")
        inc.digest.update(plain)
        inc.seq += 1
        inc.received += len(plain)
        if inc.received > MAX_FILE:
            return self._fail_in(inc, "too_large")
        self._progress(inc.tid.hex(), inc.name, inc.size, inc.received, "in", inc)

    # ---------------- استقبال ملف ----------------
    def _reject_offer(self, tidh: str, name: str, size: int, why: str) -> None:
        if len(self.rejected) > 64:
            self.rejected.clear()
        self.rejected[tidh] = why
        asyncio.ensure_future(self._send_ctl({"type": "cancel", "tid": tidh, "error": why}))
        self._emit("transfer", dict(tid=tidh, name=name, size=size, done=0, direction="in", state="failed", error=why))

    def _on_offer(self, m: dict) -> None:
        tidh = str(m.get("tid", ""))
        if not re.fullmatch(r"[0-9a-f]{32}", tidh) or tidh in self.incoming:
            return
        size = m.get("size")
        if not isinstance(size, int) or size < 0 or size > MAX_FILE:
            return
        name = sanitize_name(str(m.get("name", "file")))
        dest = m.get("dest")
        if dest is None:
            directory = self.download_dir
            try:
                directory.mkdir(parents=True, exist_ok=True)
            except OSError as e:
                return self._reject_offer(tidh, name, size, e.strerror or "disk")
        else:
            if self.share_root is None or not self.writable:
                return self._reject_offer(tidh, name, size, "forbidden")
            directory = resolve_under(self.share_root, str(dest))  # type: ignore[assignment]
            if directory is None or not directory.is_dir() or self._hidden(directory):
                return self._reject_offer(tidh, name, size, "bad_dest")
        path = unique_path(directory, name)
        tmp = Path(str(path) + ".part")
        tid = bytes.fromhex(tidh)
        try:
            fh = open(tmp, "wb")
        except OSError as e:
            return self._reject_offer(tidh, name, size, e.strerror or "disk")
        inc = _In(tid, crypto.xfer_key(self.enc_key, tid), name, size, path, tmp, fh)
        self.incoming[tidh] = inc
        self._emit("transfer", dict(tid=tidh, name=name, size=size, done=0, direction="in", state="active"))

    def _hidden(self, p: Path) -> bool:
        if not self.hide_dotfiles or self.share_root is None:
            return False
        try:
            rel = p.resolve().relative_to(self.share_root.resolve())
        except ValueError:
            return True
        return any(part.startswith(".") for part in rel.parts)

    async def _on_done(self, m: dict) -> None:
        tidh = str(m.get("tid"))
        inc = self.incoming.pop(tidh, None)
        if inc is None:
            await self._send_ctl({"type": "ack", "tid": tidh, "ok": False,
                                  "error": self.rejected.pop(tidh, "unknown_transfer")})
            return
        inc.fh.close()
        good = (
            m.get("chunks") == inc.seq
            and m.get("size") == inc.received
            and m.get("sha256") == inc.digest.hexdigest()
        )
        if good:
            os.replace(inc.tmp, inc.path)
            self._emit("transfer", dict(tid=tidh, name=inc.name, size=inc.received, done=inc.received,
                                        direction="in", state="done", path=str(inc.path)))
        else:
            inc.tmp.unlink(missing_ok=True)
            self._emit("transfer", dict(tid=tidh, name=inc.name, size=inc.size, done=inc.received,
                                        direction="in", state="failed", error="integrity"))
        await self._send_ctl({"type": "ack", "tid": tidh, "ok": bool(good), **({} if good else {"error": "integrity"})})

    def _on_cancel(self, m: dict) -> None:
        tidh = str(m.get("tid"))
        inc = self.incoming.pop(tidh, None)
        if inc:
            inc.fh.close()
            inc.tmp.unlink(missing_ok=True)
            self._emit("transfer", dict(tid=tidh, name=inc.name, size=inc.size, done=inc.received,
                                        direction="in", state="cancelled"))
        out = self.outgoing.get(tidh)
        if out:
            out.cancelled = True
            out.error = str(m.get("error", ""))
            if out.ack and not out.ack.done():  # المرسل ينتظر ack: أفشِله فورًا
                out.ack.set_result({"ok": False, "error": out.error or "cancelled"})

    def _fail_in(self, inc: _In, why: str) -> None:
        tidh = inc.tid.hex()
        self.incoming.pop(tidh, None)
        try:
            inc.fh.close()
        finally:
            inc.tmp.unlink(missing_ok=True)
        self._emit("transfer", dict(tid=tidh, name=inc.name, size=inc.size, done=inc.received,
                                    direction="in", state="failed", error=why))
        asyncio.ensure_future(self._send_ctl({"type": "cancel", "tid": tidh}))

    def _progress(self, tid: str, name: str, size: int, done: int, direction: str, obj: Any) -> None:
        now = time.monotonic()
        if now - getattr(obj, "last_emit", 0.0) >= EMIT_INTERVAL:
            obj.last_emit = now
            self._emit("transfer", dict(tid=tid, name=name, size=size, done=done, direction=direction, state="active"))

    # ---------------- طلبات للنِّد ----------------
    async def request_list(self, path: str, offset: int = 0, timeout: float = 20) -> dict:
        rid = os.urandom(8).hex()
        fut: "asyncio.Future[dict]" = asyncio.get_running_loop().create_future()
        self.pending[rid] = fut
        try:
            await self._send_ctl({"type": "list", "id": rid, "path": path, "offset": offset})
            return await asyncio.wait_for(fut, timeout)
        finally:
            self.pending.pop(rid, None)

    async def request_list_all(self, path: str) -> list[dict]:
        entries: list[dict] = []
        offset = 0
        while True:
            res = await self.request_list(path, offset)
            if not res.get("ok"):
                raise PeerError(str(res.get("error", "list_failed")))
            page = res.get("entries", [])
            entries.extend(page)
            if not res.get("more") or not page:
                return entries
            offset += len(page)

    async def request_get(self, path: str) -> None:
        await self._send_ctl({"type": "get", "id": os.urandom(8).hex(), "path": path})

    # ---------------- إرسال ملف ----------------
    async def send_file(self, path: Path, reply_to: Optional[str] = None, dest: Optional[str] = None) -> bool:
        size = path.stat().st_size
        name = sanitize_name(path.name)
        tid = os.urandom(16)
        tidh = tid.hex()
        key = crypto.xfer_key(self.enc_key, tid)
        out = _Out(ack=asyncio.get_running_loop().create_future())
        self.outgoing[tidh] = out
        base = dict(tid=tidh, name=name, size=size, direction="out")
        probe = type("P", (), {"last_emit": 0.0})()
        self._emit("transfer", dict(base, done=0, state="active"))
        sent = 0
        try:
            offer = {"type": "offer", "tid": tidh, "name": name, "size": size}
            if reply_to:
                offer["reply_to"] = reply_to
            if dest is not None:
                offer["dest"] = dest
            await self._send_ctl(offer)
            digest = hashlib.sha256()
            seq = 0
            with open(path, "rb") as f:
                while True:
                    if out.cancelled:
                        self._emit("transfer", dict(base, done=sent, state="failed" if out.error else "cancelled",
                                                    **({"error": out.error} if out.error else {})))
                        return False
                    chunk = f.read(crypto.CHUNK)
                    if not chunk:
                        break
                    await self._send_binary(crypto.seal_chunk(key, tid, seq, chunk))
                    digest.update(chunk)
                    seq += 1
                    sent += len(chunk)
                    self._progress(tidh, name, size, sent, "out", probe)
            await self._send_ctl({"type": "done", "tid": tidh, "chunks": seq, "size": sent, "sha256": digest.hexdigest()})
            ack = await asyncio.wait_for(out.ack, 120)  # type: ignore[arg-type]
            ok = bool(ack.get("ok"))
            self._emit("transfer", dict(base, done=sent, state="done" if ok else "failed",
                                        **({} if ok else {"error": str(ack.get("error", "rejected"))})))
            return ok
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self._emit("transfer", dict(base, done=sent, state="failed", error=str(e) or type(e).__name__))
            return False
        finally:
            self.outgoing.pop(tidh, None)

    # ---------------- خدمة list/get (اختياري) ----------------
    async def _serve_list(self, m: dict) -> None:
        rid = m.get("id")
        root = self.share_root
        assert root is not None
        d = resolve_under(root, str(m.get("path", "/")))
        if d is None or not d.is_dir() or self._hidden(d):
            return await self._send_ctl({"type": "list_result", "id": rid, "ok": False, "error": "not_found"})
        offset = max(0, int(m.get("offset", 0)))
        try:
            items = sorted((x for x in d.iterdir() if not (self.hide_dotfiles and x.name.startswith("."))),
                           key=lambda p: (not p.is_dir(), p.name.lower()))
        except OSError:
            return await self._send_ctl({"type": "list_result", "id": rid, "ok": False, "error": "denied"})
        page = items[offset: offset + PAGE_SIZE]
        entries = []
        for p in page:
            try:
                st = p.stat()
                entries.append({"n": p.name, "d": p.is_dir(), "s": 0 if p.is_dir() else st.st_size, "m": int(st.st_mtime * 1000)})
            except OSError:
                continue
        await self._send_ctl({"type": "list_result", "id": rid, "ok": True, "path": str(m.get("path", "/")),
                              "entries": entries, "more": offset + PAGE_SIZE < len(items)})

    async def _serve_get(self, m: dict) -> None:
        root = self.share_root
        assert root is not None
        p = resolve_under(root, str(m.get("path", "")))
        if p is None or not p.is_file() or self._hidden(p):
            return await self._send_ctl({"type": "get_error", "id": m.get("id"), "error": "not_found"})
        await self.send_file(p, reply_to=str(m.get("id")))

    # ---------------- عند قطع الاتصال ----------------
    def abort_all(self) -> None:
        for tidh, inc in list(self.incoming.items()):
            try:
                inc.fh.close()
            finally:
                inc.tmp.unlink(missing_ok=True)
            self._emit("transfer", dict(tid=tidh, name=inc.name, size=inc.size, done=inc.received,
                                        direction="in", state="failed", error="disconnected"))
        self.incoming.clear()
        for out in self.outgoing.values():
            out.cancelled = True
            if out.ack and not out.ack.done():
                out.ack.set_exception(PeerError("disconnected"))
        for fut in self.pending.values():
            if not fut.done():
                fut.set_exception(PeerError("disconnected"))
        self.pending.clear()
