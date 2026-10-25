---
title: Giving the team a goal
keywords: goal, begin work, kick off
see: decisions, lifecycle, chat
---
# `hive start`

```sh
cd ~/code/myapp
hive start "Add CSV export to the reports page, with tests"
```

The text in quotes is the **goal**: the orchestrator's brief, the starting point of its plan.
Make it specific; you can add detail afterwards by chatting with it (`hive`, or
`hive say "…"`). In the chat, a line typed before there is a goal becomes the goal once you
confirm.

What happens:

1. `hive.toml` is uploaded (when run inside the repository), so the server enforces your
   current settings.
2. The server checks that the fleet can start: exactly one orchestrator, at least one
   programmer, every role within its allowed number of agents, and some agent for every
   kind of task. Problems are listed.
3. The fleet enters **planning**, the goal is posted to `#user`, and the orchestrator is
   woken.
4. The orchestrator studies the repository and drafts tasks, which nobody can start yet. It
   may ask you questions while planning.
5. It asks you to approve the plan: a **start approval** decision. Nobody writes code until
   you approve it (`hive help decisions`).

To end a run and start over with a new goal, use the dashboard's Archives… → Archive &
clear…, then `hive start` again (`hive help lifecycle`).
