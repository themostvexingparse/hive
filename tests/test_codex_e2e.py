"""End-to-end with the real Codex CLI: server + daemon + git worktrees, the scripted fake
orchestrator, and a Codex programmer whose model is a scripted fake Responses API. Skipped
when `codex` is not installed."""

import shutil
import sys
import threading
import tomllib

import pytest
import tomli_w
from werkzeug.serving import make_server

from fake_responses import FakeResponses, mcp_call, message, patch, tool_outputs
from hive.config import MachineConfig, Slot, save_machine_config
from hive.daemon import core
from hive.daemon.client import ServerClient
from hive.daemon.localapi import create_local_app
from hive.daemon.store import Store
from hive.init import init_repo
from hive.server.app import create_app
from hive.server.service import create_hive
from test_e2e import ADMIN, FAKE, git, serve, wait_for

pytestmark = pytest.mark.skipif(not shutil.which("codex") or not shutil.which("hive"),
                                reason="needs the codex CLI, and `hive` on PATH for its tools")

HELLO = "*** Begin Patch\n*** Add File: src/hello.py\n+print('hello')\n*** End Patch\n"


@pytest.mark.parametrize("mcp_startup_delay", [0, 3], ids=["normal", "slow-mcp"])
def test_codex_programmer_leases_integrates_and_streams(tmp_path, monkeypatch,
                                                       mcp_startup_delay):
    monkeypatch.setenv("HIVE_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("FAKE_AGENT_LOG", str(tmp_path / "agent.log"))
    monkeypatch.setattr(core, "COALESCE_SECONDS", 0)
    monkeypatch.setattr(core, "MIN_GAP_SECONDS", 0)
    model = FakeResponses([
        [mcp_call("c1", "whoami")],
        [patch("c2", HELLO)],  # no lease yet: Hive's PreToolUse hook must refuse it
        [mcp_call("c3", "lease_acquire", globs=["src/hello.py"], task_id=1)],
        [patch("c4", HELLO)],
        [mcp_call("c5", "lease_release", commit_message="Add hello (task 1)", task_id=1)],
        [mcp_call("c6", "task_complete", task_id=1, result="added src/hello.py")],
        [message("Task 1 is done.")],
    ])

    origin = tmp_path / "origin.git"
    git(tmp_path, "init", "--bare", "-b", "main", str(origin))
    clone = tmp_path / "proj"
    git(tmp_path, "clone", str(origin), str(clone))
    git(clone, "config", "user.email", "t@example.com")
    git(clone, "config", "user.name", "t")
    (clone / "README.md").write_text("proj\n")
    # Codex runs hooks through the user's shell, whose startup files may put another `hive`
    # first on PATH; pin the one under test.
    init_repo(clone, "http://unused", hive_cmd=shutil.which("hive"))
    if mcp_startup_delay:
        # A fast model response must not race MCP initialization. Delay only the MCP
        # process; hooks still run immediately, as they would with a cold MCP startup.
        profile_path = clone / ".hive" / "agent" / "codex" / "hive.config.toml"
        profile = tomllib.loads(profile_path.read_text())
        mcp = profile["mcp_servers"]["hive"]
        mcp["args"] = [
            "-c", "import subprocess, sys, time; "
                  f"time.sleep({mcp_startup_delay}); "
                  "sys.exit(subprocess.call(sys.argv[1:]))",
            mcp["command"], *mcp["args"],
        ]
        mcp["command"] = sys.executable
        profile_path.write_text(tomli_w.dumps(profile))
    git(clone, "add", "-A")
    git(clone, "commit", "-m", "init")
    git(clone, "push", "origin", "main")

    hive = create_hive(f"sqlite:///{tmp_path / 'hive.db'}", tick_interval=0)
    srv = serve(create_app(hive, admin_token=ADMIN))
    url = f"http://127.0.0.1:{srv.server_port}"
    admin = ServerClient(url, ADMIN)
    admin.put("/config", {"repo": {"main": "main"}, "integration": {"review": "off"}})

    slots = []
    for name, vendor, roles in [("orch", "claude", ["orchestrator"]),
                                ("cx", "codex", ["programmer"])]:
        tok = admin.post("/agents", {"name": name, "vendor": vendor, "roles": roles})["token"]
        wt = tmp_path / f"proj-{name}"
        git(clone, "worktree", "add", "-B", f"hive/{name}", str(wt), "origin/main")
        if vendor == "claude":
            slots.append(Slot(name=name, vendor=vendor, token=tok, worktree=str(wt),
                              command=[sys.executable, str(FAKE), "{session}"]))
        else:
            codex_home = tmp_path / "codex-home"
            codex_home.mkdir()
            (codex_home / "config.toml").write_text(model.codex_config())
            slots.append(Slot(name=name, vendor=vendor, token=tok, worktree=str(wt),
                              config_dir=str(codex_home)))
    mcfg = MachineConfig(server_url=url, local_port=0, poll_interval=0.2, slots=slots)
    daemon = core.Daemon(mcfg, Store(tmp_path / "home" / "state.db"))
    local = make_server("127.0.0.1", 0, create_local_app(daemon), threaded=True)
    mcfg.local_port = local.server_port
    threading.Thread(target=local.serve_forever, daemon=True).start()
    save_machine_config(mcfg)
    daemon.start_background()
    try:
        wait_for(lambda: all(a["online"] for a in admin.get("/agents")), what="agents online")
        admin.post("/fleet/start", {"goal": "Add a hello module"})
        d = wait_for(lambda: admin.get("/decisions", state="open"), what="start approval")[0]
        admin.post(f"/decisions/{d['id']}/answer", {"answer": "approve"})
        wait_for(lambda: all(t["state"] == "done" for t in admin.get("/tasks")), timeout=120,
                 what="task done")

        out = tool_outputs(model.requests)
        assert '"cx"' in out["c1"]                                  # hive tools reach the daemon
        assert "No lease covers src/hello.py" in out["c2"]          # hook enforces leases
        assert "integrated" in out["c5"]
        assert "src/hello.py" in git(origin, "ls-tree", "-r", "--name-only", "main").split()
        assert "Hive-Agent: cx" in git(origin, "log", "-1", "--format=%B", "main")
        # Codex's own session id, stored once the turn has ended, for the next wake to resume.
        assert len(wait_for(lambda: daemon.store.get("cx", "session"), what="session")) == 36
        assert (tmp_path / "codex-home" / "skills" / "hive-worker" / "SKILL.md").exists()
        texts = wait_for(lambda: (lambda t: t if any("turn done" in x for x in t) else None)(
            [l["text"] for l in admin.get("/streams", agent="cx")["lines"]]), what="cx stream")
        assert any(t.startswith("→ mcp__hive__lease_acquire") for t in texts)
        assert any(t.startswith("→ apply_patch add ") for t in texts)
        assert any(t.startswith("× Command blocked by PreToolUse hook: No lease covers")
                   for t in texts)
    finally:
        daemon.stop()
        local.shutdown()
        srv.shutdown()
        model.close()
