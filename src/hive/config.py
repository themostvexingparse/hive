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
    # A named bundle of the settings below, set in hive.toml (FLEET_PRESETS).
    "fleet": {"preset": "custom"},
    # Shell commands run on this computer when the fleet does something (docs/CONFIGURATION.md).
    # Read only from this computer's own files (hive.local.toml, ~/.hive/config.toml), never
    # from the committed hive.toml: a repository must not run commands on every computer.
    "hooks": {"on_decision": "", "on_task_done": "", "on_main": "", "on_frozen": "",
              "on_state": "", "on_message": ""},
}

# fleet.preset: a starting point that hive.toml's own settings override.
FLEET_PRESETS = {
    "custom": {},
    "solo": {"integration": {"review": "off"}},
    "team": {"integration": {"review": "reviewer"}},
    "strict": {"integration": {"review": "reviewer"}, "permissions": {"preset": "strict"}},
}
LOCAL_ONLY_SECTIONS = ("hooks",)


def hive_home() -> Path:
    return Path(os.environ.get("HIVE_HOME", Path.home() / ".hive"))


def find_repo_root(start: Path | None = None) -> Path | None:
    cur = (start or Path.cwd()).resolve()
    for p in (cur, *cur.parents):
        if (p / "hive.toml").exists():
            return p
    return None


LOCAL_TOML = "hive.local.toml"  # this computer's overrides; git-ignored, never uploaded


def main_clone(root: Path) -> Path:
    """The main working folder of the repository `root` is a checkout of: `root` itself, or,
    for a git worktree (an agent's slot), the clone it was made from. Read from the `.git`
    file rather than by running git, since this runs on every config load."""
    git = root / ".git"
    if not git.is_file():
        return root
    try:
        gitdir = Path(git.read_text("utf-8").split("gitdir:", 1)[1].strip())
        if not gitdir.is_absolute():
            gitdir = (root / gitdir).resolve()
        common = (gitdir / "commondir").read_text("utf-8").strip()
        common_dir = (gitdir / common).resolve()
    except (OSError, IndexError):
        return root
    return common_dir.parent if common_dir.name == ".git" else root


def local_config_path(root: Path) -> Path | None:
    """This computer's hive.local.toml for the checkout `root`. For an agent's worktree it is
    the one in the clone recorded in machine.toml when the slot was made, never one found from
    the worktree's own `.git` file, which the agent can rewrite. None when there is none to
    read (a slot whose clone wasn't recorded)."""
    resolved = root.resolve()
    for s in load_machine_config().slots:
        if s.worktree and Path(s.worktree).resolve() == resolved:
            return Path(s.repo) / LOCAL_TOML if s.repo else None
    return main_clone(root) / LOCAL_TOML


_TRACKED: dict[tuple, bool] = {}


def _tracked(path: Path) -> bool:
    """Whether git tracks `path`: a committed hive.local.toml is the repository's, not this
    computer's, and .gitignore doesn't stop `git add -f`. Cached until the file or git's
    index changes, since this runs on every config load."""
    def mtime(p: Path) -> int:
        try:
            return p.stat().st_mtime_ns
        except OSError:
            return 0
    key = (str(path), mtime(path), mtime(path.parent / ".git" / "index"))
    if key not in _TRACKED:
        _TRACKED[key] = _git_tracks(path)
    return _TRACKED[key]


