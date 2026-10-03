"""E2EE primitives — المواصفات الكاملة في protocol/PROTOCOL.md (وتُطابقها Crypto.kt على أندرويد).

X25519  ->  HKDF-SHA256 (salt فارغ)  ->  AES-256-GCM
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import struct
from dataclasses import dataclass

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

CHUNK = 32 * 1024
FRAME_VERSION = 0x01
FRAME_HEADER = 1 + 16 + 4  # version + transferId + seq
CTL_AAD = b"cloudlink/ctl/v1"

_RAW = serialization.Encoding.Raw
_RAWF = serialization.PublicFormat.Raw
_PRIV_RAW = serialization.PrivateFormat.Raw
_NOENC = serialization.NoEncryption()


def generate_keypair() -> tuple[bytes, bytes]:
    priv = X25519PrivateKey.generate()
    return priv.private_bytes(_RAW, _PRIV_RAW, _NOENC), priv.public_key().public_bytes(_RAW, _RAWF)


def public_from_private(priv: bytes) -> bytes:
    return X25519PrivateKey.from_private_bytes(priv).public_key().public_bytes(_RAW, _RAWF)


def shared_secret(priv: bytes, peer_pub: bytes) -> bytes:
    return X25519PrivateKey.from_private_bytes(priv).exchange(X25519PublicKey.from_public_bytes(peer_pub))


def hkdf(ikm: bytes, info: bytes, length: int = 32) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=length, salt=None, info=info).derive(ikm)


@dataclass(frozen=True)
class SessionKeys:
    auth_key: bytes  # يُرسل للسيرفر عند hello (والسيرفر يخزّن sha256 له فقط)
    enc_key: bytes   # تشفير الرسائل والملفات
    sas: str         # 6 أرقام يقارنها المستخدم على الجهازين


def derive_session(shared: bytes) -> SessionKeys:
    auth = hkdf(shared, b"cloudlink/auth/v1")
    enc = hkdf(shared, b"cloudlink/enc/v1")
    sas_n = int.from_bytes(hkdf(shared, b"cloudlink/sas/v1", 4), "big") % 1_000_000
    return SessionKeys(auth, enc, f"{sas_n:06d}")


def auth_hash(auth_key: bytes) -> str:
    return hashlib.sha256(auth_key).hexdigest()


# ---------- رسائل التحكم ----------
def seal_ctl(enc_key: bytes, obj: dict, nonce: bytes | None = None) -> str:
    nonce = nonce or os.urandom(12)
    ct = AESGCM(enc_key).encrypt(nonce, json.dumps(obj, separators=(",", ":")).encode(), CTL_AAD)
    return base64.b64encode(nonce + ct).decode()


def open_ctl(enc_key: bytes, blob: str) -> dict:
    raw = base64.b64decode(blob)
    if len(raw) < 12 + 16:
        raise ValueError("short blob")
    return json.loads(AESGCM(enc_key).decrypt(raw[:12], raw[12:], CTL_AAD))


# ---------- أجزاء الملفات ----------
def xfer_key(enc_key: bytes, tid: bytes) -> bytes:
    return hkdf(enc_key, b"cloudlink/xfer/v1" + tid)


def _nonce(seq: int) -> bytes:
    return b"\x00" * 8 + struct.pack(">I", seq)


def seal_chunk(key: bytes, tid: bytes, seq: int, plain: bytes) -> bytes:
    hdr = bytes([FRAME_VERSION]) + tid + struct.pack(">I", seq)
    return hdr + AESGCM(key).encrypt(_nonce(seq), plain, tid + struct.pack(">I", seq))


def parse_frame(frame: bytes) -> tuple[bytes, int, bytes]:
    """-> (tid, seq, ciphertext)"""
    if len(frame) < FRAME_HEADER + 16 or frame[0] != FRAME_VERSION:
        raise ValueError("bad frame")
    return frame[1:17], struct.unpack(">I", frame[17:21])[0], frame[21:]


def open_chunk(key: bytes, tid: bytes, seq: int, ct: bytes) -> bytes:
    return AESGCM(key).decrypt(_nonce(seq), ct, tid + struct.pack(">I", seq))


# ---------- LAN v2: pairing commitment + session auth ----------
def pair_commit(pub: bytes, nonce: bytes) -> str:
    """Commitment the initiator sends BEFORE seeing the responder's key (stops SAS grinding by a MITM)."""
    return hashlib.sha256(b"cloudlink/pair/v2" + pub + nonce).hexdigest()


def mac(key: bytes, label: bytes, *parts: bytes) -> bytes:
    """HMAC-SHA256 over label + length-prefixed parts (unambiguous encoding)."""
    m = hmac.new(key, digestmod=hashlib.sha256)
    m.update(struct.pack(">H", len(label)) + label)
    for p in parts:
        m.update(struct.pack(">H", len(p)) + p)
    return m.digest()


def session_key(enc_key: bytes, nc: bytes, ns: bytes) -> bytes:
    """Fresh per-connection key: replaying recorded traffic from an old connection never decrypts."""
    return hkdf(enc_key, b"cloudlink/sess/v2" + nc + ns)
