"""المحرّك (LAN): يعمل كسيرفر وكعميل في نفس الوقت — بدون إنترنت وبدون سيرفر وسيط.

- يسمع على منفذ TCP، ويُعلن عن نفسه ويكتشف الأجهزة بـ UDP beacons.
- الاقتران لأول مرة (SAS من 6 أرقام تُقارَن على الجهازين)، وبعدها اتصال موثَّق من الطرفين.
- Thread مستقل بحلقة asyncio؛ الأحداث تُنادى على on_event(kind, data) من thread المحرّك.

الأحداث:
  peers     {peers: [{id,name,host,port,paired,online}]}
  pairing   {stage: connecting|request|verify|done|error, ...}
  paired / unpaired {peer_id}
  link      {peer_id, status: up|down}
  transfer  {peer_id,tid,name,size,done,direction,state,...}
  toast     {text}      get_error {error}      peer_rejected {peer_id}
"""
from __future__ import annotations

import asyncio
import base64
import concurrent.futures
import hmac
import json
import logging
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from . import crypto, lan
from .discovery import DEFAULT_PORT as BEACON_PORT, Discovery
from .lan import Conn, LanError
from .peer import PeerSession
from .state import State, download_dir

log = logging.getLogger("cloudlink")
OnEvent = Callable[[str, dict], None]
CONN_ERRORS = (OSError, asyncio.IncompleteReadError, LanError, asyncio.TimeoutError, ValueError)


class EngineError(Exception):
    pass


@dataclass
class Link:
    peer_id: str
    conn: Conn
    session: PeerSession
    task: Optional["asyncio.Future[None]"] = None


