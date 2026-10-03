"""حالة التطبيق المحفوظة (~/.config/cloudlink/state.json بصلاحيات 0600)."""
from __future__ import annotations

import json
import os
import socket
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "cloudlink"


def download_dir() -> Path:
    env = os.environ.get("CLOUDLINK_DOWNLOADS")
    if env:
        return Path(env)
    base = Path.home() / "Downloads"
    cfg = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "user-dirs.dirs"
    try:
        for line in cfg.read_text().splitlines():
            if line.startswith("XDG_DOWNLOAD_DIR="):
                v = line.split("=", 1)[1].strip().strip('"').replace("$HOME", str(Path.home()))
                base = Path(v)
    except OSError:
        pass
    return base / "CloudLink"


@dataclass
class State:
    device_id: str = ""
    device_name: str = ""
    port: int = 47616              # TCP port this device listens on
    share_root: str = ""           # folder exposed to paired devices (default: home)
    allow_uploads: bool = True     # let paired devices write into share_root
    # peer_id -> {name, auth_key(hex), enc_key(hex), host, port}
    peers: dict = field(default_factory=dict)

    def get_share_root(self) -> Path:
        return Path(self.share_root) if self.share_root else Path.home()

    @classmethod
    def load(cls, path: Path | None = None) -> "State":
        path = path or config_dir() / "state.json"
        st = cls()
        try:
            data = json.loads(path.read_text())
            for k, v in data.items():
                if not hasattr(st, k):
                    continue
                cur = getattr(st, k)
                if isinstance(cur, bool):
                    ok = isinstance(v, bool)
                elif isinstance(cur, int):
                    ok = isinstance(v, int) and not isinstance(v, bool)
                else:
                    ok = isinstance(v, type(cur))
                if ok:
                    setattr(st, k, v)
        except (OSError, ValueError):
            pass
        changed = False
        if not st.device_id:
            st.device_id = "linux-" + uuid.uuid4().hex[:16]
            changed = True
        if not st.device_name:
            st.device_name = socket.gethostname()[:48] or "Linux"
            changed = True
        st._path = path  # type: ignore[attr-defined]
        if changed:
            st.save()
        return st

    def save(self) -> None:
        path: Path = getattr(self, "_path", config_dir() / "state.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(asdict(self), f)
        os.replace(tmp, path)

    def add_peer(self, pid: str, name: str, auth_key: bytes, enc_key: bytes, host: str = "", port: int = 0) -> None:
        self.peers[pid] = {"name": name, "auth_key": auth_key.hex(), "enc_key": enc_key.hex(), "host": host, "port": port}
        self.save()

    def remove_peer(self, pid: str) -> None:
        if self.peers.pop(pid, None) is not None:
            self.save()
