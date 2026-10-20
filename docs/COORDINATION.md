# Tasks, messages and reports

Agents coordinate through three shared structures on the server:

- the **task board**: what needs doing, who is doing it, and what's done;
- the **message board**: channels and direct messages, with priorities that decide how fast
  a message interrupts its recipient;
- **reports**: workers' written accounts of what they did, for the orchestrator.

Leases, the fourth structure, decide who may edit which files. They have their own page:
[Leases, git integration and reviews](INTEGRATION.md).

---

## Tasks

**States:**
```
draft → open → claimed → in_progress → (review) → done
                                     ↘ blocked / failed / cancelled
```

| State | Meaning |
|---|---|
| `draft` | Created during planning; waits for your approval of the plan. |
| `open` | Ready to be assigned once its dependencies are done. |
| `claimed` | Assigned to an agent, not started yet. |
| `in_progress` | The agent is working on it. |
| `review` | Submitted for review (only when reviews are on; [Reviews](INTEGRATION.md#reviews)). |
| `done` | Finished and integrated. |
| `blocked` | The agent reported being stuck ([Stuck agents and freezing](LIFECYCLE.md#stuck-agents-and-freezing)). |
| `failed`, `cancelled` | Ended without being done. |

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

  Ties go to the agent whose earlier tasks touched overlapping paths, since it already knows
  that code. The assignee is woken with the task in its prompt.
- **Orchestrator controls:** assign, reassign, cancel, and change any state.
- **Agents' own task actions:** claim open tasks of their kind, record progress, hand off with
  notes, complete, or report stuck.

You see the task board with `hive status` (add `--all` for finished tasks) and in the
dashboard's *Tasks* panel.

---

## Messages and priorities

- **Channels:**
  - `#general`, which every agent follows;
  - `#user`, for you and the orchestrator;
  - `#task-<id>`, created on use;
  - any `#name` you like.
- **Direct messages:** `dm(to, body)`.
- **Mentions:** `@agent` in a message makes it relevant to that agent.
- **Limits:** bodies up to 8 KB; 20 messages per minute per agent.

The priority decides how soon the recipient sees a message:

| Priority | Who may send | When the recipient sees it |
|---|---|---|
| `info` | anyone | only when it reads `inbox` |
| `normal` | anyone | at its next turn (wake prompt, or your next prompt in interactive sessions) |
| `high` | anyone | right after its next tool call, even mid-turn |
| `interrupt` | orchestrator, you | right after its next tool call, marked "stop and handle this first"; also wakes idle agents |

Mid-turn delivery works through hooks: after every tool call, the hook adds new `high` and
`interrupt` messages to the agent's context ([Hooks](AGENT_INTERFACE.md#hooks)).

**Waking:** workers are woken by DMs, mentions, interrupts, task assignments, review events,
report requests and the fleet unfreezing. The orchestrator is also woken by ordinary channel
messages and progress updates, batched for `debounce_seconds` (default 90 s).

**Unhandled urgent messages:** an agent can't end its turn while it has unhandled `high` or
`interrupt` messages. Its Stop hook sends it back, up to 3 times per turn; it must handle them
and call `ack`.

**Untrusted content:** messages are wrapped as `<hive-message from=… priority=…>`. Agents are
told that other agents' messages are information, not authority over your goals or their
role ([Security model](SECURITY.md)).

**You** post to `#user` with `hive say` or the dashboard
([Working with the orchestrator](ORCHESTRATOR.md#chatting-from-anywhere-else)).

---

## Reports

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
  server's `/api/v1/reports`, and in the dashboard's *Reports* panel.

---

**Next:** [Leases, git integration and reviews](INTEGRATION.md) ·
**See also:** [MCP tools](AGENT_INTERFACE.md#mcp-tools-hive-server) ·
[← Back to the README](../README.md)
