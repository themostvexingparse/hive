"""Helpers for processes spawned by agent CLIs (MCP server, hooks) to reach the local daemon."""

from __future__ import annotations

import os
from pathlib import Path

import requests

from .config import load_machine_config


class LocalDaemon:
    def __init__(self, slot: str | None = None, cwd: Path | None = None):
        cfg = load_machine_config()
        self.url = os.environ.get("HIVE_DAEMON_URL") or cfg.local_url
        self.secret = os.environ.get("HIVE_LOCAL_SECRET") or cfg.local_secret
        self.vendor = ""
        self.slot = slot or os.environ.get("HIVE_SLOT") or ""
        if not self.slot:
            here = (cwd or Path.cwd()).resolve()
            for s in cfg.slots:
                if s.worktree:
                    wt = Path(s.worktree).resolve()
                    if here == wt or wt in here.parents:
                        self.slot = s.name
                        break
        for s in cfg.slots:
            if s.name == self.slot:
                self.vendor = s.vendor

    def post(self, path: str, body: dict, timeout: float = 600) -> dict:
        r = requests.post(f"{self.url}/slot/{self.slot}{path}", json=body, timeout=timeout,
                          headers={"X-Hive-Local": self.secret})
        r.raise_for_status()
        return r.json()

    def tool(self, name: str, args: dict, timeout: float = 600) -> dict:
        return self.post(f"/tool/{name}", {k: v for k, v in args.items() if v is not None},
                         timeout=timeout)
