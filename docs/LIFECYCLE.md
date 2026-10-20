# The fleet lifecycle

A Hive **fleet** is the team of AI coding agents working on your repository: one
**orchestrator** that plans and coordinates (and that you chat with), and **workers** that do
the work ([How Hive works](ARCHITECTURE.md)). The fleet is always in exactly one **state**,
and the state decides who may work. It is how Hive guarantees its two rules: nobody codes
until you approve the plan, and the fleet freezes when an agent is stuck on something only you
can resolve. The Hive server enforces the state on every request, so an agent can't work in a
state its [role](ROLES.md) isn't allowed to act in, whatever it is told.

```
 setup ──hive start──▶ planning ──orchestrator asks approval──▶ awaiting_approval
                          ▲                                           │ approve
                          └──────────── reject (+ your note) ─────────┤
                                                                      ▼
          frozen ◀── "stuck" decision ── running ── hive pause ──▶ paused
            │                              ▲   ▲                      │
            └──── you answer ──────────────┘   └──── hive resume ─────┘
```

| State | Meaning |
|---|---|
| `setup` | No goal yet. Agents are enrolled and machines may be running, but nobody works. |
| `planning` | The orchestrator (and any agents with the `planner` role) study the repository and draft [tasks](COORDINATION.md#tasks), the units of work. |
| `awaiting_approval` | The plan is waiting for your approval. |
| `running` | Normal operation: tasks are assigned and agents code, review and test. |
| `frozen` | An agent is stuck and only you can unblock it. Everything stops until you answer the orchestrator's question (a [decision](GLOSSARY.md#decision)). |
| `paused` | You stopped everything. |

---

## Starting

```sh
hive start "Add GitHub OAuth login with tests"
```

The text in quotes is the **goal**: what you want the fleet to achieve. You can also type the
goal into the [dashboard](MONITORING.md#the-web-dashboard) while the fleet is in `setup`. Run
`hive start` inside the repository: it also uploads the repository's Hive settings file,
`hive.toml`, so the server enforces your current settings.

**Startup checks.** The fleet won't start unless:
- exactly 1 orchestrator and at least 1 programmer (an agent that writes code) have been
  created with `hive admin enroll` ([Enrolling agents](MACHINES.md));
- every role has an allowed number of agents (a role can set a minimum and maximum);
- every kind of task (`code`, `test`, `review`, …) has some agent whose role takes it
  ([Roles](ROLES.md)).

`hive start` prints any problems.

**What happens:**
1. The fleet enters **planning**.
2. Your goal is posted to `#user`, the message channel between you and the orchestrator.
3. The orchestrator is woken.

Make the goal specific: it is the orchestrator's brief, the starting point of its plan. You can add detail afterwards by
chatting with it, and it may ask you questions while planning.

---

## Planning and approval

In `planning`:
- Only the orchestrator and planners may act.
- The orchestrator studies the repository and creates tasks, which stay **drafts**: nobody can
  start them yet. It may ask you clarifying questions in its terminal.
- It then raises a **start approval** decision (a question only you can answer) with the plan
  as context. The fleet enters **awaiting_approval**.

Read the plan with `hive decisions` or in the dashboard's *Decisions* panel. Your answer
(`<id>` is the decision's number, shown by `hive decisions`):
- **`hive decide <id> approve`:** all drafts become open (ready to be worked on), the fleet
  enters **running**, and the server immediately hands the tasks to suitable agents.
- **Anything else (e.g. `reject --note "…"`):** the fleet goes back to **planning**. The
  orchestrator gets your note and revises.

`approve`, `approved`, `yes`, `y` and `ok` all count as approval.

---

## Stuck agents and freezing

1. An agent tells Hive it can't continue, using its `report_stuck` tool. Its task becomes
   `blocked` and the orchestrator is started (woken) to deal with it.
2. If the orchestrator can solve it (clarify, reassign, split), it does. Most stuck reports
   end here, without involving you.
3. If only you can, it raises a **stuck decision**, and **the fleet freezes**:
   - **Other agents:** every action they try is refused with an explanation (by Hive's
     [hooks](GLOSSARY.md#hook), checks that run inside each agent's CLI), so they finish
     their current run. Hive starts no new runs; only reading is allowed.
   - **Server:** refuses new [leases](INTEGRATION.md#leases) (locks on files), new task
     claims, and attempts to merge work into `main`.
   - **Lease clocks:** leases normally expire after a while; while frozen, their clocks pause.
     When the fleet thaws, every active lease is extended by the time it was frozen.
4. You answer with `hive decide <id> "<your answer>" --note "…"` (any text, plus an optional
   note), or in the dashboard. Then:
   1. the fleet returns to **running**;
   2. the stuck task goes back to `in_progress`;
   3. agents that had open tasks are woken with your answer.

Why freeze everyone, not just the stuck agent? A question only you can answer usually
affects more than one task: a design choice, a missing credential, an unclear requirement.
Freezing keeps the rest of the fleet from building on a guess.

Only one freezing decision is open at a time; others queue and open in order. Ordinary
questions the orchestrator asks you (decisions of kind `question`) never freeze anything.

---

## Pausing, resuming, resetting

| Command | Effect |
|---|---|
| `hive pause` | Stops all agents, including the orchestrator. Lease timers pause. |
| `hive resume` | Returns to the previous state. |
| `hive reset` | Emergency: back to `setup`. Keeps agents, tasks and history; clears any decision that is blocking the fleet. |
| Dashboard ⋯ menu → *Archives…* → *Archive & clear…* | Ends a run: saves its history to an archive file on the server, clears it, and returns to `setup` for a new goal ([Archives](MONITORING.md#archives)). |

Pausing is safe at any time: agents finish their current run, nothing is lost, and leases
don't expire while paused.

---

## What each role may do in each state

| State | Orchestrator | Planner | Other roles (programmer, reviewer, tester, your own) |
|---|---|---|---|
| setup | – | – | – |
| planning | active | active | wait |
| awaiting_approval | active (no new work) | wait | wait |
| running | active | active | active |
| frozen | active (communication only) | wait | wait |
| paused | wait | wait | wait |

"Wait" means Hive starts no runs for the agent, and the hooks refuse everything except
reading. Roles you define yourself choose the states they work in with the `active_in`
setting ([Custom roles](ROLES.md#custom-roles-and-overrides)).

---

**Next:** [Roles](ROLES.md) ·
**See also:** [Working with the orchestrator](ORCHESTRATOR.md#answering-decisions) ·
[← Back to the README](../README.md)
