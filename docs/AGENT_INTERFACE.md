# What the agents see: MCP tools, hooks and skills

Hive's agents are ordinary AI coding CLIs: Claude Code, Gemini CLI and Codex. When `hive`
starts one (with `hive up` or `hive chat`; see [Running the fleet](RUNNING.md)), it attaches
three things for that session (that one conversation) only:

- **MCP tools**: functions the agent can call to act on the fleet: send messages, take
  tasks, lock files, file reports, and so on. MCP (Model Context Protocol) is the standard
  way these CLIs accept extra tools; Hive's tools come from a small program, `hive mcp`, which
  the CLI starts (an *MCP server*);
- **hooks**: small commands the CLI runs automatically at fixed moments, such as before and
  after every tool call and at the end of each run (*turn*). Hive's deliver urgent messages
  in the middle of a turn and refuse edits the agent isn't allowed to make;
- **skills**: written operating procedures the agent follows, `hive-orchestrator` for the
  orchestrator (the agent that plans and coordinates) and `hive-worker` for all others.

They come from the [bundle](GLOSSARY.md#bundle-plugin-extension-profile) that `hive init`
writes to the repository's `.hive/agent/` folder. Together with the
[shell-command permissions](PERMISSIONS.md) and
[your own additions to the instructions](PROMPTS.md), this is everything that makes a plain
vendor CLI a Hive agent. This page describes each piece. It is mostly useful for
understanding agent behaviour you see in the live output, and for writing custom roles.

---

## MCP tools (`hive` server)

The CLI runs `hive mcp` as a local MCP server. Each tool call goes to the computer's
[daemon](GLOSSARY.md#daemon) (the background program `hive up` runs), which forwards it to
the Hive server, or does the git work itself for `lease_release`. Tools are listed below with
their main inputs in brackets.

Every agent has the **base set**:

| Tool | Purpose |
|---|---|
| `whoami` | the agent's name, [roles](ROLES.md), which tools it has, what it may edit, and the [fleet state](LIFECYCLE.md) (the phase the team is in) |
| `roster` | all agents: roles, whether online or busy, whether their account has used up its allowance (quota) |
| `post(body, channel, priority, thread_id)` / `dm(to, body, priority)` | send a message to a channel / to one agent ([Messages](COORDINATION.md#messages-and-priorities)); `thread_id` replies to an earlier message |
| `inbox(limit, include_other_channels)` / `ack(ids)` | read messages / confirm urgent ones have been handled |
| `task_list`, `task_get`, `task_claim`, `task_update`, `task_handoff`, `task_complete` | work on [tasks](COORDINATION.md#tasks): list, read one, take one, record progress, hand one to another agent, finish one |
| `report_stuck(details, task_id)` | ask for help; may lead to the whole fleet pausing until you answer ([Stuck agents and freezing](LIFECYCLE.md#stuck-agents-and-freezing)) |
| `report_submit`, `report_list`, `report_get` | file and read [reports](COORDINATION.md#reports) |
| `request_approval(command, reason, shell, task_id, wait_seconds)`, `run_approved(id)`, `approval_list` | ask to run a dangerous shell command, run it once approved, list requests ([Shell-command permissions](PERMISSIONS.md)) |
| `lease_status`, `repo_state`, `fleet_state`, `status_read` | look around: current file locks, the repository's state, the fleet's state, the orchestrator's status updates |

**Roles that write code** (`programmer`, `tester`; a role you define gets them by listing
them in its `tools`) add the
[lease](INTEGRATION.md#leases) tools: `lease_acquire` (lock files before editing),
`lease_extend` (keep them longer) and `lease_release` (merge the work into `main` and unlock;
[What `lease_release` does](INTEGRATION.md#what-lease_releasecommit_message-task_id-does)).

**The orchestrator** adds:
- `await_events(timeout_seconds)`: wait for something to happen (below);
- `task_create`, `task_assign`, `task_cancel`: manage tasks;
- `decision_request(kind, question, options, context, task_id, freeze)`: ask you a
  question (a [decision](GLOSSARY.md#decision)); `freeze` stops the fleet until you answer;
- `status_publish(summary, body, level)`: post a status update for you;
- `report_request`: ask an agent for a report;
- `approval_decide(approval_id, approve, note)`: approve or deny a dangerous command;
- `review_verdict`: approve or reject work under [review](INTEGRATION.md#reviews);
- `lease_force_release`: take a lease away from an agent;
- `role_reassign`: change an agent's roles;
- `fleet_control(pause|resume)`: pause or resume the fleet.

**Reviewers** add `review_verdict`; **planners** add `task_create`. Roles you define add tools
with their `tools` setting ([Custom roles](ROLES.md#custom-roles-and-overrides)).

Only the tools an agent's roles permit are offered to it at all. In the CLIs, and in the live
output, the tools appear with a prefix the CLI adds: `mcp__plugin_hive_hive__<tool>` in
Claude Code and `mcp__hive__<tool>` in Codex. They're pre-approved for the session, so the
CLI never stops to ask whether the agent may use them.

### `await_events`

The orchestrator's way of waiting. It waits inside the daemon, without using any of the
account's model usage, until:
- an **urgent event** (something that happened in the fleet, see the table) arrives; it
  returns within about 3 s;
- **routine events** (progress notes, ordinary messages) have been collected for a short
  window, `debounce_seconds` in [`hive.toml`](CONFIGURATION.md#hivetoml) (90 s by default),
  so they arrive together; or
- the timeout passes (600 s by default, at most 1800 s).

It returns a summary (*digest*): the fleet state, a hint about what to do in that state, the
events, and unread messages.

| Delivered at once | Collected for `debounce_seconds` |
|---|---|
| your messages, answered decisions, new reports, finished/failed/blocked tasks, stuck agents, review verdicts, agents out of quota or offline, expired leases, merge conflicts, approval requests | task progress, ordinary messages |

---

## Hooks

The hooks ship inside the bundles and call `hive hook <event> --vendor <cli>`. Each CLI names
the moments at which it runs hooks (*events*); Gemini uses different names for the same
moments, and Codex uses Claude Code's names.

| Claude / Codex event | Gemini event | When | What Hive does |
|---|---|---|
| PreToolUse | BeforeTool | before every tool call | Refuses edits to files the agent hasn't leased (and that its role's `write_globs` don't cover), and edits in other agents' [worktrees](GLOSSARY.md#worktree). For Codex's file-editing tool, `apply_patch`, every file the edit adds, changes, deletes or moves is checked. While the fleet state doesn't allow the agent's roles to work, refuses everything except reading and Hive's own tools. Also applies the [shell-command tiers](PERMISSIONS.md). |
| PostToolUse | AfterTool | after every tool call | Adds new `high` and `interrupt` messages to what the agent reads next, in the middle of its turn. |
| UserPromptSubmit | BeforeAgent | when a turn starts, or when you send a message in an interactive session | Adds unread messages and, in sessions you're typing in, fleet events since the last check. |
| Stop | AfterAgent | when the agent tries to end its turn | Sends the agent back (at most 3 times per turn) if it has unhandled urgent messages, uncommitted changes in files it has leased, or, for the orchestrator in your terminal, urgent events waiting. |
| SessionStart | SessionStart | when a session starts | Tells the agent who it is, its roles, the fleet state, and which skill to follow. |

When a hook denies something, the denial includes the reason and what to do instead (for
example, "call `lease_acquire` first"). That is what you see in the live output as a failed
tool call.

**If something goes wrong:**
- **Edit checks fail safe:** if the hook can't reach the daemon, file edits are refused.
- **Message delivery fails harmlessly:** the agent just continues without the messages.
- **Outside Hive sessions** every hook does nothing. Hive recognises its own sessions by the
  `HIVE_SLOT` environment variable it sets when starting a CLI.
- **Git as a second line of defence:** a shell command can change files in ways a hook can't
  reliably foresee, so the git `pre-commit` hook (which git runs before every commit, and
  which `hive join` installs) checks again that an agent's commit contains only files it has
  leased.

---

## Skills

- **`hive-orchestrator`**: the operating procedure:
  - what to do when a session starts;
  - waiting with `await_events` and handling what it returns;
  - what to do in each fleet state;
  - when to freeze the fleet;
  - working from reports instead of the agents' raw output;
  - status updates, and keeping notes between sessions;
  - hard rules (no code, never answer decisions, no secrets).
- **`hive-worker`**: the task procedure:
  - read, lease, edit, test, release, complete, report;
  - report requests;
  - messages and acknowledgements;
  - being stuck or frozen;
  - reviewing.

Both are plain Markdown files in `.hive/agent/<cli>/skills/` in the repository. Don't edit
them there: `hive init` rewrites those folders. Add your own rules in `.hive/prompts/` instead
([Customising what agents are told](PROMPTS.md)). `hive prompts --full` prints the built-in
text.

---

**Next:** [Shell-command permissions](PERMISSIONS.md) ·
**See also:** [Roles](ROLES.md), [Running the fleet](RUNNING.md#how-an-agent-is-launched-for-a-headless-turn-wake) ·
[← Back to the README](../README.md)
