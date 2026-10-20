"""Loading and saving hive.toml (repo) and ~/.hive/machine.toml (per machine). SPEC §16."""

from __future__ import annotations

import os
import re
import secrets
import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path

import tomli_w

DEFAULT_REPO_CONFIG = {
    "server": {"url": "http://127.0.0.1:8765"},
    "repo": {"main": "main"},
    "integration": {"review": "off", "checks": [], "shell": "native"},
    "orchestrator": {"standby": [], "debounce_seconds": 90, "heartbeat_hours": 4},
    # When agents start a fresh CLI session instead of resuming (docs/RUNNING.md).
    "sessions": {"per_task": True, "max_context_tokens": 120_000, "max_wakes": 40,
                 "orchestrator_fresh_after_hours": 12},
    "roles": {},
    "permissions": {"preset": "standard", "allow": [], "approval": [], "deny": []},
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


VENDORS = ("claude", "gemini", "codex")


@dataclass
class Slot:
    name: str
    vendor: str  # one of VENDORS
    token: str
    worktree: str
    config_dir: str = ""
    permission_mode: str = ""
    model: str = ""  # passed to the CLI's model flag; empty = the account's default
    server_url: str = ""  # this slot's Hive server (project); empty = the machine's server_url
    command: list[str] = field(default_factory=list)  # optional override template
    session_id: str = ""
    session_started: bool = False
    wakes_in_session: int = 0


@dataclass
class MachineConfig:
    server_url: str = "http://127.0.0.1:8765"  # default for slots that name no server
    local_port: int = 8766
    local_secret: str = field(default_factory=lambda: secrets.token_urlsafe(24))
    poll_interval: float = 3.0
    slots: list[Slot] = field(default_factory=list)

    @property
    def local_url(self) -> str:
        return f"http://127.0.0.1:{self.local_port}"

    def url_for(self, slot: Slot) -> str:
        """The server (project) this slot belongs to."""
        return slot.server_url or self.server_url

    def server_urls(self) -> list[str]:
        """Every server this machine's slots talk to, in slot order."""
        return list(dict.fromkeys(self.url_for(s) for s in self.slots)) or [self.server_url]

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


# A project on a Hive server: https://host/p/<project>. The server's own URL is its default project.
_PROJECT_URL = re.compile(r"^(.*?)/p/([a-z][a-z0-9-]{1,31})/?$")


def split_project_url(url: str) -> tuple[str, str]:
    """("https://host", "acme") for "https://host/p/acme"; (url, "") for a server's own URL."""
    u = (url or "").rstrip("/")
    m = _PROJECT_URL.match(u)
    return (m.group(1), m.group(2)) if m else (u, "")


def project_url(server: str, project: str) -> str:
    """The URL of `project` on the server `server` (itself a server or project URL)."""
    base = split_project_url(server)[0]
    return f"{base}/p/{project}" if project and project != "default" else base


def repo_server_url(root: Path | None) -> str:
    """hive.toml's own [server] url, without the built-in default ("" if it names none)."""
    if not root or not (root / "hive.toml").exists():
        return ""
    try:
        with open(root / "hive.toml", "rb") as f:
            return str((tomllib.load(f).get("server") or {}).get("url") or "").rstrip("/")
    except (OSError, tomllib.TOMLDecodeError):
        return ""


@dataclass
class AdminConfig:
    server_url: str = ""  # the default target of admin commands
    token: str = ""
    servers: dict[str, str] = field(default_factory=dict)  # more URLs (projects) -> admin token

    def token_for(self, url: str) -> str:
        url = (url or "").rstrip("/")
        if url in self.servers:
            return self.servers[url]
        own = self.server_url.rstrip("/")
        if url == own:
            return self.token
        # The server's own admin token (the owner's) works in each of its projects.
        base, project = split_project_url(own)
        if own and not project and split_project_url(url)[0] == base:
            return self.token
        return ""

    def target(self, project: str | None = None, start: Path | None = None) -> tuple[str, str]:
        """(url, token) an admin command acts on: `project` (or $HIVE_PROJECT) on the default
        server; else the project the current repository's hive.toml names; else the default."""
        project = project or os.environ.get("HIVE_PROJECT", "")
        if project:
            url = project_url(self.server_url, project) if self.server_url else ""
        else:
            repo_url = repo_server_url(find_repo_root(start))
            url = repo_url if split_project_url(repo_url)[1] else self.server_url.rstrip("/")
        return url, os.environ.get("HIVE_ADMIN_TOKEN", "") or self.token_for(url)


def load_admin_config() -> AdminConfig:
    url = os.environ.get("HIVE_SERVER_URL", "")
    token = os.environ.get("HIVE_ADMIN_TOKEN", "")
    servers: dict[str, str] = {}
    path = hive_home() / "admin.toml"
    if path.exists():
        with open(path, "rb") as f:
            data = tomllib.load(f)
        url = url or data.get("server_url", "")
        token = token or data.get("token", "")
        servers = {str(k).rstrip("/"): str(v) for k, v in (data.get("servers") or {}).items()}
    return AdminConfig(url, token, servers)


def save_admin_config(cfg: AdminConfig) -> None:
    path = hive_home() / "admin.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        tomli_w.dump(asdict(cfg), f)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
