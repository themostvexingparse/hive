"""Loading and saving hive.toml (repo) and ~/.hive/machine.toml (per machine). SPEC §16."""

from __future__ import annotations

import os
import secrets
import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path

import tomli_w

DEFAULT_REPO_CONFIG = {
    "server": {"url": "http://127.0.0.1:8765"},
    "repo": {"main": "main"},
    "integration": {"review": "off", "checks": []},
    "orchestrator": {"standby": [], "debounce_seconds": 90, "heartbeat_hours": 4},
    "roles": {},
}


def hive_home() -> Path:
    return Path(os.environ.get("HIVE_HOME", Path.home() / ".hive"))


def find_repo_root(start: Path | None = None) -> Path | None:
    cur = (start or Path.cwd()).resolve()
    for p in (cur, *cur.parents):
        if (p / "hive.toml").exists():
            return p
    return None


def load_repo_config(root: Path | None) -> dict:
    cfg = {k: dict(v) for k, v in DEFAULT_REPO_CONFIG.items()}
    if root and (root / "hive.toml").exists():
        with open(root / "hive.toml", "rb") as f:
            data = tomllib.load(f)
        for k, v in data.items():
            if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                cfg[k].update(v)
            else:
                cfg[k] = v
    return cfg


@dataclass
class Slot:
    name: str
    vendor: str  # "claude" | "gemini"
    token: str
    worktree: str
    config_dir: str = ""
    permission_mode: str = ""
    command: list[str] = field(default_factory=list)  # optional override template
    session_id: str = ""
    session_started: bool = False
    wakes_in_session: int = 0


@dataclass
class MachineConfig:
    server_url: str = "http://127.0.0.1:8765"
    local_port: int = 8766
    local_secret: str = field(default_factory=lambda: secrets.token_urlsafe(24))
    poll_interval: float = 3.0
    slots: list[Slot] = field(default_factory=list)

    @property
    def local_url(self) -> str:
        return f"http://127.0.0.1:{self.local_port}"

    def slot(self, name: str) -> Slot:
        for s in self.slots:
            if s.name == name:
                return s
        raise KeyError(f"no slot named {name!r} on this machine")


def machine_config_path() -> Path:
    return hive_home() / "machine.toml"


def load_machine_config() -> MachineConfig:
    path = machine_config_path()
    if not path.exists():
        return MachineConfig()
    with open(path, "rb") as f:
        data = tomllib.load(f)
    slots = [Slot(**s) for s in data.pop("slots", [])]
    return MachineConfig(**data, slots=slots)


def save_machine_config(cfg: MachineConfig) -> None:
    path = machine_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = asdict(cfg)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "wb") as f:
        tomli_w.dump(data, f)
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


@dataclass
class AdminConfig:
    server_url: str = ""
    token: str = ""


def load_admin_config() -> AdminConfig:
    url = os.environ.get("HIVE_SERVER_URL", "")
    token = os.environ.get("HIVE_ADMIN_TOKEN", "")
    path = hive_home() / "admin.toml"
    if path.exists():
        with open(path, "rb") as f:
            data = tomllib.load(f)
        url = url or data.get("server_url", "")
        token = token or data.get("token", "")
    return AdminConfig(url, token)


def save_admin_config(cfg: AdminConfig) -> None:
    path = hive_home() / "admin.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        tomli_w.dump(asdict(cfg), f)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
