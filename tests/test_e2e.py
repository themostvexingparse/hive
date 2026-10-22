"""End-to-end: server + daemon + git worktrees + a scripted fake agent CLI."""

import json
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from werkzeug.serving import make_server

from hive.config import MachineConfig, Slot
from hive.daemon import core
from hive.daemon.client import ServerClient
from hive.daemon.localapi import create_local_app
from hive.daemon.store import Store
from hive.init import init_repo
from hive.server.app import create_app
from hive.server.service import create_hive

ADMIN = "admin-secret"
FAKE = Path(__file__).with_name("fake_agent.py")


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                          text=True).stdout.strip()


def serve(app):
    srv = make_server("127.0.0.1", 0, app, threaded=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def wait_for(pred, timeout=60, what="condition"):
    end = time.time() + timeout
    while time.time() < end:
        v = pred()
        if v:
            return v
        time.sleep(0.2)
    raise AssertionError(f"timed out waiting for {what}")


@pytest.fixture(params=["wal", "delete"])
def world(tmp_path, monkeypatch, request):
    monkeypatch.setenv("HIVE_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(core, "COALESCE_SECONDS", 0)
    monkeypatch.setattr(core, "MIN_GAP_SECONDS", 0)
    log = tmp_path / "agent.log"
    monkeypatch.setenv("FAKE_AGENT_LOG", str(log))

    origin = tmp_path / "origin.git"
    git(tmp_path, "init", "--bare", "-b", "main", str(origin))
    clone = tmp_path / "proj"
    git(tmp_path, "clone", str(origin), str(clone))
    git(clone, "config", "user.email", "t@example.com")
    git(clone, "config", "user.name", "t")
    (clone / "README.md").write_text("proj\n")
    init_repo(clone, "http://unused")
    git(clone, "add", "-A")
    git(clone, "commit", "-m", "init")
    git(clone, "push", "origin", "main")

    hive = create_hive(f"sqlite:///{tmp_path / 'hive.db'}", sqlite_journal=request.param,
                       tick_interval=0)
    srv = serve(create_app(hive, admin_token=ADMIN))
    url = f"http://127.0.0.1:{srv.server_port}"
    admin = ServerClient(url, ADMIN)
    admin.put("/config", {"repo": {"main": "main"}, "integration": {"review": "off"}})

    slots = []
    for name, roles in [("orch", ["orchestrator"]), ("prog1", ["programmer"])]:
        tok = admin.post("/agents", {"name": name, "vendor": "claude", "roles": roles})["token"]
        wt = tmp_path / f"proj-{name}"
        git(clone, "worktree", "add", "-B", f"hive/{name}", str(wt), "origin/main")
        slots.append(Slot(name=name, vendor="claude", token=tok, worktree=str(wt),
                          command=[sys.executable, str(FAKE), "{session}"]))
    mcfg = MachineConfig(server_url=url, local_port=0, poll_interval=0.2, slots=slots)
    daemon = core.Daemon(mcfg, Store(tmp_path / "home" / "state.db"))
    local = make_server("127.0.0.1", 0, create_local_app(daemon), threaded=True)
    mcfg.local_port = local.server_port
    threading.Thread(target=local.serve_forever, daemon=True).start()
    # The fake agent finds the daemon through the machine config file, like `hive mcp` does.
    from hive.config import save_machine_config
    save_machine_config(mcfg)
    daemon.start_background()
    yield {"admin": admin, "daemon": daemon, "origin": origin, "clone": clone, "log": log,
           "hive": hive}
    daemon.stop()
    local.shutdown()
    srv.shutdown()


def read_log(path):
    """Entries from every fake agent's log (`<path>.<slot>`). A line still being written by a
    running agent is incomplete; it is skipped and picked up by a later read."""
    out = []
    for f in sorted(path.parent.glob(path.name + ".*")):
        for line in f.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out


def test_full_cycle(world):
    admin, log = world["admin"], world["log"]
    wait_for(lambda: all(a["online"] for a in admin.get("/agents")), what="agents online")
    admin.post("/fleet/start", {"goal": "Add a hello module"})

    # Orchestrator plans and asks for approval; nobody codes yet.
    d = wait_for(lambda: admin.get("/decisions", state="open"), what="start approval")[0]
    assert d["kind"] == "start_approval"
    assert admin.get("/fleet")["state"] == "awaiting_approval"
    assert all(t["state"] == "draft" for t in admin.get("/tasks"))

    admin.post(f"/decisions/{d['id']}/answer", {"answer": "approve"})

    # Programmer is assigned, gets denied writing without a lease, leases, integrates, completes.
    wait_for(lambda: all(t["state"] == "done" for t in admin.get("/tasks")), timeout=90,
             what="task done")
    entries = read_log(log)
    hook = [e for e in entries if "hook" in e]
    assert hook and "No lease covers src/hello.py" in hook[0]["hook"]["deny"]
    rel = [e for e in entries if e.get("tool") == "lease_release"][0]
    assert rel["r"]["result"]["status"] == "integrated", rel
    files = git(world["origin"], "ls-tree", "-r", "--name-only", "main")
    assert "src/hello.py" in files.split()
    msg = git(world["origin"], "log", "-1", "--format=%B", "main")
    assert "Hive-Agent: prog1" in msg and "Hive-Fence:" in msg
    assert admin.get("/fleet")["main_sha"] == git(world["origin"], "rev-parse", "main")
    assert admin.get("/leases") == []

    # The orchestrator is woken by the completion and publishes progress.
    wait_for(lambda: any("running" in s["summary"] for s in admin.get("/status")),
             what="orchestrator status in running")

    # The worker filed a report for the orchestrator.
    reports = admin.get("/reports", kind="report")
    assert any("src/hello.py" in r["summary"] for r in reports)

    # Live output reached the server, formatted, and is visible to the admin only.
    texts = wait_for(lambda: (lambda t: t if any("turn done" in x for x in t) else None)(
        [l["text"] for l in admin.get("/streams", agent="prog1")["lines"]]), what="prog1 stream")
    assert any("Working as prog1" in t for t in texts)
    assert any(t.startswith("→ mcp__plugin_hive_hive__whoami") for t in texts)
    orch = ServerClient(admin.base.removesuffix("/api/v1"),
                        world["daemon"].slots["orch"].cfg.token)
    with pytest.raises(Exception) as e:
        orch.get("/streams")
    assert getattr(e.value, "status", None) == 403


def test_dangerous_command_runs_after_orchestrator_approval(world):
    """A worker is blocked from `rm -r`, asks for approval, the orchestrator approves, and the
    daemon runs the exact command once in the worker's worktree."""
    admin, daemon = world["admin"], world["daemon"]
    prog = daemon.slots["prog1"]
    wait_for(lambda: all(a["online"] for a in admin.get("/agents")), what="agents online")
    admin.post("/fleet/start", {"goal": "x"})
    d = wait_for(lambda: admin.get("/decisions", state="open"), what="start approval")[0]
    admin.post(f"/decisions/{d['id']}/answer", {"answer": "approve"})
    wait_for(lambda: all(t["state"] == "done" for t in admin.get("/tasks")), timeout=90,
             what="task done")
    daemon.refresh(prog)
    build = Path(prog.cfg.worktree) / "build"
    (build / "sub").mkdir(parents=True)
    (build / "sub" / "out.o").write_text("x")

    # The hook holds the command while an approver (here the orchestrator: no reviewer is
    # enrolled) decides, then lets the CLI run it; forbidden commands are blocked outright.
    held = {}

    def run_in_cli():
        held["v"] = daemon.handle_hook("prog1", "pre_tool", {
            "tool_name": "Bash", "tool_input": {"command": "rm -r build",
                                                "description": "Remove stale build output"}})

    th = threading.Thread(target=run_in_cli)
    th.start()
    waiting = wait_for(lambda: admin.get("/approvals", state="requested"),
                       what="approval request from the hook")[0]
    assert waiting["reason"] == "Remove stale build output" and th.is_alive()
    daemon.call_tool("orch", "approval_decide", {"approval_id": waiting["id"], "approve": True})
    th.join(30)
    assert held["v"] == {"allow": True}
    assert admin.get(f"/approvals/{waiting['id']}")["state"] == "executed"  # used up
    v = daemon.handle_hook("prog1", "pre_tool", {"tool_name": "Bash", "tool_input": {
        "command": "git status && git push origin main"}})
    assert "lease_release" in v["deny"]
    v = daemon.handle_hook("prog1", "pre_tool", {"tool_name": "Bash",
                                                 "tool_input": {"command": "git commit -m x"}})
    assert v == {"allow": True}  # ordinary git history work runs directly, approved by Hive
    with pytest.raises(Exception):
        daemon.call_tool("prog1", "request_approval", {"command": "hive decide 1 approve",
                                                       "reason": "x"})

    result = {}

    def ask():
        result["r"] = daemon.call_tool("prog1", "request_approval", {
            "command": "rm -r build", "reason": "remove stale build output",
            "wait_seconds": 60})

    th = threading.Thread(target=ask)
    th.start()
    pending = wait_for(lambda: admin.get("/approvals", state="requested"),
                       what="approval request")[0]
    assert build.exists()  # nothing runs before the decision
    daemon.call_tool("orch", "approval_decide", {"approval_id": pending["id"],
                                                 "approve": True, "note": "fine"})
    th.join(70)
    r = result["r"]
    assert r["decision"] == "approved" and r["executed"] and r["exit_code"] == 0, r
    assert not build.exists()
    assert admin.get(f"/approvals/{pending['id']}")["state"] == "executed"
    with pytest.raises(Exception):  # one-time use
        daemon.call_tool("prog1", "run_approved", {"approval_id": pending["id"]})
    # The blocked call consumed the decision, so no extra wake was queued for it.
    assert not any(t["kind"] == "approval.decided"
                   for t in daemon.store.pending_triggers("prog1"))


def test_interactive_orchestrator_await_and_reports(world):
    """The orchestrator as an attached interactive session: it waits in await_events, gets
    events without headless wakes, and requests a report from a worker."""
    admin, daemon = world["admin"], world["daemon"]
    orch = daemon.slots["orch"]
    orch.attached = True
    wait_for(lambda: all(a["online"] for a in admin.get("/agents")), what="agents online")
    result = {}

    def wait():
        result["r"] = daemon.call_tool("orch", "await_events", {"timeout_seconds": 30})

    th = threading.Thread(target=wait)
    th.start()
    time.sleep(0.5)
    assert orch.awaiting
    admin.post("/fleet/start", {"goal": "Ship it"})
    th.join(35)
    digest = result["r"]["digest"]
    assert "Ship it" in digest and result["r"]["events"] >= 1
    assert not read_log(world["log"])  # no headless wake happened for the orchestrator

    # The orchestrator plans through its tools, as the interactive CLI would.
    daemon.call_tool("orch", "task_create", {"title": "Add hello",
                                             "path_hints": ["src/hello.py"]})
    d = daemon.call_tool("orch", "decision_request", {"kind": "start_approval",
                                                      "question": "OK?"})
    admin.post(f"/decisions/{d['id']}/answer", {"answer": "approve"})
    wait_for(lambda: all(t["state"] == "done" for t in admin.get("/tasks")), timeout=90,
             what="task done")
    seen = ""
    for _ in range(4):
        seen += daemon.call_tool("orch", "await_events", {"timeout_seconds": 30})["digest"]
        if "completed" in seen and "filed report" in seen:
            break
    assert "user answered decision" in seen
    assert "Task #1 (Add hello) completed by prog1" in seen and "filed report" in seen

    req = daemon.call_tool("orch", "report_request", {"agent": "prog1",
                                                      "about": "Summarize your work"})
    wait_for(lambda: admin.get("/reports", kind="request", state="answered"),
             what="report request answered")
    filed = [x for x in admin.get("/reports", kind="report") if x["request_id"] == req["id"]]
    assert filed and filed[0]["summary"] == "Status report"
    r = daemon.call_tool("orch", "await_events", {"timeout_seconds": 30})
    assert f"report #{filed[0]['id']}" in r["digest"]
    # Workers cannot request reports.
    with pytest.raises(Exception):
        daemon.call_tool("prog1", "report_request", {"agent": "orch", "about": "x"})


def test_stuck_freezes_fleet(world):
    admin, daemon = world["admin"], world["daemon"]
    wait_for(lambda: all(a["online"] for a in admin.get("/agents")), what="agents online")
    admin.post("/fleet/start", {"goal": "x"})
    d = wait_for(lambda: admin.get("/decisions", state="open"), what="start approval")[0]
    admin.post(f"/decisions/{d['id']}/answer", {"answer": "approve"})
    wait_for(lambda: all(t["state"] == "done" for t in admin.get("/tasks")), timeout=90,
             what="task done")
    prog = ServerClient(admin.base.removesuffix("/api/v1"), daemon.slots["prog1"].cfg.token)
    prog.post("/stuck", {"details": "need credentials"})
    stuck = wait_for(lambda: [x for x in admin.get("/decisions", state="open")
                              if x["kind"] == "stuck"], what="stuck decision")[0]
    assert admin.get("/fleet")["state"] == "frozen"
    wait_for(lambda: daemon.slots["prog1"].fleet.get("state") == "frozen", what="daemon sees freeze")
    daemon.refresh(daemon.slots["prog1"])
    v = daemon.handle_hook("prog1", "pre_tool", {"tool_name": "Bash", "tool_input": {}})
    assert "frozen" in v["deny"]
    admin.post(f"/decisions/{stuck['id']}/answer", {"answer": "use the test key"})
    assert admin.get("/fleet")["state"] == "running"
