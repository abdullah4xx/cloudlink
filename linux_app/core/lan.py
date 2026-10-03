"""LAN transport: length-prefixed TCP frames + mutual-auth handshake (no internet, no server).

Frame:  u32 length(type+payload) | u8 type | payload
  type 0 = handshake/pairing JSON (plaintext, small)
  type 1 = encrypted control message (base64 blob, see crypto.seal_ctl)
  type 2 = encrypted file chunk frame (see crypto.seal_chunk)
"""
from __future__ import annotations

import asyncio
import hmac
import json
import os
import struct
from typing import Callable, Optional

from . import crypto

T_HS, T_CTL, T_BIN = 0, 1, 2
MAX_HS = 4096
MAX_FRAME = 1 << 20


class LanError(Exception):
    pass


class Conn:
    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.r, self.w = reader, writer
        self._lock = asyncio.Lock()
        self.closed = False

    @property
    def remote_host(self) -> str:
        peer = self.w.get_extra_info("peername")
        return str(peer[0]) if peer else ""

    async def send(self, typ: int, payload: bytes) -> None:
        if self.closed:
            raise LanError("closed")
        data = struct.pack(">IB", len(payload) + 1, typ) + payload
        async with self._lock:
            self.w.write(data)
            await self.w.drain()

    async def recv(self, limit: int = MAX_FRAME) -> tuple[int, bytes]:
        n, typ = struct.unpack(">IB", await self.r.readexactly(5))
        if n < 1 or n - 1 > limit:
            raise LanError("bad_frame")
        return typ, await self.r.readexactly(n - 1)

    async def send_json(self, obj: dict) -> None:
        await self.send(T_HS, json.dumps(obj, separators=(",", ":")).encode())

    async def recv_json(self, timeout: float = 10.0) -> dict:
        typ, p = await asyncio.wait_for(self.recv(MAX_HS), timeout)
        if typ != T_HS:
            raise LanError("expected_handshake")
        m = json.loads(p)
        if not isinstance(m, dict):
            raise LanError("bad_json")
        return m

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            try:
                self.w.close()
            except Exception:
                pass


async def open_conn(host: str, port: int, timeout: float = 6.0) -> Conn:
    r, w = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
    return Conn(r, w)


def _hex(m: dict, key: str, n: int | None = None) -> bytes:
    try:
        b = bytes.fromhex(str(m.get(key, "")))
    except ValueError:
        raise LanError("bad_handshake")
    if n is not None and len(b) != n:
        raise LanError("bad_handshake")
    return b


async def client_handshake(conn: Conn, my_id: str, peer_id: str, auth_key: bytes, enc_key: bytes) -> bytes:
    """Initiator side. Authenticates BOTH sides, returns the per-connection session key."""
    nc = os.urandom(16)
    await conn.send_json({"t": "hello", "v": 2, "id": my_id, "nc": nc.hex()})
    m = await conn.recv_json()
    if m.get("t") == "error":
        raise LanError(str(m.get("code", "error")))
    if m.get("t") != "hello_ok":
        raise LanError("bad_handshake")
    ns = _hex(m, "ns", 16)
    expect = crypto.mac(auth_key, b"srv", my_id.encode(), peer_id.encode(), nc, ns)
    if not hmac.compare_digest(expect, _hex(m, "mac", 32)):
        raise LanError("server_auth_failed")
    await conn.send_json({"t": "auth", "mac": crypto.mac(auth_key, b"cli", my_id.encode(), peer_id.encode(), nc, ns).hex()})
    return crypto.session_key(enc_key, nc, ns)


async def server_handshake(conn: Conn, my_id: str, hello: dict,
                           lookup: Callable[[str], Optional[dict]]) -> tuple[str, dict, bytes]:
    """Responder side, `hello` already read. Returns (peer_id, peer_record, session_key)."""
    if hello.get("v") != 2:
        raise LanError("bad_version")
    cid = str(hello.get("id", ""))
    nc = _hex(hello, "nc", 16)
    peer = lookup(cid)
    if peer is None:
        await conn.send_json({"t": "error", "code": "not_paired"})
        raise LanError("not_paired")
    auth_key, enc_key = bytes.fromhex(peer["auth_key"]), bytes.fromhex(peer["enc_key"])
    ns = os.urandom(16)
    await conn.send_json({"t": "hello_ok", "ns": ns.hex(),
                          "mac": crypto.mac(auth_key, b"srv", cid.encode(), my_id.encode(), nc, ns).hex()})
    a = await conn.recv_json()
    expect = crypto.mac(auth_key, b"cli", cid.encode(), my_id.encode(), nc, ns)
    try:
        got = bytes.fromhex(str(a.get("mac", "")))
    except ValueError:
        got = b""
    if a.get("t") != "auth" or not hmac.compare_digest(expect, got):
        await conn.send_json({"t": "error", "code": "auth_failed"})
        raise LanError("auth_failed")
    return cid, peer, crypto.session_key(enc_key, nc, ns)
