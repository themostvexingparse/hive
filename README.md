# Hive

Hive turns several subscription-based coding agents into one coordinated team. Each agent is
**Claude Code** or **Gemini CLI**, running as the vendor's official CLI on a computer you own
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
4. [Running the server](#4-running-the-server)
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
17. [What the agents see: MCP tools, hooks, skills](#17-what-the-agents-see-mcp-tools-hooks-skills)
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

  A `claude` or `gemini` you start yourself never sees any of this.
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
| A git remote (`origin`) | the repository | GitHub or any remote all machines can push to. |
| A reachable server URL | | `http://127.0.0.1:8765` for single-machine trials; a public HTTPS URL otherwise. |

**One machine can host several slots.** Each slot has its own CLI config folder, so several
Claude or Gemini accounts can run side by side on one computer. You don't need one computer
per account.

---

## 3. Installation

On **every** agent machine (and wherever you run admin commands):

```sh
uv tool install /path/to/hive          # puts the `hive` command on PATH
hive --version
```

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
hive server --admin-token "$(openssl rand -hex 24)" --db sqlite:///hive.db --port 8765
```

| Option | Default |
|---|---|
| `--host` | `127.0.0.1` |
| `--port` | `8765` |
| `--db` | `sqlite:///hive.db` |

This is Flask's built-in server: fine for trying Hive and for a LAN. Use a proper WSGI
server for anything public.

### 4.2 PythonAnywhere

1. Upload or clone this repository, then run `pip install --user '/path/to/hive[mysql]'` in a
   Bash console.
2. Create a MySQL database in the **Databases** tab, e.g. `USER$hive`.
3. **Web** tab: add a new web app with *Manual configuration* and your Python version. Edit
   its WSGI file to contain:

   ```python
   import os
   os.environ["HIVE_ADMIN_TOKEN"] = "<long random secret>"
   os.environ["HIVE_DATABASE_URL"] = (
       "mysql+pymysql://USER:PASSWORD@USER.mysql.pythonanywhere-services.com/USER$hive")
   # os.environ["HIVE_GITHUB_TOKEN"] = "<fine-grained token>"   # optional, see §14.4
   from hive.server.wsgi import application
   ```
4. Reload the web app. Open `https://USER.pythonanywhere.com/api/v1/health`; it should return
   `{"ok": true}`.

Why it works there:
- Daemons poll instead of holding connections open.
- Lease expiry, offline detection, failover and scheduling all run lazily at the start of
  requests, so no scheduled tasks are needed.

Use a paid plan: free accounts restrict outbound internet and CPU time.

### 4.3 Any other host (VPS, Fly.io, Railway, …)

Run the WSGI app with any WSGI server, for example:

```sh
HIVE_ADMIN_TOKEN=… HIVE_DATABASE_URL=… gunicorn -w 2 'hive.server.wsgi:application'
```

Put it behind TLS (Caddy, nginx, or the platform's proxy).

### 4.4 Log in as admin

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
| `hive.toml` | Fleet configuration: review policy, checks, standby orchestrator, roles (§20.1). Created only if missing. | yes |
| `.hive/agent/claude/` | **Claude Code plugin**: `.claude-plugin/plugin.json`, `.mcp.json` (hive MCP server), `hooks/hooks.json`, `skills/hive-orchestrator/SKILL.md`, `skills/hive-worker/SKILL.md`. | yes |
| `.hive/agent/gemini/` | **Gemini CLI extension**: `gemini-extension.json` (MCP server, `HIVE.md` context), `hooks/hooks.json`, the same skills. | yes |
| `.hive/plans/` | Place for written plans. | yes |
| `.hive/README.md` | Explains the folder. | yes |
| `.gitignore` | Adds `.hive/orchestrator/` (the orchestrator's private memory). | yes |

**The bundles are inert.** Neither CLI loads `.hive/agent/` on its own:
- Claude Code gets the plugin only when `hive` passes `--plugin-dir .hive/agent/claude`.
- Gemini gets the extension only through the slot's **private** Gemini home (§7.3).

Opening `claude` or `gemini` in this repository yourself loads nothing from Hive.

**Cleanup of older versions:** `hive init` removes Hive entries written by Hive 0.1 to files
the CLIs load automatically: `.mcp.json`, `.claude/settings.json`, `.gemini/settings.json`,
and the `<!-- hive:begin -->` blocks in `CLAUDE.md`, `GEMINI.md`, `AGENTS.md`. Your own
entries in those files are kept. A file is deleted only if Hive's content was all it had.

Re-run `hive init` after upgrading Hive to refresh the bundles. Then commit and push, and
upload the configuration:

```sh
git add -A && git commit -m "Add Hive" && git push
hive admin push-config        # uploads hive.toml to the server (run inside the repo)
```

`hive start` (§10) also uploads `hive.toml` automatically when run inside the repository.

---

## 6. Enrolling agents and setting up machines

### 6.1 Enroll each agent (on your computer)

```sh
hive admin enroll orch     --vendor claude --roles orchestrator
hive admin enroll claude-2 --vendor claude --roles programmer
hive admin enroll claude-3 --vendor claude --roles programmer,tester
hive admin enroll gemini-1 --vendor gemini --roles reviewer,planner
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
3. **Installs a git `pre-commit` hook** in the clone; worktrees share it. The hook does nothing
   outside agent sessions. Inside one, it refuses commits of files the agent hasn't leased. An
   existing foreign `pre-commit` hook is never overwritten: Hive tells you which line to add.
4. **Creates the slot's private CLI config folder**, by default `~/.hive/accounts/claude-2`
   (`--config-dir` to change it).
5. **Saves the slot** to `~/.hive/machine.toml`.

| `hive join` option | Meaning |
|---|---|
| `--repo PATH` | Local clone; the worktree is created next to it. |
| `--worktree PATH` | Explicit worktree path (use with or instead of `--repo`). |
| `--config-dir PATH` | The account's CLI config folder (default `~/.hive/accounts/<name>`). |
| `--permission-mode MODE` | Claude: `acceptEdits` (default for workers), `bypassPermissions`, …; Gemini: `auto_edit` (default), `yolo`. See §23. |
| `--server URL` | Server URL, saved for the whole machine. |
| `--hive-cmd CMD` | How the git hook calls `hive` (default `hive`). |

### 6.3 Log each slot's account in once

Each slot uses its own config folder, so log the matching account into it once, interactively:

```sh
# Claude slot
cd /path/to/clone-claude-2
CLAUDE_CONFIG_DIR=~/.hive/accounts/claude-2 claude      # log in, then exit

# Gemini slot
cd /path/to/clone-gemini-1
GEMINI_CLI_HOME=~/.hive/accounts/gemini-1 gemini        # log in, then exit
```

PowerShell equivalent:
`$env:CLAUDE_CONFIG_DIR="$HOME\.hive\accounts\claude-2"; claude`.

Use a different account per slot. That is the point of separate folders.

---

## 7. Running the fleet (`hive up`)

Run `hive up` on every machine that hosts slots. What it does depends on which slots live
there.

### 7.1 The machine with the orchestrator (your computer)

```sh
hive up
```

1. **Daemon:** starts in the background. Its log goes to `~/.hive/logs/daemon.log`, not the
   terminal.
2. **CLI launch:** opens the orchestrator's **native CLI in your terminal** with Hive attached
   for this session:
   - Claude: `claude --plugin-dir <worktree>/.hive/agent/claude --settings <allow hive tools>
     --session-id|--resume <id> "<start prompt>"`
   - Gemini: `gemini -e hive --session-id|--resume <id> -i "<start prompt>"`
3. **Session:** the first launch creates a new session. Later launches **resume the same
   conversation**.
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

**Environment for every launch:**

| Variable | Value |
|---|---|
| `HIVE_SLOT` | the slot name |
| `HIVE_DAEMON_URL` | the daemon's local API |
| `HIVE_LOCAL_SECRET` | the local API secret |
| `MCP_TOOL_TIMEOUT` | 30 minutes |
| `CLAUDE_CONFIG_DIR` | the slot's config folder (Claude slots) |
| `GEMINI_CLI_HOME` | the slot's config folder (Gemini slots) |

**Gemini extension:** before a Gemini slot's first launch, `hive` runs
`gemini extensions link <worktree>/.hive/agent/gemini --consent` **inside the slot's private
`GEMINI_CLI_HOME`**. Your personal `~/.gemini` is never touched.

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
- **Line format:**

  | Line | Meaning |
  |---|---|
  | `  text` | the agent's prose |
  | `→ tool {input}` | a tool call |
  | `← result` / `✗ error` | a tool result |
  | `· …` | session, wake and turn markers |

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

Built-in roles can be overridden the same way, e.g. `[roles.reviewer] prompt = "…"`.

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
- **Glob syntax:** repo-relative POSIX. `src/app.py`, `src/api/**`, `docs/*.md`. A path
  without wildcards also covers everything beneath it.
- **Overlap checks are conservative:** two leases are refused if their fixed prefixes
  overlap, even when their files are actually disjoint.
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

1. **Commit:** commits only the changed files covered by the agent's leases or `write_globs`.
   The commit message gets trailers: `Hive-Agent`, `Hive-Task`, `Hive-Fence`. Other changed
   files are set aside (stashed) and restored afterwards; the result lists them.
2. **Rebase:** `git fetch` and rebase onto `origin/main`. On a conflict:
   - the rebase is aborted;
   - the commit and leases are kept;
   - the orchestrator is told;
   - the agent must merge and call `lease_release` again.
3. **Checks:** runs every command in `[integration] checks`. On failure, the leases are kept
   and the output tail is returned to the agent.
4. **Push:** pushes the agent's branch `hive/<slot>`.
5. **Integrate:**
   - **Local mode** (default): the daemon pushes `HEAD:main` with a plain, non-forced push,
     which fails if `main` moved. In that case it re-fetches and retries from step 2, up to 3
     times. It then confirms to the server.
   - **GitHub mode** (§14.4): the server fast-forwards `main` through the GitHub API.
6. **Done:**
   - the leases are released;
   - the server records the new `main` and announces it;
   - every other slot whose worktree is clean rebases onto it immediately;
   - slots with uncommitted work get a notice and rebase at their next release.

With review enabled, step 5 becomes "submit for review" instead (§15).

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
| `reviewer` | An agent with the reviewer role reviews. If none exists, the orchestrator does. |

**Flow with review on:**
1. The programmer's `lease_release` pushes its branch and the task enters **review**. The
   programmer **keeps its leases**.
2. The reviewer is woken with the commit to inspect (`git diff origin/main...<sha>`) and calls
   `review_verdict(task_id, approve, notes)`.
3. **Approve:** the programmer's daemon re-runs the release automatically (rebase, checks,
   integrate), and the task becomes **done**.
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
| `lease_status`, `repo_state`, `fleet_state`, `status_read` | look around |

**Coding roles** add: `lease_acquire`, `lease_extend`, `lease_release`.

**Orchestrator** adds:
- `await_events(timeout_seconds)`;
- `task_create`, `task_assign`, `task_cancel`;
- `decision_request(kind, question, options, context, task_id, freeze)`;
- `status_publish(summary, body, level)`;
- `report_request`;
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

The hooks ship inside the bundles and call `hive hook <event> --vendor <cli>`.

| Claude event | Gemini event | What Hive does |
|---|---|---|
| PreToolUse | BeforeTool | Denies writes not covered by leases or `write_globs`, and writes into other slots' worktrees. While the fleet state doesn't allow the agent's roles to act, denies everything except reading and Hive tools. |
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

Both are plain Markdown in `.hive/agent/*/skills/`. Edit them to tune behaviour; `hive init`
overwrites the bundles, so keep your own copy of any edits.

---

## 18. Web dashboard

Open the server's root URL (`https://…/`) and enter the admin token. It's kept in your
browser's local storage.

| Panel | Contents |
|---|---|
| Header | fleet state, goal, Pause / Resume buttons |
| Decisions needed | open decisions with context, option buttons and a note field |
| Talk to the orchestrator | the `#user` conversation and a message box. In `setup`, a goal box and *Start fleet* button. |
| Status feed | the orchestrator's updates, newest first |
| Agents | roles and state: online, busy, offline, out of quota |
| Tasks, Leases | current board and locks |
| Live output | agents' formatted output, all or one agent |

It refreshes every 5 s; live output refreshes every 1.5 s.

---

## 19. Command reference

### User / admin

| Command | Description |
|---|---|
| `hive server [--host H] [--port P] [--db URL] [--admin-token T]` | Run the server (development). |
| `hive admin login --server URL --token T` | Save admin credentials to `~/.hive/admin.toml`. |
| `hive admin enroll NAME --vendor claude\|gemini --roles r1,r2` | Create an agent; prints its token and a `hive join` line. |
| `hive admin roles NAME r1,r2` | Change an agent's roles. |
| `hive admin push-config` | Upload `hive.toml` from the current repository. |
| `hive init [PATH] [--server URL] [--main BRANCH] [--hive-cmd CMD]` | Prepare a repository (§5). |
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
| `hive join NAME --vendor V --token T [--server URL] [--repo PATH] [--worktree PATH] [--config-dir PATH] [--permission-mode MODE] [--hive-cmd CMD]` | Add a slot to this machine (§6.2). |
| `hive up [-v] [--headless]` | Run this machine's slots (§7). |
| `hive chat SLOT [--fresh]` | Attach interactively to a slot (§7.4). |

### Used by the agent CLIs (you don't run these)

| Command | Description |
|---|---|
| `hive mcp [--slot S]` | The stdio MCP server. |
| `hive hook EVENT [--vendor V]` | Hook bridge; `hive hook git-pre-commit` for git. |

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

[orchestrator]
standby = ["claude-2"]       # failover candidates, in order (§11.3)
debounce_seconds = 90        # batching window for routine events
heartbeat_hours = 4          # headless orchestrator only: periodic check-in

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
permission_mode = ""         # empty = default (claude: acceptEdits, gemini: auto_edit)
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
| `HIVE_SERVER_URL` | admin CLI | Overrides the server URL in `admin.toml`. |
| `HIVE_HOME` | all local commands | Location of Hive's local files (default `~/.hive`). |
| `HIVE_SLOT`, `HIVE_DAEMON_URL`, `HIVE_LOCAL_SECRET` | set by Hive for agent CLIs | Identify the slot and reach the daemon. |
| `CLAUDE_CONFIG_DIR` / `GEMINI_CLI_HOME` | set by Hive per slot | Keep each account's CLI config separate. |
| `MCP_TOOL_TIMEOUT` | set by Hive | Lets `await_events` block for up to 30 minutes. |

---

## 21. Files and directories

**On each machine (`~/.hive/`, or `$HIVE_HOME`):**

| Path | Contents |
|---|---|
| `machine.toml` | server URL, local API port and secret, slots with tokens (keep private) |
| `admin.toml` | admin server URL and token (only where you ran `hive admin login`) |
| `state.db` | daemon state: event cursors, inbox cache, pending wake reasons, session ids |
| `accounts/<slot>/` | each slot's private Claude or Gemini config, including its login |
| `logs/daemon.log` | daemon log |
| `logs/<slot>.log` | raw output of every headless turn of that slot |

**In the repository:** `hive.toml`, `.hive/agent/{claude,gemini}/`, `.hive/plans/`,
`.hive/README.md`, and `.hive/orchestrator/state.md` (orchestrator worktree only,
git-ignored).

**Next to your clone:** one worktree per slot, `<clone>-<slot>/`, on branch `hive/<slot>`.

**On the server:** the database: agents, events, messages, tasks, leases, decisions, status
feed, reports, live-output lines.

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
- **Permission modes:** headless workers default to `acceptEdits` (Claude) or `auto_edit`
  (Gemini). Edits are auto-approved, but shell commands not covered by the account's own
  allow rules may be refused. If your checks or tests need shell access, either add allow
  rules in that slot's config folder, or join with `--permission-mode bypassPermissions` /
  `yolo`. That is a real trust decision: the agent can then run any command on that machine.
- **Prompt injection:** other agents' messages are marked as untrusted information, and the
  skills say so explicitly. Hive's hard limits (leases, roles, freeze, decisions) are enforced
  by the server and hooks, not by the model's goodwill.

---

## 24. Troubleshooting

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

**Not yet exercised with a live, logged-in session** (it would use account quota):
- Claude: that `--plugin-dir` loads the plugin's hooks and MCP server, and the tool name prefix
  `mcp__plugin_hive_hive__`.
- Gemini: `--resume <uuid>` (Gemini documents `latest` or an index; see §20.2) and the exact
  stream-json field names.
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
