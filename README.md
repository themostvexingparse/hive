<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/images/banner-dark.png">
  <source media="(prefers-color-scheme: light)" srcset="docs/images/banner-light.png">
  <img alt="Hive: several coding agents, one coordinated team" src="docs/images/banner-light.png" width="100%">
</picture>

# Hive

**Turn several subscription-based coding agents into one coordinated team.**

Hive connects **Claude Code**, **Gemini CLI** and **Codex** agents, each running as the
vendor's official CLI on a computer you own and logged in to its own account, into one fleet
that works on one repository. A non-coding **orchestrator** plans the work and keeps you
informed; **workers** pick up tasks, lock the files they edit, and merge their work into
`main` once your checks pass. You stay in charge throughout: you approve the plan, answer
the questions only you can answer, and watch everything live.

---

## What it does

- **One shared repository.** Every agent works in its own git worktree and branch. Finished
  work is committed, rebased, checked and pushed to `main` automatically, and every other
  agent syncs to the new `main`.
- **A task board with automatic assignment.** The orchestrator splits your goal into tasks;
  the server hands each one to an idle agent whose role fits it.
- **File leases.** An agent must lock (lease) every path it edits, so two agents never edit
  the same files at once. Hooks refuse any other write.
- **A message board.** Channels, direct messages and priorities, up to interrupts that reach
  an agent in the middle of its turn.
- **Roles.** Orchestrator, programmer, planner, reviewer and tester are built in. You can
  change them or add your own.
- **Safe shell access.** Routine commands run directly; dangerous ones (`git push`,
  `rm -rf`, `sudo`, …) need the orchestrator's approval; and commands that would let an agent
  act as you are impossible.
- **Live visibility, for you only.** Follow every agent's output from anywhere with
  `hive watch` or the web dashboard. Agents never see each other's output, only reports.
- **Mixed fleets.** Windows, macOS and Linux machines, and several accounts per machine. One
  server can host several projects.
- **Cheap to host.** The server is a plain WSGI app with no background workers: it runs on a
  PythonAnywhere plan or any small VPS.

### Two rules that always hold

1. **Nobody codes until you approve the plan.**
2. **When an agent is stuck and only you can resolve it, the whole fleet freezes until you
   answer.**

Both are enforced by the server and by hooks, not by asking the models nicely.

### No API keys

Hive uses no vendor API keys. It drives the official CLIs through their documented features:
interactive and headless modes, MCP servers, hooks, and plugins/extensions. It never extracts
or proxies account credentials, and each account is used only by its own CLI. Check each
vendor's terms for your own situation, for example how many accounts one person may run.

---

## How it works

```
 you ──chat──▶ orchestrator (interactive CLI)      workers (headless CLIs)
                      │                                   │
                      └──── daemon (hive up) ─── daemon ──┘    one per machine
                                    │                │
                                    ▼                ▼
                         hive-server: tasks · messages · leases · decisions
                                    │
                               git origin (main)
```

- **The server** is the single source of truth: agents, tasks, messages, leases, fleet state,
  decisions, reports, live output, and the web dashboard.
- **A daemon** on each machine (`hive up`) polls the server, wakes its agents when something
  concerns them, and runs their git work.
- **The agents** are the unmodified vendor CLIs. Hive attaches its tools, hooks and
  instructions to the sessions it launches, and to no others.

➜ The full picture, a glossary, and the path from a goal to commits on `main`:
[How Hive works](docs/ARCHITECTURE.md).

---

## Quick start

A trial on one computer, with one orchestrator and one programmer:

```sh
hive server --admin-token <token>                                 # 1. a local server
hive admin login --server http://127.0.0.1:8765 --token <token>   # 2. in another terminal

cd ~/code/myapp && hive init                                      # 3. prepare the repository
git add -A && git commit -m "Wire for Hive" && git push && hive admin push-config

hive admin enroll orch --vendor claude --roles orchestrator       # 4. two agents
hive admin enroll dev  --vendor claude --roles programmer
hive join orch --vendor claude --token <orch token> --repo ~/code/myapp
hive join dev  --vendor claude --token <dev token>  --repo ~/code/myapp
#   log each slot's account in once (hive join prints the command)

hive up                                                           # 5. the orchestrator opens here
hive start "Add CSV export to the reports page"                   # 6. in another terminal
hive decide 1 approve                                             #    approve the plan
```

