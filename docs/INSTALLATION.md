# Requirements and installation

This page covers what each computer in a Hive setup needs, and how to put the `hive` command
on it. Hive is a single Python package. The same `hive` command runs the server, sets up
repositories, runs agent machines and steers the fleet, so you install the same thing
everywhere.

There are three places you may install Hive:

| Where | Why it needs Hive |
|---|---|
| **The server** | Runs `hive server` or the WSGI app. For public hosting, follow [DEPLOYMENT.md](DEPLOYMENT.md) instead of this page. |
| **Your computer** | Runs admin commands (`hive admin …`, `hive start`, `hive decide`, …). Usually also hosts the orchestrator. |
| **Every agent machine** | Runs `hive join` and `hive up`, and the agent CLIs call `hive mcp` and `hive hook`. |

For a first trial, all three can be the same computer.

---

## Requirements

| What | Where | Notes |
|---|---|---|
| Python ≥ 3.12 and [uv](https://docs.astral.sh/uv/) (or pip) | every machine and the server | |
| git | every agent machine | Each slot gets its own worktree. |
| Claude Code CLI (`claude`) | machines hosting Claude slots | Each Claude slot logs into its own Claude account once. |
| Gemini CLI (`gemini`) | machines hosting Gemini slots | Each Gemini slot logs into its own Google account once. |
| Codex CLI (`codex`) | machines hosting Codex slots | Each Codex slot logs into its own ChatGPT account once (`codex login`). |
| A git remote (`origin`) | the repository | GitHub or any remote all machines can push to. |
| A reachable server URL | | `http://127.0.0.1:8765` for single-machine trials; a public HTTPS URL otherwise. |

You only need the CLIs for the vendors you actually use. A fleet of only Claude Code agents
needs no Gemini or Codex installed anywhere.

**One machine can host several slots.** Each slot has its own CLI config folder, so several
Claude, Gemini or Codex accounts can run side by side on one computer. You don't need one
computer per account.

### Platforms

Agent machines can run **Windows, macOS or Linux**, mixed freely in one fleet.
- **Paths:** `~/.hive` means `%USERPROFILE%\.hive` on Windows.
- **Commands in this documentation:** they work in bash and in PowerShell unless both
  variants are shown.
- **Agents' shells:** agents use bash (Git Bash on Windows, which comes with Git for
  Windows), PowerShell or cmd.exe. Hive's permission rules and approval checks cover all
  three ([Shell-command permissions](PERMISSIONS.md)).
- **The server:** any WSGI host, usually Linux.

---

## Installation

On **every** agent machine (and wherever you run admin commands):

```sh
uv tool install /path/to/hive          # puts the `hive` command on PATH
hive --version
```

### With the installer scripts

The project folder also has installer scripts:

| System | Run | Double-click |
|---|---|---|
| Windows | `install.bat` | `install.bat` |
| macOS | `./install.sh` (or `sh install.sh`) | `install.command`. It opens Terminal and keeps the window open at the end. If macOS says it's from an unidentified developer, right-click it → *Open* → *Open*. |
| Linux | `./install.sh` (or `sh install.sh`) | – |

They all install or re-install Hive cleanly. They:
- check for uv (offering to install it with its official installer) and git;
- deal with running Hive processes, after asking:
  - **Windows:** stops `hive.exe`, since Windows can't replace a running executable;
  - **macOS and Linux:** offers to stop them, because running processes keep the old version
    until restarted. If you say no, the install goes ahead and the final steps remind you to
    restart them;
- rebuild even when the version number hasn't changed, so stale builds never survive;
- verify the installed command and that it's the one on your PATH.

| Option (`install.bat`) | Option (`install.sh`) | Effect |
|---|---|---|
| `/test` | `--test` | Run the test suite first; install only if it passes. |
| `/yes` | `--yes`, `-y` | Answer every question with yes (unattended). |
| `/nopath` | `--no-path` | Leave your PATH alone. |

**Unattended runs:** `install.sh` asks its questions on the terminal even when its input is
redirected. With no terminal at all (e.g. over `ssh host 'sh install.sh'`), every question
is answered no unless you pass `--yes`.

**On a PythonAnywhere server,** use `install-pa-server.sh` instead. It re-installs Hive
without touching the WSGI file, secrets, database or archives
([DEPLOYMENT.md §4.7](DEPLOYMENT.md#47-updating-the-code-later)).

### `hive` must be on PATH

The agent CLIs launch `hive mcp` and `hive hook …` **by name**, from inside their own
processes. If those processes can't find `hive`, agents get no Hive tools and their file
writes are refused. If `hive` can't be on PATH, run
`hive init --hive-cmd /full/path/to/hive` ([Preparing the repository](REPOSITORY.md)) so the
bundles use an absolute path.

A version mismatch matters too. If the `hive` an agent CLI finds is older than the one
running `hive up`, hooks can fail in confusing ways. Re-install on every machine after
upgrading ([Troubleshooting](TROUBLESHOOTING.md)).

### For development

`uv sync` in this repository gives you `uv run hive …` without installing anything globally.
See [Development](DEVELOPMENT.md).

---

## After installing

1. Start a server: [Running the server](SERVER.md).
2. Prepare your repository: [Preparing the repository](REPOSITORY.md).
3. Add agents and machines: [Enrolling agents and setting up machines](MACHINES.md).

Or follow the whole sequence at once in [Getting started](GETTING_STARTED.md).

---

**Next:** [Running the server](SERVER.md) · [← Back to the README](../README.md)
