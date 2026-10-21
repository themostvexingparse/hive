"""Antigravity CLI (agy) support: bundle, the slot's private home, launch commands, output,
hooks and the daemon's checks. Event and hook payloads are recorded from agy 1.4.4."""

import asyncio
import json
import os
import re
import subprocess
import sys
import time

import pytest

from hive import mcp_server
from hive import permissions as P
from hive.config import MachineConfig, Slot, hive_home
from hive.daemon import core, wake
from hive.daemon.store import Store
from hive.hooks import agy_input, format_output
from hive.init import init_repo


def agy_slot(tmp_path, **kw):
    return Slot(name="a1", vendor="agy", token="t", worktree=str(tmp_path),
                config_dir=str(tmp_path / "home"), **kw)


def test_init_writes_agy_plugin_settings_and_skills(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    init_repo(tmp_path, "http://srv")
    agy = tmp_path / ".hive" / "agent" / "agy"
    assert json.loads((agy / "plugin.json").read_text())["name"] == "hive"
    mcp = json.loads((agy / "mcp_config.json").read_text())["mcpServers"]["hive"]
    assert mcp == {"command": "hive", "args": ["mcp"]}
    hooks = json.loads((agy / "hooks.json").read_text())["hive"]
    assert set(hooks) == {"PreToolUse", "PreInvocation", "Stop", "SessionStart"}
    # Tool events take matcher groups; the others a plain list of handlers.
    assert hooks["PreToolUse"][0]["matcher"] == "*"
    assert hooks["PreToolUse"][0]["hooks"][0]["command"] == "hive hook PreToolUse --vendor agy"
    assert hooks["Stop"] == [{"type": "command", "timeout": 10,
                              "command": "hive hook Stop --vendor agy"}]
    for cls in ("orchestrator", "worker"):
        assert "permissions" in json.loads((agy / "settings" / f"{cls}.json").read_text())
    for skill in ("hive-orchestrator", "hive-worker"):
        assert (agy / "skills" / skill / "SKILL.md").read_text().startswith(
            f"---\nname: {skill}\n")
    assert not (tmp_path / ".agents").exists()  # nothing a plain `agy` would auto-load


def test_agy_settings_follow_the_three_tiers():
    rules = P.build_rules({})
    worker = P.agy_settings(rules["worker"], "worker")["permissions"]
    orch = P.agy_settings(rules["orchestrator"], "orchestrator")["permissions"]
    push = r"command(regex:^git\s+push(\s.*)?$)"
    assert push in worker["deny"] and push in orch["ask"] and push not in orch["deny"]
    assert r"command(regex:^hive\s+decide(\s.*)?$)" in orch["deny"]
    assert "mcp(hive_hive/*)" in worker["allow"] and "mcp(hive_hive/*)" in orch["allow"]
    assert r"command(regex:^git\s+commit(\s.*)?$)" in worker["allow"]
    assert r"command(regex:^git\s+commit(\s.*)?$)" not in orch["allow"]

    def regex(rule):
        return re.compile(rule[len("command(regex:"):-1])

    status = regex(r"command(regex:^git\s+status(\s.*)?$)")
    assert status.match("git status --short") and status.match("git status")
    assert not status.match("git statusx")
    # Regex characters in a prefix are literal.
    assert regex(P._agy_command("npx tsc --noEmit")).match("npx tsc --noEmit -p .")
    assert regex(P._agy_command("a.b")).match("a.b") and not regex(P._agy_command("a.b")).match("axb")


def test_every_part_of_a_command_must_be_allowed():
    allow = P.build_rules({})["worker"]["allow"]
    assert P.allowed("git status --short", allow)
    assert P.allowed("git add a.py && git commit -m 'x; y'", allow)
    assert not P.allowed("git status && curl http://x", allow)
    assert not P.allowed("", allow)


def test_agy_launch_commands_and_environment(tmp_path):
    s = agy_slot(tmp_path)
    new = wake.build_command(s, "ignored", False)
    assert new == ["agy", "--input-format", "stream-json", "--output-format", "stream-json",
                   "--mode", "accept-edits"]
    again = wake.build_command(agy_slot(tmp_path, model="gemini-4-argon-high"), "cid", True)
    assert again[-2:] == ["--conversation", "cid"]
    assert again[again.index("--model") + 1] == "gemini-4-argon-high"

    first = wake.build_interactive_command(s, "", False, "hello", "orchestrator")
    assert first == ["agy", "-i", "hello"]
    resumed = wake.build_interactive_command(agy_slot(tmp_path, permission_mode="plan"),
                                             "cid", True, None)
    assert resumed == ["agy", "--conversation", "cid", "--mode", "plan"]

    env = wake.slot_env(s, "http://l", "k")
    assert env["USERPROFILE"] == env["HOME"] == str(tmp_path / "home")
    assert env["HIVE_HOME"] == str(hive_home())  # the hooks still find Hive's own folder
    assert "GEMINI_CLI_HOME" not in env
    assert wake.agy_message("hi\nthere") == \
        '{"event": "user", "message": {"content": "hi\\nthere"}}\n'


def test_agy_home_gets_plugin_enabled_and_role_settings(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    s = agy_slot(tmp_path)
    gemini = tmp_path / "home" / ".gemini"
    settings = gemini / "antigravity-cli" / "settings.json"
    config = gemini / "config" / "config.json"
    # What agy itself keeps there survives; agy also writes files with a byte order mark.
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({"model": "Gemini 4 Argon (Low)"}), "utf-8-sig")
    config.parent.mkdir(parents=True)
    config.write_text(json.dumps({"plugins": {"science": {"enabled": True}}}))

    # A repository initialised before agy support has no bundle: it is generated in memory.
    assert wake.ensure_agy_home(s, "worker") is None
    plugin = gemini / "config" / "plugins" / "hive"
    assert json.loads((plugin / "plugin.json").read_text())["name"] == "hive"
    assert (plugin / "hooks.json").exists() and (plugin / "mcp_config.json").exists()
    assert (plugin / "skills" / "hive-worker" / "SKILL.md").exists()
    assert not (plugin / "settings").exists()
    assert json.loads(config.read_text())["plugins"] == {"science": {"enabled": True},
                                                         "hive": {"enabled": True}}
    data = json.loads(settings.read_text())
    assert data["model"] == "Gemini 4 Argon (Low)"
    assert data["trustedWorkspaces"] == [os.path.normpath(str(tmp_path))]
    worker_deny = data["permissions"]["deny"]
    assert r"command(regex:^git\s+push(\s.*)?$)" in worker_deny

    # With a committed bundle, that is what the slot gets, with the rules for its role.
    init_repo(tmp_path, "http://srv")
    assert wake.prepare_vendor(s, {}, "orchestrator") is None
    data = json.loads(settings.read_text())
    assert data["permissions"] == json.loads(
        (tmp_path / ".hive" / "agent" / "agy" / "settings" / "orchestrator.json").read_text()
    )["permissions"]
    assert data["trustedWorkspaces"] == [os.path.normpath(str(tmp_path))]  # not repeated

    no_home = Slot(name="a2", vendor="agy", token="t", worktree=str(tmp_path))
    assert "hive join" in wake.ensure_agy_home(no_home)
    settings.write_text("[1]")
    assert "could not write" in wake.ensure_agy_home(s)


def test_agy_session_id_is_read_back_after_an_interactive_session(tmp_path):
    conv = tmp_path / ".gemini" / "antigravity-cli" / "conversations"
    conv.mkdir(parents=True)
    old = conv / "9702575b-cd15-475d-8c92-0305dae08fa7.db"
    old.write_text("")
    os.utime(old, (1000, 1000))
    since = time.time()
    (conv / "9810790e-99d4-4221-97b2-b2c1747890cd.db").write_text("")
    (conv / "summaries.db").write_text("")
    s = Slot(name="a1", vendor="agy", token="t", worktree=str(tmp_path),
             config_dir=str(tmp_path))
    assert wake.latest_session(s, since) == "9810790e-99d4-4221-97b2-b2c1747890cd"
    assert wake.agy_latest_session(str(tmp_path), time.time() + 60) is None
    assert wake.agy_latest_session(str(tmp_path / "none"), 0) is None


CID = "9810790e-99d4-4221-97b2-b2c1747890cd"


def step(**kw):
    return {"event": "step_update", "step_update": {"conversation_id": CID, **kw}}


# Recorded from `agy --output-format stream-json` 1.4.4 (paths shortened).
AGY_EVENTS = [
    {"event": "init", "conversation_id": CID,
     "init": {"cwd": "C:\\ws", "tools": ["run_command"], "permission_mode": "request-review"}},
    step(step_index=0, state="DONE", step_type="user_input"),
    step(step_index=1, state="DONE", step_type="system_message", duration_seconds=0),
    step(step_index=2, state="DONE", step_type="agent_response", duration_seconds=4.2,
         usage={"input_tokens": 7278, "output_tokens": 72, "thinking_tokens": 25,
                "cache_read_tokens": 0, "total_tokens": 7350}),
    step(step_index=3, state="ACTIVE", step_type="tool", tool_name="run_command",
         tool_info={"name": "run_command", "parameters": {"CommandLine": "git status --short"}}),
    step(step_index=3, state="DONE", step_type="tool", tool_name="run_command",
         tool_info={"name": "run_command", "parameters": {"CommandLine": "git status --short"},
                    "output": "Command exited with code 0.\n\nCommand output:\n?? a.txt\n"}),
    step(step_index=4, state="DONE", step_type="agent_response",
         usage={"input_tokens": 2220, "output_tokens": 56, "thinking_tokens": 13,
                "cache_read_tokens": 5224, "total_tokens": 2276}),
    step(step_index=5, state="ACTIVE", step_type="tool", tool_name="run_command",
         tool_info={"name": "run_command", "parameters": {"CommandLine": "git push"}}),
    step(step_index=5, state="ERROR", step_type="tool", tool_name="run_command",
         tool_info={"name": "run_command", "parameters": {"CommandLine": "git push"},
                    "error": {"type": "TOOL_ERROR", "message": "tool call denied by pre-tool "
                                                               "hook: needs approval"}}),
    step(step_index=6, state="ACTIVE", step_type="tool", tool_name="call_mcp_tool",
         tool_info={"name": "call_mcp_tool", "parameters": {
             "Arguments": {"text": "hi"}, "ServerName": "hive_hive", "ToolName": "whoami"}}),
    step(step_index=6, state="DONE", step_type="tool", tool_name="call_mcp_tool",
         tool_info={"name": "call_mcp_tool", "parameters": {
             "Arguments": {"text": "hi"}, "ServerName": "hive_hive", "ToolName": "whoami"},
             "output": '{"slot":"a1"}'}),
    step(step_index=7, state="ACTIVE", step_type="tool", tool_name="write_file",
         tool_info={"name": "write_file", "parameters": {"Path": "C:\\ws\\note.txt"}}),
    step(step_index=7, state="DONE", step_type="tool", tool_name="write_file",
         tool_info={"name": "write_file", "parameters": {"Path": "C:\\ws\\note.txt"}}),
    step(step_index=8, state="ACTIVE", step_type="agent_response", text_delta="Secret word: **PEL"),
    step(step_index=8, state="ACTIVE", step_type="agent_response", text_delta="ICAN**\n\nSumm"),
    step(step_index=8, state="DONE", step_type="agent_response", text_delta="ary: done.\n",
         usage={"input_tokens": 2534, "output_tokens": 628, "thinking_tokens": 166,
                "cache_read_tokens": 6390, "total_tokens": 3162}),
    {"event": "result", "result": {"conversation_id": CID, "status": "SUCCESS",
                                   "response": "Secret word...", "num_turns": 1}},
]


def test_agy_stream_formatting_joins_text_and_counts_tokens():
    joiner, meter, lines = wake.DeltaJoiner(), wake.TokenMeter(), []
    for ev in AGY_EVENTS:
        meter.feed(ev)
        lines += joiner.feed(ev)
    lines += joiner.flush()
    assert lines == [
        f"· session {CID} started",
        "→ run_command {\"CommandLine\": \"git status --short\"}",
        "← Command exited with code 0. Command output: ?? a.txt",
        "→ run_command {\"CommandLine\": \"git push\"}",
        "× tool call denied by pre-tool hook: needs approval",
        "→ mcp__hive_hive__whoami {\"text\": \"hi\"}",
        '← {"slot":"a1"}',
        "→ write_file {\"Path\": \"C:\\\\ws\\\\note.txt\"}",
        "  Secret word: **PELICAN**",
        "  Summary: done.",
        "· turn done",
    ]
    # Three requests; the context is the last one's input, cached part included.
    assert meter.summary() == {"input": 7278 + 7444 + 8924, "output": 97 + 69 + 794,
                               "requests": 3, "context": 8924}
    meter.feed(AGY_EVENTS[3])  # the same step again is not counted twice
    assert meter.requests == 3
    # A headless turn that needed a confirmation nobody could give ends with no answer.
    assert wake.format_stream_event({"event": "result", "result": {
        "status": "SUCCESS", "response": "",
        "denied_actions": [{"action": "command", "display_name": "RunCommand"}]}}) == [
        "× needed a permission nobody could give: command (RunCommand)", "· turn done"]
    assert wake.format_stream_event({"event": "result", "result": {
        "status": "ERROR", "error": "boom"}}) == ["· turn error: boom"]


def test_run_cli_sends_the_prompt_as_stream_json_and_reads_the_conversation_id(tmp_path):
    script = ("import json, sys\n"
              "msg = json.loads(sys.stdin.readline())\n"
              "print(json.dumps({'event': 'init', 'conversation_id': 'cid-1', 'init': {}}))\n"
              "print(json.dumps({'event': 'step_update', 'step_update': {'step_index': 1, "
              "'state': 'DONE', 'step_type': 'agent_response', "
              "'text_delta': msg['message']['content']}}))\n"
              "print(json.dumps({'event': 'result', 'result': {'status': 'ERROR', "
              "'error': 'RESOURCE_EXHAUSTED: quota exceeded'}}))\n")
    seen = []
    res = wake.run_cli(agy_slot(tmp_path), [sys.executable, "-c", script], "do the task",
                       dict(os.environ), tmp_path / "log.txt", on_lines=seen.extend)
    assert res.session_id == "cid-1" and res.quota_hit
    assert "  do the task" in seen


def test_agy_hook_input_and_output():
    # Recorded hook input (common fields left out).
    event, data = agy_input("PreToolUse", {"stepIdx": 4, "toolCall": {
        "name": "run_command", "args": {"CommandLine": "git log --oneline -1",
                                        "WaitMsBeforeAsync": 5000}}})
    assert event == "pre_tool" and data["tool_name"] == "run_command"
    assert data["tool_input"]["CommandLine"] == "git log --oneline -1"
    assert agy_input("PreInvocation", {"invocationNum": 0, "initialNumSteps": 1})[0] == "prompt"
    assert agy_input("PreInvocation", {"invocationNum": 2})[0] == "post_tool"
    assert agy_input("SessionStart", {})[0] == "session_start"
    assert agy_input("Stop", {"terminationReason": "NO_TOOL_CALL"})[0] == "stop"

    out = lambda ev, v: format_output("agy", ev, v)
    assert out("PreToolUse", {"deny": "No lease"}) == {"decision": "deny", "reason": "No lease"}
    assert out("PreToolUse", {"ask": "dangerous"}) == {"decision": "ask", "reason": "dangerous"}
    assert out("PreToolUse", {"allow": True}) == {"decision": "auto_approve"}
    assert out("PreToolUse", {}) == {"decision": "allow"}
    assert out("Stop", {"block": "note first"}) == {"decision": "continue",
                                                    "reason": "note first"}
    assert out("Stop", {}) == {}
    assert out("SessionStart", {"context": "c"}) == {
        "injectSteps": [{"systemMessage": {"systemMessage": "c"}}]}
    assert out("PreInvocation", {}) == {}


@pytest.fixture
def daemon(tmp_path):
    d = core.Daemon(MachineConfig(server_url="http://127.0.0.1:9", slots=[agy_slot(tmp_path)]),
                    Store(tmp_path / "state.db"), echo=False)
    rt = d.slots["a1"]
    rt.me = {"roles": ["programmer"], "active": True, "can_code": True, "write_globs": []}
    rt.leases = [{"globs": ["src/**"]}]
    return d, rt


def pre(d, rt, tool, **args):
    _, data = agy_input("PreToolUse", {"toolCall": {"name": tool, "args": args}})
    return d._pre_tool(rt, "running", data)


def test_agy_shell_commands_are_approved_or_refused_by_hive(daemon):
    d, rt = daemon
    # Allowed commands are approved by Hive: agy's own rules would ask, which ends the turn.
    assert pre(d, rt, "run_command", CommandLine="git status --short") == {"allow": True}
    assert pre(d, rt, "run_command", CommandLine="git add a && git commit -m x") == \
        {"allow": True}
    assert "not allowed" in pre(d, rt, "run_command", CommandLine="git push origin main")["deny"]
    assert "request_approval" in pre(d, rt, "run_command", CommandLine="git reset --hard")["deny"]
    assert "never allowed" in pre(d, rt, "run_command", CommandLine="hive admin token")["deny"]
    unlisted = pre(d, rt, "run_command", CommandLine="curl https://example.com")["deny"]
    assert "isn't on the project's list" in unlisted and "request_approval" in unlisted
    # The orchestrator's interactive session leaves unlisted commands to agy, which asks.
    rt.me = {**rt.me, "roles": ["orchestrator"]}
    assert pre(d, rt, "run_command", CommandLine="curl https://example.com") == {}
    assert "ask" in pre(d, rt, "run_command", CommandLine="git reset --hard")


def test_agy_hive_tools_writes_and_leases(daemon, tmp_path):
    d, rt = daemon
    hive_call = {"ServerName": "hive_hive", "ToolName": "lease_acquire", "Arguments": {}}
    assert pre(d, rt, "call_mcp_tool", **hive_call) == {"allow": True}
    assert pre(d, rt, "call_mcp_tool", ServerName="other", ToolName="whoami") == {}
    assert pre(d, rt, "write_file", Path=str(tmp_path / "src" / "a.py"), Content="x") == {}
    assert pre(d, rt, "edit_file", Path="src/a.py", Target="x", Replacement="y") == {}
    assert "No lease covers docs/a.md" in pre(d, rt, "write_file", Path="docs/a.md")["deny"]
    assert "Agents may not edit" in pre(d, rt, "write_file",
                                        Path=".hive/agent/agy/hooks.json")["deny"]
    # Fail closed: a write whose file Hive can't find is refused, not waved through.
    assert "could not find which file" in pre(d, rt, "sed_file", Script="s/a/b/")["deny"]
    # A frozen fleet still lets the agent read and use Hive's tools.
    rt.me = {**rt.me, "active": False}
    assert pre(d, rt, "view_file", Path="src/a.py") == {}
    assert pre(d, rt, "call_mcp_tool", **hive_call) == {"allow": True}
    assert "must not act now" in pre(d, rt, "write_file", Path="src/a.py")["deny"]


def test_blocking_hive_tools_return_before_agy_gives_up(monkeypatch):
    calls = {}

    class D:
        slot = "a1"
        vendor = "agy"

        def tool(self, name, args, timeout=None):
            if name == "whoami":
                return {"result": {"tools": ["await_events", "request_approval"]}}
            calls[name] = args
            return {"result": {}}

    monkeypatch.setattr(mcp_server, "LocalDaemon", lambda slot=None: D())
    server = mcp_server.build_server("a1")
    asyncio.run(server.call_tool("await_events", {"timeout_seconds": 600}))
    asyncio.run(server.call_tool("request_approval", {"command": "git push", "reason": "r"}))
    assert calls["await_events"]["timeout_seconds"] <= 150
    assert calls["request_approval"]["wait_seconds"] <= 90
    D.vendor = "claude"  # the other CLIs let a call wait as long as it likes
    asyncio.run(server.call_tool("await_events", {"timeout_seconds": 600}))
    assert calls["await_events"]["timeout_seconds"] == 600
