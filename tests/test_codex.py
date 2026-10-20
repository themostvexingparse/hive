"""Codex CLI support: bundle, launch commands, the slot's private CODEX_HOME, output, hooks."""

import json
import os
import shutil
import subprocess
import sys
import time
import tomllib

import pytest

from hive import permissions as P
from hive.config import MachineConfig, Slot
from hive.daemon import core, wake
from hive.daemon.store import Store
from hive.hooks import format_output
from hive.init import init_repo


def codex_slot(tmp_path, **kw):
    return Slot(name="x1", vendor="codex", token="t", worktree=str(tmp_path),
                config_dir=str(tmp_path / "home"), **kw)


def test_init_writes_codex_profile_rules_and_skills(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    init_repo(tmp_path, "http://srv")
    codex = tmp_path / ".hive" / "agent" / "codex"
    profile = tomllib.loads((codex / "hive.config.toml").read_text())
    mcp = profile["mcp_servers"]["hive"]
    assert mcp["command"] == "hive" and mcp["args"] == ["mcp"]
    assert mcp["required"] is True
    assert set(mcp["env_vars"]) == {"HIVE_SLOT", "HIVE_DAEMON_URL", "HIVE_LOCAL_SECRET",
                                    "HIVE_HOME"}
    assert mcp["default_tools_approval_mode"] == "approve" and mcp["tool_timeout_sec"] >= 1800
    hooks = profile["hooks"]
    assert set(hooks) == {"PreToolUse", "PostToolUse", "UserPromptSubmit", "Stop", "SessionStart"}
    assert hooks["PreToolUse"][0]["matcher"] == "*" and "matcher" not in hooks["Stop"][0]
    assert hooks["PreToolUse"][0]["hooks"][0]["command"] == "hive hook PreToolUse --vendor codex"
    for cls in ("orchestrator", "worker"):
        assert "prefix_rule(" in (codex / "rules" / f"{cls}.rules").read_text()
    for skill in ("hive-orchestrator", "hive-worker"):
        assert (codex / "skills" / skill / "SKILL.md").read_text().startswith(
            f"---\nname: {skill}\n")
    assert not (tmp_path / ".codex").exists()  # nothing a plain `codex` would auto-load


def test_codex_rules_follow_the_three_tiers():
    rules = P.build_rules({})
    worker = P.codex_rules(rules["worker"], "worker")
    orch = P.codex_rules(rules["orchestrator"], "orchestrator")
    assert 'prefix_rule(pattern = ["git", "push"], decision = "forbidden"' in worker
    assert 'prefix_rule(pattern = ["git", "push"], decision = "prompt")' in orch
    assert 'prefix_rule(pattern = ["hive", "decide"], decision = "forbidden"' in orch
    assert 'prefix_rule(pattern = ["git", "status"], decision = "allow")' in worker
    assert 'prefix_rule(pattern = ["git", "commit"], decision = "allow")' in worker
    assert '["git", "commit"]' not in orch


@pytest.mark.skipif(not shutil.which("codex"), reason="codex CLI not installed")
def test_codex_accepts_the_generated_rules(tmp_path):
    f = tmp_path / "worker.rules"
    f.write_text(P.codex_rules(P.build_rules({})["worker"], "worker"))

    def decision(*cmd):
        out = subprocess.run(["codex", "execpolicy", "check", "--rules", str(f), "--", *cmd],
                             capture_output=True, text=True, check=True,
                             env={**os.environ, "CODEX_HOME": str(tmp_path / "home")}).stdout
        return json.loads(out).get("decision")

    assert decision("git", "push", "origin", "x") == "forbidden"
    assert decision("hive", "decide", "1", "approve") == "forbidden"
    assert decision("git", "status") == "allow"


def test_codex_launch_commands(tmp_path):
    s = codex_slot(tmp_path)
    new = wake.build_command(s, "ignored", False)
    assert new[:3] == ["codex", "exec", "--json"] and new[-1] == "-" and "ignored" not in new
    assert new[new.index("-p") + 1] == "hive" and "--dangerously-bypass-hook-trust" in new
    assert 'sandbox_mode="workspace-write"' in new and 'approval_policy="never"' in new
    assert wake.build_command(s, "abc", True)[-3:] == ["resume", "abc", "-"]
    yolo = wake.build_command(codex_slot(tmp_path, permission_mode="yolo", model="gpt-6-astra"),
                              "abc", True)
    assert "--dangerously-bypass-approvals-and-sandbox" in yolo
    assert not any("sandbox_mode" in a for a in yolo)
    assert yolo[yolo.index("-m") + 1] == "gpt-6-astra" and yolo.index("-m") < yolo.index("resume")

    first = wake.build_interactive_command(s, "", False, "hello", "orchestrator")
    assert first[0] == "codex" and first[-1] == "hello" and "exec" not in first
    assert not any("approval_policy" in a for a in first)  # interactive: the user approves
    again = wake.build_interactive_command(s, "abc", True, None)
    assert again[:2] == ["codex", "resume"] and again[-1] == "abc"
    assert wake.slot_env(s, "http://l", "k")["CODEX_HOME"] == str(tmp_path / "home")


def test_model_flag_for_every_cli(tmp_path):
    c = Slot(name="c", vendor="claude", token="t", worktree=str(tmp_path), model="opus")
    cmd = wake.build_command(c, "sid", False)
    assert cmd[cmd.index("--model") + 1] == "opus"
    g = Slot(name="g", vendor="gemini", token="t", worktree=str(tmp_path), model="pro")
    cmd = wake.build_interactive_command(g, "sid", False, None)
    assert cmd[cmd.index("-m") + 1] == "pro"
    assert "--model" not in wake.build_command(Slot(name="c", vendor="claude", token="t",
                                                    worktree=str(tmp_path)), "sid", False)


def test_codex_home_gets_profile_role_rules_and_skills(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    s = codex_slot(tmp_path)
    home = tmp_path / "home"
    # A repository initialised before Codex support has no bundle: it is generated in memory.
    assert wake.ensure_codex_home(s, "worker") is None
    assert "mcp_servers" in tomllib.loads((home / "hive.config.toml").read_text())
    assert 'decision = "forbidden"' in (home / "rules" / "hive.rules").read_text()
    assert (home / "skills" / "hive-worker" / "SKILL.md").exists()
    # With a committed bundle, that is what the slot gets, with the rules for its role.
    init_repo(tmp_path, "http://srv")
    assert wake.prepare_vendor(s, {}, "orchestrator") is None
    assert (home / "rules" / "hive.rules").read_text() == \
        (tmp_path / ".hive" / "agent" / "codex" / "rules" / "orchestrator.rules").read_text()
    no_home = Slot(name="x2", vendor="codex", token="t", worktree=str(tmp_path))
    assert "hive join" in wake.ensure_codex_home(no_home)
    assert wake.prepare_vendor(Slot(name="c", vendor="claude", token="t",
                                    worktree=str(tmp_path)), {}) is None


def test_codex_session_id_is_read_back_after_an_interactive_session(tmp_path):
    day = tmp_path / "sessions" / "2026" / "10" / "20"
    day.mkdir(parents=True)
    old = day / "rollout-2026-10-20T10-00-00-01a15859-0000-7000-8000-000000000001.jsonl"
    old.write_text("{}")
    os.utime(old, (1000, 1000))
    since = time.time()
    (day / "rollout-2026-10-20T11-00-00-01a15859-0000-7000-8000-000000000002.jsonl") \
        .write_text("{}")
    assert wake.codex_latest_session(str(tmp_path), since) == \
        "01a15859-0000-7000-8000-000000000002"
    assert wake.codex_latest_session(str(tmp_path), time.time() + 60) is None
    assert wake.codex_latest_session(str(tmp_path / "none"), 0) is None


# Recorded from `codex exec --json` 0.163.
CODEX_EVENTS = [
    {"type": "thread.started", "thread_id": "01a1585b-bfcc-7972-b40b-a2bf555d59e9"},
    {"type": "item.completed", "item": {"id": "item_0", "type": "error", "message":
        "`--dangerously-bypass-hook-trust` is enabled. Enabled hooks may run without review "
        "for this invocation."}},
    {"type": "turn.started"},
    {"type": "item.started", "item": {"id": "item_1", "type": "command_execution",
                                      "command": "/bin/zsh -lc 'echo hi && ls'",
                                      "aggregated_output": "", "exit_code": None,
                                      "status": "in_progress"}},
    {"type": "item.completed", "item": {"id": "item_1", "type": "command_execution",
                                        "command": "/bin/zsh -lc 'echo hi && ls'",
                                        "aggregated_output": "hi\na.txt\n", "exit_code": 0,
                                        "status": "completed"}},
    {"type": "item.started", "item": {"id": "item_2", "type": "mcp_tool_call", "server": "hive",
                                      "tool": "whoami", "arguments": {}, "result": None,
                                      "error": None, "status": "in_progress"}},
    {"type": "item.completed", "item": {"id": "item_2", "type": "mcp_tool_call",
                                        "server": "hive", "tool": "whoami", "arguments": {},
                                        "result": {"content": [{"type": "text",
                                                                "text": '{"slot": "x1"}'}]},
                                        "error": None, "status": "completed"}},
    {"type": "item.started", "item": {"id": "item_3", "type": "file_change", "changes": [
        {"path": "/r/a.txt", "kind": "update"}, {"path": "/r/sub/new.txt", "kind": "add"}],
        "status": "in_progress"}},
    {"type": "item.completed", "item": {"id": "item_3", "type": "file_change", "changes": [
        {"path": "/r/a.txt", "kind": "update"}], "status": "completed"}},
    {"type": "item.completed", "item": {"id": "item_4", "type": "agent_message",
                                        "text": "all done"}},
    {"type": "turn.completed", "usage": {"input_tokens": 40, "output_tokens": 20}},
]


def test_codex_stream_formatting():
    lines = [line for ev in CODEX_EVENTS for line in wake.format_stream_event(ev)]
    assert lines == [
        "· session 01a1585b-bfcc-7972-b40b-a2bf555d59e9 started",
        "→ shell /bin/zsh -lc 'echo hi && ls'",
        "← hi a.txt",
        "→ mcp__hive__whoami {}",
        '← {"slot": "x1"}',
        "→ apply_patch update /r/a.txt, add /r/sub/new.txt",
        "  all done",
        "· turn done",
    ]
    assert wake.format_stream_event({"type": "item.completed", "item": {
        "type": "command_execution", "command": "false", "aggregated_output": "",
        "exit_code": 1, "status": "failed"}}) == ["× exit 1: "]
    assert wake.format_stream_event({"type": "turn.failed", "error": {"message": "boom"}}) == \
        ["· turn error: boom"]


def test_run_cli_reads_codex_session_id_and_usage_limit(tmp_path):
    script = ("import json\n"
              "print(json.dumps({'type': 'thread.started', 'thread_id': 'tid-1'}))\n"
              "print(json.dumps({'type': 'turn.failed', 'error': "
              "{'message': 'You have hit your usage limit.'}}))\n")
    res = wake.run_cli(codex_slot(tmp_path), [sys.executable, "-c", script], "prompt",
                       dict(os.environ), tmp_path / "log.txt")
    assert res.session_id == "tid-1" and res.quota_hit


def test_codex_hook_output():
    deny = format_output("codex", "PreToolUse", {"deny": "No lease covers a.py."})
    assert deny["hookSpecificOutput"]["permissionDecision"] == "deny"
    # Codex hooks can't ask, so a question becomes a refusal that says what to do instead.
    ask = format_output("codex", "PreToolUse", {"ask": "Hive: this command is dangerous."})
    assert ask["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "ask the user" in ask["hookSpecificOutput"]["permissionDecisionReason"]
    assert format_output("codex", "Stop", {"block": "x"}) == {"decision": "block", "reason": "x"}
    ctx = format_output("codex", "SessionStart", {"context": "c"})
    assert ctx["hookSpecificOutput"]["additionalContext"] == "c"


def test_codex_patches_need_leases_for_every_file(tmp_path):
    d = core.Daemon(MachineConfig(server_url="http://127.0.0.1:9", slots=[codex_slot(tmp_path)]),
                    Store(tmp_path / "state.db"), echo=False)
    rt = d.slots["x1"]
    rt.me = {"roles": ["programmer"], "active": True, "can_code": True, "write_globs": []}
    rt.leases = [{"globs": ["src/**"]}]

    def pre(tool, **tool_input):
        return d._pre_tool(rt, "running", {"tool_name": tool, "tool_input": tool_input})

    patch = "*** Begin Patch\n*** Update File: src/a.py\n@@\n-a\n+b\n*** End Patch\n"
    assert pre("apply_patch", command=patch) == {}
    moved = ("*** Begin Patch\n*** Update File: src/a.py\n*** Move to: docs/a.py\n@@\n-a\n+b\n"
             "*** End Patch\n")
    assert core.patch_paths(moved) == ["src/a.py", "docs/a.py"]
    assert "No lease covers docs/a.py" in pre("apply_patch", command=moved)["deny"]
    added = f"*** Begin Patch\n*** Add File: {tmp_path / 'src' / 'new.py'}\n+x\n*** End Patch\n"
    assert pre("apply_patch", command=added) == {}  # absolute paths in the worktree
    assert "not allowed" in pre("Bash", command="git push origin main")["deny"]
