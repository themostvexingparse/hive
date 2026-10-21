# Glossary

Every term used in Hive's documentation, in plain words, with a link to the page that
explains it in full. The other pages link here the first time they use a term, so you can
read any page on its own.

- [Conventions: example names and placeholders](#conventions-example-names-and-placeholders)
- [Hive's parts](#hives-parts)
- [Agents and how they run](#agents-and-how-they-run)
- [People, credentials and projects](#people-credentials-and-projects)
- [Work and coordination](#work-and-coordination)
- [Git and integration](#git-and-integration)
- [What Hive attaches to an agent](#what-hive-attaches-to-an-agent)
- [Files](#files)
- [Outside tools and technologies](#outside-tools-and-technologies)

---

## Conventions: example names and placeholders

The examples in these docs use made-up names. Replace them with your own.

| In the examples | What it stands for |
|---|---|
| `myapp`, `~/code/myapp` | The repository the agents work on, and your local [clone](#repository-clone) of it. |
| `your-project`, `/path/to/clone` | The same: any repository and the folder where you cloned it. |
| `acme`, `beta`, `Acme Corp`, `acme-repo` | An example [project](#project) on a server that hosts several, and its repository. "Acme" is the traditional made-up company name. |
| `orch` | An example [agent](#agent) name for the [orchestrator](#orchestrator). |
| `dev`, `claude-2`, `claude-3`, `gemini-1`, `codex-1` | Example agent names for [workers](#worker). Putting the [vendor](#vendor-cli) in the name is a habit, not a rule. |
| `USER.pythonanywhere.com` | Your web app's address on [PythonAnywhere](#pythonanywhere); `USER` is your PythonAnywhere username. |
| `host`, `hive.example.com`, `https://…` | Your Hive [server](#server)'s address. |
| `http://127.0.0.1:8765` | A server running on your own computer, as in a first trial. |
| `<token>`, `<admin token>`, `<orch token>` | A [token](#admin-token) you were given; paste it in place of the whole `<…>`. |
| `<slot>`, `<project>`, `<id>`, `NAME` | Replace with the actual slot name, project name, number or name. |
| `owner/repo` | A GitHub repository, e.g. `octocat/hello-world`. |
| `"Add CSV export to the reports page"` | An example goal given to the fleet. |

Commands are shown for a POSIX shell (bash, zsh). They work the same in PowerShell unless
a page shows both forms. `~` means your home folder (`%USERPROFILE%` on Windows).

---

## Hive's parts

### Server

The `hive-server` program: one per [fleet](#fleet) (or per several, see [project](#project)).
It stores everything the fleet shares (agents, [tasks](#task), [messages](#message-channel-priority),
[leases](#lease), [decisions](#decision), [live output](#live-output)) and decides who gets
which task and lease. Agents and you reach it over HTTP(S). It also serves the
[dashboard](#dashboard). ➜ [Running the server](SERVER.md)

### Daemon

The background program `hive up` starts on each [machine](#machine). It asks the server every
few seconds whether anything concerns its [slots](#slot), starts ([wakes](#wake)) agents when
it does, runs their git work, and uploads their output. ➜ [Running the fleet](RUNNING.md)

### Local API

A small web service the daemon offers only to its own machine (`127.0.0.1`), protected by a
secret. An agent's [MCP tools](#mcp-mcp-server-mcp-tool) and [hooks](#hook) talk to it; it talks to the
server.

### Machine

Any computer that runs agents with `hive up`. One machine can run many slots.

### Dashboard

The web page the server shows at its address: the whole fleet at a glance, plus controls.
Only you can sign in. ➜ [Watching the fleet](MONITORING.md#the-web-dashboard)

### `hive` command

The one program you install. Its subcommands run the server (`hive server`), set up a
repository (`hive init`), add agents (`hive admin enroll`, `hive join`), run machines
(`hive up`) and steer the fleet (`hive start`, `hive decide`, …). ➜ [CLI.md](CLI.md)

---

## Agents and how they run

### Agent

One AI coding assistant taking part in the fleet, with a name (e.g. `claude-2`),
[roles](#role) and its own account. On a machine it lives in a [slot](#slot); "agent" and
"slot" usually mean the same thing.

### Slot

An agent's home on a machine: its own private CLI config folder (where its account is logged
in) plus its own [worktree](#worktree). Created by `hive join`.
➜ [Setting up machines](MACHINES.md)

### Fleet

All the agents of one project, working towards one goal you set with `hive start`.

### Vendor, CLI

The company and command-line program an agent runs: **Claude Code** (`claude`, Anthropic),
**Codex** (`codex`, OpenAI), **Antigravity CLI** (`agy`, Google) or **Gemini CLI** (`gemini`,
Google; replaced by agy for personal accounts). A CLI (command-line
interface) is a program you use by typing in a terminal. Hive uses each vendor's official
CLI, unmodified, logged in to a normal subscription account.

### Orchestrator

The one agent that plans and coordinates. It splits your goal into tasks, assigns them,
answers workers, approves their risky commands, and reports to you. It never writes code.
You chat with it in a terminal. ➜ [Working with the orchestrator](ORCHESTRATOR.md)

### Worker

Every agent that isn't the orchestrator: programmers, reviewers, testers, planners. Workers
run [headless](#headless-and-interactive) and are woken when work arrives.

### Headless and interactive

**Interactive:** the CLI runs in a terminal and a person types to it (the orchestrator, or
`hive chat`). **Headless:** the CLI runs without a terminal, given one prompt, and exits when
it's done (workers).

### Session

One continuing conversation of a CLI with its model. Every request re-reads the whole
session, so Hive keeps sessions short: a worker resumes its session while it works on the same
task, and starts a fresh one for each new task or once the session grows large
([Wake rules](RUNNING.md#wake-rules)).

### Turn

One run of an agent: it reads its prompt, works (calls tools, edits files), and stops.

### Wake

A [headless](#headless-and-interactive) turn the daemon starts because something concerns a
worker: a task was assigned, a message mentions it, a review came back, and so on.
➜ [How a wake is launched](RUNNING.md#how-an-agent-is-launched-for-a-headless-turn-wake)

### Quota, usage limit

The amount of work a subscription account may do in a period. When an agent's account hits
its limit, Hive pauses that agent for an hour and the [scheduler](#scheduler) skips it.

---

## People, credentials and projects

### Admin, you

The person running the fleet. Admin commands (`hive admin …`, `hive start`, `hive decide`, …)
and the dashboard need the [admin token](#admin-token).

### Admin token

The password for the whole server, set when you start it (`HIVE_ADMIN_TOKEN`). Whoever holds
it can answer decisions, start and stop the fleet, read live output and add agents. Agents
never get it. ➜ [Security model](SECURITY.md#tokens)

### Agent token

A per-agent password, printed once when you [enroll](#enroll) the agent. The slot uses it to
talk to the server as that agent, and can do only what the agent's [roles](#role) allow.

### Enroll

Creating an agent on the server: `hive admin enroll NAME --vendor … --roles …`. Prints its
[agent token](#agent-token). ➜ [Enrolling agents](MACHINES.md#enroll-each-agent-on-your-computer)

### Join

Adding an enrolled agent to a machine as a [slot](#slot): `hive join NAME --token …`.
➜ [Joining a slot](MACHINES.md#join-each-slot-on-the-machine-that-will-run-it)

### Project

One repository with its own fleet, agents, tasks and database. A server starts with one
project, called `default`, at its own address; it can host more at `https://host/p/<project>/`,
each with its own [project admin token](#project-admin-token). ➜ [Several projects](PROJECTS.md)

### Project admin token

An admin token that works in one project only. The server's own admin token works in all of
them.

---

## Work and coordination

### Goal

What you want the fleet to achieve, in one sentence: `hive start "…"`. The orchestrator turns
it into a plan.

### Fleet state

Which phase the fleet is in: `setup`, `planning`, `awaiting_approval`, `running`, `frozen` or
`paused`. It decides who may work. ➜ [The fleet lifecycle](LIFECYCLE.md)

### Decision

A question the orchestrator asks *you*, with options. Only you can answer it
(`hive decide`, or the dashboard). The plan approval is one; a [stuck](#stuck-freeze)
agent's question is another. ➜ [Answering decisions](ORCHESTRATOR.md#answering-decisions)

### Stuck, freeze

An agent reports itself stuck when it can't continue. If only you can unblock it, the
orchestrator asks you, and the whole fleet **freezes** (stops) until you answer.
➜ [Stuck agents and freezing](LIFECYCLE.md#stuck-agents-and-freezing)

### Role

What an agent may and should do: `orchestrator`, `programmer`, `planner`, `reviewer`,
`tester`, or one you define. ➜ [Roles](ROLES.md)

### Task

One unit of work, with a title, a description (spec), a [kind](#task-kind) and dependencies
on other tasks. ➜ [Tasks](COORDINATION.md#tasks)

### Task kind

The type of a task, such as `code`, `test`, `docs`, `review` or `plan`. Roles say which kinds
they take.

### Scheduler

The part of the server that hands each ready task to a suitable idle agent.

### Message, channel, priority

Agents and you talk through the server: in **channels** (`#general`, `#user`, …) or by
direct message. A message's **priority** (`info`, `normal`, `high`, `interrupt`) decides how
fast it reaches the recipient. ➜ [Messages](COORDINATION.md#messages-and-priorities)

### Report

A worker's written summary of what it did, for the orchestrator.
➜ [Reports](COORDINATION.md#reports)

### Status update

The orchestrator's summary for you, shown in `hive status` and the dashboard.

### Live output

What each agent prints while it works (its text, tool calls and their results), uploaded to
the server for you to watch with `hive watch` or the dashboard. Agents can't see it.

### Approval

A worker's request to run a dangerous shell command, which the orchestrator (or you) approves
or denies. ➜ [Shell-command permissions](PERMISSIONS.md)

### Archive

A saved copy of a run's history, made from the dashboard. ➜ [Archives](MONITORING.md#archives)

---

## Git and integration

### Repository, clone

The git project the agents work on (a **repository**), and a copy of it on a computer (a
**clone**).

### `origin`

The name git gives the shared copy of the repository that every clone pushes to, for example
on GitHub. All machines need to be able to push to it.

### `main`, integration branch

The branch finished work goes into. Its name is set in `hive.toml` (`main` by default).
Agents never change it directly; Hive does, after checks pass.

### Worktree

A second working folder of the same git repository, with its own branch checked out. Each
slot gets one next to your clone (e.g. `~/code/myapp-claude-2` on branch `hive/claude-2`), so
agents never see each other's half-finished edits.

### Rebase

Replaying your commits on top of the newest `main`, so your work sits after everyone else's.

### Fast-forward

Moving a branch forward to a newer commit that already contains it. A fast-forward never
throws away anyone's work.

### Lease

A time-limited lock on files. An agent must lease every path it edits, and nobody else can
lease overlapping paths meanwhile. ➜ [Leases](INTEGRATION.md#leases)

### Glob

A path pattern: `src/api/**` means everything under `src/api/`, `docs/*.md` every Markdown
file directly in `docs/`.

### Fencing number

A serial number on each lease. If a lease is taken away or expires, its number becomes
invalid, so stale work can never be merged with it.

### Integration, `lease_release`

Getting an agent's work into `main`. The agent calls the `lease_release` tool; its daemon
commits the leased files, rebases, runs the [checks](#checks), and pushes.
➜ [What `lease_release` does](INTEGRATION.md#what-lease_releasecommit_message-task_id-does)

### Checks

Commands that must succeed before work reaches `main`, usually your test suite. Set in
`hive.toml` under `[integration] checks`.

### Review

Optionally, another agent must approve work before it's integrated.
➜ [Reviews](INTEGRATION.md#reviews)

### GitHub mode

An optional setup where the server, rather than the machines, moves `main` through GitHub's
API. ➜ [GitHub mode](INTEGRATION.md#github-mode-optional)

---

## What Hive attaches to an agent

### MCP, MCP server, MCP tool

MCP (Model Context Protocol) is a standard way to give an AI agent extra tools. `hive mcp` is
an MCP server; each **MCP tool** it offers (`lease_acquire`, `post`, `task_complete`, …) is
something the agent can call, like a function. ➜ [MCP tools](AGENT_INTERFACE.md#mcp-tools-hive-server)

### Hook

A command the agent CLI runs automatically at fixed moments, such as before every tool call
or at the end of a turn. Hive's hooks deliver urgent messages and refuse writes the agent
isn't allowed to make. ➜ [Hooks](AGENT_INTERFACE.md#hooks)

### Skill

A written procedure an agent follows, in Markdown: `hive-orchestrator` for the orchestrator,
`hive-worker` for everyone else. ➜ [Skills](AGENT_INTERFACE.md#skills)

### Bundle (plugin, extension, profile)

The package of MCP server, hooks, skills and permission rules that `hive init` writes to
`.hive/agent/`: a Claude Code **plugin**, a Gemini CLI **extension**, a Codex **profile** and
an agy **plugin**.
Hive attaches it only to sessions it launches. ➜ [Preparing the repository](REPOSITORY.md)

### Permission tiers

Hive's sorting of shell commands into allowed, needs approval, and never.
➜ [Shell-command permissions](PERMISSIONS.md)

### Permission mode

Each CLI's own setting for how much it may do without asking, e.g. Claude Code's
`acceptEdits`, Codex's `workspace-write` sandbox or agy's `accept-edits`. Set per slot with
`hive join --permission-mode`.

### Prompt

The text given to an AI agent. Hive builds it from built-in instructions plus your additions
in `.hive/prompts/`. ➜ [Customising what agents are told](PROMPTS.md)

---

## Files

| File or folder | What it is | ➜ |
|---|---|---|
| `hive.toml` | The project's fleet settings, in the repository. | [Configuration](CONFIGURATION.md#hivetoml) |
| `.hive/` | Hive's folder in the repository: bundles, plans, your prompt additions. | [Preparing the repository](REPOSITORY.md#what-it-creates) |
| `~/.hive/` | Hive's folder in your home folder on each machine. | [Files and directories](CONFIGURATION.md#files-and-directories) |
| `~/.hive/machine.toml` | That machine's slots and their tokens. | [Configuration](CONFIGURATION.md#hivemachinetoml) |
| `~/.hive/admin.toml` | Your saved admin logins. | [Logging in as admin](SERVER.md#log-in-as-admin) |
| `~/.hive/accounts/<slot>/` | A slot's private CLI config folder, holding its login. | [Setting up machines](MACHINES.md) |

TOML is a simple settings-file format: `key = "value"` lines under `[section]` headings.

---

## Outside tools and technologies

### uv

A fast installer for Python programs. `uv tool install` puts the `hive` command on your
system. ➜ [docs.astral.sh/uv](https://docs.astral.sh/uv/)

### PATH

The list of folders your shell searches for commands. "`hive` must be on PATH" means typing
`hive` works in any terminal.

### WSGI

The standard way Python web applications plug into web servers. Hive's server is a WSGI app,
so any Python web host can run it.

### PythonAnywhere

A Python web-hosting service with free and paid plans, and the easiest place to put a Hive
server online. ➜ [DEPLOYMENT.md §4](DEPLOYMENT.md#4-deploying-on-pythonanywhere)

### VPS

A virtual private server: a rented Linux machine you control.
➜ [DEPLOYMENT.md §5](DEPLOYMENT.md#5-deploying-on-a-vps-gunicorn--caddy--systemd)

### SQLite, MySQL, Postgres, database URL

Databases Hive's server can store its data in. SQLite is a single file and needs no setup;
MySQL and Postgres are database servers. A **database URL** names one, e.g.
`sqlite:///hive.db` or `mysql+pymysql://user:password@host/dbname` (the SQLAlchemy format).

### Git Bash

The bash shell that comes with Git for Windows. Hive uses it to run bash commands on Windows.

### GitHub token

A password-like key for GitHub's API, created in GitHub's settings. Only needed for
[GitHub mode](#github-mode).

---

[← Back to the README](../README.md)
