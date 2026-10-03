"""mDNS / DNS-SD discovery (Bonjour / Avahi compatible) using python-zeroconf.

Every device registers `<id>._cloudlink._tcp.local.` with TXT {id, name, v}. Unlike UDP broadcast, multicast DNS
is accepted by nearly every home router, so devices find each other without typing an IP.
`zeroconf` is optional: if it is missing, `available()` is False and discovery falls back to UDP broadcast.
"""
from __future__ import annotations

import logging
import re
import socket
import threading
from typing import Callable, Optional

from .netinfo import local_ips

try:  # optional dependency
    from zeroconf import ServiceBrowser, ServiceInfo, ServiceStateChange, Zeroconf
except Exception:  # noqa: BLE001
    Zeroconf = None  # type: ignore[assignment,misc]

log = logging.getLogger("cloudlink.mdns")
SERVICE_TYPE = "_cloudlink._tcp.local."


def available() -> bool:
    return Zeroconf is not None


class Mdns:
    """on_found(id, name, host, port) / on_lost(id) are called from zeroconf threads."""

    def __init__(self, my_id: str, my_name: str, tcp_port: int,
                 on_found: Callable[[str, str, str, int], None], on_lost: Callable[[str], None]) -> None:
        self.my_id, self.my_name, self.tcp_port = my_id, my_name, tcp_port
        self.on_found, self.on_lost = on_found, on_lost
        self._zc: Optional["Zeroconf"] = None
        self._browser = None
        self._info: Optional["ServiceInfo"] = None
        self._names: dict[str, str] = {}   # service name -> peer id
        self._closed = False

    @property
    def _instance(self) -> str:
        return re.sub(r"[^A-Za-z0-9-]", "", self.my_id)[:16] or "device"

    def start(self) -> None:
        """Blocking — call from a worker thread, not from the asyncio loop."""
        if Zeroconf is None:
            raise RuntimeError("python3-zeroconf is not installed")
        self._zc = Zeroconf()
        ips = local_ips()
        self._info = ServiceInfo(
            SERVICE_TYPE, f"{self._instance}.{SERVICE_TYPE}",
            addresses=[socket.inet_aton(ip) for ip in ips] or None,
            port=self.tcp_port,
            properties={"id": self.my_id, "name": self.my_name[:60], "v": "2"},
            server=f"cloudlink-{self._instance}.local.",
        )
        self._zc.register_service(self._info, allow_name_change=True)
        self._browser = ServiceBrowser(self._zc, SERVICE_TYPE, handlers=[self._on_change])

    def stop(self) -> None:
        """Blocking — call from a worker thread."""
        self._closed = True
        try:
            if self._zc is not None:
                if self._info is not None:
                    self._zc.unregister_service(self._info)
                self._zc.close()
        except Exception:  # noqa: BLE001
            log.debug("mdns stop failed", exc_info=True)

    # zeroconf thread
    def _on_change(self, zeroconf, service_type, name, state_change) -> None:  # noqa: ANN001
        if self._closed:
            return
        if state_change == ServiceStateChange.Removed:
            pid = self._names.pop(name, None)
            if pid:
                self.on_lost(pid)
            return
        # resolving blocks, so never do it on the browser thread
        threading.Thread(target=self._resolve, args=(zeroconf, service_type, name), daemon=True).start()

    def _resolve(self, zeroconf, service_type: str, name: str) -> None:  # noqa: ANN001
        try:
            info = zeroconf.get_service_info(service_type, name, timeout=3000)
            if info is None or self._closed:
                return
            props = {(k.decode() if isinstance(k, bytes) else k): (v.decode("utf-8", "replace") if isinstance(v, bytes) else v)
                     for k, v in (info.properties or {}).items()}
            pid = str(props.get("id") or "")
            if not pid or pid == self.my_id or not info.port:
                return
            addrs = [a for a in info.parsed_addresses() if "." in a and not a.startswith(("127.", "169.254."))]
            if not addrs:
                return
            self._names[name] = pid
            self.on_found(pid, str(props.get("name") or "")[:64], addrs[0], int(info.port))
        except Exception:  # noqa: BLE001
            log.debug("mdns resolve failed", exc_info=True)
