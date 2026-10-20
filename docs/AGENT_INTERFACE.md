# What the agents see: MCP tools, hooks and skills

When `hive` launches an agent's CLI, it attaches three things for that session only:

- **MCP tools**, through the `hive` MCP server: how the agent acts on the fleet (messages,
  tasks, leases, reports, …);
- **hooks**: small commands the CLI runs around every tool call and turn, which deliver
  messages mid-turn and refuse writes the agent isn't allowed to make;
- **skills**: the written operating procedures, `hive-orchestrator` and `hive-worker`.

Together with the [shell-command permissions](PERMISSIONS.md) and
[your own additions to the instructions](PROMPTS.md), this is everything that makes a plain
vendor CLI a Hive agent. This page describes each piece. It is mostly useful for
understanding agent behaviour you see in the live output, and for writing custom roles.

---

## MCP tools (`hive` server)

The CLI runs `hive mcp` as a local MCP server. Each tool call goes to the machine's daemon,
which forwards it to the server, or runs the git transaction itself for `lease_release`.

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
| `request_approval(command, reason, shell, task_id, wait_seconds)`, `run_approved(id)`, `approval_list` | run a dangerous command with the orchestrator's approval ([Shell-command permissions](PERMISSIONS.md)) |
| `lease_status`, `repo_state`, `fleet_state`, `status_read` | look around |

**Coding roles** add: `lease_acquire`, `lease_extend`, `lease_release`.

**Orchestrator** adds:
- `await_events(timeout_seconds)`;
- `task_create`, `task_assign`, `task_cancel`;
- `decision_request(kind, question, options, context, task_id, freeze)`;
- `status_publish(summary, body, level)`;
- `report_request`;
- `approval_decide(approval_id, approve, note)`;
- `review_verdict`;
- `lease_force_release`;
- `role_reassign`;
- `fleet_control(pause|resume)`.

**Reviewer** adds `review_verdict`; **planner** adds `task_create`. Custom roles add tools
with `tools` ([Custom roles](ROLES.md#custom-roles-and-overrides)).

Only permitted tools are registered in an agent's session. The tools appear as
`mcp__plugin_hive_hive__<tool>` in Claude Code and `mcp__hive__<tool>` in Codex, and they're
pre-approved for the session, so there are no permission prompts.

### `await_events`

The orchestrator's way of waiting. It blocks inside the daemon, without using tokens, until:
- an **urgent event** arrives (returns within about 3 s);
- **batched events** (progress, ordinary messages) have waited `debounce_seconds`; or
- the timeout passes (default 600 s, max 1800 s).

It returns a digest: fleet state, a state-specific hint, the events, and unread messages.

| Delivered at once | Batched for `debounce_seconds` |
|---|---|
| your messages, answered decisions, new reports, finished/failed/blocked tasks, stuck agents, review verdicts, agents out of quota or offline, expired leases, merge conflicts, approval requests | task progress, ordinary messages |

---

## Hooks

The hooks ship inside the bundles and call `hive hook <event> --vendor <cli>`. Codex uses
Claude Code's event names and output format.

| Claude / Codex event | Gemini event | What Hive does |
|---|---|---|
| PreToolUse | BeforeTool | Denies writes not covered by leases or `write_globs`, and writes into other slots' worktrees. For Codex's `apply_patch`, every file the patch adds, updates, deletes or moves is checked. While the fleet state doesn't allow the agent's roles to act, denies everything except reading and Hive tools. Also applies the [shell-command tiers](PERMISSIONS.md). |
| PostToolUse | AfterTool | Injects new `high` and `interrupt` messages mid-turn. |
| UserPromptSubmit | BeforeAgent | Injects unread messages and, in interactive sessions, events queued since the last check. |
| Stop | AfterAgent | Sends the agent back (max 3 times per turn) if it has unhandled urgent messages, uncommitted changes under its leases, or, in the interactive orchestrator, urgent events waiting. |
| SessionStart | SessionStart | Tells the agent who it is, its roles, the fleet state, and which skill to follow. |

When a hook denies something, the denial includes the reason and what to do instead (for
example, "call `lease_acquire` first"). That is what you see in the live output as a failed
tool call.

**Failure handling:**
- **Write checks fail closed:** if the daemon is unreachable, file writes are refused.
- **Message delivery fails open:** the agent continues normally.
- **Outside Hive sessions** (no `HIVE_SLOT`), every hook does nothing.
- **Git backstop:** shell commands can't be inspected reliably, so the git `pre-commit` hook
  re-checks staged files against leases for agent commits.

---

## Skills

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

Both are plain Markdown in `.hive/agent/*/skills/`. Don't edit them there: `hive init`
rewrites those folders. Add your own rules in `.hive/prompts/` instead
([Customising what agents are told](PROMPTS.md)). `hive prompts --full` prints the built-in
text.

---

**Next:** [Shell-command permissions](PERMISSIONS.md) ·
**See also:** [Roles](ROLES.md), [Running the fleet](RUNNING.md#how-an-agent-is-launched-for-a-headless-turn-wake) ·
[← Back to the README](../README.md)
