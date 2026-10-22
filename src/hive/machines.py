"""Paired machines (docs/CLI_REDESIGN.md, rung 3): a computer joins a project once with a short
code, then runs whichever agents the admin places on it. Its daemon claims each placed agent
(the server hands it a fresh token) and sets up the slot; agents placed elsewhere or removed
are taken off it again."""

from __future__ import annotations

import logging
import platform
import re
import shutil
import socket
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .config import VENDORS, Slot, load_machine_config, save_machine_config
from .daemon.client import HiveError, ServerClient

log = logging.getLogger("hive.machines")

def installed_clis() -> list[str]:
    return [v for v in VENDORS if shutil.which(v)]


def default_name() -> str:
    return re.sub(r"[^a-z0-9-]+", "-", socket.gethostname().lower()).strip("-") or "machine"


def pair(server: str, code: str, repo: Path, name: str = "") -> dict:
    """Redeem `code` on `server`, remember the pairing in machine.toml; returns the server's
    answer ({"name", "token"})."""
    r = ServerClient(server, "").post("/machines/pair", {
        "code": code, "name": name or default_name(), "os": platform.system(),
        "clis": installed_clis()})
    cfg = load_machine_config()
    cfg.machines[server.rstrip("/")] = {"name": r["name"], "token": r["token"],
                                        "repo": str(repo)}
    if not cfg.slots and len(cfg.machines) == 1:
        cfg.server_url = server.rstrip("/")
    save_machine_config(cfg)
    return r


@dataclass
class SyncResult:
    added: list[Slot] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def sync(only_server: str | None = None,
         busy: Callable[[str], bool] = lambda name: False) -> SyncResult:
    """Make this computer's slots match what each paired server placed on it. A slot that is
    `busy` (mid-turn, or a person is using it) is left alone until a later sync."""
    from . import agents
    out = SyncResult()
    cfg = load_machine_config()
    if cfg.accept_placements == "never":
        return out
    for server, pairing in cfg.machines.items():
        if only_server and server != only_server.rstrip("/"):
            continue
        c = ServerClient(server, pairing.get("token", ""))
        try:
            placed = c.post("/machines/me/placements", {"clis": installed_clis(),
                                                        "os": platform.system()})
        except HiveError as e:
            out.errors.append(f"{server}: {e.message}")
            continue
        slots = load_machine_config().slots
        here = {s.name: s for s in slots if cfg.url_for(s).rstrip("/") == server}
        others = {s.name for s in slots} - set(here)
        want = {p["agent"]: p for p in placed}
        repo = Path(pairing["repo"]) if pairing.get("repo") else None
        for name, p in want.items():
            have = here.get(name)
            # A claim hands out a new token, so an agent that is already here (and claimed)
            # keeps running untouched; one moved back here is claimed again.
            if have is not None and (p["claimed"] and have.placed or busy(name)):
                continue
            if name in others:  # checked first: claiming would rotate its token for nothing
                out.errors.append(f"{name}: this computer already has a slot of that name for "
                                  "another server or project")
                continue
            try:
                got = c.post(f"/machines/me/claim/{name}")
                slot, _ = agents.place(name, got["vendor"], got["token"], server, repo,
                                       placed=True)
            except (HiveError, agents.SetupError) as e:
                out.errors.append(f"{name}: {getattr(e, 'message', e)}")
                continue
            out.added.append(slot)
        for name, s in here.items():
            if s.placed and name not in want and not busy(name):
                agents.unplace(name)
                out.removed.append(name)
    return out


def describe(result: SyncResult) -> list[str]:
    lines = [f"Set up {s.name} ({s.vendor}), placed on this computer → {s.worktree}"
             for s in result.added]
    lines += [f"Took {n} off this computer (removed, or placed elsewhere)"
              for n in result.removed]
    return lines
