"""LAN discovery: tiny UDP broadcast beacons (stdlib only).

Each device broadcasts {"app":"cloudlink","v":2,"id","name","port"} every `interval` seconds and listens
for others. Beacons are unauthenticated hints (address + display name) — trust comes from pairing/handshake.
"""
from __future__ import annotations

import asyncio
import json
import socket
import struct
import time
from typing import Callable, Optional

DEFAULT_PORT = 47615
_SIOCGIFBRDADDR = 0x8919


def broadcast_addrs() -> list[str]:
    """Directed-broadcast address of every IPv4 interface (Linux). Many routers/APs drop 255.255.255.255
    but forward subnet-directed broadcasts, so we beacon to both. Returns [] if it can't be determined."""
    out: list[str] = []
    try:
        import fcntl
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            for _, name in socket.if_nameindex():
                try:
                    raw = fcntl.ioctl(s.fileno(), _SIOCGIFBRDADDR, struct.pack("256s", name.encode()[:15]))
                    addr = socket.inet_ntoa(raw[20:24])
                except OSError:
                    continue
                if addr not in ("0.0.0.0", "255.255.255.255") and addr not in out:
                    out.append(addr)
        finally:
            s.close()
    except Exception:  # noqa: BLE001 - non-Linux / restricted environments
        pass
    return out


def default_targets() -> list[tuple[str, int]]:
    return [("255.255.255.255", DEFAULT_PORT)] + [(a, DEFAULT_PORT) for a in broadcast_addrs()]


class Discovery:
    def __init__(self, my_id: str, my_name: str, tcp_port: int, on_change: Callable[[], None],
                 listen_port: int = DEFAULT_PORT, targets: Optional[list[tuple[str, int]]] = None,
                 interval: float = 2.0, ttl: float = 8.0) -> None:
        self.my_id, self.my_name, self.tcp_port = my_id, my_name, tcp_port
        self.on_change = on_change
        self.listen_port = listen_port
        self.targets = targets if targets is not None else default_targets()
        self.interval, self.ttl = interval, ttl
        self.peers: dict[str, dict] = {}
        self._transport: Optional[asyncio.DatagramTransport] = None
        self._task: Optional[asyncio.Future] = None

    async def start(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        if hasattr(socket, "SO_REUSEPORT") and self.listen_port == DEFAULT_PORT:
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            except OSError:
                pass
        sock.bind(("0.0.0.0", self.listen_port))
        sock.setblocking(False)
        loop = asyncio.get_running_loop()
        outer = self

        class Proto(asyncio.DatagramProtocol):
            def datagram_received(self, data: bytes, addr) -> None:  # noqa: ANN001
                outer._on_datagram(data, addr[0])

        self._transport, _ = await loop.create_datagram_endpoint(Proto, sock=sock)  # type: ignore[assignment]
        self._task = asyncio.ensure_future(self._loop())

    def stop(self) -> None:
        if self._task:
            self._task.cancel()
        if self._transport:
            self._transport.close()

    def _on_datagram(self, data: bytes, host: str) -> None:
        try:
            m = json.loads(data)
            if m.get("app") != "cloudlink" or m.get("v") != 2:
                return
            pid, name, port = str(m["id"]), str(m.get("name", ""))[:64], int(m["port"])
            if pid == self.my_id or not (0 < port < 65536):
                return
        except (ValueError, KeyError, TypeError):
            return
        old = self.peers.get(pid)
        new = {"id": pid, "name": name, "host": host, "port": port, "seen": time.monotonic()}
        self.peers[pid] = new
        if old is None or (old["name"], old["host"], old["port"]) != (name, host, port):
            self.on_change()

    async def _loop(self) -> None:
        beacon = json.dumps({"app": "cloudlink", "v": 2, "id": self.my_id, "name": self.my_name,
                             "port": self.tcp_port}).encode()
        while True:
            for t in self.targets:
                try:
                    assert self._transport is not None
                    self._transport.sendto(beacon, t)
                except OSError:
                    pass
            now = time.monotonic()
            gone = [k for k, v in self.peers.items() if now - v["seen"] > self.ttl]
            for k in gone:
                del self.peers[k]
            if gone:
                self.on_change()
            await asyncio.sleep(self.interval)
