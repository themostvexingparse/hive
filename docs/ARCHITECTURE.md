# How Hive works

Hive turns several AI coding agents into one team that works on one git repository. This
page explains Hive's moving parts, and how a goal you type turns into commits on the
repository's `main` branch. Read it first if you want to understand what you are setting up
before you set it up. Every term is also in the [glossary](GLOSSARY.md); the design rationale
and full specification are in [SPEC.md](../SPEC.md).

---

## Key terms

| Term | Meaning |
|---|---|
| **Agent** | One AI coding assistant in the team: Claude Code, Codex, Antigravity CLI (`agy`) or Gemini CLI, running as the vendor's own command-line program (CLI), logged in to its own account. |
| **Fleet** | All the agents working on one repository towards one goal. |
| **Orchestrator** | The one agent that plans and coordinates, and that you chat with. It never writes code. |
| **Worker** | Every other agent: programmers, reviewers, testers, planners. |
| **Machine** | Any computer that runs agents. |
| **Slot** | An agent's home on a machine: a private CLI settings folder holding its account login, plus its own [git worktree](GLOSSARY.md#worktree) (a separate working folder of the repository) on branch `hive/<slot>`. "Agent" and "slot" mean the same thing in practice. |
| **Role** | What an agent is allowed and expected to do: `orchestrator`, `programmer`, `planner`, `reviewer`, `tester`, or your own ([Roles](ROLES.md)). |
| **Task** | A unit of work with a kind (`code`, `test`, `review`, …), a description and dependencies. The server assigns ready tasks to agents by role ([Tasks](COORDINATION.md#tasks)). |
| **Lease** | A time-limited, exclusive lock on a set of paths. An agent must hold a lease on every file it edits ([Leases](INTEGRATION.md#leases)). |
| **Integration** | Getting an agent's work into `main`: commit, rebase (replay it on top of the newest `main`), run your tests, push. Triggered by releasing leases ([What `lease_release` does](INTEGRATION.md#what-lease_releasecommit_message-task_id-does)). |
| **Fleet state** | `setup`, `planning`, `awaiting_approval`, `running`, `frozen` or `paused`. It decides who may work ([The fleet lifecycle](LIFECYCLE.md)). |
| **Decision** | A question the orchestrator asks *you*. Only you can answer it. Some decisions freeze the fleet until you do. |
| **Headless, interactive** | An interactive CLI runs in a terminal you type into. A headless one runs without a terminal: it gets one prompt, works, and exits. |
| **Turn, wake** | A turn is one run of an agent: it reads its prompt, works, and stops. A wake is a headless turn the daemon starts because something concerns a worker ([How an agent is launched](RUNNING.md#how-an-agent-is-launched-for-a-headless-turn-wake)). |
| **Report** | A worker's written summary of what it did, filed for the orchestrator. |
| **Status update** | The orchestrator's summary for you, shown in `hive status` and the dashboard. |
| **Approval** | A worker's request to run a dangerous shell command, decided by the orchestrator ([Shell-command permissions](PERMISSIONS.md)). |
| **Bundle** | The per-CLI package `hive init` writes to `.hive/agent/` in the repository: a Claude Code plugin, a Gemini CLI extension, a Codex profile or an agy plugin, holding Hive's tools, hooks and instructions. |

---

## The big picture

### A sample setup

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="images/architecture-dark.svg">
  <source media="(prefers-color-scheme: light)" srcset="images/architecture-light.svg">
  <img alt="A sample Hive setup in layers. You: a terminal chat with the orchestrator, hive commands and the web dashboard, which reach the hive-server over HTTPS with the admin token. Shared state: the hive-server and the git origin. One daemon per machine exchanges events and heartbeats with the server; on the two worker machines it also wakes agents and pushes their work to the git origin. Agent CLIs, one worktree each: the orchestrator on your computer, claude-2 (programmer) and codex-1 (tester) on worker machine A, gemini-1 (reviewer) on worker machine B." src="images/architecture-light.svg" width="100%">
</picture>

This is one possible fleet: three computers and four agents. Any split works, including
everything on one computer ([Setting up machines](MACHINES.md#a-typical-layout)). How to
read it:

- **Rows are layers**, one kind of object per row, named on the left:
  1. **You:** the terminal where you chat with the orchestrator, the `hive` commands, and
     the web dashboard. The commands and the dashboard talk to the server directly, using
     your admin token.
  2. **Shared state:** the hive-server, which every machine talks to, and the git origin,
     which every worker pushes to.
  3. **One daemon per machine:** the program `hive up` runs. It exchanges events and
     heartbeats with the server every few seconds. On worker machines it also starts
     (*wakes*) the agents and does their git work: commit, rebase, push.
  4. **Agent CLIs, one worktree each:** the orchestrator runs interactively in your
     terminal; the workers run headless. Each one works in its own
     [worktree](GLOSSARY.md#worktree), on its own branch (`hive/claude-2`, …).
- **Large boxes are computers.** Worker machine A shows that one computer can run several
  agents, each with its own account and worktree.
- **Arrows show who talks to whom:** grey for your connections, slate for connections to
  the shared server and repository, amber for traffic inside a machine. An agent reaches its
  daemon through Hive's [MCP tools](GLOSSARY.md#mcp-mcp-server-mcp-tool) and
  [hooks](GLOSSARY.md#hook); agents never talk to the server or to each other directly.

The diagram is drawn by `scripts/render_diagram.py`; change it there and re-run the script
to regenerate both versions.

Hive has three kinds of parts:

1. **One server** that holds the shared state.
2. **One daemon per machine** that runs that machine's agents.
3. **The agents themselves:** unmodified vendor CLIs, with Hive attached for the session.

They all share one git remote: `origin`, the shared copy of the repository every machine
pushes to (for example on GitHub). Agents never talk to the server directly, and they never talk
to each other directly either. Everything goes through the server: messages, tasks, leases
and decisions. The server is also the audit trail of what happened.

---

## The components

### The server

The server (`hive server` on your computer, or the same program as a
[WSGI](GLOSSARY.md#wsgi) web app on a host such as
[PythonAnywhere](GLOSSARY.md#pythonanywhere)) is the **single source of truth**:
- **What it stores:** agents, messages, tasks, leases, fleet state, decisions, the status
  feed, reports and agents' live output.
- **What it decides:** whether a lease is granted, which agent gets which task, and which
  state the fleet is in.
- **What it needs:** no background processes and no permanently open connections
  (WebSockets). Periodic work (expiring leases, assigning tasks, replacing an orchestrator
  that went offline) is done at the start of whichever request comes next. That is why it
  runs on cheap shared hosting.
- **The dashboard:** the server also serves the web dashboard, where you watch and steer the
  fleet ([Watching the fleet](MONITORING.md)).

One server can host [several projects](PROJECTS.md), each a completely separate fleet with its
own database and its own address (`https://host/p/<project>/`).

### The daemon

Every machine that runs agents runs one daemon: a background program started by `hive up`.
The daemon talks to the server for every [slot](#key-terms) on that machine. It:
- **polls** the server every 3 seconds for events (anything that happened in the fleet) that
  concern its slots;
- **starts agents** ("wakes" them) when something needs their attention;
- **runs git operations:** commits, rebases, checks and pushes when an agent releases its
  leases;
- **uploads live output** from its agents to the server, about once a second;
- **serves a local API**: a small web service reachable only from the machine itself
  (`127.0.0.1`), protected by a per-machine secret. The agents' tools and hooks (below) call
  this API, and the daemon forwards to the server. So agents never hold the server's address
  or talk to it directly.

### The agents

Agents are the vendors' own CLIs: **Claude Code** (`claude`), **Codex** (`codex`),
**Antigravity CLI** (`agy`) and **Gemini CLI** (`gemini`). Each one is logged in to its own
account, except that agy keeps one login per computer
([Accounts](MACHINES.md#log-each-agents-account-in-once)). Google has replaced Gemini CLI with
agy for personal accounts ([Known limitations](LIMITATIONS.md#gemini-cli-and-antigravity-cli)).
When `hive` launches one, it attaches Hive **for that session only**:
- the `hive` **MCP server**, which gives the agent its tools (messages, tasks, leases, …).
  [MCP](GLOSSARY.md#mcp-mcp-server-mcp-tool) (Model Context Protocol) is the standard way
  these CLIs accept extra tools;
- **hooks**: commands the CLI runs automatically at fixed moments, such as before every tool
  call. Hive's deliver urgent messages in the middle of a turn and refuse file writes the
  agent isn't allowed to make;
- two **skills**, written procedures the agent follows: `hive-orchestrator` and
  `hive-worker`.

A `claude`, `codex`, `agy` or `gemini` you start yourself never sees any of this
([Preparing the repository](REPOSITORY.md) explains why).

Agents come in two kinds:

- **The orchestrator** runs as the CLI's normal **interactive** session in your terminal.
  You talk to it like any session. It plans, assigns and monitors, but never writes code.
  When idle it waits inside one of Hive's tools, `await_events`, which blocks without using
  any of the account's model usage, and returns the moment something needs its attention ([Working with the orchestrator](ORCHESTRATOR.md)).
- **Workers** (programmers, reviewers, testers, …) run **headless**. The daemon starts a
  turn (a *wake*) when work arrives for them, and otherwise they don't run at all. Their output is shown live on their machine and uploaded so that
  **only you** can watch it from anywhere. The orchestrator never sees their raw output; it
  gets **reports** instead ([Tasks, messages and reports](COORDINATION.md#reports)).

---

## From a goal to commits on `main`

This is what happens in a typical run. Each step links to the page that explains it.

1. **You give the fleet a goal:** for example
   `hive start "Add GitHub OAuth login with tests"`. The fleet enters the `planning` state
   and the orchestrator is woken ([Starting](LIFECYCLE.md#starting)).
2. **The orchestrator plans.** It studies the repository, may ask you clarifying questions,
   and creates tasks. Until you approve, they are only drafts.
3. **You approve the plan** (`hive decide <id> approve`, where `<id>` is the decision's
   number from `hive decisions`; or the dashboard). The drafts become
   open tasks and the fleet enters `running` ([Planning and approval](LIFECYCLE.md#planning-and-approval)).
4. **The server assigns tasks.** Each open task whose dependencies are done goes to an idle,
   online agent whose role handles its kind ([Tasks](COORDINATION.md#tasks)).
5. **The assignee's daemon wakes it.** The agent's CLI starts a headless turn with the task in
   its prompt (the instructions it's given).
6. **The agent leases the files it needs**, edits them, and runs tests. Hooks refuse any write
   outside its leases ([Leases](INTEGRATION.md#leases)).
7. **It releases its leases.** The daemon commits exactly the leased files, rebases onto
   `origin/main` (the shared copy's `main`), runs your checks (the test commands you set in
   `hive.toml`) and pushes. With reviews switched on, a reviewer approves first
   ([Reviews](INTEGRATION.md#reviews)).
8. **`main` moves.** Every other slot whose worktree has no uncommitted changes rebases onto
   the new `main` at once; the others do so at their next release.
9. **The agent completes its task and files a report.** The orchestrator checks the report
   against the repository and publishes a status update for you
   ([How it keeps you informed](ORCHESTRATOR.md#how-it-keeps-you-informed)).
10. **If an agent gets stuck** and only you can unblock it, the orchestrator asks you, and
    **the whole fleet freezes** until you answer ([Stuck agents and freezing](LIFECYCLE.md#stuck-agents-and-freezing)).

Meanwhile you can watch any agent's live output from anywhere with `hive watch` or the
dashboard ([Watching the fleet](MONITORING.md)).

---

## Two rules that always hold

1. **Nobody codes until you approve the plan.** Before approval, only the orchestrator and
   planners may act, and the tasks they create stay drafts.
2. **When an agent is stuck and only you can resolve it, the whole fleet freezes until you
   answer.** Agents stop, the clocks on their leases pause, and nothing reaches `main`.

Neither rule depends on the agents behaving. The server refuses leases, task claims and
integrations in the wrong state, and the hooks refuse the tool calls.

---

## How rules are enforced

Hive never relies on a prompt alone for anything that matters. Each rule is enforced in up to
three places:

| Layer | What it checks | If it fails |
|---|---|---|
| **Server** | Every action against the caller's roles and the fleet state; at integration, that the agent still holds its leases (each carries a serial *fencing number* that becomes invalid if the lease is taken away). | The request is refused: HTTP error `403` (the role may not do this) or `423` (not allowed in the current fleet state) |
| **Hooks and git** | File writes against leases; shell commands against the [permission tiers](PERMISSIONS.md) (allowed, needs approval, never). A git `pre-commit` hook, which git runs before every commit, re-checks the files being committed. | The tool call or commit is refused with an explanation |
| **Instructions** | The role's prompt and the skills explain the rules, so agents rarely hit the other two. | – |

See [Roles](ROLES.md#how-roles-are-enforced), [Shell-command permissions](PERMISSIONS.md) and
the [Security model](SECURITY.md).

---

## Accounts and vendor terms

Hive uses no vendor API keys (the paid, per-request access some vendors sell). It drives the official CLIs through their documented features:
interactive and headless modes, MCP servers, hooks, and plugins/extensions. It never extracts
or proxies account credentials. Each account is used only by its own CLI, from its own
private config folder. Check each vendor's terms for your own situation, for example how many
accounts one person may run.

---

**Next:** [Requirements and installation](INSTALLATION.md) ·
**See also:** [Timings and limits](CONFIGURATION.md#timings-and-limits) ·
[← Back to the README](../README.md)
