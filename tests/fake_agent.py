"""A scripted stand-in for an agent CLI, used by the end-to-end test.

Reads the wake prompt on stdin and acts through the daemon's local API like a real agent would
through the hive MCP tools.
"""

import json
import os
import sys
from pathlib import Path

from hive.local import LocalDaemon

prompt = sys.stdin.read()
d = LocalDaemon()
# One file per agent: two fake agents may run at once, and interleaved appends to a shared
# file are not atomic on every platform.
log = Path(os.environ["FAKE_AGENT_LOG"] + "." + d.slot)


def emit(ev):
    print(json.dumps(ev), flush=True)


emit({"type": "system", "subtype": "init", "session_id": sys.argv[1], "model": "fake"})
emit({"type": "assistant", "message": {"content": [
    {"type": "text", "text": f"Working as {d.slot}"},
    {"type": "tool_use", "name": "mcp__plugin_hive_hive__whoami", "input": {}}]}})


def tool(name, **args):
    r = d.tool(name, args)
    with open(log, "a", encoding="utf-8") as f:
        f.write(json.dumps({"slot": d.slot, "tool": name, "args": args, "r": r}) + "\n")
    return r


me = tool("whoami")["result"]
if "orchestrator" in me["roles"]:
    state = me["fleet"]["state"]
    if state == "planning":
        tool("task_create", title="Add hello module", spec="Create src/hello.py",
             path_hints=["src/hello.py"])
        tool("decision_request", kind="start_approval", question="Approve the plan?",
             context="1 task: add hello")
    elif "STUCK" in prompt:
        tool("decision_request", kind="stuck", question="Agent is stuck, what now?",
             task_id=None)
    tool("status_publish", summary=f"wake in state {state}")
else:
    tasks = [t for t in tool("task_list", assignee=d.slot)["result"]
             if t["state"] in ("claimed", "in_progress")]
    for t in tasks:
        # Writing without a lease must be denied by the PreToolUse hook.
        verdict = d.post("/hook/pre_tool", {"tool_name": "Write",
                                            "tool_input": {"file_path": "src/hello.py"}})
        with open(log, "a", encoding="utf-8") as f:
            f.write(json.dumps({"slot": d.slot, "hook": verdict}) + "\n")
        tool("lease_acquire", globs=t["path_hints"], task_id=t["id"])
        Path("src").mkdir(exist_ok=True)
        Path("src/hello.py").write_text("print('hello')\n")
        tool("lease_release", commit_message=f"Add hello (task {t['id']})", task_id=t["id"])
        tool("task_complete", task_id=t["id"], result="added src/hello.py")
        tool("report_submit", summary=f"Task {t['id']}: added src/hello.py", task_id=t["id"],
             body="Created src/hello.py; integrated into main.")
    if "requests a report" in prompt:
        rid = int(prompt.split("request #")[1].split(")")[0].split(",")[0])
        tool("report_submit", summary="Status report", body="All good.", request_id=rid)
emit({"type": "result", "is_error": False, "num_turns": 3, "result": "ok"})
