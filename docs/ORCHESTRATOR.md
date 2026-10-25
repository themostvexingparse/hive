# Working with the orchestrator

A Hive fleet is a team of AI coding agents working on one repository. The orchestrator is
the one agent in it that you normally talk to. It is a regular Claude Code, Codex, agy or
Gemini CLI session given the `orchestrator` [role](ROLES.md): it plans the work, splits it into
[tasks](COORDINATION.md#tasks), assigns them, answers the other agents' (the **workers'**)
questions, approves their risky shell commands, checks the **reports** they write about
their work, and keeps you informed. It never writes code itself.

Think of it as a project lead you chat with. You give it the goal and your preferences; it
comes back to you with a plan to approve, questions only you can answer, and regular status
updates. This page covers how to talk to it, how to answer what it asks, and what it can and
can't do.

---

## Chatting in the Hive chat (`hive`)

Run `hive` in a terminal (or `hive chat`): once the team exists and runs, it opens a chat with
the orchestrator, wherever the orchestrator runs ([CLI.md §9](CLI.md#hive-the-chat-with-the-team)).
What you type goes to it in `#user`; its replies, its status updates and the fleet's state
appear as they happen; a decision appears as a prompt (`y` approves, `n` sends it back), which
Hive answers on your keypress. Slash commands are the `hive` commands (`/agents add codex`,
`/status`, `/watch claude-2`). This works the same with a headless orchestrator
(`hive up --headless`), which never needs its own terminal.

---

## Chatting in its terminal

On the computer that hosts the orchestrator, `hive up` opens its CLI in your terminal. That
terminal **is** the chat
([Running the fleet](RUNNING.md#the-machine-with-the-orchestrator-your-computer)).
- **When the orchestrator is working or replying,** just type, as in any session.
- **When it's idle,** it is waiting inside `await_events`, one of the tools Hive gives it.
  Your CLI shows this as a tool call that's still running. Press **Esc** to cancel the wait,
  then type. If you type without Esc, the message is most likely queued until the wait
  returns, which can take up to 10 minutes.
- **After replying,** it goes back to `await_events` on its own, as its
  [skill](GLOSSARY.md#skill) (its written operating procedure) instructs.

`await_events` is how the orchestrator waits without using any of its account's model usage.
It waits inside the [daemon](GLOSSARY.md#daemon) (the background program `hive up` runs) and
returns as soon as something needs attention:
- **urgent events** (your messages, failed or blocked tasks, stuck agents, approval
  requests, …) within about 3 seconds;
- **routine events** (progress notes, ordinary messages, completed tasks, new reports)
  collected over a short window, 90 s by default (`debounce_seconds` in
  [`hive.toml`](CONFIGURATION.md#hivetoml)), so they arrive together. Every batch it handles
  re-reads its whole conversation, so fewer, fuller batches cost less.

It then returns a summary (*digest*) of what happened. See
[MCP tools](AGENT_INTERFACE.md#await_events) for details.

---

## Chatting from anywhere else

- **Dashboard:** the *Orchestrator* panel ([The web dashboard](MONITORING.md#the-web-dashboard)).
- **CLI:** `hive say "Prioritise the API before the UI"` from any computer where you've run
  `hive login` ([Log in as admin](SERVER.md#log-in-as-admin)).

Both post to the `#user` channel, the message channel between you and the orchestrator
([Messages](COORDINATION.md#messages-and-priorities)). That **ends the orchestrator's wait
within seconds**, and your message is in the digest it receives. Its skill tells it to answer
`#user` messages in `#user`, where the chat and the dashboard show them.

---

## Answering decisions

A **decision** is a question the orchestrator raises for you, with options and context.
Each has a number. The orchestrator can **ask** you things but can never **answer** for you:
answering needs the admin token (your password for the server), which agents don't have.
There are three kinds:

| Kind | Raised when | Effect while open |
|---|---|---|
| Start approval | The plan is ready ([Planning and approval](LIFECYCLE.md#planning-and-approval)) | Nobody codes until you approve. |
| Stuck | An agent is blocked and only you can unblock it ([Stuck agents and freezing](LIFECYCLE.md#stuck-agents-and-freezing)) | The whole fleet freezes: every agent stops until you answer. |
| Question | Anything else it wants your view on, such as changing an agent's role | Nothing is blocked. |

Ways to answer (in these examples, `3` is the decision's number, shown by `hive decisions`):

| Where | How |
|---|---|
| The chat (`hive`) | the decision appears as a prompt: `y` approves, `n` sends it back with a note, `/answer …` answers other questions |
| Orchestrator's terminal | `! hive approve` (the `!` prefix runs a shell command in Claude Code and Gemini CLI) |
| Any terminal where you're logged in as admin | `hive decisions` to read; `hive approve`, `hive reject "keep it small"` or `hive answer "…"` (with the number when several are open) |
| Dashboard | the *Decisions* panel: option buttons plus a note field |

The answer can be one of the offered options or any text. The note is passed to the
orchestrator with your answer. Use it to explain a rejection or to add constraints.

---

## How it keeps you informed

- When something changed that you'd want to know (a task or milestone finished, a blocker, a
  decision raised, a problem), it **publishes a status update**: a one-line summary of what
  changed since the last update, details in the body, and a level: `fyi` (for your
  information), `decision-needed` (you need to answer a decision) or `alert` (something went
  wrong). Routine batches pass without one.
- Status updates appear in `hive status`, `hive status -v` (with bodies), and the dashboard's
  *Updates* panel.
- It keeps notes in `.hive/orchestrator/state.md` in its own working folder of the
  repository (its [worktree](GLOSSARY.md#worktree)); git ignores the file, so it's never
  committed. That lets it pick up where it left off after a restart or a fresh conversation:
  `hive up` starts it in a fresh conversation after 12 hours idle (configurable), rather than
  resuming a long, stale one.
- It learns what workers did from their **reports**, not from their raw output (everything
  they printed), and checks their claims against the repository
  ([Reports](COORDINATION.md#reports)).

You can shape how it reports to you (length, frequency, what to lead with) by writing
instructions in `.hive/prompts/orchestrator.md` in the repository
([Customising what agents are told](PROMPTS.md)).

---

## What the orchestrator can and can't do

**Can:**
- create, assign, reassign and cancel tasks;
- message anyone, including with `interrupt` priority, which makes the recipient stop and
  handle it first ([Messages](COORDINATION.md#messages-and-priorities));
- raise decisions;
- request reports and read every report;
- approve or deny workers' commands that need approval, when no reviewer is available or
  none decided within a minute and a half ([Shell-command permissions](PERMISSIONS.md));
- take away [leases](INTEGRATION.md#leases) (agents' locks on files) that an agent no longer
  needs or can't release, e.g. because its computer died;
- propose role changes (changes to required roles, `orchestrator` and `programmer`, need
  your approval);
- pause and resume the fleet;
- read the repository and run read-only commands;
- review work when no agent has the `reviewer` role ([Reviews](INTEGRATION.md#reviews)).

**Can't:**
- edit source files (it may only write inside the repository's `.hive/` folder, and not in
  `.hive/prompts/`);
- take leases or commit;
- answer decisions;
- see agents' live output.

These limits are enforced by the server and by Hive's [hooks](GLOSSARY.md#hook) (automatic
checks inside the agent's CLI), not just by its instructions
([How roles are enforced](ROLES.md#how-roles-are-enforced)).

If the orchestrator's account runs out of usage or its computer goes offline, a **standby**
(another agent you named in `hive.toml` in advance) can take over
([Changing roles](ROLES.md#changing-roles)).

---

**Next:** [Watching the fleet](MONITORING.md) ·
**See also:** [The fleet lifecycle](LIFECYCLE.md) ·
[← Back to the README](../README.md)
