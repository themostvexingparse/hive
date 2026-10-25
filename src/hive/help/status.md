---
title: What's going on: fleet state, agents, tasks, leases, updates
keywords: overview of fleet, tasks, progress, what is happening, agents online, status updates
see: watch, decisions, lifecycle
---
# `hive status`

```sh
hive status           # the fleet at a glance
hive status --all     # include finished and cancelled tasks
hive status -n 30     # the last 30 status updates (default 5)
hive status -v        # status updates with their full text
```

It shows:

- **the fleet's state** (`setup`, `planning`, `awaiting_approval`, `running`, `frozen`,
  `paused`; `hive help lifecycle`) and the goal;
- **a decision waiting for you**, with the commands that answer it;
- **the agents**: roles, whether they are online, working or idle, out of quota;
- **open tasks**: who has each, and its state (`open`, `claimed`, `in_progress`, `review`,
  `blocked`);
- **leases**: which agent has locked which files, until when;
- **recent status updates** from the orchestrator, the reports it writes for you.

`hive watch` follows what agents print as they work; the web dashboard (at the server's
address) shows all of this live, in one page.
