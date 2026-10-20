# The fleet lifecycle

The fleet is always in exactly one **state**, and the state decides who may work. It is how
Hive guarantees its two rules: nobody codes until you approve the plan, and the fleet freezes
when an agent is stuck on something only you can resolve. The server enforces the state on
every request, so an agent can't work in a state its role isn't allowed to act in, whatever
it is told.

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
| `planning` | The orchestrator (and any planners) study the repository and draft tasks. |
| `awaiting_approval` | The plan is waiting for your approval. |
| `running` | Normal operation: tasks are assigned and agents code, review and test. |
| `frozen` | An agent is stuck and only you can unblock it. Everything stops until you answer. |
| `paused` | You stopped everything. |

---

## Starting

```sh
hive start "Add GitHub OAuth login with tests"
```

You can also type the goal into the dashboard while the fleet is in `setup`. Run `hive start`
inside the repository: it also uploads `hive.toml`, so the server enforces your current
configuration.

**Startup checks.** The fleet won't start unless:
- exactly 1 orchestrator and at least 1 programmer are enrolled;
- every role is within its min/max;
- every task kind has a handler ([Roles](ROLES.md)).

`hive start` prints any problems.

**What happens:**
1. The fleet enters **planning**.
2. Your goal is posted to `#user`.
3. The orchestrator is woken.

Make the goal specific: it is the orchestrator's brief. You can add detail afterwards by
chatting with it, and it may ask you questions while planning.

---

## Planning and approval

In `planning`:
- Only the orchestrator and planners may act.
- The orchestrator studies the repository and creates tasks, which stay **drafts**. It may ask
  you clarifying questions.
- It then raises a **start approval** decision with the plan as context. The fleet enters
  **awaiting_approval**.

Read the plan with `hive decisions` or in the dashboard's *Decisions* panel. Your answer:
- **`hive decide <id> approve`:** all drafts become open, the fleet enters **running**, and the
  scheduler assigns tasks immediately.
- **Anything else (e.g. `reject --note "…"`):** the fleet goes back to **planning**. The
  orchestrator gets your note and revises.

`approve`, `approved`, `yes`, `y` and `ok` all count as approval.

---

## Stuck agents and freezing

1. An agent calls `report_stuck`. Its task becomes `blocked` and the orchestrator is woken.
2. If the orchestrator can solve it (clarify, reassign, split), it does. Most stuck reports
   end here, without involving you.
3. If only you can, it raises a **stuck decision**, and **the fleet freezes**:
   - **Other agents:** their tool calls are denied with an explanation and they end their
     turns. No new wakes start; only reading is allowed.
   - **Server:** refuses new leases, task claims and integrations from frozen roles.
   - **Lease timers:** paused. When the fleet thaws, every active lease is extended by the
     frozen duration.
4. You answer with `hive decide <id> "<your answer>" --note "…"`, or the dashboard. Then:
   1. the fleet returns to **running**;
   2. the stuck task goes back to `in_progress`;
   3. agents that had open tasks are woken with your answer.

Why freeze everyone, not just the stuck agent? A question only you can answer usually
affects more than one task: a design choice, a missing credential, an unclear requirement.
Freezing keeps the rest of the fleet from building on a guess.

Only one freezing decision is open at a time; others queue and open in order. Non-freezing
questions (`kind="question"`) never freeze anything.

---

## Pausing, resuming, resetting

| Command | Effect |
|---|---|
| `hive pause` | Stops all agents, including the orchestrator. Lease timers pause. |
| `hive resume` | Returns to the previous state. |
| `hive reset` | Emergency: back to `setup`. Keeps agents, tasks and history; clears any blocking decision. |
| Dashboard ⋯ → *Archives…* → *Archive & clear…* | Ends a run: saves its history to an archive, clears it, and returns to `setup` for a new goal ([Archives](MONITORING.md#archives)). |

Pausing is safe at any time: running turns end, nothing is lost, and leases don't expire
while paused.

---

## What each role may do in each state

| State | Orchestrator | Planner | Other roles |
|---|---|---|---|
| setup | – | – | – |
| planning | active | active | wait |
| awaiting_approval | active (no new work) | wait | wait |
| running | active | active | active |
| frozen | active (communication only) | wait | wait |
| paused | wait | wait | wait |

"Wait" means the daemon starts no turns for the agent, and the hooks refuse everything except
reading. Custom roles choose the states they work in with `active_in`
([Custom roles](ROLES.md#custom-roles-and-overrides)).

---

**Next:** [Roles](ROLES.md) ·
**See also:** [Working with the orchestrator](ORCHESTRATOR.md#answering-decisions) ·
[← Back to the README](../README.md)
