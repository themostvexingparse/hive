"""Agents as the user sees them: named, set up on this computer (a slot), logged in. Shared by
`hive agents …` and `hive join`; the server side is the /agents API."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from . import ui
from .config import (MachineConfig, Slot, hive_home, load_machine_config, load_repo_config,
                     main_clone, save_machine_config)

LOGIN_VAR = {"claude": "CLAUDE_CONFIG_DIR", "codex": "CODEX_HOME", "gemini": "GEMINI_CLI_HOME"}


class SetupError(Exception):
    pass


def clone_here(start: Path | None = None) -> Path | None:
    """The main clone of the git repository around `start` (default: the current folder);
    from inside an agent's worktree, the clone it was made from."""
    try:
        top = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=start or Path.cwd(),
                             capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None
    return main_clone(Path(top).resolve()) if top else None


def free_names(vendor: str, count: int, taken: set[str]) -> list[str]:
    """`count` unused names <vendor>-<n>, lowest n first."""
    out, n = [], 1
    while len(out) < count:
        name = f"{vendor}-{n}"
        if name not in taken:
            out.append(name)
        n += 1
    return out


def default_roles(roster: list[dict], index: int) -> list[str]:
    """What a new agent does when nobody said: the fleet's first agent is its orchestrator,
    every later one a programmer."""
    has_orch = any("orchestrator" in a["roles"] for a in roster)
    return ["orchestrator"] if not has_orch and index == 0 else ["programmer"]


def parse_roles(text: str | None) -> list[str]:
    return [r.strip() for r in re.split(r"[,\s]+", text or "") if r.strip()]


def place(name: str, vendor: str, token: str, server: str, repo: Path | None,
          worktree: Path | None = None, config_dir: str | None = None,
          permission_mode: str = "", model: str = "", hive_cmd: str | None = None,
          replace: bool = False, placed: bool = False) -> tuple[Slot, list[str]]:
    """Make the agent `name` a slot on this computer: its worktree (next to the clone, on
    branch hive/<name>), the git hook, its private CLI settings folder, and its entry in
    machine.toml. Returns the slot and the lines worth telling the user."""
    from .daemon import gitops
    from .init import install_git_hook
    cfg = load_machine_config()
    notes: list[str] = []
    old = next((s for s in cfg.slots if s.name == name), None)
    if old and cfg.url_for(old).rstrip("/") != server and not replace:
        # Agent names are unique per project, not per machine: this may be another project's
        # agent of the same name, whose slot (worktree, account, session) must not be lost.
        raise SetupError(
            f"this machine already has a slot named {name!r}, for {cfg.url_for(old)}. Slot "
            "names must be unique on a machine: use another name, or pass --replace to move "
            "this slot to " + server)
    if vendor in ("codex", "agy") and permission_mode:
        from .daemon.wake import AGY_MODES, CODEX_MODES
        modes = CODEX_MODES if vendor == "codex" else AGY_MODES
        if permission_mode not in modes:
            raise SetupError(f"{vendor} --permission-mode must be one of {', '.join(modes)}")
    if worktree is None:
        if repo is None:
            raise SetupError("run this inside your clone of the repository, or pass --repo "
                             "<clone> (the agent's worktree is created next to it)")
        worktree = repo.parent / f"{repo.name}-{name}"
    if repo is not None:
        main = load_repo_config(repo)["repo"].get("main", "main")
        try:
            gitops.ensure_worktree(repo, worktree, f"hive/{name}", main)
        except gitops.GitError as e:
            raise SetupError(str(e)) from e
        from .init import git_hook_path
        had = "hook git-pre-commit" in (git_hook_path(repo).read_text("utf-8", "replace")
                                        if git_hook_path(repo).exists() else "")
        hook = install_git_hook(repo, hive_cmd)
        if hook and not had:
            notes.append(f"Installed the git pre-commit hook at {ui.path(hook)}")
    config_dir = config_dir or (old.config_dir if old and old.config_dir else
                                str(hive_home() / "accounts" / name))
    Path(config_dir).mkdir(parents=True, exist_ok=True)
    if old and cfg.url_for(old).rstrip("/") != server:
        # Event cursors, message ids and sessions were the old server's; keeping them would
        # skip or replay the new one's events.
        from .daemon.store import Store
        Store(hive_home() / "state.db").forget(name)
        notes.append(f"Slot {ui.agent(name)} moved from {ui.path(cfg.url_for(old))} to "
                     f"{ui.path(server)}; its local state was reset.")
    if not cfg.slots:
        cfg.server_url = server  # the first slot's server is the default for later joins
    if old:  # re-placing keeps what was chosen for the slot on this computer
        permission_mode = permission_mode or old.permission_mode
        model = model or old.model
    # The clone, recorded now (by you, not the agent): where its hive.local.toml is read.
    clone = repo or (main_clone(Path(worktree)) if Path(worktree).is_dir() else None)
    slot = Slot(name=name, vendor=vendor, token=token, worktree=str(worktree),
                config_dir=config_dir, permission_mode=permission_mode or "",
                model=model or "", server_url=server, placed=placed,
                repo=str(Path(clone).resolve()) if clone else (old.repo if old else ""))
    cfg.slots = [s for s in cfg.slots if s.name != name] + [slot]
    save_machine_config(cfg)
    return slot, notes


