# Roles

Each AI agent in a Hive fleet (the team working on your repository) has one or more roles. A
role says what an agent is allowed to do and what it is expected to do:
- which files it may edit;
- which of Hive's tools it gets ([MCP tools](AGENT_INTERFACE.md#mcp-tools-hive-server));
- which kinds of [task](COORDINATION.md#tasks) it takes: each task has a kind such as `code`,
  `test`, `docs`, `review` or `plan`;
- in which [fleet states](LIFECYCLE.md) (phases such as `planning` or `running`) it may work.

You give each agent its roles when you create it with `hive admin enroll`
([Enrolling agents](MACHINES.md#enroll-each-agent-on-your-computer)). The Hive server uses
roles to assign tasks and to refuse anything an agent's roles don't allow.

Hive comes with five built-in roles. You can change them, or add your own, in `hive.toml`,
the project's Hive settings file in the repository.

---

## Built-in roles

In the table, *holders* are the agents that have the role. *Leased paths* are files the agent
has locked for itself first with a [lease](INTEGRATION.md#leases), so no other agent edits them
at the same time. Paths are written as patterns ([globs](GLOSSARY.md#glob)): `.hive/**` means
everything in the repository's `.hive/` folder.

| Role | Required | Holders | May edit | Takes task kinds | If nobody holds it |
|---|---|---|---|---|---|
| **orchestrator** | yes | exactly 1 | only `.hive/**` | (fallback for plan, review) | – |
| **programmer** | yes | ≥ 1 | leased paths | code, test, docs | – |
| planner | no | any | `.hive/plans/**` | plan | orchestrator takes plan tasks |
| reviewer | no | any | nothing | review | orchestrator reviews |
| tester | no | any | leased paths plus `tests/**` | test | programmers take test tasks |

- **orchestrator:** plans, assigns, monitors and talks to you. Never codes
  ([Working with the orchestrator](ORCHESTRATOR.md)). As a fallback it also takes `plan` and
  `review` tasks when no planner or reviewer exists.
- **programmer:** the workhorse. Leases files, edits them, and merges its work into the
  repository's `main` branch ([Leases, git integration and reviews](INTEGRATION.md)).
- **planner:** helps the orchestrator plan. Active during the `planning` phase.
- **reviewer:** reviews other agents' work before it reaches `main`, when reviews are on
  ([Reviews](INTEGRATION.md#reviews)). Reviewers also decide other agents' requests to run
  commands that need approval, before the orchestrator
  ([How approval works](PERMISSIONS.md#how-approval-works-for-workers)). Any role you define
  with `approval_decide` in its `tools` does the same.
- **tester:** writes and runs tests.

Combining roles:
- An agent may hold several roles, e.g. `reviewer,planner`.
- `orchestrator` can't be combined with any role that codes.

**Fallbacks** keep the fleet small. You don't need a reviewer, planner or tester: if no agent
has the role, its tasks go to the role named in "If nobody holds it". The smallest fleet that can
start is one orchestrator and one programmer.

---

## Custom roles and overrides

Define roles in `hive.toml`, in a section called `[roles.<name>]` (for a role called
`docs-writer`, `[roles.docs-writer]`), then upload the settings to the server with
`hive admin push-config`. A role you define yourself starts with only the tools every agent
has: it can't edit files and takes no tasks. Give it what it needs with these fields:

| Field | Type | Meaning |
|---|---|---|
| `mandatory` | true/false | The fleet won't start without an agent holding this role. |
| `min`, `max` | number | Allowed number of agents holding it. |
| `can_code` | true/false | May take leases and edit the leased files. |
| `write_globs` | list of path patterns | Paths it may edit without a lease, e.g. `["docs/**"]`. |
| `tools` | list of tool names, or `"*"` for all | Hive tools it gets on top of the ones every agent has ([MCP tools](AGENT_INTERFACE.md#mcp-tools-hive-server)). |
| `task_kinds` | list of kinds | Task kinds this role takes. |
| `fallback_to` | a role name | Who takes this role's tasks when no agent holds it. |
| `wake_on` | list of event names | Things that happen in the fleet (events, such as `task.assigned`) that start an idle agent with this role. |
| `active_in` | list of fleet states | Fleet states in which agents with this role may work (default `["running"]`). |
| `prompt` | text | Instructions given to every agent with this role. |

Example: a role that writes documentation and nothing else. It may lease files
(`can_code`), takes `docs` tasks, and gets the three lease tools it needs to lock files and
merge its work:

```toml
[roles.docs-writer]
can_code = true
task_kinds = ["docs"]
tools = ["lease_acquire", "lease_extend", "lease_release"]
prompt = "You write and maintain user documentation under docs/."
```

Once an agent holds `docs-writer`, the server sends `docs` tasks to it before falling back
to programmers.

Built-in roles can be overridden the same way, e.g. `[roles.reviewer] prompt = "…"`. A
`prompt` given here **replaces** the role's built-in prompt. To **add** to it instead, or to
write longer instructions than fit in `hive.toml`, use `.hive/prompts/roles/<role>.md`
([Customising what agents are told](PROMPTS.md)).

`hive overview` lists every role, where its definition and prompt come from, and any errors
in `[roles]` ([Checking a project's setup](REPOSITORY.md#checking-a-projects-setup-hive-overview)).

---

## Changing roles

- **You:** `hive admin roles claude-3 reviewer` gives the agent `claude-3` (an example name)
  the roles listed, replacing its old ones.
- **The orchestrator:** with its `role_reassign` tool. Changes to optional roles apply
  immediately. Changes that add or remove a required role become a question for you to answer
  (a [decision](GLOSSARY.md#decision)) that doesn't stop the fleet.
- **Standby orchestrator:** list agents that should take over if the orchestrator becomes
  unavailable, in `hive.toml`'s `[orchestrator]` section, e.g. `standby = ["claude-2"]`:
  - **When:** the current orchestrator's account has used up its allowance (quota), or it
    hasn't contacted the server for 5 minutes.
  - **What happens:** the first available standby becomes orchestrator; its coding roles move
    to the old orchestrator, and its open tasks are released for reassignment.
  - **You're told:** a status update with level `alert` appears in `hive status` and the
    dashboard.

---

## How roles are enforced

Enforcement has three layers:
1. **Server:** every action is checked against the caller's roles. Forbidden actions are
   refused (HTTP error 403). Actions not allowed in the current fleet state are refused too
   (HTTP error 423).
2. **[Hooks](GLOSSARY.md#hook)** (checks Hive runs inside the agent's CLI before every tool
   call): edits outside the agent's leases and `write_globs` are refused. While the agent's
   roles may not work in the current fleet state, only tools that read are allowed.
3. **Instructions:** the role's prompt and the agent's [skill](GLOSSARY.md#skill) (its
   written procedure) explain the rules, so agents rarely hit the other two.

Only the tools an agent's roles permit are registered in its session, so it doesn't even see
the others ([What the agents see](AGENT_INTERFACE.md)).

---

**Next:** [Tasks, messages and reports](COORDINATION.md) ·
**See also:** [The fleet lifecycle](LIFECYCLE.md#what-each-role-may-do-in-each-state),
[`hive.toml` reference](CONFIGURATION.md#hivetoml) ·
[← Back to the README](../README.md)
