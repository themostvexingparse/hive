# How Hive works

This page explains Hive's moving parts, the words the rest of the documentation uses, and
how a goal you type turns into commits on `main`. Read it first if you want to understand
what you are setting up before you set it up. The design rationale and full specification are
in [SPEC.md](../SPEC.md).

---

## The big picture

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

Hive has three kinds of parts:

1. **One server** that holds the shared state.
2. **One daemon per machine** that runs that machine's agents.
3. **The agents themselves:** unmodified vendor CLIs, with Hive attached for the session.

They all share one git remote. Agents never talk to the server directly, and they never talk
to each other directly either. Everything goes through the server: messages, tasks, leases
and decisions. The server is also the audit trail of what happened.

---

## The components

### The server

The server (`hive server`, or a WSGI app on any host such as PythonAnywhere) is the **single
source of truth**:
- **What it stores:** agents, messages, tasks, leases, fleet state, decisions, the status
  feed, reports and agents' live output.
- **What it decides:** whether a lease is granted, which agent gets which task, and which
  state the fleet is in.
- **What it needs:** no background workers and no WebSockets. Periodic work (lease expiry,
  scheduling, failover) is done lazily at the start of requests. That is why it runs on cheap
  shared hosting.
- **The dashboard:** the server also serves the web dashboard, where you watch and steer the
  fleet ([Watching the fleet](MONITORING.md)).

One server can host [several projects](PROJECTS.md), each a completely separate fleet with its
own database.

### The daemon

Every machine that runs agents runs one daemon (`hive up`). The daemon talks to the server for
every agent **slot** on that machine. It:
- **polls** the server every 3 seconds for events that concern its slots;
- **starts agents** ("wakes" them) when something needs their attention;
- **runs git operations:** commits, rebases, checks and pushes when an agent releases its
  leases;
- **uploads live output** from its agents to the server, about once a second;
- **serves a local API** on `127.0.0.1`, protected by a per-machine secret. The agents' MCP
  tools and hooks call this API, and the daemon forwards to the server.

### The agents

Agents are the vendors' own CLIs: **Claude Code** (`claude`), **Gemini CLI** (`gemini`) and
**Codex** (`codex`). Each one is logged in to its own account. When `hive` launches one, it
attaches Hive **for that session only**:
- the `hive` **MCP server**, which gives the agent its tools (messages, tasks, leases, …);
- **hooks**, which deliver messages mid-turn and refuse file writes the agent isn't allowed
  to make;
- two **skills**: `hive-orchestrator` and `hive-worker`, the operating procedures.

A `claude`, `gemini` or `codex` you start yourself never sees any of this
([Preparing the repository](REPOSITORY.md) explains why).

Agents come in two kinds:

- **The orchestrator** runs as the **native interactive CLI** in your terminal. You talk to it
  like any session. It plans, assigns and monitors, but never writes code. When idle it waits
  inside the `await_events` tool, which uses no tokens, and returns the moment something needs
  its attention ([Working with the orchestrator](ORCHESTRATOR.md)).
