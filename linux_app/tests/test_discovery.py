import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import discovery  # noqa: E402


class BroadcastTargets(unittest.TestCase):
    def test_default_targets_shape(self):
        t = discovery.default_targets()
        self.assertEqual(t[0], ("255.255.255.255", discovery.DEFAULT_PORT))
        for host, port in t:
            self.assertEqual(port, discovery.DEFAULT_PORT)
            self.assertEqual(len(host.split(".")), 4)
        self.assertEqual(len(set(t)), len(t))  # no duplicates

    def test_broadcast_addrs_never_raises_or_returns_unusable(self):
        for a in discovery.broadcast_addrs():
            self.assertNotIn(a, ("0.0.0.0", "255.255.255.255"))


if __name__ == "__main__":
    unittest.main()
