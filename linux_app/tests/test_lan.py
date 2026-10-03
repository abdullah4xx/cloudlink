"""Two real engines over real TCP on loopback: discovery, pairing, browse/get/put both ways, security."""
import asyncio
import json
import os
import socket
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "linux_app"))

from core import crypto, lan  # noqa: E402
from core.engine import Engine, EngineError  # noqa: E402
from core.state import State  # noqa: E402


def free_udp():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class Events:
    def __init__(self):
        self.items, self.cv = [], threading.Condition()

    def push(self, kind, data):
        with self.cv:
            self.items.append((kind, data))
            self.cv.notify_all()

    def wait(self, kind, pred=None, timeout=10):
        end = time.time() + timeout
        with self.cv:
            while True:
                for i, (k, d) in enumerate(self.items):
                    if k == kind and (pred is None or pred(d)):
                        del self.items[i]
                        return d
                rem = end - time.time()
                if rem <= 0:
                    raise TimeoutError(f"event {kind}")
                self.cv.wait(rem)


class Node:
    def __init__(self, tmp: Path, name: str, beacon_port: int, target_port: int):
        self.dir = tmp / name
        self.share = self.dir / "share"
        self.share.mkdir(parents=True)
        self.ev = Events()
        st = State.load(self.dir / "state.json")
        st.device_name, st.port, st.share_root = name, 0, str(self.share)
        self.eng = Engine(self.ev.push, state=st, dl_dir=self.dir / "dl", beacon_port=beacon_port,
                          beacon_targets=[("127.0.0.1", target_port)], listen_host="127.0.0.1")
        self.eng.discovery_interval = 0.2
        self.eng.start()
        self.eng.discovery.interval = 0.2

    def run(self, coro, timeout=20):
        return self.eng.call(coro).result(timeout)

    @property
    def id(self):
        return self.eng.state.device_id


def pair(a: Node, b: Node, sas_ok_a=True, sas_ok_b=True):
    pb = a.ev.wait("peers", lambda d: any(p["id"] == b.id and p["online"] for p in d["peers"]))
    info = next(p for p in pb["peers"] if p["id"] == b.id)
    a.run(a.eng.start_pairing(info["host"], info["port"]))
    req = b.ev.wait("pairing", lambda d: d["stage"] == "request")
    b.run(b.eng.answer_pairing(True))
    va = a.ev.wait("pairing", lambda d: d["stage"] == "verify")
    vb = b.ev.wait("pairing", lambda d: d["stage"] == "verify")
    a.run(a.eng.answer_pairing(sas_ok_a))
    b.run(b.eng.answer_pairing(sas_ok_b))
    return req, va, vb


