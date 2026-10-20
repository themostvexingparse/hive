# Working with the orchestrator

The orchestrator is the only agent you normally talk to. It is a regular Claude Code, Gemini
CLI or Codex session with the `orchestrator` role: it plans the work, splits it into tasks,
assigns them, answers workers' questions, approves their risky commands, checks their
reports, and keeps you informed. It never writes code itself.

Think of it as a project lead you chat with. You give it the goal and your preferences; it
comes back to you with a plan to approve, questions only you can answer, and regular status
updates. This page covers how to talk to it, how to answer what it asks, and what it can and
can't do.

---

## Chatting in its terminal

The terminal where `hive up` launched the orchestrator **is** the chat
([Running the fleet](RUNNING.md#the-machine-with-the-orchestrator-your-computer)).
- **When the orchestrator is working or replying,** just type, as in any session.
- **When it's waiting in `await_events`** (a long-running tool call), press **Esc** to cancel
  the wait, then type. If you type without Esc, the message is most likely queued until the
  wait returns, which can take up to 10 minutes.
- **After replying,** it goes back to `await_events` on its own, as its skill instructs.

`await_events` is how the orchestrator waits without spending tokens. It blocks inside the
daemon and returns as soon as something needs attention: an urgent event within about 3
seconds, routine events after the batching window (`debounce_seconds`, 90 s by default)
([MCP tools](AGENT_INTERFACE.md#mcp-tools-hive-server)).

---

## Chatting from anywhere else

- **Dashboard:** the *Orchestrator* panel ([The web dashboard](MONITORING.md#the-web-dashboard)).
- **CLI:** `hive say "Prioritise the API before the UI"` from any machine logged in as admin.

Both post to the `#user` channel. That **ends the orchestrator's wait within seconds**, and
your message is in the digest it receives.

**Caveat:** its replies may appear only in its terminal. The skill doesn't yet tell it to
answer `#user` messages in `#user` ([Known limitations](LIMITATIONS.md)).

---

## Answering decisions

A **decision** is a question the orchestrator raises for you, with options and context. The
orchestrator can **ask** you things but can never **answer** for you: answering needs the
admin token, which agents don't have. There are three kinds:

| Kind | Raised when | Effect while open |
|---|---|---|
| Start approval | The plan is ready ([Planning and approval](LIFECYCLE.md#planning-and-approval)) | Nobody codes until you approve. |
| Stuck | An agent is blocked and only you can unblock it ([Stuck agents and freezing](LIFECYCLE.md#stuck-agents-and-freezing)) | The whole fleet freezes. |
| Question | Anything else it wants your view on, such as a role change | Nothing is blocked. |

Ways to answer:

| Where | How |
|---|---|
| Orchestrator's terminal | `! hive decide 3 approve` (the `!` prefix runs a shell command in Claude Code and Gemini CLI) |
| Any admin terminal | `hive decisions` to read; `hive decide 3 approve --note "keep it small"` to answer |
| Dashboard | the *Decisions* panel: option buttons plus a note field |

The note is passed to the orchestrator with your answer. Use it to explain a rejection or to
add constraints.

---

## How it keeps you informed

- After handling a batch of events it **publishes a status update**: a one-line summary of
  what changed since the last update, details in the body, and a level of `fyi`,
  `decision-needed` or `alert`.
- Status updates appear in `hive status`, `hive status -v` (with bodies), and the dashboard's
  *Updates* panel.
- It keeps its memory in `.hive/orchestrator/state.md` in its worktree (git-ignored), so it
  can resume after restarts and new sessions.
- It learns what workers did from their **reports**, not from their raw output, and checks
  their claims against the repository ([Reports](COORDINATION.md#reports)).

You can shape how it reports to you (length, frequency, what to lead with) in
`.hive/prompts/orchestrator.md` ([Customising what agents are told](PROMPTS.md)).

---

## What the orchestrator can and can't do

**Can:**
- create, assign, reassign and cancel tasks;
- message anyone, including `interrupt` priority;
- raise decisions;
- request reports and read every report;
- approve or deny workers' dangerous shell commands ([Shell-command permissions](PERMISSIONS.md));
- revoke stale leases;
- propose role changes (changes to mandatory roles need your approval);
- pause and resume the fleet;
- read the repository and run read-only commands;
- review work when there is no reviewer.

**Can't:**
- edit source files (only `.hive/**` is writable for it, and not `.hive/prompts/`);
- take leases or commit;
- answer decisions;
- see agents' live output.

These limits are enforced by the server and hooks, not just by its instructions
([How roles are enforced](ROLES.md#how-roles-are-enforced)).

If the orchestrator runs out of quota or goes offline, a **standby** can take over
([Changing roles](ROLES.md#changing-roles)).

---

**Next:** [Watching the fleet](MONITORING.md) ·
**See also:** [The fleet lifecycle](LIFECYCLE.md) ·
[← Back to the README](../README.md)
