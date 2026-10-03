"""Local network info: the IPv4 addresses other devices can use to reach this computer."""
from __future__ import annotations

import socket


def local_ips() -> list[str]:
    """Private IPv4 addresses of this machine, best guess first (the default-route address)."""
    out: list[str] = []
    try:  # the address the OS would use to reach the LAN (no packet is actually sent for UDP connect)
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("10.255.255.255", 1))
            out.append(s.getsockname()[0])
        finally:
            s.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            out.append(info[4][0])
    except OSError:
        pass
    try:
        import fcntl
        import struct
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            for _, name in socket.if_nameindex():
                try:
                    raw = fcntl.ioctl(s.fileno(), 0x8915, struct.pack("256s", name.encode()[:15]))  # SIOCGIFADDR
                    out.append(socket.inet_ntoa(raw[20:24]))
                except OSError:
                    continue
        finally:
            s.close()
    except Exception:  # noqa: BLE001 - non-Linux
        pass
    seen: list[str] = []
    for ip in out:
        if ip and not ip.startswith(("127.", "0.", "169.254.")) and ip not in seen:
            seen.append(ip)
    return seen
