"""Hooks on fleet events (docs/CONFIGURATION.md, [hooks]): shell commands this computer's daemon
runs when the fleet does something, e.g. a desktop notification when a decision waits for you.

They come only from this computer's own settings (hive.local.toml, ~/.hive/config.toml), never
from the committed hive.toml. Each command gets the event in its environment:
HIVE_EVENT (the event type), HIVE_SUMMARY (one line for a person) and HIVE_EVENT_JSON.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import threading

log = logging.getLogger("hive.hooks")

HOOK_TIMEOUT = 120  # seconds a hook may run before it is stopped


def match(ev: dict) -> tuple[str, str] | None:
    """(hook name, summary) for an event a hook can follow, else None."""
    t, p = ev.get("type", ""), ev.get("payload") or {}
    if t == "decision.requested":
        return "on_decision", f"Decision #{p.get('id')} waits for you: {p.get('question', '')}"
    if t == "task.completed":
        return "on_task_done", f"Task #{p.get('id')} done: {p.get('title', '')}"
    if t == "main.advanced":
        return "on_main", f"main is now {str(p.get('sha', ''))[:10]} (by {p.get('by', '?')})"
    if t == "fleet.frozen":
        return "on_frozen", (f"The fleet is frozen until you answer decision "
                             f"#{p.get('decision_id')}")
    if t == "fleet.state":
        return "on_state", f"The fleet is {p.get('state', '?')}"
    if t == "message.posted":
        m = p.get("message") or {}
        if m.get("channel") == "#user" and m.get("author") not in ("user", None):
            return "on_message", f"{m['author']}: {str(m.get('body', ''))[:500]}"
    return None


def run(command: str, ev: dict, summary: str, cwd: str | None = None) -> subprocess.Popen | None:
    """Start the hook without waiting; it is stopped if it runs longer than HOOK_TIMEOUT."""
    env = dict(os.environ, HIVE_EVENT=ev.get("type", ""), HIVE_SUMMARY=summary,
               HIVE_EVENT_JSON=json.dumps(ev, default=str)[:30000])
    for var in ("HIVE_SLOT", "HIVE_LOCAL_SECRET"):  # a hook is the user's, not an agent's
        env.pop(var, None)
    try:
        proc = subprocess.Popen(command, shell=True, cwd=cwd, env=env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    except OSError as e:
        log.warning("hook %r could not start: %s", command, e)
        return None

    def reap():
        try:
            _, err = proc.communicate(timeout=HOOK_TIMEOUT)
            if proc.returncode:
                log.warning("hook %r exited %s: %s", command, proc.returncode,
                            (err or b"").decode("utf-8", "replace").strip()[-300:])
        except subprocess.TimeoutExpired:
            proc.kill()
            log.warning("hook %r ran over %ss and was stopped", command, HOOK_TIMEOUT)

    threading.Thread(target=reap, daemon=True, name="hook").start()
    return proc