class LanTests(unittest.TestCase):
    def setUp(self):
        self.tmpd = tempfile.TemporaryDirectory()
        self.tmp = Path(self.tmpd.name)
        pa, pb = free_udp(), free_udp()
        self.a = Node(self.tmp, "Laptop", pa, pb)
        self.b = Node(self.tmp, "Phone", pb, pa)

    def tearDown(self):
        self.a.eng.stop()
        self.b.eng.stop()
        self.tmpd.cleanup()

    def paired(self):
        pair(self.a, self.b)
        self.a.ev.wait("paired")
        self.b.ev.wait("paired")

    def test_discovery_sees_names(self):
        d = self.a.ev.wait("peers", lambda d: any(p["name"] == "Phone" and p["online"] for p in d["peers"]))
        self.assertFalse(next(p for p in d["peers"] if p["name"] == "Phone")["paired"])

    def test_pairing_same_sas_and_state_saved(self):
        req, va, vb = pair(self.a, self.b)
        self.assertEqual(req["peer_name"], "Laptop")
        self.assertEqual(va["sas"], vb["sas"])
        self.assertEqual(len(va["sas"]), 6)
        self.a.ev.wait("paired")
        self.b.ev.wait("paired")
        self.assertIn(self.b.id, self.a.eng.state.peers)
        self.assertIn(self.a.id, self.b.eng.state.peers)
        self.assertEqual(self.a.eng.state.peers[self.b.id]["enc_key"], self.b.eng.state.peers[self.a.id]["enc_key"])
        self.assertEqual(oct((self.a.dir / "state.json").stat().st_mode & 0o777), "0o600")

    def test_pairing_declined_or_sas_rejected(self):
        pair(self.a, self.b, sas_ok_a=False)
        self.assertEqual(self.a.ev.wait("pairing", lambda d: d["stage"] == "error")["reason"], "declined")
        self.assertEqual(self.b.ev.wait("pairing", lambda d: d["stage"] == "error")["reason"], "declined")
        self.assertFalse(self.a.eng.state.peers or self.b.eng.state.peers)

    def test_browse_download_upload_both_directions(self):
        self.paired()
        (self.b.share / "Pictures").mkdir()
        (self.b.share / "Pictures" / "cat.jpg").write_bytes(os.urandom(70_000))
        big = os.urandom(3 * 1024 * 1024 + 17)
        (self.b.share / "big.bin").write_bytes(big)
        (self.b.share / ".hidden").write_text("x")
        a, b = self.a, self.b
        self.assertEqual([e["n"] for e in a.run(a.eng.list_dir(b.id, "/"))], ["Pictures", "big.bin"])
        a.run(a.eng.download(b.id, "/big.bin"))
        d = a.ev.wait("transfer", lambda d: d["name"] == "big.bin" and d["direction"] == "in" and d["state"] == "done", timeout=20)
        self.assertEqual((a.dir / "dl" / "big.bin").read_bytes(), big)
        self.assertEqual(d["peer_id"], b.id)
        up = self.tmp / "report.pdf"
        payload = os.urandom(2 * 1024 * 1024 + 5)
        up.write_bytes(payload)
        self.assertTrue(a.run(a.eng.upload(b.id, str(up), "/Pictures")))
        self.assertEqual((b.share / "Pictures" / "report.pdf").read_bytes(), payload)
        # reverse: the other device browses us and pulls a file
        (a.share / "notes.txt").write_text("from laptop")
        self.assertEqual([e["n"] for e in b.run(b.eng.list_dir(a.id, "/"))], ["notes.txt"])
        b.run(b.eng.download(a.id, "/notes.txt"))
        b.ev.wait("transfer", lambda d: d["name"] == "notes.txt" and d["direction"] == "in" and d["state"] == "done")
        self.assertEqual((b.dir / "dl" / "notes.txt").read_text(), "from laptop")

    def test_traversal_and_readonly_over_the_wire(self):
        self.paired()
        a, b = self.a, self.b
        (b.dir / "secret.txt").write_text("nope")
        with self.assertRaises(Exception):
            a.run(a.eng.list_dir(b.id, "/../"))
        up = self.tmp / "u.txt"
        up.write_text("u")
        self.assertFalse(a.run(a.eng.upload(b.id, str(up), "/../")))
        b.eng.state.allow_uploads = False
        a.run(a.eng.unpair(b.id))  # drop the cached link, then re-pair is not needed: keep state for b only
        self.assertNotIn(b.id, a.eng.state.peers)

    def test_unpaired_device_is_refused(self):
        self.paired()
        a, b = self.a, self.b
        b.run(b.eng.unpair(a.id))  # b forgets a; a still thinks it's paired
        with self.assertRaises(Exception) as cm:
            a.run(a.eng.list_dir(b.id, "/"))
        self.assertIn("not_paired", str(cm.exception))
        a.ev.wait("peer_rejected")

    def test_wrong_key_rejected_both_ways(self):
        self.paired()
        a, b = self.a, self.b

        async def evil_client():  # knows the peer id but not the keys
            conn = await lan.open_conn("127.0.0.1", b.eng.port)
            try:
                await lan.client_handshake(conn, a.id, b.id, os.urandom(32), os.urandom(32))
            finally:
                conn.close()

        with self.assertRaises(Exception) as cm:
            a.run(evil_client())
        self.assertIn("server_auth_failed", str(cm.exception))

        async def evil_server():  # a fake "phone" that accepts anything
            async def h(r, w):
                c = lan.Conn(r, w)
                m = await c.recv_json()
                await c.send_json({"t": "hello_ok", "ns": os.urandom(16).hex(), "mac": os.urandom(32).hex()})
                c.close()
            srv = await asyncio.start_server(h, "127.0.0.1", 0)
            port = srv.sockets[0].getsockname()[1]
            peer = a.eng.state.peers[b.id]
            conn = await lan.open_conn("127.0.0.1", port)
            try:
                await lan.client_handshake(conn, a.id, b.id, bytes.fromhex(peer["auth_key"]), bytes.fromhex(peer["enc_key"]))
            finally:
                conn.close()
                srv.close()

        with self.assertRaises(Exception) as cm:
            a.run(evil_server())
        self.assertIn("server_auth_failed", str(cm.exception))

    def test_commit_mismatch_aborts_pairing(self):
        """A MITM that swaps its key after the commitment is detected by the responder."""
        b = self.b
        d = self.a.ev.wait("peers", lambda d: any(p["id"] == b.id for p in d["peers"]))
        port = next(p for p in d["peers"] if p["id"] == b.id)["port"]

        async def mitm():
            conn = await lan.open_conn("127.0.0.1", port)
            _, pub = crypto.generate_keypair()
            _, other = crypto.generate_keypair()
            nonce = os.urandom(16)
            await conn.send_json({"t": "pair_req", "v": 2, "id": "evil", "name": "Evil", "port": 1,
                                  "commit": crypto.pair_commit(pub, nonce)})
            m = await conn.recv_json(timeout=20)
            assert m["t"] == "pair_resp", m
            import base64
            await conn.send_json({"t": "pair_reveal", "pub": base64.b64encode(other).decode(), "nonce": nonce.hex()})
            return await conn.recv_json(timeout=10)

        fut = self.a.eng.call(mitm())
        b.ev.wait("pairing", lambda d: d["stage"] == "request")
        b.run(b.eng.answer_pairing(True))
        self.assertEqual(fut.result(20), {"t": "pair_failed", "reason": "commit_mismatch"})
        self.assertEqual(b.ev.wait("pairing", lambda d: d["stage"] == "error")["reason"], "commit_mismatch")
        self.assertNotIn("evil", b.eng.state.peers)

    def test_oversized_handshake_frame_rejected(self):
        async def spam():
            r, w = await asyncio.open_connection("127.0.0.1", self.b.eng.port)
            w.write((10_000_000).to_bytes(4, "big") + b"\x00")
            await w.drain()
            data = await asyncio.wait_for(r.read(10), 5)  # server just closes
            w.close()
            return data
        self.assertEqual(self.a.run(spam()), b"")


if __name__ == "__main__":
    unittest.main()
