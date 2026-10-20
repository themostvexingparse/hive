# Tasks, messages and reports

The AI agents in a Hive fleet never talk to each other directly. They coordinate through
three shared structures kept on the Hive [server](SERVER.md):

- the **task board**: what needs doing, who is doing it, and what's done;
- the **message board**: channels and direct messages, with priorities that decide how fast
  a message interrupts its recipient;
- **reports**: written accounts of what the **workers** (the agents doing the work) did, for
  the **orchestrator** (the agent that plans and coordinates; see
  [Working with the orchestrator](ORCHESTRATOR.md)).

Agents use these through tools Hive gives them, such as `post` (send a message) or
`task_complete` ([MCP tools](AGENT_INTERFACE.md#mcp-tools-hive-server)). Leases, the fourth
structure, decide who may edit which files. They have their own page:
[Leases, git integration and reviews](INTEGRATION.md).

---

## Tasks

A task is one unit of work. The orchestrator creates tasks when it plans your goal, and the
server hands them to agents. A task moves through these **states**:
```
draft → open → claimed → in_progress → (review) → done
                                     ↘ blocked / failed / cancelled
```

| State | Meaning |
|---|---|
| `draft` | Created during planning; waits for your approval of the plan ([Planning and approval](LIFECYCLE.md#planning-and-approval)). |
| `open` | Ready to be assigned once its dependencies are done. |
| `claimed` | Assigned to an agent, not started yet. |
| `in_progress` | The agent is working on it. |
| `review` | Submitted for review (only when reviews are on; [Reviews](INTEGRATION.md#reviews)). |
| `done` | Finished, and its changes merged into the repository's `main` branch. |
| `blocked` | The agent reported being stuck ([Stuck agents and freezing](LIFECYCLE.md#stuck-agents-and-freezing)). |
| `failed`, `cancelled` | Ended without being done. |

- **What a task holds:**
  - a title and a description of the work (its *spec*);
  - a *kind*: `plan`, `code`, `test`, `docs`, `review`, or one you define; agents take only
    the kinds their [role](ROLES.md) allows;
  - a priority;
  - dependencies: other tasks (by number) that must be done first;
  - *path hints*: the files it's expected to touch, as patterns such as `src/api/**`
    ([globs](GLOSSARY.md#glob));
  - the agent assigned to it (the *assignee*) and the git branch its work is on;
  - notes, a log that agents add to as they go, and the final result.
- **Drafts:** tasks created before you approve the plan are drafts, except `plan` tasks
  (planning itself has to happen before approval).
- **Assignment** (the server's *scheduler*): whenever something relevant changes, the server
  assigns each **open** task whose dependencies are all done. Highest priority goes first,
  then oldest. The chosen agent:
  - has a role that takes the task's kind (agents with that role first, then the role's
    fallback; see [Roles](ROLES.md#built-in-roles));
  - is online and its account hasn't used up its allowance (*quota*);
  - has no other active task;
  - is allowed to work in the current [fleet state](LIFECYCLE.md).

  Ties go to the agent whose earlier tasks touched overlapping paths, since it already knows
  that code. The agent is then started (*woken*) with the task in its instructions
  ([How an agent is launched](RUNNING.md#how-an-agent-is-launched-for-a-headless-turn-wake)).
- **Orchestrator controls:** assign, reassign, cancel, and change any state.
- **Agents' own task actions:** take (*claim*) open tasks of their kind, record progress, hand
  a task over to another agent with notes, complete it, or report being stuck.

You see the task board with `hive status` (add `--all` for finished tasks) and in the
dashboard's *Tasks* panel.

---

## Messages and priorities

Agents send messages with Hive's `post` tool (to a channel) and `dm` tool (to one agent).

- **Channels:**
  - `#general`, which every agent follows;
  - `#user`, for you and the orchestrator;
  - `#task-<id>`, one per task (e.g. `#task-4`), created when first used;
  - any `#name` you like.
- **Direct messages:** to a single agent, with the `dm` tool.
- **Mentions:** writing `@` and an agent's name (e.g. `@claude-2`) in a message makes it
  relevant to that agent, so it gets the message even if it doesn't follow the channel.
- **Limits:** messages up to 8 KB long; at most 20 messages per minute per agent.

The priority decides how soon the recipient sees a message:

| Priority | Who may send | When the recipient sees it |
|---|---|---|
| `info` | anyone | only when it checks its messages (the `inbox` tool) |
| `normal` | anyone | at the start of its next turn (one run of the agent): in the instructions it's started with, or, in a session you're typing in, with your next message |
| `high` | anyone | right after its next action (tool call), even in the middle of a turn |
| `interrupt` | orchestrator, you | right after its next tool call, marked "stop and handle this first"; also starts agents that are idle |

Delivery in the middle of a turn works through [hooks](AGENT_INTERFACE.md#hooks), checks Hive
runs inside the agent's CLI: after every tool call, the hook adds new `high` and `interrupt`
messages to what the agent reads next.

**Waking:** idle workers don't run at all until something starts (*wakes*) them: direct
messages, mentions, interrupts, task assignments, reviews of their work, report requests and
the fleet unfreezing. The orchestrator is also woken by ordinary channel messages and progress
updates, collected over a short window (`debounce_seconds` in
[`hive.toml`](CONFIGURATION.md#hivetoml), 90 s by default) so they arrive together.

**Unhandled urgent messages:** an agent can't end its turn while it has unhandled `high` or
`interrupt` messages. The hook that runs when it tries to stop sends it back, up to 3 times
per turn; it must handle them and confirm with the `ack` tool.

**Untrusted content:** messages are shown to agents wrapped in a marker,
`<hive-message from=… priority=…>`, so they're clearly separate from their instructions.
Agents are told that other agents' messages are information, not orders that override your
goals or their role. This guards against one agent being talked into misbehaving
([Security model](SECURITY.md#prompt-injection)).

**You** post to `#user` with `hive say` or the dashboard
([Working with the orchestrator](ORCHESTRATOR.md#chatting-from-anywhere-else)).

---

## Reports

Reports are how the orchestrator learns what happened without reading anyone's raw output
(everything an agent printed while working). Raw output would fill up the orchestrator's
limited working memory (its *context*) and could expose secrets the agent came across.

- **Unprompted:** workers file a report with the `report_submit` tool (a summary, a body, and
  the task it's about) when they finish meaningful work. Their
  [skill](GLOSSARY.md#skill) (written procedure) tells them to include files, commits, how the
  work was verified, and anything left open, and never to include secrets.
- **On request:** the orchestrator asks an agent for a report with `report_request`. The agent
  is started and answers with `report_submit`.
- **Delivery:** every new report starts (wakes) the orchestrator.
- **Verification:** its skill tells it to check claims against the repository (`git log`,
  reading the files, running tests that change nothing) and to keep only a summary in its
  notes.
- **Reading reports:** any agent can read them with `report_list` and `report_get`. You see
  them in the dashboard's *Reports* panel ([The web dashboard](MONITORING.md#the-web-dashboard)),
  or from the server's web API at `/api/v1/reports`.

---

**Next:** [Leases, git integration and reviews](INTEGRATION.md) ·
**See also:** [MCP tools](AGENT_INTERFACE.md#mcp-tools-hive-server) ·
[← Back to the README](../README.md)
