import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "linux_app"))

from core import crypto  # noqa: E402

V = json.loads((ROOT / "protocol" / "test_vectors.json").read_text())


class CryptoVectors(unittest.TestCase):
    def test_key_agreement_and_derivation(self):
        privA, privB = bytes.fromhex(V["privA"]), bytes.fromhex(V["privB"])
        self.assertEqual(crypto.public_from_private(privA).hex(), V["pubA"])
        self.assertEqual(crypto.public_from_private(privB).hex(), V["pubB"])
        sh = crypto.shared_secret(privA, bytes.fromhex(V["pubB"]))
        self.assertEqual(sh.hex(), V["shared"])
        k = crypto.derive_session(sh)
        self.assertEqual(k.auth_key.hex(), V["authKey"])
        self.assertEqual(k.enc_key.hex(), V["encKey"])
        self.assertEqual(k.sas, V["sas"])
        self.assertEqual(crypto.auth_hash(k.auth_key), V["authHash"])

    def test_chunk_vector(self):
        enc = bytes.fromhex(V["encKey"])
        tid = bytes.fromhex(V["tid"])
        key = crypto.xfer_key(enc, tid)
        self.assertEqual(key.hex(), V["xferKey"])
        frame = crypto.seal_chunk(key, tid, V["chunkSeq"], V["chunkPlain"].encode())
        self.assertEqual(frame.hex(), V["chunkFrame"])
        t, seq, ct = crypto.parse_frame(frame)
        self.assertEqual(crypto.open_chunk(key, t, seq, ct), V["chunkPlain"].encode())

    def test_ctl_vector(self):
        enc = bytes.fromhex(V["encKey"])
        self.assertEqual(json.dumps(crypto.open_ctl(enc, V["ctlBlob"]), separators=(",", ":")), V["ctlJson"])
        self.assertEqual(crypto.seal_ctl(enc, {"type": "ping"}, nonce=bytes(range(12))), V["ctlBlob"])

    def test_tamper_detected(self):
        enc = bytes.fromhex(V["encKey"])
        tid = bytes.fromhex(V["tid"])
        key = crypto.xfer_key(enc, tid)
        f = bytearray(crypto.seal_chunk(key, tid, 0, b"data"))
        f[-1] ^= 1
        t, seq, ct = crypto.parse_frame(bytes(f))
        with self.assertRaises(Exception):
            crypto.open_chunk(key, t, seq, ct)
        # seq مختلف => AAD/nonce مختلف
        good = crypto.seal_chunk(key, tid, 0, b"data")
        t, _, ct = crypto.parse_frame(good)
        with self.assertRaises(Exception):
            crypto.open_chunk(key, t, 1, ct)


if __name__ == "__main__":
    unittest.main()


class LanVectors(unittest.TestCase):
    def test_v2_vectors(self):
        v = V["v2"]
        enc, auth = bytes.fromhex(V["encKey"]), bytes.fromhex(V["authKey"])
        nc, ns = bytes.fromhex(v["ncHex"]), bytes.fromhex(v["nsHex"])
        self.assertEqual(crypto.pair_commit(bytes.fromhex(V["pubA"]), bytes.fromhex(v["pairNonce"])), v["pairCommit"])
        self.assertEqual(crypto.mac(auth, b"srv", v["idClient"].encode(), v["idServer"].encode(), nc, ns).hex(), v["macSrv"])
        self.assertEqual(crypto.mac(auth, b"cli", v["idClient"].encode(), v["idServer"].encode(), nc, ns).hex(), v["macCli"])
        self.assertEqual(crypto.session_key(enc, nc, ns).hex(), v["sessionKey"])
