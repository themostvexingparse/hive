import json
import subprocess

from hive.config import Slot
from hive.daemon import wake
from hive.daemon.core import is_hive_tool
from hive.init import init_repo


def test_init_writes_session_bundles_and_removes_legacy_wiring(tmp_path):
    root = tmp_path
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    # Simulate wiring written by hive 0.1 next to the user's own settings.
    (root / ".mcp.json").write_text(json.dumps({"mcpServers": {
        "hive": {"command": "hive", "args": ["mcp"]}, "other": {"command": "x"}}}))
    (root / ".claude").mkdir()
    (root / ".claude" / "settings.json").write_text(json.dumps({
        "enabledMcpjsonServers": ["hive"], "permissions": {"allow": ["mcp__hive", "Bash(ls)"]},
        "hooks": {"Stop": [{"hooks": [{"type": "command",
                                       "command": "hive hook Stop --vendor claude"}]}]}}))
    (root / ".gemini").mkdir()
    (root / ".gemini" / "settings.json").write_text(json.dumps({"mcpServers": {"hive": {}}}))
    (root / "CLAUDE.md").write_text("# Mine\n\n<!-- hive:begin -->\nold\n<!-- hive:end -->\n")
    (root / "AGENTS.md").write_text("<!-- hive:begin -->\nold\n<!-- hive:end -->\n")

    written, cleaned = init_repo(root, "http://srv")

    assert json.loads((root / ".mcp.json").read_text()) == {"mcpServers": {"other":
                                                                          {"command": "x"}}}
    assert json.loads((root / ".claude" / "settings.json").read_text()) == {
        "permissions": {"allow": ["Bash(ls)"]}}
    assert not (root / ".gemini").exists()
    assert (root / "CLAUDE.md").read_text() == "# Mine\n"
    assert not (root / "AGENTS.md").exists()
    assert set(cleaned) == {".mcp.json", ".claude/settings.json", ".gemini/settings.json",
                            "CLAUDE.md", "AGENTS.md"}

    claude = root / ".hive" / "agent" / "claude"
    assert json.loads((claude / ".claude-plugin" / "plugin.json").read_text())["name"] == "hive"
    assert "hive" in json.loads((claude / ".mcp.json").read_text())["mcpServers"]
    hooks = json.loads((claude / "hooks" / "hooks.json").read_text())["hooks"]
    assert set(hooks) == {"PreToolUse", "PostToolUse", "UserPromptSubmit", "Stop",
                          "SessionStart"}
    gem = root / ".hive" / "agent" / "gemini"
    ext = json.loads((gem / "gemini-extension.json").read_text())
    assert ext["name"] == "hive" and ext["contextFileName"] == "HIVE.md"
    assert "AfterAgent" in json.loads((gem / "hooks" / "hooks.json").read_text())["hooks"]
    for base in (claude, gem):
        for skill in ("hive-orchestrator", "hive-worker"):
            text = (base / "skills" / skill / "SKILL.md").read_text()
            assert text.startswith(f"---\nname: {skill}\n")


def test_launch_commands_attach_capabilities_per_session(tmp_path):
    c = Slot(name="c1", vendor="claude", token="t", worktree=str(tmp_path))
    cmd = wake.build_interactive_command(c, "sid", False, "hello")
    assert cmd[0] == "claude" and "--plugin-dir" in cmd
    assert cmd[cmd.index("--plugin-dir") + 1].endswith(str(tmp_path / ".hive/agent/claude")
                                                       .replace("/", __import__("os").sep)) \
        or cmd[cmd.index("--plugin-dir") + 1].replace("\\", "/").endswith(".hive/agent/claude")
    assert cmd[-3:] == ["--session-id", "sid", "hello"]
    head = wake.build_command(c, "sid", True)
    assert "-p" in head and "stream-json" in head and head[-2:] == ["--resume", "sid"]
    g = Slot(name="g1", vendor="gemini", token="t", worktree=str(tmp_path), config_dir="/x")
    assert wake.build_interactive_command(g, "sid", True, "hi")[:3] == ["gemini", "-e", "hive"]
    assert "stream-json" in wake.build_command(g, "sid", False)
    env = wake.slot_env(g, "http://l", "s")
    assert env["GEMINI_CLI_HOME"] == "/x" and env["HIVE_SLOT"] == "g1"


def test_hive_tool_names_across_clis():
    assert is_hive_tool("mcp__plugin_hive_hive__lease_acquire")
    assert is_hive_tool("mcp__hive__post")
    assert is_hive_tool("mcp_hive_await_events")      # Gemini fully-qualified name
    assert not is_hive_tool("mcp_hivemind_post")
    assert is_hive_tool("await_events")
    assert not is_hive_tool("mcp__other__post")
    assert not is_hive_tool("Write")


def test_stream_formatting_claude_and_gemini():
    lines = wake.format_stream_event({"type": "assistant", "message": {"content": [
        {"type": "text", "text": "hi\nthere"},
        {"type": "tool_use", "name": "Edit", "input": {"file_path": "a.py"}}]}})
    assert lines == ["  hi", "  there", '→ Edit {"file_path": "a.py"}']
    j = wake.GeminiDeltaJoiner()
    assert j.feed({"type": "message", "role": "assistant", "content": "par", "delta": True}) == []
    assert j.feed({"type": "message", "role": "assistant", "content": "tial\nnext",
                   "delta": True}) == ["  partial"]
    out = j.feed({"type": "tool_use", "tool_name": "read_file", "parameters": {"p": 1}})
    assert out == ["  next", '→ read_file {"p": 1}']
    assert wake.format_stream_event({"type": "result", "status": "error"}) == ["· turn error"]