class Engine:
    def __init__(self, on_event: OnEvent, state: Optional[State] = None, dl_dir: Optional[Path] = None,
                 beacon_port: int = BEACON_PORT, beacon_targets: Optional[list[tuple[str, int]]] = None,
                 listen_host: str = "0.0.0.0") -> None:
        self.on_event = on_event
        self.state = state or State.load()
        self.download_dir = dl_dir or download_dir()
        self.beacon_port, self.beacon_targets, self.listen_host = beacon_port, beacon_targets, listen_host
        self.loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run, name="cloudlink-engine", daemon=True)
        self._ready = threading.Event()
        self._startup_error: Optional[BaseException] = None
        self.port = 0
        self.server: Optional[asyncio.AbstractServer] = None
        self.discovery: Optional[Discovery] = None
        self.links: dict[str, Link] = {}
        self._link_locks: dict[str, asyncio.Lock] = {}
        self._pair_task: Optional["asyncio.Future[None]"] = None
        self._pair_busy = False
        self._answer: Optional["asyncio.Future[bool]"] = None
        self._handlers: set = set()

    # ------------------------------------------------------------ lifecycle
    def start(self) -> None:
        self._thread.start()
        self._ready.wait(10)
        if self._startup_error:
            raise EngineError(f"startup failed: {self._startup_error}")

    def _run(self) -> None:
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_until_complete(self._startup())
        except BaseException as e:  # noqa: BLE001
            self._startup_error = e
            self._ready.set()
            return
        self._ready.set()
        self.loop.run_forever()

    async def _startup(self) -> None:
        base = self.state.port
        for port in ([base] if base == 0 else range(base, base + 20)):
            try:
                self.server = await asyncio.start_server(self._on_client, self.listen_host, port)
                break
            except OSError:
                continue
        if self.server is None:
            raise EngineError("no free TCP port")
        self.port = self.server.sockets[0].getsockname()[1]
        self.discovery = Discovery(self.state.device_id, self.state.device_name, self.port, self._publish_peers,
                                   listen_port=self.beacon_port, targets=self.beacon_targets)
        await self.discovery.start()
        self._publish_peers()

    async def _shutdown(self) -> None:
        if self.discovery:
            self.discovery.stop()
        if self.server:
            self.server.close()
        await self.cancel_pairing()
        for link in list(self.links.values()):
            link.conn.close()
            if link.task:
                link.task.cancel()
        for t in list(self._handlers):
            t.cancel()
        await asyncio.sleep(0)

    def stop(self) -> None:
        try:
            self.call(self._shutdown()).result(5)
        except Exception:
            pass
        self.loop.call_soon_threadsafe(self.loop.stop)
        self._thread.join(5)
        if not self.loop.is_running():
            self.loop.close()

    def call(self, coro: Awaitable[Any]) -> "concurrent.futures.Future[Any]":
        return asyncio.run_coroutine_threadsafe(coro, self.loop)  # type: ignore[arg-type]

    def _emit(self, kind: str, **data: Any) -> None:
        try:
            self.on_event(kind, data)
        except Exception:  # لا نسمح لخطأ UI بإسقاط المحرّك
            log.exception("on_event failed")

    # ------------------------------------------------------------ peers view
    def _publish_peers(self) -> None:
        seen = self.discovery.peers if self.discovery else {}
        out = []
        for pid, p in self.state.peers.items():
            s = seen.get(pid)
            out.append(dict(id=pid, name=(s["name"] if s else p.get("name", "")), paired=True, online=s is not None,
                            host=(s["host"] if s else p.get("host", "")), port=(s["port"] if s else p.get("port", 0))))
        for pid, s in seen.items():
            if pid not in self.state.peers:
                out.append(dict(id=pid, name=s["name"], paired=False, online=True, host=s["host"], port=s["port"]))
        self._emit("peers", peers=out)

    def _addr(self, peer_id: str) -> tuple[str, int]:
        s = self.discovery.peers.get(peer_id) if self.discovery else None
        if s:
            return s["host"], s["port"]
        p = self.state.peers.get(peer_id, {})
        if p.get("host") and p.get("port"):
            return p["host"], int(p["port"])
        raise EngineError("Device not found on the network")

    # ------------------------------------------------------------ sessions
    def _make_session(self, peer_id: str, conn: Conn, key: bytes) -> PeerSession:
        def emit(kind: str, d: dict) -> None:
            self._emit(kind, peer_id=peer_id, **d)

        return PeerSession(
            key,
            lambda blob: conn.send(lan.T_CTL, blob.encode()),
            lambda frame: conn.send(lan.T_BIN, frame),
            emit, self.download_dir,
            share_root=self.state.get_share_root(), writable=self.state.allow_uploads,
        )

    async def _reader(self, link: Link) -> None:
        conn, session = link.conn, link.session
        try:
            while True:
                typ, payload = await conn.recv()
                if typ == lan.T_CTL:
                    await session.handle_ctl(payload.decode("ascii", "replace"))
                elif typ == lan.T_BIN:
                    session.handle_binary(payload)
                else:
                    raise LanError("bad_frame_type")
        except asyncio.CancelledError:
            raise
        except CONN_ERRORS:
            pass
        finally:
            session.abort_all()
            conn.close()
            if self.links.get(link.peer_id) is link:
                del self.links[link.peer_id]
            self._emit("link", peer_id=link.peer_id, status="down")

    async def _link(self, peer_id: str) -> Link:
        """Outgoing connection to a paired peer (reused while alive)."""
        lock = self._link_locks.setdefault(peer_id, asyncio.Lock())
        async with lock:
            cur = self.links.get(peer_id)
            if cur and not cur.conn.closed:
                return cur
            peer = self.state.peers.get(peer_id)
            if peer is None:
                raise EngineError("not_paired")
            host, port = self._addr(peer_id)
            try:
                conn = await lan.open_conn(host, port)
            except CONN_ERRORS as e:
                raise EngineError(f"Cannot reach device ({e.__class__.__name__})")
            try:
                key = await lan.client_handshake(conn, self.state.device_id, peer_id,
                                                 bytes.fromhex(peer["auth_key"]), bytes.fromhex(peer["enc_key"]))
            except LanError as e:
                conn.close()
                if str(e) in ("not_paired", "auth_failed", "server_auth_failed"):
                    self._emit("peer_rejected", peer_id=peer_id, reason=str(e))
                raise EngineError(f"Handshake failed: {e}")
            except CONN_ERRORS as e:
                conn.close()
                raise EngineError(f"Cannot reach device ({e.__class__.__name__})")
            link = Link(peer_id, conn, self._make_session(peer_id, conn, key))
            link.task = asyncio.ensure_future(self._reader(link))
            self.links[peer_id] = link
            self._emit("link", peer_id=peer_id, status="up")
            return link

    # ------------------------------------------------------------ incoming connections
    async def _on_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        conn = Conn(reader, writer)
        me = asyncio.current_task()
        self._handlers.add(me)
        try:
            first = await conn.recv_json()
            t = first.get("t")
            if t == "hello":
                pid, _peer, key = await lan.server_handshake(conn, self.state.device_id, first,
                                                            self.state.peers.get)
                link = Link(pid, conn, self._make_session(pid, conn, key))
                self._emit("link", peer_id=pid, status="up")
                await self._reader(link)  # runs until the connection ends
            elif t == "pair_req":
                await self._pair_respond(conn, first)
        except asyncio.CancelledError:
            raise
        except CONN_ERRORS:
            pass
        except Exception:
            log.exception("client handler failed")
        finally:
            self._handlers.discard(me)
            conn.close()

    # ------------------------------------------------------------ API للـ UI
    async def list_dir(self, peer_id: str, path: str) -> list[dict]:
        return await (await self._link(peer_id)).session.request_list_all(path)

    async def download(self, peer_id: str, path: str) -> None:
        await (await self._link(peer_id)).session.request_get(path)

    async def upload(self, peer_id: str, local_path: str, dest_dir: str = "/") -> bool:
        return await (await self._link(peer_id)).session.send_file(Path(local_path), dest=dest_dir)

    async def unpair(self, peer_id: str) -> None:
        link = self.links.pop(peer_id, None)
        if link:
            link.conn.close()
        self.state.remove_peer(peer_id)
        self._publish_peers()
        self._emit("unpaired", peer_id=peer_id)

    def set_share_root(self, path: str) -> None:
        self.state.share_root = path
        self.state.save()

    # ------------------------------------------------------------ الاقتران
    async def answer_pairing(self, ok: bool) -> None:
        """UI answer for the current pairing question (accept request / SAS matches)."""
        if self._answer and not self._answer.done():
            self._answer.set_result(ok)

    async def _ask_user(self, timeout: float) -> bool:
        self._answer = self.loop.create_future()
        try:
            return await asyncio.wait_for(self._answer, timeout)
        except asyncio.TimeoutError:
            return False
        finally:
            self._answer = None

    async def start_pairing(self, host: str, port: int) -> None:
        await self.cancel_pairing()
        self._pair_task = asyncio.ensure_future(self._pair_initiate(host, port))

    async def cancel_pairing(self) -> None:
        t, self._pair_task = self._pair_task, None
        if self._answer and not self._answer.done():
            self._answer.set_result(False)
        if t and not t.done():
            t.cancel()
            try:
                await t
            except BaseException:
                pass

    async def _pair_initiate(self, host: str, port: int) -> None:
        conn = None
        try:
            self._emit("pairing", stage="connecting", host=host)
            conn = await lan.open_conn(host, port)
            priv, pub = crypto.generate_keypair()
            nonce = os.urandom(16)
            await conn.send_json({"t": "pair_req", "v": 2, "id": self.state.device_id, "name": self.state.device_name,
                                  "port": self.port, "commit": crypto.pair_commit(pub, nonce)})
            m = await conn.recv_json(timeout=75)  # the other user must accept
            if m.get("t") == "pair_failed":
                return self._emit("pairing", stage="error", reason=str(m.get("reason", "failed")))
            if m.get("t") != "pair_resp":
                raise LanError("bad_pairing")
            peer_pub = base64.b64decode(str(m["pub"]), validate=True)
            await conn.send_json({"t": "pair_reveal", "pub": base64.b64encode(pub).decode(), "nonce": nonce.hex()})
            keys = crypto.derive_session(crypto.shared_secret(priv, peer_pub))
            await self._pair_finish(conn, keys, str(m["id"]), str(m.get("name", ""))[:64], host, port)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self._emit("pairing", stage="error", reason=str(e) or type(e).__name__)
        finally:
            if conn:
                conn.close()

    async def _pair_respond(self, conn: Conn, req: dict) -> None:
        if self._pair_busy:
            await conn.send_json({"t": "pair_failed", "reason": "busy"})
            return
        self._pair_busy = True
        try:
            pid, name = str(req["id"]), str(req.get("name", ""))[:64]
            commit = str(req["commit"])
            if not pid or len(commit) != 64:
                raise LanError("bad_pairing")
            self._emit("pairing", stage="request", peer_name=name, host=conn.remote_host)
            if not await self._ask_user(60):
                await conn.send_json({"t": "pair_failed", "reason": "declined"})
                return self._emit("pairing", stage="error", reason="declined")
            priv, pub = crypto.generate_keypair()
            await conn.send_json({"t": "pair_resp", "id": self.state.device_id, "name": self.state.device_name,
                                  "pub": base64.b64encode(pub).decode()})
            r = await conn.recv_json(timeout=30)
            if r.get("t") != "pair_reveal":
                raise LanError("bad_pairing")
            peer_pub = base64.b64decode(str(r["pub"]), validate=True)
            if not hmac.compare_digest(crypto.pair_commit(peer_pub, bytes.fromhex(str(r["nonce"]))), commit):
                await conn.send_json({"t": "pair_failed", "reason": "commit_mismatch"})
                return self._emit("pairing", stage="error", reason="commit_mismatch")
            keys = crypto.derive_session(crypto.shared_secret(priv, peer_pub))
            port = int(req.get("port", 0)) if isinstance(req.get("port"), int) else 0
            await self._pair_finish(conn, keys, pid, name, conn.remote_host, port)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self._emit("pairing", stage="error", reason=str(e) or type(e).__name__)
        finally:
            self._pair_busy = False

    async def _pair_finish(self, conn: Conn, keys: crypto.SessionKeys, pid: str, name: str, host: str, port: int) -> None:
        """Both users compare the SAS; each side proves it derived the same keys."""
        self._emit("pairing", stage="verify", sas=keys.sas, peer_name=name)
        if not await self._ask_user(120):
            try:
                await conn.send_json({"t": "pair_failed", "reason": "declined"})
            except Exception:
                pass
            return self._emit("pairing", stage="error", reason="declined")
        mine = crypto.auth_hash(keys.auth_key)
        await conn.send_json({"t": "pair_confirm", "h": mine})
        m = await conn.recv_json(timeout=120)
        if m.get("t") == "pair_failed":
            return self._emit("pairing", stage="error", reason=str(m.get("reason", "declined")))
        if m.get("t") != "pair_confirm" or not hmac.compare_digest(str(m.get("h", "")), mine):
            return self._emit("pairing", stage="error", reason="sas_mismatch")
        self.state.add_peer(pid, name, keys.auth_key, keys.enc_key, host, port)
        self._emit("pairing", stage="done", peer_id=pid)
        self._emit("paired", peer_id=pid, peer_name=name)
        self._publish_peers()
