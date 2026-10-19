"""`hive hook <event>`: bridge from CLI hooks (Claude Code / Gemini CLI) to the daemon. SPEC §11.

Reads the hook JSON on stdin, asks the daemon for a neutral verdict (deny / context / block) and
prints it in the vendor's hook output format. Delivery fails open; write enforcement fails closed.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

from .local import LocalDaemon

EVENTS = {
    # claude
    "PreToolUse": "pre_tool", "PostToolUse": "post_tool", "UserPromptSubmit": "prompt",
    "Stop": "stop", "SessionStart": "session_start",
    # gemini
    "BeforeTool": "pre_tool", "AfterTool": "post_tool", "BeforeAgent": "prompt",
    "AfterAgent": "stop",
}
WRITE_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit", "write_file", "replace", "edit"}


def format_output(vendor: str, raw_event: str, verdict: dict) -> dict | None:
    if "deny" in verdict:
        if vendor == "gemini":
            return {"decision": "deny", "reason": verdict["deny"]}
        return {"hookSpecificOutput": {"hookEventName": raw_event, "permissionDecision": "deny",
                                       "permissionDecisionReason": verdict["deny"]}}
    if "block" in verdict:
        if vendor == "gemini":
            return {"decision": "deny", "reason": verdict["block"]}
        return {"decision": "block", "reason": verdict["block"]}
    if verdict.get("context"):
        return {"hookSpecificOutput": {"hookEventName": raw_event,
                                       "additionalContext": verdict["context"]}}
    return None


def run_hook(raw_event: str, vendor: str | None) -> int:
    if raw_event == "git-pre-commit":
        return git_pre_commit()
    event = EVENTS.get(raw_event)
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        data = {}
    d = LocalDaemon(cwd=None)
    if not d.slot or not event:
        return 0  # not a Hive session
    vendor = vendor or d.vendor or "claude"
    try:
        verdict = d.post(f"/hook/{event}", data, timeout=5)
    except Exception as e:
        if event == "pre_tool" and data.get("tool_name") in WRITE_TOOLS:
            verdict = {"deny": f"Hive daemon unreachable ({type(e).__name__}); file writes are "
                               "blocked until `hive up` is running on this machine."}
        else:
            return 0
    out = format_output(vendor, raw_event, verdict)
    if out:
        sys.stdout.write(json.dumps(out))
    return 0


def git_pre_commit() -> int:
    d = LocalDaemon()
    if not os.environ.get("HIVE_SLOT") or not d.slot:
        return 0  # commits by the user or the daemon itself
    staged = subprocess.run(["git", "diff", "--cached", "--name-only"], capture_output=True,
                            text=True).stdout.split()
    try:
        r = d.post("/check-paths", {"paths": staged}, timeout=5)
    except Exception:
        print("hive: daemon unreachable; refusing agent commit", file=sys.stderr)
        return 1
    if r["uncovered"]:
        print("hive: these staged files are not covered by your leases: "
              + ", ".join(r["uncovered"]) + "\nUse lease_acquire, and commit via lease_release.",
              file=sys.stderr)
        return 1
    return 0
