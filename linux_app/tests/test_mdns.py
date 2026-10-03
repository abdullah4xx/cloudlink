"""mDNS plumbing that doesn't need the network: sticky peers, drop, local IP helper."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.discovery import Discovery  # noqa: E402
from core.netinfo import local_ips  # noqa: E402

changes = []
d = Discovery("me", "Me", 47616, lambda: changes.append(1), targets=[])
assert d.use_mdns is False  # explicit targets (tests) → no real mDNS

d._ingest("p1", "Phone", "192.168.1.9", 47616, True)
assert d.peers["p1"]["sticky"] and len(changes) == 1
d._ingest("p1", "Phone", "192.168.1.9", 47616, False)   # a later UDP beacon keeps it sticky
assert d.peers["p1"]["sticky"] and len(changes) == 1     # unchanged → no event
d.peers["p1"]["seen"] = time.monotonic() - 999
now = time.monotonic()
assert not [k for k, v in d.peers.items() if not v.get("sticky") and now - v["seen"] > d.ttl]  # never TTL-expired
d._drop("p1")
assert "p1" not in d.peers and len(changes) == 2

for ip in local_ips():
    assert ip.count(".") == 3 and not ip.startswith("127.")
print("OK")