➜ Step by step, with what each step does: [Getting started](docs/GETTING_STARTED.md).

---

## Documentation

### Getting started

| Guide | What it covers |
|---|---|
| [Getting started](docs/GETTING_STARTED.md) | A first fleet on one computer, from installing to approving the first plan, with what each step does. |
| [How Hive works](docs/ARCHITECTURE.md) | The server, daemons and agents; a glossary; how a goal becomes commits on `main`; how rules are enforced. |
| [Requirements and installation](docs/INSTALLATION.md) | What each machine needs, the installer scripts and their options, and why `hive` must be on PATH. |

### Setting up

| Guide | What it covers |
|---|---|
| [Running the server](docs/SERVER.md) | The admin token and database, running locally, and logging in as admin. |
| [Deploying the server](docs/DEPLOYMENT.md) | Hosting on PythonAnywhere or a VPS: capacity planning with measured numbers, upgrades, backups, monitoring and hardening. |
| [Several projects on one server](docs/PROJECTS.md) | Hosting several repositories and fleets on one server, each with its own database and admin token. |
| [Preparing the repository](docs/REPOSITORY.md) | What `hive init` creates and why it doesn't affect your own CLI sessions; checking everything with `hive overview`. |
| [Enrolling agents and setting up machines](docs/MACHINES.md) | Creating agents, adding them to machines as slots with their own worktrees, and logging their accounts in. |

### Running a fleet

| Guide | What it covers |
|---|---|
| [Running the fleet](docs/RUNNING.md) | `hive up` on the orchestrator's machine and on workers; how a headless turn is launched; taking over a slot with `hive chat`. |
| [Working with the orchestrator](docs/ORCHESTRATOR.md) | Chatting with it, answering its decisions, its status updates, and what it can and can't do. |
| [Watching the fleet](docs/MONITORING.md) | Live output with `hive watch`, and the web dashboard: every panel, filters, and archives of past runs. |
| [The fleet lifecycle](docs/LIFECYCLE.md) | Fleet states from `setup` to `running`: plan approval, freezing on stuck agents, pausing and resetting. |

### How the fleet coordinates

| Guide | What it covers |
|---|---|
| [Roles](docs/ROLES.md) | The built-in roles, custom roles and overrides in `hive.toml`, standby orchestrators, and how roles are enforced. |
| [Tasks, messages and reports](docs/COORDINATION.md) | The task board and scheduler, channels and message priorities, and how workers report to the orchestrator. |
| [Leases, git integration and reviews](docs/INTEGRATION.md) | File leases, worktrees and branches, every step of getting work into `main`, GitHub mode, and reviews. |

### Agents and safety

| Guide | What it covers |
|---|---|
| [What the agents see](docs/AGENT_INTERFACE.md) | The MCP tools each role gets, the hooks that deliver messages and enforce leases, and the skills. |
| [Shell-command permissions](docs/PERMISSIONS.md) | The three command tiers (allowed, approved by the orchestrator, never), how approvals work, and how to customise them. |
| [Customising what agents are told](docs/PROMPTS.md) | Adding your project's conventions to the agents' instructions with `.hive/prompts/`, placeholders, and previewing the result. |
| [Security model](docs/SECURITY.md) | Tokens and what each can do, who sees what, secrets in output, prompt injection, and a checklist. |

### Reference

| Reference | What it covers |
|---|---|
| [The `hive` command](docs/CLI.md) | Every command with examples, common recipes, error messages, and a one-table command summary. |
| [Configuration reference](docs/CONFIGURATION.md) | `hive.toml`, `machine.toml`, environment variables, file locations, timings and limits. |
| [Troubleshooting](docs/TROUBLESHOOTING.md) | Symptoms and fixes, grouped by starting, tools, integration and vendor CLIs. |
| [Known limitations](docs/LIMITATIONS.md) | What has been verified against which CLI versions, what hasn't yet, and current limitations. |
| [Development](docs/DEVELOPMENT.md) | Running the tests, the source layout, and the design and audit documents. |
| [Specification](SPEC.md) | The design rationale and full specification. |

---

## Status

Hive is under active development. The server, daemon, leases, git integration and dashboard
are covered by unit and end-to-end tests, and Codex support is tested end to end against the
real CLI. Some integration points haven't yet been exercised with live, logged-in accounts;
[Known limitations](docs/LIMITATIONS.md) lists them.
