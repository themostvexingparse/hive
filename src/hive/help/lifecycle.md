---
title: The fleet's states, and pausing, resuming and resetting
keywords: state, states, fleet state, planning, awaiting_approval, running, frozen, paused, pause, resume, reset, freeze, archive, archives, end a run
see: start, decisions, roles
---
# The fleet lifecycle

The fleet is always in exactly one **state**, and the state decides who may work. The server
enforces it on every request, so an agent can't work in a state its role isn't allowed to
act in, whatever it is told.

```
setup ──hive start──▶ planning ──plan ready──▶ awaiting_approval ──approve──▶ running
                         ▲                            │
                         └──── reject (+ your note) ──┘
running ── stuck decision ──▶ frozen ── you answer ──▶ running
running ── hive pause ──▶ paused ── hive resume ──▶ (the state before)
```

- **setup**: no goal yet; nobody works.
- **planning**: the orchestrator (and planners) study the repository and draft tasks.
- **awaiting_approval**: the plan waits for you.
- **running**: tasks are assigned; agents code, review and test.
- **frozen**: an agent is stuck on something only you can resolve. Other agents finish their
  current turn and only read; no new leases, task claims or merges into `main`; lease clocks
  pause. Answering the decision unfreezes it.
- **paused**: you stopped everything.

## Pausing, resuming, resetting

```sh
hive pause     # stop every agent, the orchestrator included; lease clocks pause
hive resume    # back to the state before the pause
hive reset     # emergency: back to setup; keeps agents, tasks and history
```

Pausing is safe at any time: agents finish their current turn and nothing is lost.

To **end a run** and start again with a new goal, use the web dashboard's ⋯ menu →
Archives… → Archive & clear…: it saves the run's history to an archive on the server, clears
it, and returns to setup. Then `hive start "NEXT GOAL"`.

## Who works in which state

- orchestrator: planning, awaiting_approval (no new work), running, frozen (communication
  only);
- planner: planning and running;
- every other role: running only, unless a custom role says otherwise with `active_in`
  (`hive help roles`).