- **Workers** (programmers, reviewers, testers, …) run **headless**. The daemon starts a turn
  when work arrives for them. Their output is shown live on their machine and uploaded so that
  **only you** can watch it from anywhere. The orchestrator never sees their raw output; it
  gets **reports** instead ([Tasks, messages and reports](COORDINATION.md#reports)).

---

## Glossary

| Term | Meaning |
|---|---|
| **Fleet** | All the agents of one project, working towards one goal. |
| **Slot** | One agent identity on one machine: an account (a private CLI config folder) plus its own git worktree on branch `hive/<slot>`. One machine can host many slots. "Agent" and "slot" are used interchangeably. |
| **Role** | What an agent is allowed and expected to do: `orchestrator`, `programmer`, `planner`, `reviewer`, `tester`, or your own ([Roles](ROLES.md)). |
| **Task** | A unit of work with a kind (`code`, `test`, `review`, …), a spec and dependencies. The server assigns open tasks to agents by role ([Tasks](COORDINATION.md#tasks)). |
| **Lease** | A time-limited, exclusive lock on a set of paths. An agent must hold a lease on every file it edits ([Leases](INTEGRATION.md#leases)). |
| **Integration** | Getting an agent's work into `main`: commit, rebase, run checks, push. Triggered by releasing leases ([What `lease_release` does](INTEGRATION.md#what-lease_releasecommit_message-task_id-does)). |
| **Fleet state** | `setup`, `planning`, `awaiting_approval`, `running`, `frozen` or `paused`. It decides who may work ([The fleet lifecycle](LIFECYCLE.md)). |
| **Decision** | A question the orchestrator asks *you*. Only you can answer it. Some decisions freeze the fleet until you do. |
| **Wake** | One headless turn of a worker's CLI, started by its daemon because something concerns it ([How an agent is launched](RUNNING.md#how-an-agent-is-launched-for-a-headless-turn-wake)). |
| **Report** | A worker's written summary of what it did, filed for the orchestrator. |
| **Status update** | The orchestrator's summary for you, shown in `hive status` and the dashboard. |
| **Approval** | A worker's request to run a dangerous shell command, decided by the orchestrator ([Shell-command permissions](PERMISSIONS.md)). |
| **Bundle** | The per-CLI package `hive init` writes to `.hive/agent/`: a Claude Code plugin, a Gemini CLI extension, or a Codex profile. |

---

## From a goal to commits on `main`

This is what happens in a typical run. Each step links to the page that explains it.

1. **You give the fleet a goal:** `hive start "Add GitHub OAuth login with tests"`. The fleet
   enters `planning` and the orchestrator is woken ([Starting](LIFECYCLE.md#starting)).
2. **The orchestrator plans.** It studies the repository, may ask you clarifying questions,
   and creates tasks. Until you approve, they are only drafts.
3. **You approve the plan** (`hive decide <id> approve`, or the dashboard). The drafts become
   open tasks and the fleet enters `running` ([Planning and approval](LIFECYCLE.md#planning-and-approval)).
4. **The server assigns tasks.** Each open task whose dependencies are done goes to an idle,
   online agent whose role handles its kind ([Tasks](COORDINATION.md#tasks)).
5. **The assignee's daemon wakes it.** The agent's CLI starts a headless turn with the task in
   its prompt.
6. **The agent leases the files it needs**, edits them, and runs tests. Hooks refuse any write
   outside its leases ([Leases](INTEGRATION.md#leases)).
7. **It releases its leases.** The daemon commits exactly the leased files, rebases onto
   `origin/main`, runs your checks and pushes. With review on, a reviewer approves first
   ([Reviews](INTEGRATION.md#reviews)).
8. **`main` moves.** Every other slot with a clean worktree rebases onto the new `main` at once.
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
   answer.** Agents stop, lease timers pause, and nothing integrates.

Neither rule depends on the agents behaving. The server refuses leases, task claims and
integrations in the wrong state, and the hooks refuse the tool calls.

---

## How rules are enforced

Hive never relies on a prompt alone for anything that matters. Each rule is enforced in up to
three places:

| Layer | What it checks | If it fails |
|---|---|---|
| **Server** | Every action against the caller's roles and the fleet state; lease ownership and fencing numbers at integration. | `403` (role) or `423` (fleet state) |
| **Hooks and git** | File writes against leases, shell commands against the permission tiers; the git `pre-commit` hook re-checks staged files. | The tool call or commit is refused with an explanation |
| **Instructions** | Role prompts and skills explain the rules, so agents rarely hit the other two. | – |

See [Roles](ROLES.md#how-roles-are-enforced), [Shell-command permissions](PERMISSIONS.md) and
the [Security model](SECURITY.md).

---

## Accounts and vendor terms

Hive uses no vendor API keys. It drives the official CLIs through their documented features:
interactive and headless modes, MCP servers, hooks, and plugins/extensions. It never extracts
or proxies account credentials. Each account is used only by its own CLI, from its own
private config folder. Check each vendor's terms for your own situation, for example how many
accounts one person may run.

---

**Next:** [Requirements and installation](INSTALLATION.md) ·
**See also:** [Timings and limits](CONFIGURATION.md#timings-and-limits) ·
[← Back to the README](../README.md)
