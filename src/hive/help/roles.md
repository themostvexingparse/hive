---
title: Roles: what each agent may and should do
keywords: role, orchestrator, programmer, planner, reviewer, tester, custom role, custom roles, standby, failover, can_code, write_globs, task_kinds, active_in, wake_on, fallback_to
see: agents, permissions, review, prompts, lifecycle
---
# Roles

Each agent has one or more roles. A role says which files it may edit, which of Hive's tools
it gets, which kinds of task it takes, and in which fleet states it may work. The server
assigns tasks by role and refuses anything an agent's roles don't allow.

## Built-in roles

- **orchestrator** (exactly one): plans, assigns, monitors and talks to you. Never codes;
  may edit only `.hive/**`. Takes plan and review tasks when nobody else holds those roles.
- **programmer** (at least one): leases files, edits them and merges its work into `main`.
  Takes code, test and docs tasks.
- **planner**: helps the orchestrator plan; edits only `.hive/plans/**`.
- **reviewer**: reviews other agents' work before it reaches `main` when reviews are on, and
  decides other agents' requests to run commands that need approval. Edits nothing.
- **tester**: writes and runs tests; edits leased paths and `tests/**`.

An agent may hold several roles (`reviewer,planner`), but `orchestrator` can't be combined
with a role that codes. The smallest fleet that can start is one orchestrator and one
programmer: without a planner, reviewer or tester, their tasks fall back to another role.

```sh
hive agents add gemini reviewer,planner
hive agents edit claude-3 --roles programmer,tester     # replaces its roles
```

While the fleet runs, a change that adds or removes a required role becomes a decision for
you to approve.

## Your own roles, and overrides

In `hive.toml`, a `[roles.<name>]` section defines a role, or changes a built-in one:

```toml
[roles.docs-writer]
can_code = true                    # may take leases and edit leased files
task_kinds = ["docs"]
tools = ["lease_acquire", "lease_extend", "lease_release"]
prompt = "You write and maintain user documentation under docs/."
```

Fields: `mandatory`, `min`, `max`, `can_code`, `write_globs` (paths it may edit without a
lease), `tools` (Hive tools on top of the basic ones, or `"*"`), `task_kinds`, `fallback_to`,
`wake_on` (events that wake it), `active_in` (fleet states it works in; default
`["running"]`) and `prompt` (replaces the built-in prompt; to add to it instead, use
`.hive/prompts/roles/<role>.md`, `hive help prompts`). A new role starts with only the basic
tools: it can't edit files and takes no tasks until you say so.

## A standby orchestrator

```sh
hive config set orchestrator.standby '["claude-2"]'
```

If the orchestrator's account runs out of quota, or it hasn't contacted the server for 5
minutes, the first available standby takes over; you get an alert in `hive status`.
