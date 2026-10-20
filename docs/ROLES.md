# Roles

A role says what an agent is allowed to do and what it is expected to do: which files it may
edit, which tools it gets, which kinds of task it takes, and in which fleet states it may
work. You give each agent one or more roles when you enroll it. The server uses roles to
assign tasks and to refuse anything an agent's roles don't allow.

Hive comes with five built-in roles. You can change them, or add your own, in `hive.toml`.

---

## Built-in roles

| Role | Required | Holders | May edit | Takes task kinds | If nobody holds it |
|---|---|---|---|---|---|
| **orchestrator** | yes | exactly 1 | only `.hive/**` | (fallback for plan, review) | – |
| **programmer** | yes | ≥ 1 | leased paths | code, test, docs | – |
| planner | no | any | `.hive/plans/**` | plan | orchestrator takes plan tasks |
| reviewer | no | any | nothing | review | orchestrator reviews |
| tester | no | any | leased paths plus `tests/**` | test | programmers take test tasks |

- **orchestrator:** plans, assigns, monitors and talks to you. Never codes
  ([Working with the orchestrator](ORCHESTRATOR.md)).
- **programmer:** the workhorse. Leases files, edits them, and integrates its work into `main`.
- **planner:** helps the orchestrator plan. Active during `planning`.
- **reviewer:** reviews other agents' work before it reaches `main`, when reviews are on
  ([Reviews](INTEGRATION.md#reviews)).
- **tester:** writes and runs tests.

Combining roles:
- An agent may hold several roles, e.g. `reviewer,planner`.
- `orchestrator` can't be combined with any role that codes.

**Fallbacks** keep the fleet small. You don't need a reviewer or a planner: if nobody holds
the role, its tasks go to the role named in "If nobody holds it". The smallest fleet that can
start is one orchestrator and one programmer.

---

## Custom roles and overrides

Define roles in `hive.toml` under `[roles.<name>]`, then upload the config with
`hive admin push-config`.

| Field | Type | Meaning |
|---|---|---|
| `mandatory` | bool | The fleet won't start without a holder. |
| `min`, `max` | int | Allowed number of holders. |
| `can_code` | bool | May take leases and edit leased paths. |
| `write_globs` | [glob] | Paths editable without a lease. |
| `tools` | [name] or `"*"` | Extra MCP tools on top of the base set ([MCP tools](AGENT_INTERFACE.md#mcp-tools-hive-server)). |
| `task_kinds` | [kind] | Task kinds this role accepts. |
| `fallback_to` | role | Who covers this role's task kinds when nobody holds it. |
| `wake_on` | [event] | Events that wake an idle holder. |
| `active_in` | [state] | Fleet states in which holders may work (default `["running"]`). |
| `prompt` | string | Role instructions given to the agent. |

Example: a role that writes documentation and nothing else.

```toml
[roles.docs-writer]
can_code = true
task_kinds = ["docs"]
tools = ["lease_acquire", "lease_extend", "lease_release"]
prompt = "You write and maintain user documentation under docs/."
```

Once an agent holds `docs-writer`, the scheduler sends `docs` tasks to it before falling back
to programmers.

Built-in roles can be overridden the same way, e.g. `[roles.reviewer] prompt = "…"`. A
`prompt` given here **replaces** the role's built-in prompt. To **add** to it instead, or to
write longer instructions than fit in `hive.toml`, use `.hive/prompts/roles/<role>.md`
([Customising what agents are told](PROMPTS.md)).

`hive overview` lists every role, where its definition and prompt come from, and any errors
in `[roles]` ([Checking a project's setup](REPOSITORY.md#checking-a-projects-setup-hive-overview)).

---

## Changing roles

- **You:** `hive admin roles claude-3 reviewer`.
- **The orchestrator:** `role_reassign`. Changes to optional roles apply immediately. Changes
  that add or remove a mandatory role become a (non-freezing) decision for you.
- **Standby orchestrator** (`[orchestrator] standby = ["claude-2"]`):
  - **When:** the current orchestrator is out of quota, or hasn't been seen for 5 minutes.
  - **What happens:** the first available standby becomes orchestrator; its coding roles move
    to the old orchestrator, and its open tasks are released for reassignment.
  - **You're told:** an `alert` status entry is published.

---

## How roles are enforced

Enforcement has three layers:
1. **Server:** every action is checked against the caller's roles. Forbidden calls fail with
   403. Actions not allowed in the current fleet state fail with 423.
2. **Hooks:** before every tool call, writes outside the agent's leases and `write_globs` are
   denied. While the agent's roles are inactive, only read-only tools are allowed.
3. **Instructions:** the role prompt and skill explain the rules, so agents rarely hit the
   other two.

Only the tools an agent's roles permit are registered in its session, so it doesn't even see
the others ([What the agents see](AGENT_INTERFACE.md)).

---

**Next:** [Tasks, messages and reports](COORDINATION.md) ·
**See also:** [The fleet lifecycle](LIFECYCLE.md#what-each-role-may-do-in-each-state),
[`hive.toml` reference](CONFIGURATION.md#hivetoml) ·
[← Back to the README](../README.md)
