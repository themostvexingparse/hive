"""Running commands in the platform's shells (bash, PowerShell, cmd.exe)."""

import os
import shutil

import pytest

from hive.daemon import shells
from hive.daemon.gitops import native_shell
from hive.hooks import format_output

WINDOWS = os.name == "nt"


def test_shell_resolution(monkeypatch):
    assert shells.resolve_shell("auto") == ("powershell" if WINDOWS else "bash")
    assert shells.tool_shell("Bash") == "bash"
    assert shells.tool_shell("PowerShell") == "powershell"
    assert shells.tool_shell("run_shell_command") == shells.default_shell()
    assert native_shell("native") == ("cmd" if WINDOWS else "sh")
    assert native_shell("bash") == "bash"
    with pytest.raises(ValueError):
        shells.resolve_shell("fish")


def test_bash_runs_posix_commands(tmp_path):
    code, out = shells.run_shell(str(tmp_path), "mkdir -p a/b && echo hi > a/b/f && cat a/b/f",
                                 "bash")
    assert code == 0 and out.strip() == "hi"
    code, out = shells.run_shell(str(tmp_path), "exit 3", "bash")
    assert code == 3


@pytest.mark.skipif(not (shutil.which("pwsh") or shutil.which("powershell")),
                    reason="PowerShell not installed")
def test_powershell_runs_commands_with_quotes(tmp_path):
    script = ('New-Item -ItemType Directory "a b" | Out-Null; '
              'Set-Content "a b/f.txt" \'say "hi"\'; Get-Content "a b/f.txt"')
    code, out = shells.run_shell(str(tmp_path), script, "powershell")
    assert code == 0, out
    assert out.strip() == 'say "hi"'
    code, _ = shells.run_shell(str(tmp_path), "exit 4", "powershell")
    assert code == 4


@pytest.mark.skipif(not WINDOWS, reason="cmd.exe is Windows-only")
def test_cmd_runs_commands_with_quotes_and_chaining(tmp_path):
    code, out = shells.run_shell(str(tmp_path),
                                 'mkdir "a b" && echo hello> "a b\\f.txt" && type "a b\\f.txt"',
                                 "cmd")
    assert code == 0, out
    assert out.strip() == "hello"


@pytest.mark.skipif(WINDOWS, reason="POSIX-only check")
def test_cmd_is_rejected_off_windows(tmp_path):
    code, out = shells.run_shell(str(tmp_path), "dir", "cmd")
    assert code == -1 and "Windows" in out


def test_timeout_is_reported(tmp_path):
    code, out = shells.run_shell(str(tmp_path), "sleep 5", "bash", timeout=0.5)
    assert code == -1 and "killed" in out


def test_hook_ask_verdicts_per_cli():
    out = format_output("claude", "PreToolUse", {"ask": "dangerous"})
    assert out["hookSpecificOutput"]["permissionDecision"] == "ask"
    assert format_output("gemini", "BeforeTool", {"ask": "dangerous"}) is None
