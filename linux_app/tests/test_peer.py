"""اختبار PeerSession: جلستان متصلتان مباشرة (بدون شبكة)."""
import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "linux_app"))

from core import crypto  # noqa: E402
from core.peer import PeerSession, resolve_under, sanitize_name  # noqa: E402


class Pair:
    """A <-> B بنقل في الذاكرة. tamper(frame)->frame لتخريب الإطارات."""

    def __init__(self, tmp: Path, share_b: Path | None = None, tamper=None):
        enc = os.urandom(32)
        self.ev_a, self.ev_b = [], []
        self.tamper = tamper
        self.a = PeerSession(enc, self._to_b_text, self._to_b_bin, lambda k, d: self.ev_a.append((k, d)), tmp / "a")
        self.b = PeerSession(enc, self._to_a_text, self._to_a_bin, lambda k, d: self.ev_b.append((k, d)), tmp / "b", share_root=share_b)

    async def _to_b_text(self, text):
        await self.b.handle_ctl(text)

    async def _to_a_text(self, text):
        await self.a.handle_ctl(text)

    async def _to_b_bin(self, data):
        self.b.handle_binary(self.tamper(data) if self.tamper else data)

    async def _to_a_bin(self, data):
        self.a.handle_binary(data)


def last_state(events, direction):
    xs = [d for k, d in events if k == "transfer" and d["direction"] == direction]
    return xs[-1] if xs else None


class PeerTests(unittest.IsolatedAsyncioTestCase):
    async def test_send_multi_chunk_file(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            data = os.urandom(crypto.CHUNK * 5 + 123)
            src = tmp / "my file.bin"
            src.write_bytes(data)
            p = Pair(tmp)
            ok = await p.a.send_file(src)
            self.assertTrue(ok)
            self.assertEqual((tmp / "b" / "my file.bin").read_bytes(), data)
            self.assertEqual(last_state(p.ev_b, "in")["state"], "done")
            self.assertEqual(last_state(p.ev_a, "out")["state"], "done")
            self.assertFalse(list((tmp / "b").glob("*.part")))

    async def test_empty_file_and_name_collision(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            src = tmp / "e.txt"
            src.write_bytes(b"")
            p = Pair(tmp)
            self.assertTrue(await p.a.send_file(src))
            self.assertTrue(await p.a.send_file(src))
            names = sorted(x.name for x in (tmp / "b").iterdir())
            self.assertEqual(names, ["e (1).txt", "e.txt"])

    async def test_tampered_chunk_fails_and_cleans_up(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            src = tmp / "x.bin"
            src.write_bytes(os.urandom(100_000))

            def tamper(f):
                b = bytearray(f)
                b[-1] ^= 0xFF
                return bytes(b)

            p = Pair(tmp, tamper=tamper)
            self.assertFalse(await p.a.send_file(src))
            self.assertEqual(last_state(p.ev_b, "in")["state"], "failed")
            self.assertEqual(list((tmp / "b").iterdir()), [])

    async def test_dropped_chunk_detected(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            src = tmp / "x.bin"
            src.write_bytes(os.urandom(crypto.CHUNK * 3))
            n = {"i": 0}

            def drop_second(f):
                n["i"] += 1
                return b"" if n["i"] == 2 else f

            p = Pair(tmp, tamper=drop_second)
            self.assertFalse(await p.a.send_file(src))
            self.assertEqual(list((tmp / "b").iterdir()), [])

    async def test_list_and_get(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            share = tmp / "share"
            (share / "Docs").mkdir(parents=True)
            (share / "Docs" / "a.txt").write_text("hello")
            for i in range(650):  # أكثر من صفحتين
                (share / f"f{i:04d}.dat").write_bytes(b"x")
            p = Pair(tmp, share_b=share)
            entries = await p.a.request_list_all("/")
            self.assertEqual(len(entries), 651)
            self.assertTrue(entries[0]["d"] and entries[0]["n"] == "Docs")
            sub = await p.a.request_list_all("/Docs")
            self.assertEqual([e["n"] for e in sub], ["a.txt"])
            await p.a.request_get("/Docs/a.txt")
            await asyncio.sleep(0.2)
            self.assertEqual((tmp / "a" / "a.txt").read_text(), "hello")

    async def test_path_traversal_blocked(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            share = tmp / "share"
            share.mkdir()
            (tmp / "secret.txt").write_text("nope")
            p = Pair(tmp, share_b=share)
            res = await p.a.request_list("/../")
            self.assertFalse(res["ok"])
            await p.a.request_get("/../secret.txt")
            await asyncio.sleep(0.1)
            self.assertFalse((tmp / "a" / "secret.txt").exists())
            self.assertTrue(any(k == "get_error" for k, _ in p.ev_a))
            (share / "link").symlink_to(tmp)
            self.assertIsNone(resolve_under(share, "/link/secret.txt"))

    async def test_upload_to_dest_folder_and_blocked_dests(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            share = tmp / "share"
            (share / "Docs").mkdir(parents=True)
            (share / ".ssh").mkdir()
            src = tmp / "up.txt"
            src.write_text("payload")
            p = Pair(tmp, share_b=share)
            self.assertTrue(await p.a.send_file(src, dest="/Docs"))
            self.assertEqual((share / "Docs" / "up.txt").read_text(), "payload")
            for bad in ("/../", "/.ssh", "/nope", "/Docs/up.txt"):
                self.assertFalse(await p.a.send_file(src, dest=bad), bad)
            self.assertEqual(list((share / ".ssh").iterdir()), [])
            self.assertEqual(last_state(p.ev_a, "out")["state"], "failed")

    async def test_read_only_share_rejects_upload(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            share = tmp / "share"
            share.mkdir()
            src = tmp / "up.txt"
            src.write_text("x")
            p = Pair(tmp, share_b=share)
            p.b.writable = False
            self.assertFalse(await p.a.send_file(src, dest="/"))
            self.assertEqual(last_state(p.ev_a, "out")["error"], "forbidden")
            self.assertEqual(list(share.iterdir()), [])

    async def test_dotfiles_hidden(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            share = tmp / "share"
            (share / ".ssh").mkdir(parents=True)
            (share / ".ssh" / "id_rsa").write_text("secret")
            (share / "ok.txt").write_text("fine")
            p = Pair(tmp, share_b=share)
            self.assertEqual([e["n"] for e in await p.a.request_list_all("/")], ["ok.txt"])
            self.assertFalse((await p.a.request_list("/.ssh"))["ok"])
            await p.a.request_get("/.ssh/id_rsa")
            await asyncio.sleep(0.1)
            self.assertFalse((tmp / "a" / "id_rsa").exists())

    def test_sanitize(self):
        self.assertEqual(sanitize_name("../../etc/passwd"), "passwd")
        self.assertEqual(sanitize_name("..\\..\\x.txt"), "x.txt")
        self.assertEqual(sanitize_name(".."), "file")
        self.assertEqual(sanitize_name(""), "file")
        self.assertEqual(sanitize_name("a\x00b\n.txt"), "ab.txt")


if __name__ == "__main__":
    unittest.main()