def unplace(name: str) -> tuple[Slot | None, bool]:
    """Take the slot `name` off this computer: machine.toml, its local state and its worktree
    (unless that has uncommitted changes). Its account folder is kept, so adding it back
    needs no new login. Returns (the removed slot or None, whether the worktree went)."""
    from .daemon.store import Store
    cfg = load_machine_config()
    slot = next((s for s in cfg.slots if s.name == name), None)
    if slot is None:
        return None, False
    cfg.slots = [s for s in cfg.slots if s.name != name]
    save_machine_config(cfg)
    Store(hive_home() / "state.db").forget(name)
    import shutil
    shutil.rmtree(hive_home() / "bundles" / name, ignore_errors=True)  # regenerated if re-added
    wt = Path(slot.worktree)
    if not wt.is_dir():
        return slot, True
    # Without --force, git refuses a worktree with uncommitted changes: that work stays.
    subprocess.run(["git", "-C", slot.repo or str(main_clone(wt)), "worktree", "remove",
                    str(wt)], capture_output=True, text=True)
    return slot, not wt.exists()


def is_logged_in(slot: Slot) -> bool | None:
    """Whether the slot's CLI has used its account folder (None for agy, whose login lives in
    the computer's keyring, not in a folder Hive can look at)."""
    if slot.vendor == "agy":
        return None
    d = Path(slot.config_dir) if slot.config_dir else None
    return bool(d and d.is_dir() and any(d.iterdir()))


def login_command(slot: Slot) -> tuple[list[str], dict]:
    """The command (and environment) that logs the slot's account in: its CLI, run against
    the slot's private settings folder, without Hive attached."""
    env = dict(os.environ)
    for var in ("HIVE_SLOT", "HIVE_DAEMON_URL", "HIVE_LOCAL_SECRET"):
        env.pop(var, None)
    if slot.vendor in LOGIN_VAR and slot.config_dir:
        env[LOGIN_VAR[slot.vendor]] = slot.config_dir
    cmd = {"codex": ["codex", "login"], "agy": ["agy"]}.get(slot.vendor, [slot.vendor])
    return cmd, env


def run_login(slot: Slot, device: bool = False) -> int:
    from .daemon import wake
    cmd, env = login_command(slot)
    if device and slot.vendor == "codex":
        cmd.append("--device-auth")
    cwd = slot.worktree if Path(slot.worktree).is_dir() else None
    with daemon_attached(slot.name):
        try:
            return subprocess.run(wake.resolve(cmd), cwd=cwd, env=env).returncode
        except FileNotFoundError:
            raise SetupError(f"{cmd[0]} isn't installed on this computer (or isn't on PATH); "
                             f"install it, then run `hive agents login {slot.name}`") from None
        except KeyboardInterrupt:
            return 130


class daemon_attached:
    """While a person uses a slot's CLI by hand, tell this computer's daemon (if it runs) not
    to start that slot in the background; leaving also clears a "needs login" pause."""

    def __init__(self, name: str, cfg: MachineConfig | None = None):
        self.name, self.cfg = name, cfg or load_machine_config()
        self.reply: dict | None = None

    def _post(self, attached: bool) -> dict | None:
        import requests
        try:
            return requests.post(f"{self.cfg.local_url}/slot/{self.name}/attach",
                                 json={"attached": attached}, timeout=5,
                                 headers={"X-Hive-Local": self.cfg.local_secret}).json()
        except (requests.RequestException, ValueError):
            return None

    def __enter__(self):
        self.reply = self._post(True)
        return self

    def __exit__(self, *exc):
        self._post(False)
        return False
