# Hive

Hive turns several subscription-based coding agents into one coordinated team. Each agent is
**Claude Code**, **Gemini CLI** or **Codex**, running as the vendor's official CLI on a computer you own
and logged into its own account. Together they share:

- one git repository;
- a message board (channels, direct messages, priorities, interrupts);
- a task board with automatic assignment by role;
- path **leases** (locks), so two agents never edit the same files at once.

A **non-coding orchestrator** plans the work, assigns it, monitors progress and keeps you
informed. It runs as a normal interactive CLI session on your computer, and you talk to it
directly. Two rules always hold:

1. **Nobody codes until you approve the plan.**
2. **When an agent is stuck and only you can resolve it, the whole fleet freezes until you
   answer.**

Hive uses no vendor API keys. It drives the official CLIs through their documented features:
interactive and headless modes, MCP servers, hooks, and plugins/extensions. It never extracts
or proxies account credentials. Each account is used only by its own CLI. Check each vendor's
terms for your own situation, for example how many accounts one person may run.

The design rationale and full specification are in [SPEC.md](SPEC.md). This README is the
operating manual.

---

## Contents

1. [How it works](#1-how-it-works)
2. [Requirements](#2-requirements)
3. [Installation](#3-installation)
4. [Running the server](#4-running-the-server) (full guide: [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md))
5. [Preparing the repository (`hive init`)](#5-preparing-the-repository-hive-init)
6. [Enrolling agents and setting up machines](#6-enrolling-agents-and-setting-up-machines)
7. [Running the fleet (`hive up`)](#7-running-the-fleet-hive-up)
8. [Working with the orchestrator](#8-working-with-the-orchestrator)
9. [Watching the workers](#9-watching-the-workers)
10. [The fleet lifecycle](#10-the-fleet-lifecycle)
11. [Roles](#11-roles)
12. [Tasks](#12-tasks)
13. [Messages and priorities](#13-messages-and-priorities)
14. [Leases and git integration](#14-leases-and-git-integration)
15. [Reviews](#15-reviews)
16. [Reports](#16-reports)
17. [What the agents see: MCP tools, hooks, skills, permissions](#17-what-the-agents-see-mcp-tools-hooks-skills)
18. [Web dashboard](#18-web-dashboard)
19. [Command reference](#19-command-reference)
20. [Configuration reference](#20-configuration-reference)
21. [Files and directories](#21-files-and-directories)
22. [Timings and limits](#22-timings-and-limits)
23. [Security model](#23-security-model)
24. [Troubleshooting](#24-troubleshooting)
25. [Known limitations and verification status](#25-known-limitations-and-verification-status)
26. [Development](#26-development)

---

## 1. How it works

```
                 ┌──────────────── hive-server (Flask / WSGI) ─────────────────┐
                 │ agents · messages · tasks · leases · fleet state ·          │
                 │ decisions · status feed · reports · live output (admin)     │
                 │ web dashboard                                               │
                 └──────────▲──────────────────────────────▲───────────────────┘
                HTTPS poll  │                              │   HTTPS poll
   ┌────────────────────────┴───────────┐       ┌──────────┴─────────────────────────┐
   │ Your computer: `hive up`           │       │ Worker machine: `hive up`          │
   │  daemon (background)               │       │  daemon                            │
   │  ┌───────────────────────────────┐ │       │  ┌───────────────────────────────┐ │
   │  │ claude (interactive)          │ │       │  │ claude -p … (headless wakes)  │ │
   │  │  = orchestrator, you chat here│ │       │  │ gemini -p … (headless wakes)  │ │
   │  └───────────────────────────────┘ │       │  └───────────────────────────────┘ │
   │  worktree: proj-orch               │       │  worktrees: proj-claude-2, …       │
   └────────────────────────────────────┘       └────────────────────────────────────┘
                 \__________________ GitHub / git origin ___________________/
```

- **Server** (`hive server`, or WSGI on any host such as PythonAnywhere): the single source of
  truth. It stores everything and decides lease grants, task assignment and fleet state. It
  needs no background workers or WebSockets.
- **Daemon** (`hive up`, one per machine): talks to the server for every agent **slot** on
  that machine. A slot is one agent identity: an account plus a git worktree. The daemon
  checks the server every 3 seconds, starts agents when something concerns them, runs git
  operations, uploads live output, and serves a local API on `127.0.0.1` for the agents' tools
  and hooks.
- **Agents** are the vendor CLIs. When `hive` launches one, it attaches Hive **for that
  session only**:
  - the `hive` MCP server (the agent's tools);
  - hooks (message delivery and write enforcement);
  - two skills: `hive-orchestrator` and `hive-worker`.

  A `claude`, `gemini` or `codex` you start yourself never sees any of this.
- **The orchestrator** runs as the **native interactive CLI** in your terminal. You talk to it
  like any session. When idle it waits inside the `await_events` tool, which uses no tokens,
  and returns the moment something needs its attention.
- **Workers** (programmers, reviewers, testers, …) run **headless**. The daemon starts a turn
  when work arrives for them. Their output is shown live on their machine and uploaded so that
  **only you** can watch it from anywhere. The orchestrator never sees their raw output; it
  gets **reports** instead.

---

## 2. Requirements

| What | Where | Notes |
|---|---|---|
| Python ≥ 3.12 and [uv](https://docs.astral.sh/uv/) (or pip) | every machine and the server | |
| git | every agent machine | Each slot gets its own worktree. |
| Claude Code CLI (`claude`) | machines hosting Claude slots | Each Claude slot logs into its own Claude account once. |
| Gemini CLI (`gemini`) | machines hosting Gemini slots | Each Gemini slot logs into its own Google account once. |
| Codex CLI (`codex`) | machines hosting Codex slots | Each Codex slot logs into its own ChatGPT account once (`codex login`). |
| A git remote (`origin`) | the repository | GitHub or any remote all machines can push to. |
| A reachable server URL | | `http://127.0.0.1:8765` for single-machine trials; a public HTTPS URL otherwise. |

**One machine can host several slots.** Each slot has its own CLI config folder, so several
Claude, Gemini or Codex accounts can run side by side on one computer. You don't need one computer
per account.

**Platforms.** Agent machines can run **Windows, macOS or Linux**, mixed freely in one fleet.
- **Paths:** `~/.hive` means `%USERPROFILE%\.hive` on Windows.
- **Commands in this guide:** they work in bash and in PowerShell unless both variants are
  shown.
- **Agents' shells:** agents use bash (Git Bash on Windows, which comes with Git for
  Windows), PowerShell or cmd.exe. Hive's permission rules and approval checks cover all
  three (§17.4).
- **The server:** any WSGI host, usually Linux.

---

## 3. Installation

On **every** agent machine (and wherever you run admin commands):

```sh
uv tool install /path/to/hive          # puts the `hive` command on PATH
hive --version
```

**Or use the installer script in the project folder:**

| System | Run | Double-click |
|---|---|---|
| Windows | `install.bat` | `install.bat` |
| macOS | `./install.sh` (or `sh install.sh`) | `install.command`. It opens Terminal and keeps the window open at the end. If macOS says it's from an unidentified developer, right-click it → *Open* → *Open*. |
| Linux | `./install.sh` (or `sh install.sh`) | – |

They all install or re-install Hive cleanly:
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

`hive` **must be on PATH**: the agent CLIs launch `hive mcp` and `hive hook …` by name. If it
can't be, run `hive init --hive-cmd /full/path/to/hive` (see §5) so the bundles use an
absolute path.

For development, `uv sync` in this repository gives you `uv run hive …`.

---

## 4. Running the server

The server needs three things:
- an **admin token**, which is your password; keep it secret;
- a **database URL**;
- optionally, a GitHub token (see §14.4).

| Environment variable | Required | Meaning |
|---|---|---|
| `HIVE_ADMIN_TOKEN` | yes | Admin (your) token. Anyone holding it controls the fleet. |
| `HIVE_DATABASE_URL` | no (default `sqlite:///hive.db`) | SQLAlchemy URL: SQLite, MySQL (`mysql+pymysql://…`), or Postgres. |
| `HIVE_GITHUB_TOKEN` | no | Lets the server move `main` itself through the GitHub API (§14.4). |

### 4.1 Locally (trials, single machine)

```sh
python -c "import secrets; print(secrets.token_urlsafe(32))"     # generate an admin token
hive server --admin-token <that token> --db sqlite:///hive.db --port 8765
```

| Option | Default |
|---|---|
| `--host` | `127.0.0.1` |
| `--port` | `8765` |
| `--db` | `sqlite:///hive.db` |

This is Flask's built-in server: fine for trying Hive and for a LAN. Use a proper WSGI
server for anything public.

### 4.2 Public deployment (PythonAnywhere, VPS, containers)

**➜ See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)** for the full guide:
- choosing a host;
- **capacity planning** with measured storage and request-load numbers;
- step-by-step PythonAnywhere and VPS (gunicorn + Caddy + systemd) setups;
- upgrades, backups, data pruning, monitoring, hardening and troubleshooting.

In short, the server is a WSGI app (`hive.server.wsgi:application`) that needs no background
workers. A PythonAnywhere **Developer** plan with its included MySQL has a wide margin for a
7-agent fleet: well under 1 GB of data per busy year. The **free plan** works for trials and
small fleets, using a SQLite file in Hive's network-filesystem mode
([DEPLOYMENT.md §4.8](docs/DEPLOYMENT.md#48-free-beginner-plan-with-sqlite)).

### 4.3 Log in as admin

On your computer:

```sh
hive admin login --server https://USER.pythonanywhere.com --token <admin token>
```

This saves `~/.hive/admin.toml` (user-only permissions). Every user-side command uses it.
`HIVE_SERVER_URL` and `HIVE_ADMIN_TOKEN` environment variables override the file.

---

## 5. Preparing the repository (`hive init`)

Run once in a clone of the project the agents will work on:

```sh
cd your-project
hive init                 # options: --server URL  --main main  --hive-cmd hive
```

It creates or updates:

| Path | Purpose | Commit it? |
|---|---|---|
| `hive.toml` | Fleet configuration: review policy, checks, standby orchestrator, shell-command permissions, roles (§20.1). Created only if missing. | yes |
| `.hive/agent/claude/` | **Claude Code plugin**: `.claude-plugin/plugin.json`, `.mcp.json` (hive MCP server), `hooks/hooks.json`, `skills/hive-orchestrator/SKILL.md`, `skills/hive-worker/SKILL.md`, plus per-role permission files `settings/{orchestrator,worker}.json` (§17.4). | yes |
| `.hive/agent/gemini/` | **Gemini CLI extension**: `gemini-extension.json` (MCP server, `HIVE.md` context), `hooks/hooks.json`, the same skills, plus per-role policy files `policies/{orchestrator,worker}.toml` (§17.4). | yes |
| `.hive/agent/codex/` | **Codex profile**: `hive.config.toml` (hive MCP server and hooks), the same skills, plus per-role execpolicy rules `rules/{orchestrator,worker}.rules` (§17.4). | yes |
| `.hive/plans/` | Place for written plans. | yes |
| `.hive/prompts/` | **Your additions to the agents' instructions** (§17.5): `README.md`, plus commented-out starters `orchestrator.md`, `worker.md` and `roles/<role>.md` for every role. Each is created only if missing; your files are never overwritten. Until you write something outside the comments, they add nothing. | yes |
| `.hive/README.md` | Explains the folder. | yes |
| `.gitignore` | Adds `.hive/orchestrator/` (the orchestrator's private memory). | yes |

**The bundles are inert.** Neither CLI loads `.hive/agent/` on its own:
- Claude Code gets the plugin only when `hive` passes `--plugin-dir .hive/agent/claude`.
- Gemini gets the extension only through the slot's **private** Gemini home (§7.3).
- Codex gets the profile, rules and skills only through the slot's **private** `CODEX_HOME`,
  selected with `-p hive` (§7.3).

Opening `claude`, `gemini` or `codex` in this repository yourself loads nothing from Hive.

**Cleanup of older versions:** `hive init` removes Hive entries written by Hive 0.1 to files
the CLIs load automatically: `.mcp.json`, `.claude/settings.json`, `.gemini/settings.json`,
and the `<!-- hive:begin -->` blocks in `CLAUDE.md`, `GEMINI.md`, `AGENTS.md`. Your own
entries in those files are kept. A file is deleted only if Hive's content was all it had.

Re-run `hive init` after upgrading Hive, or after changing `.hive/prompts/orchestrator.md`
or `worker.md`, to refresh the bundles. Then commit and push, and upload the configuration:

```sh
git add -A
git commit -m "Add Hive"
git push
hive admin push-config        # uploads hive.toml to the server (run inside the repo)
```

`hive start` (§10) also uploads `hive.toml` automatically when run inside the repository.

### Checking a project's setup (`hive overview`)

Run `hive overview` anywhere inside the project (or `hive overview PATH`) for a full report on
its Hive setup, ending with a list of problems and the command that fixes each one:

| Section | What it shows |
|---|---|
| **Repository** | Branch, `origin`, the integration branch and whether it's fetched, GitHub mode, whether Hive's commit hook is installed, and whether `.hive/agent/` matches what `hive init` would write now. Changes to Hive's version, `[permissions]` or `.hive/prompts/` all make it stale. |
| **Configuration** | Server URL, review policy, checks and their shell, standby orchestrators, pacing (`hive.toml`). |
| **Shell-command permissions** | The preset and how many command prefixes are allowed, need approval, or are never allowed, for the orchestrator and for workers; your extra lists. |
| **Roles** | Every role: built-in, built-in but changed in `hive.toml`, or custom; required or optional; how many holders; what it may write; task kinds; where its prompt comes from (built-in, `hive.toml`, `.hive/prompts/roles/`). |
| **Project prompts** | Each file in `.hive/prompts/`: active (with line count), comments only, or too large; and where it goes. |
| **This machine** | The machine config, whether `hive up` is running, whether you're logged in as admin, and this repository's slots: CLI, what each is doing now, its branch, whether its account has been logged in, and its worktree. |
| **Fleet** | Asked from the server: state, goal, orchestrator, `main`, agents (online, and which run on this machine), open tasks and active leases. |
| **Problems** | Everything above that needs attention. |

**Problems it reports include:**
- no `origin` remote, or the integration branch not fetched;
- stale agent bundles, with the reason;
- `.hive/orchestrator/` not git-ignored;
- errors in `hive.toml` (`[roles]`, `[permissions]`);
- prompt-file warnings (§17.5);
- slots with a missing worktree, the wrong branch, a never-logged-in account, or a worktree
  shared with another slot;
- a server URL that differs between `hive.toml` and this machine;
- an unreachable server, or credentials it refuses;
- a server whose review policy differs from `hive.toml`, meaning the uploaded config is stale;
- slots set up here but not enrolled on the server;
- required roles nobody holds.

**Which server it asks:** the one this project really uses. `hive.toml`'s `[server] url` is
informational only (§20.1), so the overview picks, in this order:
1. the server this project's slots on this machine connect to (`~/.hive/machine.toml`, set by
   `hive join --server`);
2. the server your admin login points at (`~/.hive/admin.toml`, set by `hive admin login`);
3. `hive.toml`'s value, only if neither exists.

The Fleet section names the source. It also lists the other addresses whenever they differ,
and reports:
- agents and admin commands pointing at different servers, which means two different fleets;
- an out-of-date `hive.toml` value, which is harmless for connecting but misleading for
  anyone setting up a new machine.

`hive init` records this machine's server in a new `hive.toml` when you don't pass `--server`
and aren't logged in as admin.

**How it gets the fleet data:** it uses your admin login if it points at that server.
Otherwise it uses the token of one of this machine's slots for the repository, which can read
the fleet but nothing admin-only. It asks the local daemon whether `hive up` is running.

**Offline:** `--offline` skips both the server and the daemon, so it works with no network.
Without it, each check gives up after a few seconds. Nothing is ever changed.

---

## 6. Enrolling agents and setting up machines

### 6.1 Enroll each agent (on your computer)

```sh
hive admin enroll orch     --vendor claude --roles orchestrator
hive admin enroll claude-2 --vendor claude --roles programmer
hive admin enroll claude-3 --vendor claude --roles programmer,tester
hive admin enroll gemini-1 --vendor gemini --roles reviewer,planner
hive admin enroll codex-1  --vendor codex  --roles programmer
```

- **Names** must match `^[a-z][a-z0-9-]{1,31}$`. `user`, `system` and `admin` are reserved.
- **Roles** are comma-separated (§11). `orchestrator` can't be combined with a coding role.
- Each command prints the agent's **token once**, plus a ready-made `hive join` command. The
  server stores only a hash.
- A lost token can be replaced: `POST /api/v1/agents/<name>/token` with the admin token
  rotates it.

### 6.2 Join each slot on the machine that will run it

```sh
hive join claude-2 --vendor claude --token <token> --server <url> --repo /path/to/clone
```

What `hive join` does:
1. **Checks** the token against the server and that it belongs to `claude-2`.
2. **Creates a git worktree** for the slot next to your clone:
   `/path/to/<clone-name>-claude-2`, on branch `hive/claude-2`, starting from `origin/main`.
   Use `--worktree PATH` to choose the location.
   - **Re-joining:** if a branch `hive/claude-2` already exists with commits that aren't in
     `main`, `hive join` refuses rather than resetting it. Integrate those commits or delete
     the branch first.
3. **Installs a git `pre-commit` hook** in the clone; worktrees share it.
   - **Your own commits:** the hook exits immediately outside agent sessions, so they never
     need `hive` on PATH.
   - **Agent sessions:** it refuses commits of files the agent hasn't leased.
   - **Existing hooks:** a foreign `pre-commit` hook is never overwritten; Hive tells you
     which line to add.
4. **Creates the slot's private CLI config folder**, by default `~/.hive/accounts/claude-2`
   (`--config-dir` to change it).
5. **Saves the slot** to `~/.hive/machine.toml`.

| `hive join` option | Meaning |
|---|---|
| `--repo PATH` | Local clone; the worktree is created next to it. |
| `--worktree PATH` | Explicit worktree path (use with or instead of `--repo`). |
| `--config-dir PATH` | The account's CLI config folder (default `~/.hive/accounts/<name>`). |
| `--permission-mode MODE` | Claude: `acceptEdits` (default for workers), `bypassPermissions`, …; Gemini: `auto_edit` (default), `yolo`; Codex: `workspace-write` (default for workers), `read-only`, `danger-full-access`, `yolo`. See §23. |
| `--model MODEL` | The model the slot's CLI uses, e.g. `--model gpt-6-astra` for a Codex slot. Default: the account's default model. |
| `--server URL` | Server URL, saved for the whole machine. |
| `--hive-cmd CMD` | How the git hook calls `hive` (default `hive`). |

### 6.3 Log each slot's account in once

Each slot uses its own config folder, so log the matching account into it once, interactively:

**bash / zsh (macOS, Linux, Git Bash):**
```sh
cd /path/to/clone-claude-2
CLAUDE_CONFIG_DIR=~/.hive/accounts/claude-2 claude      # log in, then exit
cd /path/to/clone-gemini-1
GEMINI_CLI_HOME=~/.hive/accounts/gemini-1 gemini        # log in, then exit
cd /path/to/clone-codex-1
CODEX_HOME=~/.hive/accounts/codex-1 codex login         # add --device-auth over SSH
```

**PowerShell (Windows):**
```powershell
cd C:\path\to\clone-claude-2
$env:CLAUDE_CONFIG_DIR = "$HOME\.hive\accounts\claude-2"; claude; Remove-Item Env:CLAUDE_CONFIG_DIR
cd C:\path\to\clone-gemini-1
$env:GEMINI_CLI_HOME = "$HOME\.hive\accounts\gemini-1"; gemini; Remove-Item Env:GEMINI_CLI_HOME
cd C:\path\to\clone-codex-1
$env:CODEX_HOME = "$HOME\.hive\accounts\codex-1"; codex login; Remove-Item Env:CODEX_HOME
```

**cmd.exe (Windows):** `set "CLAUDE_CONFIG_DIR=%USERPROFILE%\.hive\accounts\claude-2" && claude`.
Close the window afterwards, so the variable doesn't leak into other commands.

`hive join` prints these commands for your platform with the real paths filled in.

Use a different account per slot. That is the point of separate folders.

---

## 7. Running the fleet (`hive up`)

Run `hive up` on every machine that hosts slots. What it does depends on which slots live
there.

### 7.1 The machine with the orchestrator (your computer)

```sh
hive up
```

1. **Daemon:**
   - It first fetches every slot's identity and roles from the server, retrying for up to
     30 s. If the server can't be reached, `hive up` stops with a clear message instead of
     guessing.
   - It then starts in the background. Its log goes to `~/.hive/logs/daemon.log`, not the
     terminal.
2. **CLI launch:** opens the orchestrator's **native CLI in your terminal** with Hive attached
   for this session:
   - Claude: `claude --plugin-dir <worktree>/.hive/agent/claude --settings <allow hive tools>
     --session-id|--resume <id> "<start prompt>"`
   - Gemini: `gemini -e hive --session-id|--resume <id> -i "<start prompt>"`
   - Codex: `codex [resume <id>] -p hive --dangerously-bypass-hook-trust "<start prompt>"`
3. **Session:** the first launch creates a new session. Later launches **resume the same
   conversation**. (Codex picks its own session ids; Hive reads the id back from the slot's
   `CODEX_HOME/sessions/` when the session ends.)
4. **Start prompt:** tells the agent to use the `hive-orchestrator` skill. It then:
   1. calls `whoami`;
   2. reads its memory (`.hive/orchestrator/state.md`);
   3. acts on the fleet state;
   4. waits in `await_events`.
5. **On exit:** when you quit the CLI, `hive up` asks *"Relaunch? [Y/n]"*. Answering `n`, or
   pressing Ctrl+C, stops everything on this machine.

**Other slots on the same machine:** they keep running headless in the background. Their
output isn't printed here (the terminal belongs to the CLI); watch them with `hive watch`
(§9).

**Headless orchestrator:** `hive up --headless` runs the orchestrator like a worker: the
daemon starts a headless turn per event, plus a check-in every `heartbeat_hours`. Use it only
if you don't want an interactive session.

### 7.2 Worker machines

```sh
hive up            # -v for debug logging
```

1. The daemon starts and prints each agent's formatted live output, prefixed with its name:
   ```
   [claude-2] · wake 20261019-213012
   [claude-2] · why: Task #4 was assigned to you: Add login endpoint
   [claude-2]   I'll lease the auth module first.
   [claude-2] → mcp__plugin_hive_hive__lease_acquire {"globs": ["src/auth/**"], "task_id": 4}
   [claude-2] ← {"id": 7, "holder": "claude-2", "fencing": 12, …}
   [claude-2] → Edit {"file_path": "src/auth/login.py", …}
   [claude-2] · turn done (14 turns)
   ```
2. The same lines are uploaded to the server about once a second for `hive watch` and the
   dashboard.
3. Ctrl+C stops the daemon.

### 7.3 How an agent is launched for a headless turn ("wake")

When something concerns a worker, its daemon starts one headless turn:

| CLI | Command (prompt goes on stdin) |
|---|---|
| Claude | `claude -p --output-format stream-json --verbose --plugin-dir <bundle> --settings <allow hive tools> --permission-mode <mode> --session-id/--resume <id>` |
| Gemini | `gemini -p "Follow the Hive wake notice…" -o stream-json -e hive --approval-mode <mode> --session-id/--resume <id>` |
| Codex | `codex exec --json -p hive --dangerously-bypass-hook-trust -c sandbox_mode="<mode>" -c approval_policy="never" [resume <id>] -` |

**Environment for every launch:**

| Variable | Value |
|---|---|
| `HIVE_SLOT` | the slot name |
| `HIVE_DAEMON_URL` | the daemon's local API |
| `HIVE_LOCAL_SECRET` | the local API secret |
| `MCP_TOOL_TIMEOUT` | 30 minutes |
| `CLAUDE_CONFIG_DIR` | the slot's config folder (Claude slots) |
| `GEMINI_CLI_HOME` | the slot's config folder (Gemini slots) |
| `CODEX_HOME` | the slot's config folder (Codex slots) |

A slot's `--model` is passed as `--model` (Claude) or `-m` (Gemini, Codex).

**Gemini extension:** before a Gemini slot's first launch, `hive` runs
`gemini extensions link <worktree>/.hive/agent/gemini --consent` **inside the slot's private
`GEMINI_CLI_HOME`**. Your personal `~/.gemini` is never touched.

**Codex profile:** before every Codex launch, `hive` copies into the slot's **private**
`CODEX_HOME` (never `~/.codex`):
- `.hive/agent/codex/hive.config.toml` as `hive.config.toml`, selected with `-p hive`. It
  adds the `hive` MCP server (forwarding `HIVE_SLOT`, `HIVE_DAEMON_URL`, `HIVE_LOCAL_SECRET`
  and `HIVE_HOME`, with Hive's tools pre-approved) and the hooks;
- the rules for the slot's role as `rules/hive.rules`;
- the skills to `skills/hive-orchestrator/` and `skills/hive-worker/`.

The slot's own `config.toml` and login are left alone. If the repository was initialised by
an older Hive and has no `.hive/agent/codex/` yet, the files are generated on the fly.
The Hive MCP server is required: Codex waits for it before the first turn and fails startup
if it cannot initialize, so an agent cannot begin work before its Hive tools are ready.

Codex asks you to review new hooks before they run. Hive generates its hooks itself, and the
slot's private `CODEX_HOME` holds no others, so it launches Codex with
`--dangerously-bypass-hook-trust`. Codex reports this as a warning at the start of each turn;
`hive watch` hides that line.

Codex chooses its own session ids. The daemon reads the id from the `thread.started` event of
the first turn and resumes it with `codex exec resume <id>` afterwards.

**The wake prompt contains:**
- who the agent is and its roles;
- the fleet state;
- why it was woken;
- its current tasks with spec, path hints and notes;
- new messages;
- on the first turn of a session, its role instructions and protocol reminders.

**Wake rules:**
- **One turn at a time per slot.** Events that arrive during a turn reach the agent through
  hooks, or trigger a follow-up turn.
- **Sessions persist** across turns. A new session starts every 40 turns, or after a failed
  resume.
- **Wakes are rate-limited**: at most 20 per slot per hour, at least 10 seconds apart.
- **Usage limits:** if the CLI fails with a usage-limit message, the slot pauses for an hour.
  The server is told, so the scheduler skips the agent and the orchestrator is notified.
- **Turn timeout:** a turn longer than one hour is killed.

### 7.4 Taking over a slot interactively (`hive chat`)

```sh
hive chat claude-2          # resume its session in your terminal
hive chat claude-2 --fresh  # start a new session
```

- The daemon stops headless turns for that slot while you're attached and resumes them when
  you exit.
- If a headless turn is running, it finishes first.
- Hive's plugin or extension is attached exactly as for `hive up`.

---

## 8. Working with the orchestrator

The orchestrator is the only agent you normally talk to.

### 8.1 Chatting in its terminal

The terminal where `hive up` launched the orchestrator **is** the chat.
- **When the orchestrator is working or replying,** just type, as in any session.
- **When it's waiting in `await_events`** (a long-running tool call), press **Esc** to cancel
  the wait, then type. If you type without Esc, the message is most likely queued until the
  wait returns, which can take up to 10 minutes.
- **After replying,** it goes back to `await_events` on its own, as its skill instructs.

### 8.2 Chatting from anywhere else

- **Dashboard:** the "Talk to the orchestrator" box (§18).
- **CLI:** `hive say "Prioritise the API before the UI"` from any machine logged in as admin.

Both post to the `#user` channel. That **ends the orchestrator's wait within seconds**, and
your message is in the digest it receives.

**Caveat:** its replies may appear only in its terminal. The skill doesn't yet tell it to
answer `#user` messages in `#user` (§25).

### 8.3 Answering decisions

The orchestrator can **ask** you things but can never **answer** for you. Ways to answer:

| Where | How |
|---|---|
| Orchestrator's terminal | `! hive decide 3 approve` (the `!` prefix runs a shell command in Claude Code and Gemini CLI) |
| Any admin terminal | `hive decisions` to read; `hive decide 3 approve --note "keep it small"` to answer |
| Dashboard | the *Decisions needed* panel: option buttons plus a note field |

### 8.4 How it keeps you informed

- After handling a batch of events it **publishes a status update**: a one-line summary of
  what changed since the last update, details in the body, and a level of `fyi`,
  `decision-needed` or `alert`.
- Status updates appear in `hive status`, `hive status -v` (with bodies), and the dashboard.
- It keeps its memory in `.hive/orchestrator/state.md` in its worktree (git-ignored), so it
  can resume after restarts and new sessions.

### 8.5 What the orchestrator can and can't do

**Can:**
- create, assign, reassign and cancel tasks;
- message anyone, including `interrupt` priority;
- raise decisions;
- request reports and read every report;
- revoke stale leases;
- propose role changes (changes to mandatory roles need your approval);
- pause and resume the fleet;
- read the repository and run read-only commands;
- review work when there is no reviewer.

**Can't:**
- edit source files (only `.hive/**` is writable for it);
- take leases or commit;
- answer decisions;
- see agents' live output.

---

## 9. Watching the workers

Workers' live output is **visible to you only**. The server refuses it to every agent token,
including the orchestrator's.

```sh
hive watch                 # all agents, follows new lines (Ctrl+C to stop)
hive watch claude-2        # one agent
hive watch -n 1000         # start with the last 1000 lines (default 200)
hive watch --no-follow     # print and exit
```

- **Dashboard:** the *Live output* pane, with an agent selector; it updates every 1.5 s.
- **Line format:** each line starts with the agent's name in its own colour, then a `│`
  separator, then:

  | Line | Colour | Meaning |
  |---|---|---|
  | `  text` | default | the agent's prose |
  | `→ tool {input}` | blue | a tool call |
  | `← result` | gray | a tool result |
  | `× error` | red | a failed tool call or an error |
  | `· …` | magenta, italic | session, wake and turn markers |

- **Retention:** the server keeps the latest 20,000 lines per agent.
- **Raw output:** every turn's complete unformatted output is also appended to
  `~/.hive/logs/<slot>.log` on the worker's machine.

---

## 10. The fleet lifecycle

```
 setup ──hive start──▶ planning ──orchestrator asks approval──▶ awaiting_approval
                          ▲                                           │ approve
                          └──────────── reject (+ your note) ─────────┤
                                                                      ▼
          frozen ◀── "stuck" decision ── running ── hive pause ──▶ paused
            │                              ▲   ▲                      │
            └──── you answer ──────────────┘   └──── hive resume ─────┘
```

### 10.1 Starting

```sh
hive start "Add GitHub OAuth login with tests"
```

**Startup checks.** The fleet won't start unless:
- exactly 1 orchestrator and at least 1 programmer are enrolled;
- every role is within its min/max;
- every task kind has a handler (§11).

`hive start` prints any problems.

**What happens:**
1. The fleet enters **planning**.
2. Your goal is posted to `#user`.
3. The orchestrator is woken.

### 10.2 Planning and approval

In `planning`:
- Only the orchestrator and planners may act.
- The orchestrator studies the repository and creates tasks, which stay **drafts**. It may ask
  you clarifying questions.
- It then raises a **start approval** decision with the plan as context. The fleet enters
  **awaiting_approval**.

Your answer:
- **`hive decide <id> approve`:** all drafts become open, the fleet enters **running**, and the
  scheduler assigns tasks immediately.
- **Anything else (e.g. `reject --note "…"`):** the fleet goes back to **planning**. The
  orchestrator gets your note and revises.

`approve`, `approved`, `yes`, `y` and `ok` all count as approval.

### 10.3 Stuck agents and freezing

1. An agent calls `report_stuck`. Its task becomes `blocked` and the orchestrator is woken.
2. If the orchestrator can solve it (clarify, reassign, split), it does.
3. If only you can, it raises a **stuck decision**, and **the fleet freezes**:
   - **Other agents:** their tool calls are denied with an explanation and they end their
     turns. No new wakes start; only reading is allowed.
   - **Server:** refuses new leases, task claims and integrations from frozen roles.
   - **Lease timers:** paused. When the fleet thaws, every active lease is extended by the
     frozen duration.
4. You answer with `hive decide <id> "<your answer>" --note "…"`, or the dashboard. Then:
   1. the fleet returns to **running**;
   2. the stuck task goes back to `in_progress`;
   3. agents that had open tasks are woken with your answer.

Only one freezing decision is open at a time; others queue and open in order. Non-freezing
questions (`kind="question"`) never freeze anything.

### 10.4 Pausing, resuming, resetting

| Command | Effect |
|---|---|
| `hive pause` | Stops all agents, including the orchestrator. Lease timers pause. |
| `hive resume` | Returns to the previous state. |
| `hive reset` | Emergency: back to `setup`. Keeps agents, tasks and history; clears any blocking decision. |
| Dashboard ⋯ → *Archives…* → *Archive & clear…* | Ends a run: saves its history to an archive, clears it, and returns to `setup` for a new goal (§18). |

### 10.5 What each role may do in each state

| State | Orchestrator | Planner | Other roles |
|---|---|---|---|
| setup | – | – | – |
| planning | active | active | wait |
| awaiting_approval | active (no new work) | wait | wait |
| running | active | active | active |
| frozen | active (communication only) | wait | wait |
| paused | wait | wait | wait |

---

## 11. Roles

### 11.1 Built-in roles

| Role | Required | Holders | May edit | Takes task kinds | If nobody holds it |
|---|---|---|---|---|---|
| **orchestrator** | yes | exactly 1 | only `.hive/**` | (fallback for plan, review) | – |
| **programmer** | yes | ≥ 1 | leased paths | code, test, docs | – |
| planner | no | any | `.hive/plans/**` | plan | orchestrator takes plan tasks |
| reviewer | no | any | nothing | review | orchestrator reviews |
| tester | no | any | leased paths plus `tests/**` | test | programmers take test tasks |

- An agent may hold several roles, e.g. `reviewer,planner`.
- `orchestrator` can't be combined with any role that codes.

### 11.2 Custom roles and overrides

Define roles in `hive.toml` under `[roles.<name>]`, then `hive admin push-config`.

| Field | Type | Meaning |
|---|---|---|
| `mandatory` | bool | The fleet won't start without a holder. |
| `min`, `max` | int | Allowed number of holders. |
| `can_code` | bool | May take leases and edit leased paths. |
| `write_globs` | [glob] | Paths editable without a lease. |
| `tools` | [name] or `"*"` | Extra MCP tools on top of the base set (§17.1). |
| `task_kinds` | [kind] | Task kinds this role accepts. |
| `fallback_to` | role | Who covers this role's task kinds when nobody holds it. |
| `wake_on` | [event] | Events that wake an idle holder. |
| `active_in` | [state] | Fleet states in which holders may work (default `["running"]`). |
| `prompt` | string | Role instructions given to the agent. |

Example:

```toml
[roles.docs-writer]
can_code = true
task_kinds = ["docs"]
tools = ["lease_acquire", "lease_extend", "lease_release"]
prompt = "You write and maintain user documentation under docs/."
```

Built-in roles can be overridden the same way, e.g. `[roles.reviewer] prompt = "…"`. A
`prompt` given here **replaces** the role's built-in prompt. To **add** to it instead, or to
write longer instructions than fit in `hive.toml`, use `.hive/prompts/roles/<role>.md`
(§17.5).

### 11.3 Changing roles

- **You:** `hive admin roles claude-3 reviewer`.
- **The orchestrator:** `role_reassign`. Changes to optional roles apply immediately. Changes
  that add or remove a mandatory role become a (non-freezing) decision for you.
- **Standby orchestrator** (`[orchestrator] standby = ["claude-2"]`):
  - **When:** the current orchestrator is out of quota, or hasn't been seen for 5 minutes.
  - **What happens:** the first available standby becomes orchestrator; its coding roles move
    to the old orchestrator, and its open tasks are released for reassignment.
  - **You're told:** an `alert` status entry is published.

### 11.4 How roles are enforced

Enforcement has three layers:
1. **Server:** every action is checked against the caller's roles. Forbidden calls fail with
   403. Actions not allowed in the current fleet state fail with 423.
2. **Hooks:** before every tool call, writes outside the agent's leases and `write_globs` are
   denied. While the agent's roles are inactive, only read-only tools are allowed.
3. **Instructions:** the role prompt and skill explain the rules, so agents rarely hit the
   other two.

---

## 12. Tasks

**States:**
```
draft → open → claimed → in_progress → (review) → done
                                     ↘ blocked / failed / cancelled
```

- **Fields:** title, spec, kind (`plan`, `code`, `test`, `docs`, `review`, or custom),
  priority, deps (task ids), path hints (globs), assignee, branch, notes (an append-only log),
  result.
- **Drafts:** tasks created before approval are drafts, except `plan` tasks.
- **Scheduling:** runs on every relevant change and assigns each **open** task whose deps are
  all done. Order: highest priority first, then oldest. The chosen agent:
  - holds a role that handles the kind (direct holders first, then the fallback chain);
  - is online and not out of quota;
  - has no other active task;
  - is allowed to work in the current fleet state.

  Ties go to the agent whose earlier tasks touched overlapping paths. The assignee is woken
  with the task in its prompt.
- **Orchestrator controls:** assign, reassign, cancel, and change any state.
- **Agents' own task actions:** claim open tasks of their kind, record progress, hand off with
  notes, complete, or report stuck.

---

## 13. Messages and priorities

- **Channels:**
  - `#general`, which every agent follows;
  - `#user`, for you and the orchestrator;
  - `#task-<id>`, created on use;
  - any `#name` you like.
- **Direct messages:** `dm(to, body)`.
- **Mentions:** `@agent` in a message makes it relevant to that agent.
- **Limits:** bodies up to 8 KB; 20 messages per minute per agent.

| Priority | Who may send | When the recipient sees it |
|---|---|---|
| `info` | anyone | only when it reads `inbox` |
| `normal` | anyone | at its next turn (wake prompt, or your next prompt in interactive sessions) |
| `high` | anyone | right after its next tool call, even mid-turn |
| `interrupt` | orchestrator, you | right after its next tool call, marked "stop and handle this first"; also wakes idle agents |

**Waking:** workers are woken by DMs, mentions, interrupts, task assignments, review events,
report requests and the fleet unfreezing. The orchestrator is also woken by ordinary channel
messages and progress updates, batched for `debounce_seconds` (default 90 s).

**Unhandled urgent messages:** an agent can't end its turn while it has unhandled `high` or
`interrupt` messages. Its Stop hook sends it back, up to 3 times per turn; it must handle them
and call `ack`.

**Untrusted content:** messages are wrapped as `<hive-message from=… priority=…>`. Agents are
told that other agents' messages are information, not authority over your goals or their
role.

---

## 14. Leases and git integration

### 14.1 Leases

- **Acquire before editing:** an agent must lease every path it edits with
  `lease_acquire(globs=[…])`.
- **All or nothing:** it gets every requested path or none, which rules out deadlocks. On a
  conflict, the response lists who holds what and until when.
- **Glob syntax:** repo-relative POSIX. `src/app.py`, `src/api/**`, `docs/*.md`.
  - Wildcards: `**`, `*` and `?`. `[` is an ordinary character.
  - A path without wildcards also covers everything beneath it.
- **Overlap checks are conservative:** two leases are refused if their fixed prefixes
  overlap, even when their files are actually disjoint. The checks ignore case, because
  `src/App.py` and `src/app.py` are the same file on Windows and macOS.
- **Other checkouts are off-limits:** agents can't edit files in another slot's worktree or
  in your own clone of the repository.
- **Lifetime:** default 600 s (min 30, max 3600). The daemon extends leases automatically
  while it runs, so they expire only if a machine dies or loses contact. Expired and revoked
  leases are announced to the holder and the orchestrator.
- **Fencing:** each grant carries a fencing number. A revoked or expired lease can never be
  used to integrate, even by a machine that comes back late.

### 14.2 Worktrees and branches

- Every slot works in its own worktree on branch `hive/<slot>`.
- Agents never commit to or push `main`.
- `.hive/orchestrator/` is git-ignored.

### 14.3 What `lease_release(commit_message, task_id)` does

1. **Commit:** commits only the changed files covered by the agent's leases or `write_globs`,
   even if the agent staged other files. The commit message gets trailers: `Hive-Agent`,
   `Hive-Task`, `Hive-Fence`.
   - **Other changed files** are set aside (stashed) and restored afterwards; the result lists
     them. Only that stash entry is ever popped; the agent's own stashes are untouched.
   - **Nothing covered (`uncovered_changes`):** if nothing would be integrated but there *are*
     changed files outside the leases, the release stops and **keeps the leases**. The agent
     must lease those files (or revert them) and release again.
   - **Earlier commits (`uncovered_commits`):** if commits already on the agent's branch touch
     files its leases don't cover, the release stops the same way.
2. **Rebase:** `git fetch` and rebase onto `origin/main`. On a conflict:
   - the rebase is aborted;
   - the commit and leases are kept;
   - the orchestrator is told;
   - the agent must merge and call `lease_release` again.
3. **Checks:** runs every command in `[integration] checks`. On failure, the leases are kept
   and the output tail is returned to the agent.
   - **Which shell:** by default checks run in the machine's native shell, cmd.exe on
     Windows and `/bin/sh` elsewhere.
   - **Mixed fleets:** one `hive.toml` serves every machine, so write portable commands
     (`python -m pytest -q`, `npm test`), or set `[integration] shell` to `bash` (Git Bash on
     Windows) or `powershell` so every machine uses the same shell.
   - **Time limit:** each check may run for up to 30 minutes.
4. **Push:** pushes the agent's branch `hive/<slot>`.
5. **Integrate:**
   - **Local mode** (default):
     1. The server grants a **one-time integration grant**, bound to this agent, this commit,
        and its leases with their fencing numbers.
     2. The daemon pushes `HEAD:main` with a plain, non-forced push, which fails if `main`
        moved. In that case it re-fetches and retries from step 2, up to 3 times.
     3. It then confirms to the server with the grant. Without a valid grant, nobody can
        release leases or move the recorded `main`.

     **If the server can't be reached for the confirmation,** the daemon retries. The result
     is `integrated_unconfirmed`, and the daemon keeps resending the confirmation in the
     background until it gets through.
   - **GitHub mode** (§14.4): the server fast-forwards `main` through the GitHub API.
6. **Done:**
   - the leases are released;
   - the server records the new `main` and announces it;
   - every other slot whose worktree is clean rebases onto it immediately;
   - slots with uncommitted work get a notice and rebase at their next release.

With review enabled, step 5 becomes "submit for review" instead (§15). The `task_id` is then
required, and it must be the agent's own task.

`lease_release(abandon=true)` frees the leases without integrating anything.

### 14.4 GitHub mode (optional)

To let the server, rather than the machines, move `main`:
1. Set `[repo] github = "owner/repo"` in `hive.toml`.
2. Give the server `HIVE_GITHUB_TOKEN`, a fine-grained token with *Contents: write* on that
   repo.

The server then advances `main` with a fast-forward-only ref update, so `main` is never
force-moved. Combine this with branch protection so only that token can push `main`, and give
the machines push rights for `hive/*` branches only.

This mode is implemented but not yet tested against GitHub (§25).

---

## 15. Reviews

Set `[integration] review` in `hive.toml`:

| Value | Behaviour |
|---|---|
| `off` (default) | Work integrates as soon as checks pass. |
| `orchestrator` | The orchestrator reviews every integration. |
| `reviewer` | An agent with the reviewer role reviews. If no reviewer other than the author exists, the orchestrator does. |

**Flow with review on:**
1. The programmer's `lease_release` pushes its branch and the task enters **review**. The
   programmer **keeps its leases**. The `task_id` must be passed and must be the
   programmer's own task, so review can't be skipped.
2. The reviewer is woken with the commit to inspect (`git diff origin/main...<sha>`) and calls
   `review_verdict(task_id, approve, notes)`. Nobody can review their own task.
3. **Approve:** the programmer's daemon re-runs the release automatically (rebase, checks,
   integrate) and the task becomes **done**.
   - **If the programmer is mid-turn,** the daemon waits for the turn to end first, so
     half-finished edits are never committed.
   - **The approval is single-use:** it only counts for that review.
4. **Changes requested:** the task returns to `in_progress`, and the programmer is woken with
   the notes.

---

## 16. Reports

Reports are how the orchestrator learns what happened without reading anyone's raw output. Raw
output would bloat its context and could expose secrets.

- **Unprompted:** workers file `report_submit(summary, body, task_id)` when they finish
  meaningful work. Their skill tells them to include files, commits, how the work was
  verified, and anything left open, and never to include secrets.
- **On request:** the orchestrator calls `report_request(agent, about, task_id)`. The agent is
  woken and answers with `report_submit(request_id=…)`.
- **Delivery:** every new report wakes the orchestrator.
- **Verification:** its skill tells it to check claims against the repository (`git log`,
  reading the files, running read-only tests) and to keep only a summary in its notes.
- **Reading reports:** `report_list` and `report_get`, for any agent. Reports are also in the
  server's `/api/v1/reports`.

---

## 17. What the agents see: MCP tools, hooks, skills

### 17.1 MCP tools (`hive` server)

Every agent has the **base set**:

| Tool | Purpose |
|---|---|
| `whoami` | name, roles, permitted tools, write permissions, fleet state |
| `roster` | all agents: roles, online/busy, quota |
| `post(body, channel, priority, thread_id)` / `dm(to, body, priority)` | send messages |
| `inbox(limit, include_other_channels)` / `ack(ids)` | read messages / acknowledge urgent ones |
| `task_list`, `task_get`, `task_claim`, `task_update`, `task_handoff`, `task_complete` | work on tasks |
| `report_stuck(details, task_id)` | ask for help (may lead to a freeze) |
| `report_submit`, `report_list`, `report_get` | reports |
| `request_approval(command, reason, shell, task_id, wait_seconds)`, `run_approved(id)`, `approval_list` | run a dangerous command with the orchestrator's approval (§17.4) |
| `lease_status`, `repo_state`, `fleet_state`, `status_read` | look around |

**Coding roles** add: `lease_acquire`, `lease_extend`, `lease_release`.

**Orchestrator** adds:
- `await_events(timeout_seconds)`;
- `task_create`, `task_assign`, `task_cancel`;
- `decision_request(kind, question, options, context, task_id, freeze)`;
- `status_publish(summary, body, level)`;
- `report_request`;
- `approval_decide(approval_id, approve, note)`;
- `review_verdict`;
- `lease_force_release`;
- `role_reassign`;
- `fleet_control(pause|resume)`.

**Reviewer** adds `review_verdict`; **planner** adds `task_create`.

Only permitted tools are registered in an agent's session. In Claude Code the tools appear as
`mcp__plugin_hive_hive__<tool>`, and they're pre-approved for the session, so no permission
prompts.

**`await_events`** blocks inside the daemon, without using tokens, until:
- an **urgent event** arrives (returns within about 3 s);
- **batched events** (progress, ordinary messages) have waited `debounce_seconds`; or
- the timeout passes (default 600 s, max 1800 s).

It returns a digest: fleet state, a state-specific hint, the events, and unread messages.

### 17.2 Hooks

The hooks ship inside the bundles and call `hive hook <event> --vendor <cli>`. Codex uses
Claude Code's event names and output format.

| Claude / Codex event | Gemini event | What Hive does |
|---|---|---|
| PreToolUse | BeforeTool | Denies writes not covered by leases or `write_globs`, and writes into other slots' worktrees. For Codex's `apply_patch`, every file the patch adds, updates, deletes or moves is checked. While the fleet state doesn't allow the agent's roles to act, denies everything except reading and Hive tools. |
| PostToolUse | AfterTool | Injects new `high` and `interrupt` messages mid-turn. |
| UserPromptSubmit | BeforeAgent | Injects unread messages and, in interactive sessions, events queued since the last check. |
| Stop | AfterAgent | Sends the agent back (max 3 times per turn) if it has unhandled urgent messages, uncommitted changes under its leases, or, in the interactive orchestrator, urgent events waiting. |
| SessionStart | SessionStart | Tells the agent who it is, its roles, the fleet state, and which skill to follow. |

**Failure handling:**
- **Write checks fail closed:** if the daemon is unreachable, file writes are refused.
- **Message delivery fails open:** the agent continues normally.
- **Outside Hive sessions** (no `HIVE_SLOT`), every hook does nothing.
- **Git backstop:** shell commands can't be inspected reliably, so the git `pre-commit` hook
  re-checks staged files against leases for agent commits.

### 17.3 Skills

- **`hive-orchestrator`**: the operating procedure:
  - session start-up;
  - the `await_events` loop;
  - what to do in each fleet state;
  - when to freeze the fleet;
  - working from reports instead of streams;
  - status updates and memory;
  - hard rules (no code, never answer decisions, no secrets).
- **`hive-worker`**: the task procedure:
  - read, lease, edit, test, release, complete, report;
  - report requests;
  - messages and acknowledgements;
  - being stuck or frozen;
  - reviewing.

Both are plain Markdown in `.hive/agent/*/skills/`. Don't edit them there: `hive init`
rewrites those folders. Add your own rules in `.hive/prompts/` instead (§17.5).

### 17.4 Shell-command permissions

Headless agents can't answer "may I run this command?" prompts; an unapproved command is
simply refused. So `hive init` pre-approves the commands agents routinely need, **per
session**:

| CLI | Generated file | Attached with |
|---|---|---|
| Claude Code | `.hive/agent/claude/settings/{orchestrator,worker}.json` | `--settings` |
| Gemini CLI | `.hive/agent/gemini/policies/{orchestrator,worker}.toml` | `--policy` |
| Codex | `.hive/agent/codex/rules/{orchestrator,worker}.rules` (execpolicy `prefix_rule`s) | copied to the slot's `CODEX_HOME/rules/hive.rules` |

- **Which file:** the orchestrator gets the `orchestrator` set; every other role gets the
  `worker` set.
- **Scope:** nothing is added to your own CLI settings, so your normal sessions are unaffected.
- **Hive's own tools:** always allowed.

Commands fall into three tiers. The aim is to need as little human intervention as possible
while staying safe.

**1. Allowed: run directly.** With the `standard` preset:

| Group | Examples | Orchestrator | Workers |
|---|---|---|---|
| Reading | `git status/diff/log/show/blame/fetch/reflog`, `ls`, `cat`, `grep`, `rg`, `find`, `tree`, PowerShell `Get-ChildItem`/`Get-Content`/`Select-String` | ✓ | ✓ |
| Tests and checks | `pytest`, `python -m pytest`, `npm test`, `cargo test`, `go test`, `ruff check`, `mypy`, `tsc --noEmit`, plus every command in `[integration] checks` | ✓ | ✓ |
| Git history work | `git add`, `commit`, `merge`, `rebase`, `cherry-pick`, `revert`, `checkout`, `switch`, `restore`, `stash`, `tag`, `mv`, `rm`, `am`, `apply` | – | ✓ |
| Building and running | `python`, `node`, `uv run`, `npm run`, `npm install`, `npx`, `make`, `cargo build`, `go build`, formatters and linters, `mkdir`, `touch`, `cp`, `mv`, plain `rm`, `Copy-Item`/`Move-Item`/`New-Item`/`Remove-Item` | – | ✓ |

**2. Needs approval: dangerous commands.** These publish, discard or rewrite history, or delete
recursively:

| Group | Commands |
|---|---|
| Git | `git push`, `git reset --hard`, `git clean`, `git checkout --`/`-f`, `git restore --source`, `git branch -D`, `git update-ref`, `git filter-branch`/`filter-repo`, `git reflog expire`, `git gc --prune`, `git stash drop`/`clear`, `git rebase --root` |
| Recursive or forced deletes | POSIX `rm -r`/`-rf`/`-f`/`--recursive`, `find -delete`, `find -exec rm …`, `… \| xargs rm -rf`; cmd.exe `del /s /q /f`, `rd /s`, `rmdir /s`; PowerShell `Remove-Item -Recurse`/`-Force` (any abbreviation, any position) and its aliases `rm`, `del`, `ri`, `rd` |
| Elevation and permissions | `sudo`, `su`, `doas`, `runas`, `gsudo`, `Start-Process -Verb RunAs`; `chmod -R`, `chown -R`, `takeown`, `Set-Acl`, `icacls` with `/grant`, `/deny`, `/t`, … |
| Machine-level | `dd`, `mkfs`, `shred`, `format`, `diskpart`, `Format-Volume`, `Clear-Disk`, `reg delete`, `Remove-ItemProperty`, `shutdown`, `Stop-Computer`, `Restart-Computer`, `Set-ExecutionPolicy` |

**How Hive recognises these in every shell:** it parses each command itself instead of
matching text prefixes only:
- **Compound commands** are split, while separators inside quotes are left alone. So
  `git commit -m "fix; rm -rf x"` is just a commit.
- **Nested shells are unwrapped:** `bash -c`, `cmd /c`, `powershell -Command` and
  `-EncodedCommand`.
- **Program names are normalised:** `C:\…\git.exe` → `git`, `/bin/rm` → `rm`.
- **Dangerous flags are found in any position:** `Remove-Item build -Recurse` is caught just
  like `rm -rf build`.

The CLIs' own permission files hold the common prefix forms as a first layer. Hive's hook is
the authority.

**Codex specifics:**
- Codex workers run in its `workspace-write` sandbox by default: they can write in their
  worktree, and commands without a matching rule run sandboxed, without network access.
  Commands in the *allowed* tier run outside the sandbox, as with the other CLIs. Give a slot
  `--permission-mode danger-full-access` (or `yolo`) if its work needs more.
- Codex hooks can't ask for confirmation, so for the interactive orchestrator a dangerous
  command that only the hook recognises (a flag after a path, for example) is refused with a
  note to ask you; the common prefix forms prompt you through the `.rules` file.

**How approval works for workers:** **the orchestrator approves, not you.**
1. The worker's CLI blocks the command, and the hook tells it to call
   `request_approval(command, reason)`.
2. The orchestrator is woken within seconds (`approval.requested`). It approves or denies with
   a note, following criteria in its skill:
   - the command is needed for the task;
   - it's limited to the agent's own worktree and branch;
   - it's the narrowest command that works.

   It escalates to you with a question only when consequences are unclear or reach outside
   the repository.
3. **The Hive daemon runs the exact approved command once** in the worker's worktree, with a
   10-minute limit, and returns the output to the agent. It uses the shell the command was
   written for:
   - `bash`: Git Bash on Windows;
   - `powershell`: pwsh if installed, otherwise Windows PowerShell;
   - `cmd`: Windows only.

   The hook tells the agent which shell it used. `auto` means PowerShell on Windows and bash
   elsewhere.

**Why the worker usually doesn't need a new turn:** `request_approval` waits for the decision
(up to 10 minutes, using no tokens), so the worker usually gets the result within the same
turn. If no decision arrives, it ends its turn, is woken when the decision lands, and calls
`run_approved(id)`.

**Approval rules:**
- An approval is single-use and expires after an hour.
- The requesting agent can't approve itself.
- Everything is recorded on the server: who asked, why, who decided, the exit code and the
  output.
- **Who sees the output:** only the requesting agent and you. Other agents, including the
  orchestrator, see the request and the decision but not the output, which can contain
  secrets.
- **Git options don't hide a command:** git global options (`git -C dir …`, `git -c k=v …`)
  are looked through, so `git -C . push origin main` is still recognised as a push to `main`.

The **orchestrator's own** dangerous commands prompt *you* in its interactive session, since
there's nobody else to approve them.

**3. Never: no approval possible.**
- **Hive's user commands:** `hive decide`, `admin`, `start`, `say`, `pause`, `resume`,
  `reset`, `init`, `join`, `up`. An agent must never answer your decisions or speak as you,
  even on a machine where you're logged in as admin.
- **Pushing to `main`,** including `--all` and `--mirror`. Integration goes through
  `lease_release`.

These are checked by the hook, by the server when a request is made, and by the daemon again
just before it runs an approved command.

**Anything not covered** by these lists is refused in headless sessions. In an interactive
session (the orchestrator, or `hive chat`), you're asked instead.

**Customize** in `hive.toml`, then re-run `hive init` and commit:

```toml
[permissions]
preset = "standard"     # standard | strict (no build/run tools) | none (only your lists)
allow = ["docker compose ps", "npm run e2e"]
approval = ["npm publish", "terraform apply"]   # extra commands the orchestrator must approve
deny = ["kubectl delete"]                       # extra commands agents may never run
```

- **Prefix format:** entries are command prefixes without wildcards. Hive translates each one
  per CLI: `"git diff"` becomes `Bash(git diff *)` and `PowerShell(git diff *)` for Claude, and
  `commandPrefix = "git diff"` for Gemini.
- **Stricter tier wins:** a command can't be allowed if it's also listed for approval or
  never-allowed, and the built-in dangerous commands always need approval, whatever the
  preset.

**Trust implications:**
- **Interpreters run anything.** `python`, `node`, `npx`, `uv run`, `npm run` and `make` can
  run arbitrary code. That's why `strict` leaves them out.
- **Pattern rules aren't a security boundary.** Both CLIs' docs say so: an agent could reach
  the same program another way, e.g. `sh -c '…'`. The workers' skill forbids working around a
  block. The hard protections are:
  - the admin token, needed for decisions;
  - lease checks on integration;
  - the server's and the daemon's checks on approved commands.

  Keep the admin token off machines that host worker agents.

### 17.5 Customising what agents are told

Hive's built-in instructions stay as they are, and you add your own after them. Use this for
**preferences and conventions**:
- how the orchestrator plans, how finely it splits tasks, and how and when it reports to you;
- testing expectations, commit style, coding conventions;
- how a particular role should work.

Rules that must hold no matter what an agent decides belong in `hive.toml` instead
(`[permissions]`, roles). Hive enforces those in code; prompts only ask.

**The files** live in `.hive/prompts/` in the repository and are committed:

| File | Added to | When it takes effect |
|---|---|---|
| `orchestrator.md` | The end of the orchestrator's skill (`hive-orchestrator`), under **Project rules** | After `hive init`; commit the refreshed `.hive/agent/` |
| `worker.md` | The end of every other agent's skill (`hive-worker`), under **Project rules** | After `hive init`; commit the refreshed `.hive/agent/` |
| `roles/<role>.md` | The start of every session of an agent holding that role, after the role's `prompt` from `hive.toml`. Works for built-in roles (`orchestrator`, `programmer`, `reviewer`, …) and your own. | Once the file is on `main`; each agent reads it from its own checkout when a session starts |

**Starter files.** `hive init` creates each of these files that doesn't exist yet: both skill
files, and a role file for every role, built-in or defined in `hive.toml`. Files that already
exist are never touched, even if you emptied them. A file you delete comes back as a starter
on the next `hive init`; leave it in place, commented out, instead. Each starter is one
`<!-- … -->` comment explaining the file, with examples for that role, its placeholders and
the preview command.

**Comments and empty files.** `<!-- … -->` comments are removed before anything is sent, and
runs of blank lines left behind are collapsed. A file with nothing left after that (only
comments and whitespace) adds **nothing**:
- no "Project rules" heading in the skill, which stays byte-for-byte the built-in one;
- no text at the start of sessions.

Unused starters therefore cost the agents no context. To use one, write below the comment, or
replace it. Example `orchestrator.md`:

```markdown
<!-- (the starter comment can stay; it isn't sent) -->
- Report to me in at most five bullets, and say first whether anything needs my decision.
- Keep tasks to half a day or less; split anything bigger.
- Ask before adding a new dependency.
```

An unclosed `<!--` hides everything after it, as in HTML; `hive init` and `hive prompts`
warn about it. Comments are removed anywhere in the file, including inside code blocks.

**The "Project rules" section** starts with a note telling the agent the rules come from the
project, and that Hive's protocol wins where the two conflict: leases, `lease_release`,
decisions, approvals, never pushing to `main`. Hive enforces those regardless of what any
prompt says.

**Placeholders.** Write `{{name}}` and Hive fills it in:

| Placeholder | Value | Where |
|---|---|---|
| `{{main}}` | Integration branch (`[repo] main`) | all files |
| `{{review}}` | Review policy (`[integration] review`) | all files |
| `{{checks}}` | The integration checks, joined with `; `, or `(none)` | all files |
| `{{project}}` | The repository folder's name | all files |
| `{{slot}}`, `{{roles}}`, `{{vendor}}` | The agent's name, roles and CLI | `roles/<role>.md` only |

Unknown names are left exactly as written, so code samples with braces (`${{ github.ref }}`,
`{key}`) are safe. `hive init` and `hive prompts` warn about placeholders that won't be
filled (placeholders inside comments are ignored), and about role files with real content
whose name isn't a role. A comment-only role file left over from a removed role is ignored
silently.

**Checking the result.** `hive prompts` shows what an agent with the given roles receives:
its skill's project rules, the role prompts from `hive.toml` and your role files, with
comments removed and placeholders filled. Files with only comments are reported as adding
nothing. It also warns when the committed bundles are older than your files.

```sh
hive prompts --role orchestrator                   # --full also prints the built-in skill
hive prompts --role programmer,tester --slot claude-2 --vendor gemini
```

**Agents can't change these files.** A file that tells agents how to behave mustn't be
editable by them:
- Write and edit tools are refused under `.hive/prompts/`, even with a lease covering it and
  even for the orchestrator, which may otherwise write anywhere in `.hive/`.
- `lease_release` never commits files there, and an agent's commit touching them can't be
  integrated (`uncovered_commits`).
- The pre-commit hook refuses them in agents' own commits.

You change them in your own clone and push to `main` like any other change.

**Limits:** the text left after removing comments may be at most 32 KB per file, since every
byte of it is sent to the agent; comments don't count, up to a 1 MB file. `hive init` refuses
a larger file and leaves the existing bundles untouched. Windows line endings and a UTF-8
byte-order mark are handled.

**What stays fixed:** the built-in skill text, the MCP tool descriptions, the messages Hive's
hooks send (refusals, approvals, end-of-turn reminders) and the formats of messages and event
digests. They describe exactly what the code does, so changing them would break the fleet
rather than change its behaviour. `hive prompts --full` shows the built-in text; §17.1–17.4
describe it.

---

## 18. Web dashboard

Open the server's root URL (`https://…/`) and sign in with the admin token. If you tick
*Remember on this device*, the token stays in the browser's local storage until you sign out
(⋯ menu); otherwise it's forgotten when the tab closes.

| Area | Contents |
|---|---|
| Top bar | Fleet state, goal, orchestrator, `main` commit, and a *Live* / *Reconnecting…* indicator. *Pause* or *Resume*, whichever applies. A theme button that cycles light → dark → system. The ⋯ menu has the theme, *Archives…* (below), *Reset fleet…* (asks for confirmation) and *Sign out*. |
| Decisions | Shown across the top while one is open. Each has its context, one button per option, a free-text answer and a note. A queued decision is shown as *Up next*. In `setup`, this area holds the goal box and *Start fleet* instead. |
| Agents | One card per agent: colour, vendor, roles, state (idle, working, interactive, offline, out of quota), last seen and current task. Click a card to show only that agent's output. |
| Leases | Holder, path globs and time left (amber when under 2 minutes). *Revoke* takes the files away from the holder after a confirmation; its next merge of them is refused. |
| Live output | All agents' formatted output in one scrolling panel; see below. |
| Tasks | Filter by *Active*, *Review*, *Finished* or *All* (with counts). Click a row for its spec, notes, result, paths, dependencies, branch and reviewer. |
| Orchestrator | The `#user` conversation. Enter sends; Shift+Enter starts a new line. |
| Updates | The status feed, newest first. Alerts are tinted; long bodies fold behind *Show more*. |
| Approvals | Commands workers asked to run. You can approve (after a confirmation showing the exact command) or deny with a note, just as the orchestrator can. The tab shows how many are waiting. |
| Reports | Report requests and filed reports. |

**Live output:**
- **Agent colours:** every agent name has its own colour, the same one `hive watch` uses in
  the terminal.
- **Filtering:** chips filter by agent (click one to show only it; Ctrl/Shift+click to
  combine several). The filter box (shortcut `/`) narrows to matching lines and marks the matches.
- **Highlighting:** done in the browser, with no extra requests or prompting. It marks tool
  calls, errors, failure and success words, file paths, URLs, commit hashes, `#task`
  references, quoted strings, Hive tool names, and agent names.
- **Toggles:** *Results* hides tool results, *Wrap* switches between wrapping long lines and
  scrolling sideways (inside the panel only), and *Follow* keeps the newest line in view.
  Scrolling up pauses *Follow*, and a *N new lines* button takes you back down.
- **Clear:** the bin button empties the view without deleting the server's history.
- **Limit:** up to 5,000 lines stay in the page.

**Archives (⋯ → *Archives…*):** keep a run's history without letting it pile up in the live view.
- **Save snapshot** writes everything the dashboard shows to an archive file on the server:
  fleet and goal, agents (without their tokens), tasks, decisions, all messages (including
  agent-to-agent ones), updates, reports, command approvals with their output, leases and the
  recorded agent output. Nothing is removed.
- **Archive & clear…** does the same, then removes all of that from the database and puts the
  fleet back in `setup`, ready for a new goal. Agents, their tokens, the uploaded `hive.toml`,
  the orchestrator assignment and `main`'s commit are kept, so machines keep running without
  re-joining. It's refused unless the fleet is paused (or in `setup`) and nothing is in flight:
  no leased files, no waiting or approved commands, no merge into `main` in progress. The
  dialog says which of these is in the way; revoke leases from the Leases panel.
- **Open** shows an archive in the normal layout, read-only: an *Archive* banner with its
  decisions and their answers, the agents, tasks (all of them), the recorded output (with the
  usual filters and search), every message with where it went, updates, approvals and
  reports. *Back to live* returns to the live view. Archives with more than 5,000 output lines
  show the newest 5,000; picking an agent or searching works on all of them.
- **Download** saves the archive as `hive-archive-<id>.json.gz` (gzip-compressed JSON).
  *Open a file…*, or *Open an archive file* on the sign-in page, opens such a file (or a plain
  `.json`) in the browser without signing in; nothing is uploaded.
- **Delete** removes an archive from the server for good.
- **Where the files are:** see `HIVE_ARCHIVE_DIR` in §20.3. Archives are history, not
  backups: they can't be loaded back into the live database.

**Other behaviour:**
- **Theme:** light by default; the choice is remembered in the browser.
- **Small screens:** the layout stacks into one column on phones.
- **Tab title:** shows how many decisions and approvals are waiting, e.g. `(2) Hive · Frozen`.
- **Refresh rate:** the dashboard refreshes every 4 s and live output every 1.5 s. A hidden
  tab slows to 30 s and 10 s, to spare the server. While the server is unreachable, it retries
  with back-off.
- **Typing is safe:** text you're typing in a decision or approval isn't lost when the data
  refreshes.

---

## 19. Command reference

### User / admin

| Command | Description |
|---|---|
| `hive server [--host H] [--port P] [--db URL] [--sqlite-journal wal\|delete\|truncate] [--admin-token T]` | Run the server (development). |
| `hive admin backup [-o FILE]` | Download a consistent copy of the server's SQLite database. |
| `hive admin login --server URL --token T` | Save admin credentials to `~/.hive/admin.toml`. |
| `hive admin enroll NAME --vendor claude\|gemini\|codex --roles r1,r2` | Create an agent; prints its token and a `hive join` line. |
| `hive admin roles NAME r1,r2` | Change an agent's roles. |
| `hive admin push-config` | Upload `hive.toml` from the current repository. |
| `hive init [PATH] [--server URL] [--main BRANCH] [--hive-cmd CMD]` | Prepare a repository (§5). |
| `hive overview [PATH] [--offline]` | Describe this project's Hive setup: repository, configuration, permissions, roles, prompts, this machine's slots, the fleet, and problems with their fixes (§5). |
| `hive prompts [--role R1,R2] [--slot NAME] [--vendor claude\|gemini\|codex] [--full] [--path DIR]` | Show what Hive tells an agent with those roles, including your additions (§17.5). |
| `hive start "GOAL"` | Start the fleet; uploads `hive.toml` if run in the repo. |
| `hive status [--all] [-n N] [-v]` | Fleet state, waiting decision, agents, tasks (`--all` includes finished ones), leases, last N status entries (`-v` with bodies). |
| `hive decisions [--all]` | Open and queued decisions with context (`--all`: also answered ones). |
| `hive decide ID ANSWER [--note TEXT]` | Answer a decision. |
| `hive say "TEXT"` | Message the orchestrator in `#user`. |
| `hive watch [AGENT] [-n N] [--no-follow]` | Follow live output (§9). |
| `hive pause` / `hive resume` / `hive reset` | Fleet control (§10.4). |

### Machine

| Command | Description |
|---|---|
| `hive join NAME --vendor V --token T [--server URL] [--repo PATH] [--worktree PATH] [--config-dir PATH] [--permission-mode MODE] [--model MODEL] [--hive-cmd CMD]` | Add a slot to this machine (§6.2). |
| `hive up [-v] [--headless]` | Run this machine's slots (§7). |
| `hive chat SLOT [--fresh]` | Attach interactively to a slot (§7.4). |

### Used by the agent CLIs (you don't run these)

| Command | Description |
|---|---|
| `hive mcp [--slot S]` | The stdio MCP server. |
| `hive hook EVENT [--vendor V]` | Hook bridge; `hive hook git-pre-commit` for git. |

### Coloured output

Everything Hive prints for a person is coloured and laid out the same way on Windows, macOS
and Linux:

- **Message markers:** `√` done, `!` warning, `×` error, `»` step, `•` info, `→` note.
- **Sections:** `── Title ──` headings and aligned tables.
- **Colour by meaning:** states are coloured by what they mean (green running/online/done,
  yellow waiting or needing you, red frozen/failed/offline). Each agent name keeps its own
  colour everywhere.
- **Agent output:** in `hive up` and `hive watch`, tool calls, results, errors and session
  notes each get their own colour.

Hive's ANSI colours work in Windows Terminal, PowerShell and cmd.exe on Windows 10+, where Hive
switches the console's VT mode on itself, and in any macOS or Linux terminal. If a console font
can't show a symbol, or the output encoding can't represent it, Hive falls back to ASCII.

Colour is used only when the output is a terminal. Pipes, files, git hooks and agent CLIs get
plain text, and the MCP and hook protocols are never coloured. To force colour on or off,
these settings are checked in order and the first one set wins:

1. `hive --color always|never|auto ...` (before the subcommand);
2. `HIVE_COLOR=always|never`;
3. `NO_COLOR=1` (off);
4. `FORCE_COLOR=1` (on).

Git Bash's default terminal (mintty) doesn't present itself as a console to Windows programs,
so set `HIVE_COLOR=always` there. The installers (`install.bat`, `install.sh`) are coloured
too and respect `NO_COLOR`; `install.sh` uses no colour when its output isn't a terminal.

---

## 20. Configuration reference

### 20.1 `hive.toml` (repository, committed; upload with `hive admin push-config`)

```toml
[server]
url = "https://USER.pythonanywhere.com"   # informational; machines use their own --server

[repo]
main = "main"                # integration branch
# github = "owner/repo"      # enables GitHub mode (§14.4)

[integration]
review = "off"               # off | orchestrator | reviewer   (§15)
checks = ["python -m pytest -q"]   # run before every integration; must exit 0
shell = "native"             # native (cmd.exe on Windows, sh elsewhere) | bash | powershell | cmd

[orchestrator]
standby = ["claude-2"]       # failover candidates, in order (§11.3)
debounce_seconds = 90        # batching window for routine events
heartbeat_hours = 4          # headless orchestrator only: periodic check-in

[permissions]                # shell-command tiers for agents (§17.4)
preset = "standard"          # standard | strict | none
allow = ["docker compose ps"]          # run directly
approval = ["npm publish"]             # run once the orchestrator approves
deny = ["kubectl delete"]              # never

[roles.docs-writer]          # custom roles / overrides (§11.2)
can_code = true
task_kinds = ["docs"]
```

### 20.2 `~/.hive/machine.toml` (per machine, private, written by `hive join`)

```toml
server_url = "https://USER.pythonanywhere.com"
local_port = 8766            # daemon's local API (127.0.0.1 only)
local_secret = "…"           # generated; protects the local API
poll_interval = 3.0          # seconds between server polls

[[slots]]
name = "claude-2"
vendor = "claude"
token = "hv_…"
worktree = "/path/to/proj-claude-2"
config_dir = "/home/you/.hive/accounts/claude-2"
permission_mode = ""         # empty = default (claude: acceptEdits, gemini: auto_edit,
                             # codex: workspace-write)
model = ""                   # empty = the account's default model, e.g. "gpt-6-astra"
command = []                 # optional custom headless command, see below
```

**Custom headless command:** use `command` if a CLI's flags differ on your system. Hive
replaces these placeholders and passes the prompt on stdin:

| Placeholder | Replaced with |
|---|---|
| `{session}` | the session id |
| `{session_flag}` | `--session-id` or `--resume` |
| `{mode}` | the permission mode |
| `{bundle}` | the slot's `.hive/agent/<vendor>` folder |

```toml
command = ["gemini", "-p", "Follow the notice on stdin.", "-o", "stream-json", "-e", "hive",
           "--approval-mode", "{mode}", "--resume", "latest"]
```

### 20.3 Environment variables

| Variable | Used by | Meaning |
|---|---|---|
| `HIVE_ADMIN_TOKEN` | server, admin CLI | Admin token (overrides `admin.toml` for the CLI). |
| `HIVE_DATABASE_URL` | server | Database URL. |
| `HIVE_GITHUB_TOKEN` | server | Enables GitHub mode. |
| `HIVE_SQLITE_JOURNAL` | server | SQLite journal mode: `wal` (default) or `delete`/`truncate` for network filesystems; auto-`delete` on PythonAnywhere. |
| `HIVE_ARCHIVE_DIR` | server | Folder for dashboard archives (§18). Default: `hive-archives/` next to a SQLite database file, otherwise `~/hive-archives`. |
| `HIVE_SERVER_URL` | admin CLI | Overrides the server URL in `admin.toml`. |
| `HIVE_HOME` | all local commands | Location of Hive's local files (default `~/.hive`). |
| `HIVE_COLOR`, `NO_COLOR`, `FORCE_COLOR` | all commands | Colour on/off (§19, "Coloured output"). |
| `HIVE_SLOT`, `HIVE_DAEMON_URL`, `HIVE_LOCAL_SECRET` | set by Hive for agent CLIs | Identify the slot and reach the daemon. |
| `CLAUDE_CONFIG_DIR` / `GEMINI_CLI_HOME` / `CODEX_HOME` | set by Hive per slot | Keep each account's CLI config separate. |
| `MCP_TOOL_TIMEOUT` | set by Hive | Lets `await_events` block for up to 30 minutes. |

---

## 21. Files and directories

**On each machine (`~/.hive/`, or `$HIVE_HOME`):**

| Path | Contents |
|---|---|
| `machine.toml` | server URL, local API port and secret, slots with tokens (keep private) |
| `admin.toml` | admin server URL and token (only where you ran `hive admin login`) |
| `state.db` | daemon state: event cursors, inbox cache, pending wake reasons, session ids |
| `accounts/<slot>/` | each slot's private Claude, Gemini or Codex config, including its login |
| `logs/daemon.log` | daemon log |
| `logs/<slot>.log` | raw output of every headless turn of that slot |

**In the repository:** `hive.toml`, `.hive/agent/{claude,gemini,codex}/`, `.hive/plans/`,
`.hive/prompts/` (yours; §17.5), `.hive/README.md`, and `.hive/orchestrator/state.md` (orchestrator worktree only,
git-ignored).

**Next to your clone:** one worktree per slot, `<clone>-<slot>/`, on branch `hive/<slot>`.

**On the server:** the database: agents, events, messages, tasks, leases, decisions, status
feed, reports, live-output lines. Dashboard archives are files in `$HIVE_ARCHIVE_DIR`: one
`<id>.json.gz` and a small `<id>.meta.json` each.

---

## 22. Timings and limits

| What | Value |
|---|---|
| Daemon polls the server | every 3 s (`poll_interval`) |
| Heartbeat / agent shown offline after | every 20 s / 90 s without one |
| Orchestrator failover after | 5 min unseen, or out of quota |
| Urgent events delivered (wake or `await_events`) | ≈ 3 s after arrival |
| Routine events batched for the orchestrator | `debounce_seconds` (90 s) |
| Minimum gap between wakes of one slot | 10 s |
| Maximum wakes per slot | 20 per hour |
| Headless turn timeout | 1 hour |
| Pause after a usage-limit error | 1 hour |
| New CLI session after | 40 turns |
| Lease TTL | 600 s default, 30–3600 s; auto-extended when < 5 min remain |
| Stop-hook pushbacks per turn | 3 |
| Message size / rate | 8 KB / 20 per minute per agent |
| Live-output upload | every 1 s, ≤ 500 lines per batch, ≤ 4000 chars per line |
| Live output kept on server | last 20,000 lines per agent |
| `await_events` timeout | 600 s default, 1800 s max |

---

## 23. Security model

- **Tokens:**
  - The admin token controls everything; keep it off agent machines unless you also work
    there.
  - Agent tokens are per agent and stored only as hashes on the server.
  - The local API binds to `127.0.0.1` and needs the per-machine secret.
- **Who sees what:**
  - Only the admin can read live output.
  - Only the admin can answer decisions and start the fleet.
  - Agents get only the tools their roles allow, enforced server-side.
- **Secrets in output:** worker output is uploaded to your server; anything an agent prints,
  including file contents it reads, ends up there. Use HTTPS, protect the admin token, and
  keep real credentials out of the repository. Reports and status updates are instructed
  never to contain secrets.
- **Permissions:** headless workers run in `acceptEdits` (Claude) or `auto_edit` (Gemini) mode,
  or Codex's `workspace-write` sandbox,
  with the session permission files from §17.4. File edits are auto-approved, while lease
  enforcement still blocks edits outside leases. Shell commands follow the three tiers of
  §17.4: allowed, approved by the orchestrator, or never. Anything else is refused. Widen
  `[permissions]` rather than joining with `--permission-mode bypassPermissions` / `yolo`.
  That flag lets the agent run any command on that machine and skips the approval step;
  Hive's hook still blocks the never-allowed commands.
- **Your prompt files:** agents can't edit `.hive/prompts/` or get changes to it into `main`
  (§17.5), so an agent can't rewrite its own instructions or another agent's.
- **Prompt injection:** other agents' messages are marked as untrusted information, and the
  skills say so explicitly. Hive's hard limits (leases, roles, freeze, decisions) are enforced
  by the server and hooks, not by the model's goodwill.

---

## 24. Troubleshooting

Start with `hive overview` in the project (§5). It checks most of the causes below and prints
the fix for each problem it finds.

| Symptom | Check |
|---|---|
| `hive start` fails with `invalid_fleet` | The message lists missing roles: exactly one orchestrator and at least one programmer must be enrolled. |
| An agent never wakes | Is `hive up` running on its machine? Run `hive status`: is it online, or out of quota? Is the fleet in a state where its role may act (§10.5)? Check `~/.hive/logs/daemon.log`. |
| Agent says "No lease covers …" | Expected: it must call `lease_acquire` first. If it repeats, see whether another agent holds the path (`hive status` → Leases). |
| "Hive daemon unreachable; file writes are blocked" | `hive up` isn't running on that machine, or `machine.toml` changed. Restart `hive up`. |
| Tools missing in the orchestrator session | `hive` must be on PATH (§3); re-run `hive init` and commit; launch through `hive up`, not plain `claude`. |
| `lease_release` returns `conflict` | The agent must merge `origin/main` into its work, then release again. The orchestrator is notified. |
| `lease_release` returns `checks_failed` | A command in `[integration] checks` failed; the output tail is in the result. |
| Push to `main` rejected | Another machine integrated first; Hive retries automatically, up to 3 times. |
| A Gemini slot fails to resume | Set a custom `command` with `--resume latest` (§20.2). |
| A Codex turn ends at once, or its Stop hook repeats `invalid choice: 'codex'` | The `hive` that Codex finds on PATH is older than the one running `hive up`. Codex runs hooks through your shell, so its startup files decide; reinstall Hive (§3) or pin it with `hive init --hive-cmd /full/path/to/hive`. |
| Codex says the profile `hive` doesn't exist | The slot has no private `CODEX_HOME`; re-run `hive join` (it creates `~/.hive/accounts/<name>`). |
| Orchestrator doesn't respond to typing | It's waiting in `await_events`: press Esc first (§8.1). |
| Lease stays after a machine died | It expires within its TTL (≤ 10 min by default), or the orchestrator or you can force-release it. |
| Fleet stuck in `frozen` | Run `hive decisions` and answer the open one. `hive reset` is the emergency exit. |

---

## 25. Known limitations and verification status

**Verified against the installed CLIs (Claude Code 2.1.x, Gemini CLI 0.41):**
- `--plugin-dir`, `--settings`, `--session-id`, `--resume` and the stream-json flags exist.
- The generated plugin passes `claude plugin validate`.
- The generated extension passes `gemini extensions validate`.
- Linking the extension into a private `GEMINI_CLI_HOME` loads its context, MCP server and
  skills there and leaves `~/.gemini` untouched.
- Gemini's hook decision format matches its bundled documentation.

**Verified with Codex CLI 0.163**, driven end to end by a scripted model server in place of
the OpenAI API (`tests/test_codex_e2e.py`, which runs when `codex` is installed):
- `codex exec --json` and `exec resume <id>` with `-p hive`; the JSONL events Hive formats;
  the session id from `thread.started`.
- The `hive` MCP server starts from the profile, receives the forwarded `HIVE_*` variables,
  and its tools arrive as `mcp__hive__<tool>` without approval prompts.
- The hooks run with `--dangerously-bypass-hook-trust`; PreToolUse denials stop `apply_patch`
  and shell commands, and SessionStart context reaches the model.
- The generated `.rules` pass `codex execpolicy check`; skills in `CODEX_HOME/skills/` are
  listed to the model.
- A Codex programmer is refused an unleased write, leases, writes, integrates with
  `lease_release` and completes its task.

**Not yet exercised with a live, logged-in session** (it would use account quota):
- Claude: that `--plugin-dir` loads the plugin's hooks and MCP server, and the tool name prefix
  `mcp__plugin_hive_hive__`.
- Gemini: `--resume <uuid>` (Gemini documents `latest` or an index; see §20.2) and the exact
  stream-json field names.
- Codex with a real model and account: the interactive orchestrator (`codex resume <id>`) and
  usage-limit messages.
- GitHub mode (§14.4).

**Current limitations:**
- Typing into the orchestrator while it waits needs **Esc** first (§8.1).
- The orchestrator isn't yet told to answer `#user` messages in `#user`, so replies to
  dashboard or `hive say` messages may appear only in its terminal.
- Plans written to `.hive/plans/` aren't synchronized between machines; plans reach you
  through the approval decision and reach programmers through task specs.
- No detection of agents messaging each other in loops, and no push notifications yet.
- Lease overlap checks are conservative (§14.1).
- Shell commands can't be inspected reliably by hooks; the git `pre-commit` hook is the
  backstop.

---

## 26. Development

```sh
uv sync
uv run pytest -q
```

| Test file | Covers |
|---|---|
| `tests/test_server.py` | roles and fleet validation, the approval gate, scheduling, leases and fencing, freeze and thaw, message audiences, reviews, role-change decisions |
| `tests/test_e2e.py` | a real server, daemon, bare git origin and two worktrees. A scripted fake agent CLI (`tests/fake_agent.py`) plans, gets approved, is denied an unleased write, leases, integrates into `main`, completes, reports and streams output. Also covers an interactive orchestrator in `await_events` and the freeze on a stuck agent. |
| `tests/test_init_and_launch.py` | bundle generation and legacy cleanup, launch commands, tool-name matching, stream formatting |

**Source layout:**

| Path | Contents |
|---|---|
| `src/hive/server/` | Flask app, business logic (`service.py`), schema, dashboard |
| `src/hive/daemon/` | daemon core, local API, git operations, CLI launching and stream formatting, local state |
| `src/hive/mcp_server.py`, `hooks.py`, `local.py` | the pieces the agent CLIs run |
| `src/hive/init.py` | bundles and skills |
| `src/hive/cli.py` | the `hive` command |
| `src/hive/roles.py`, `globs.py`, `config.py` | shared logic |
| `scripts/simulate_load.py` | storage and request-cost simulation behind [docs/DEPLOYMENT.md §3](docs/DEPLOYMENT.md#3-capacity-planning-how-much-data-and-load-to-expect) |