def _git_tracks(path: Path) -> bool:
    import subprocess
    try:
        r = subprocess.run(["git", "-C", str(path.parent), "ls-files", "--error-unmatch", "--",
                            path.name], capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return r.returncode == 0


def tracked_local_config(root: Path | None) -> Path | None:
    """The hive.local.toml that is ignored because git tracks it, if any."""
    local = local_config_path(root) if root else None
    return local if local and local.exists() and _tracked(local) else None


def _read_toml(path: Path) -> dict:
    if not path.exists():
        return {}
    with open(path, "rb") as f:
        return tomllib.load(f)


USER_TOML = "config.toml"  # ~/.hive/config.toml: this user's settings, every project


def user_config_path() -> Path:
    return hive_home() / USER_TOML


def preset_layer(toml: dict) -> dict:
    name = str((toml.get("fleet") or {}).get("preset", "custom"))
    if name not in FLEET_PRESETS:
        raise ValueError(f"fleet.preset must be one of {', '.join(FLEET_PRESETS)}, not {name!r}")
    return FLEET_PRESETS[name]


def config_layers(root: Path | None) -> list[tuple[str, Path | None, dict]]:
    """(layer name, file, its settings), lowest priority first: built-in defaults, the preset
    hive.toml names, ~/.hive/config.toml (this user), hive.toml (committed: the whole fleet),
    hive.local.toml (this computer only)."""
    layers: list[tuple[str, Path | None, dict]] = [("default", None, DEFAULT_REPO_CONFIG)]
    toml = _read_toml(root / "hive.toml") if root else {}
    try:
        preset = preset_layer(toml)
    except ValueError:
        preset = {}  # reported where the configuration is checked (hive config, the server)
    layers.append(("preset", None, preset))
    layers.append(("user", user_config_path(), _read_toml(user_config_path())))
    if root:
        layers.append(("hive.toml", root / "hive.toml", toml))
        local = local_config_path(root)
        data = _read_toml(local) if local and local.exists() and not _tracked(local) else {}
        layers.append((LOCAL_TOML, local, data))
    return layers


def _merge(cfg: dict, data: dict) -> None:
    """Merge `data` into `cfg` key by key at every depth, so a layer that sets one setting of
    a role (roles.<role>.can_code) keeps the role's other settings from the layers below."""
    for k, v in data.items():
        if isinstance(v, dict) and isinstance(cfg.get(k), dict):
            cfg[k] = dict(cfg[k])
            _merge(cfg[k], v)
        else:
            cfg[k] = v


def load_repo_config(root: Path | None) -> dict:
    """The settings in force on this computer, every layer merged. Hooks come only from this
    computer's own files (LOCAL_ONLY_SECTIONS)."""
    cfg = {k: dict(v) for k, v in DEFAULT_REPO_CONFIG.items()}
    for name, _, data in config_layers(root)[1:]:
        if name == "hive.toml":
            data = {k: v for k, v in data.items() if k not in LOCAL_ONLY_SECTIONS}
        _merge(cfg, data)
    return cfg


def shared_config(root: Path) -> dict:
    """What the server gets: hive.toml over the preset it names. Nothing of this computer's
    own (user or local settings) is in it."""
    toml = _read_toml(root / "hive.toml")
    cfg: dict = {}
    _merge(cfg, preset_layer(toml))
    _merge(cfg, {k: v for k, v in toml.items() if k not in LOCAL_ONLY_SECTIONS})
    return cfg


def load_shared_config(root: Path | None) -> dict:
    """The fleet's settings, the same on every computer: the defaults under shared_config.
    What the committed files in .hive/agent/ are built from."""
    cfg = {k: dict(v) for k, v in DEFAULT_REPO_CONFIG.items()}
    if root:
        try:
            _merge(cfg, shared_config(root))
        except ValueError:  # an unknown preset: reported where the configuration is checked
            _merge(cfg, {k: v for k, v in _read_toml(root / "hive.toml").items()
                         if k not in LOCAL_ONLY_SECTIONS})
    return cfg


VENDORS = ("claude", "gemini", "codex", "agy")


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
    placed: bool = False  # set up because the server placed the agent on this paired machine
    # The clone the worktree was made from, recorded when the slot was set up: where this
    # slot's hive.local.toml is read (config.local_config_path).
    repo: str = ""
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
    # Servers (projects) this computer is paired with: url -> {"name", "token", "repo"}, where
    # repo is the clone that placed agents' worktrees are made next to.
    machines: dict[str, dict] = field(default_factory=dict)
    accept_placements: str = "auto"  # auto: set up agents the server places here | never
    slots: list[Slot] = field(default_factory=list)

    @property
    def local_url(self) -> str:
        return f"http://127.0.0.1:{self.local_port}"

    def url_for(self, slot: Slot) -> str:
        """The server (project) this slot belongs to."""
        return slot.server_url or self.server_url

    def server_urls(self) -> list[str]:
        """Every server this machine's slots talk to (or it is paired with), in slot order."""
        urls = [self.url_for(s) for s in self.slots] + list(self.machines)
        return list(dict.fromkeys(urls)) or [self.server_url]

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


def load_admin_config(env: bool = True) -> AdminConfig:
    """~/.hive/admin.toml, with $HIVE_SERVER_URL and $HIVE_ADMIN_TOKEN over it unless `env` is
    false (when saving a login: the environment's values must not be written to the file)."""
    url = os.environ.get("HIVE_SERVER_URL", "") if env else ""
    token = os.environ.get("HIVE_ADMIN_TOKEN", "") if env else ""
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
