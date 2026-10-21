# Requirements and installation

This page covers what each computer in a Hive setup needs, and how to put the `hive` command
on it. Hive is a single Python package. The same `hive` command runs the server, sets up
repositories, runs agents and steers the **fleet** (the team of AI agents working on your
repository), so you install the same thing everywhere. Terms are explained in the
[glossary](GLOSSARY.md); [How Hive works](ARCHITECTURE.md) shows how the parts fit together.

There are three places you may install Hive:

| Where | Why it needs Hive |
|---|---|
| **The server** | The one program that holds the fleet's shared state. It runs as `hive server`, or as a Python web app ([WSGI](GLOSSARY.md#wsgi)) on a web host. For public hosting, follow [DEPLOYMENT.md](DEPLOYMENT.md) instead of this page. |
| **Your computer** | Runs admin commands, the ones you use to control the fleet (`hive admin …`, `hive start`, `hive decide`, …). Usually also runs the orchestrator, the agent you chat with. |
| **Every agent machine** | Any computer that runs agents. It runs `hive join` (adds an agent to the computer) and `hive up` (runs them). The agents' CLIs also call `hive mcp` and `hive hook` themselves, to get Hive's tools and automatic checks. |

For a first trial, all three can be the same computer.

---

## Requirements

| What | Where | Notes |
|---|---|---|
| Python ≥ 3.12 and [uv](https://docs.astral.sh/uv/) (or pip) | every machine and the server | uv and pip are Python package installers; uv is faster and keeps `hive` isolated. |
| git | every agent machine | Each agent gets its own [worktree](GLOSSARY.md#worktree): a separate working folder of the repository. |
| Claude Code CLI (`claude`) | machines running Claude agents | Each Claude agent logs into its own Claude account once. |
| Gemini CLI (`gemini`) | machines running Gemini agents | Each Gemini agent logs into its own Google account once. Since June 2026 only with a paid Gemini API key or a Gemini Code Assist Standard/Enterprise licence; personal accounts use agy instead ([details](LIMITATIONS.md#gemini-cli-and-antigravity-cli)). |
| Codex CLI (`codex`) | machines running Codex agents | Each Codex agent logs into its own ChatGPT account once (`codex login`). |
| Antigravity CLI (`agy`) | machines running agy agents | Google's successor to Gemini CLI. Log in once per computer by running `agy`; all agy agents on that computer share that Google account ([why](MACHINES.md#log-each-slots-account-in-once)). |
| A git remote (`origin`) | the repository | The shared copy of the repository that every machine pushes to: GitHub or any remote all machines can reach. |
| A reachable server URL | | The address of the Hive server: `http://127.0.0.1:8765` (your own computer) for single-machine trials; a public HTTPS address otherwise. |

On a machine, each agent lives in a **slot**: its own private CLI settings folder (holding its
account login) plus its own worktree ([Setting up machines](MACHINES.md)).

You only need the CLIs for the vendors you actually use. A fleet of only Claude Code agents
needs no Gemini, Codex or agy installed anywhere.

**One machine can host several slots.** Each slot has its own CLI config folder, so several
Claude, Gemini or Codex accounts can run side by side on one computer. You don't need one
computer per account. (agy is the exception: it has one login per computer.)

### Platforms

Agent machines can run **Windows, macOS or Linux**, mixed freely in one fleet.
- **Paths:** `~/.hive` means `%USERPROFILE%\.hive` on Windows.
- **Commands in this documentation:** they work in bash and in PowerShell unless both
  variants are shown.
- **Agents' shells:** agents run commands in bash ([Git Bash](GLOSSARY.md#git-bash) on
  Windows, which comes with Git for Windows), PowerShell or cmd.exe. Hive's rules about which
  commands agents may run, and its approval checks for dangerous ones, cover all three
  ([Shell-command permissions](PERMISSIONS.md)).
- **The server:** any host that can run a Python web app, usually Linux.

---

## Installation

On **every** agent machine (and wherever you run admin commands):

```sh
uv tool install /path/to/hive          # /path/to/hive: where you downloaded Hive's source
hive --version
```

`uv tool install` installs `hive` in its own isolated environment and puts the command on
your [PATH](GLOSSARY.md#path), the list of folders your shell searches for commands, so
`hive` works in any terminal.

### With the installer scripts

The project folder also has installer scripts:

| System | Run | Double-click |
|---|---|---|
| Windows | `install.bat` | `install.bat` |
| macOS | `./install.sh` (or `sh install.sh`) | `install.command`. It opens Terminal and keeps the window open at the end. If macOS says it's from an unidentified developer, right-click it → *Open* → *Open*. |
| Linux | `./install.sh` (or `sh install.sh`) | – |

They all install or re-install Hive cleanly. They:
- check for uv and git (offering to install uv with its official installer);
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

**On a [PythonAnywhere](GLOSSARY.md#pythonanywhere) server** (a Python web host where you
can put the Hive server online), use `install-pa-server.sh` instead. It re-installs Hive
without touching the WSGI file, secrets, database or archives
([DEPLOYMENT.md §4.7](DEPLOYMENT.md#47-updating-the-code-later)).

### `hive` must be on PATH

The agent CLIs launch `hive mcp` (the program giving agents Hive's tools, through
[MCP](GLOSSARY.md#mcp-mcp-server-mcp-tool)) and `hive hook …` (Hive's
[hooks](GLOSSARY.md#hook), automatic checks the CLI runs before and after each action)
**by name**, from inside their own processes. If those processes can't find `hive`, agents get
no Hive tools and their file writes are refused. If `hive` can't be on PATH, run
`hive init --hive-cmd /full/path/to/hive` ([Preparing the repository](REPOSITORY.md)) so the
files `hive init` generates for the CLIs (the [bundles](GLOSSARY.md#bundle-plugin-extension-profile))
call it by its full path instead.

A version mismatch matters too. If the `hive` an agent CLI finds is older than the one
running `hive up` (the command that runs a machine's agents), hooks can fail in confusing
ways. Re-install on every machine after
upgrading ([Troubleshooting](TROUBLESHOOTING.md)).

### For development

In a clone of Hive's own source, `uv sync` sets up a development environment and
`uv run hive …` runs that copy, without installing anything globally.
See [Development](DEVELOPMENT.md).

---

## After installing

1. Start a server: [Running the server](SERVER.md).
2. Prepare your repository: [Preparing the repository](REPOSITORY.md).
3. Add agents and the computers that run them:
   [Enrolling agents and setting up machines](MACHINES.md).

Or follow the whole sequence at once in [Getting started](GETTING_STARTED.md).

---

**Next:** [Running the server](SERVER.md) · [← Back to the README](../README.md)
