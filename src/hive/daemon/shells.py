"""Running commands in the right shell on Windows, macOS and Linux.

Used for commands the orchestrator approved and for `[integration] checks`. Shells:
  bash        POSIX shell; Git Bash on Windows (what Claude Code's Bash tool uses)
  powershell  pwsh if installed, else Windows PowerShell (what Gemini CLI and agy use on Windows)
  cmd         cmd.exe (Windows only)
  sh          /bin/sh on POSIX; Git Bash on Windows
  auto        powershell on Windows, bash elsewhere
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

SHELLS = ("bash", "powershell", "cmd", "sh")
APPROVED_COMMAND_TIMEOUT = 600


def default_shell() -> str:
    return "powershell" if os.name == "nt" else "bash"


def tool_shell(tool: str) -> str:
    """Which shell a CLI's shell tool runs commands in."""
    if tool == "PowerShell":
        return "powershell"
    if tool == "Bash":  # Claude Code: Git Bash on Windows
        return "bash"
    return default_shell()  # Gemini CLI's run_shell_command, agy's run_command


def resolve_shell(shell: str | None) -> str:
    shell = (shell or "auto").lower()
    if shell == "auto":
        return default_shell()
    if shell not in SHELLS:
        raise ValueError(f"shell must be auto or one of {SHELLS}")
    if shell == "cmd" and os.name != "nt":
        raise ValueError("cmd is only available on Windows")
    return shell


def find_bash() -> list[str]:
    """A POSIX shell. On Windows prefer Git Bash (which ships with Git for Windows, a Hive
    requirement) over System32's WSL launcher."""
    if os.name == "nt":
        git = shutil.which("git")
        if git:
            root = Path(git).resolve().parent.parent
            for cand in (root / "bin" / "bash.exe", root / "usr" / "bin" / "bash.exe"):
                if cand.exists():
                    return [str(cand), "-c"]
    bash = shutil.which("bash")
    return [bash, "-c"] if bash else ["/bin/sh", "-c"]


def shell_args(shell: str, command: str) -> list[str] | str:
    """What to pass to subprocess to run `command` in `shell`. cmd.exe gets a raw command line:
    it doesn't understand the backslash-escaped quotes Python would produce from a list."""
    shell = resolve_shell(shell)
    if shell == "powershell":
        exe = shutil.which("pwsh") or shutil.which("powershell") or "powershell"
        return [exe, "-NoProfile", "-NonInteractive", "-Command", command]
    if shell == "cmd":
        comspec = os.environ.get("COMSPEC", "cmd.exe")
        return f'"{comspec}" /d /s /c "{command}"'
    if shell == "sh" and os.name != "nt":
        return ["/bin/sh", "-c", command]
    return [*find_bash(), command]


def popen_group_kwargs() -> dict:
    """Start children in their own process group (POSIX) so kill_tree can stop grandchildren."""
    return {} if os.name == "nt" else {"start_new_session": True}


def kill_tree(proc: subprocess.Popen) -> None:
    """Kill a process and everything it started. Killing only the direct child is not enough:
    on Windows a `.cmd` shim (npm-installed CLIs) or cmd.exe would leave its child running,
    holding the output pipe open."""
    if proc.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                           capture_output=True)
        else:
            import signal
            os.killpg(proc.pid, signal.SIGKILL)
    except (OSError, ValueError):
        pass
    try:
        proc.kill()
    except OSError:
        pass


def run_shell(cwd: str, command: str, shell: str,
              timeout: float = APPROVED_COMMAND_TIMEOUT) -> tuple[int, str]:
    """Run `command`; returns (exit code, combined output tail). -1 on timeout or failure."""
    try:
        args = shell_args(shell, command)
    except ValueError as e:
        return -1, f"[hive] {e}"
    try:
        proc = subprocess.Popen(args, cwd=cwd, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, text=True,
                                encoding="utf-8", errors="replace", **popen_group_kwargs())
    except OSError as e:
        return -1, f"[hive] could not start {shell}: {e}"
    try:
        out, _ = proc.communicate(timeout=timeout)
        return proc.returncode, (out or "")[-20000:]
    except subprocess.TimeoutExpired:
        kill_tree(proc)
        try:
            out, _ = proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            out = ""
        return -1, (out or "")[-20000:] + f"\n[hive] killed after {timeout:.0f}s"
