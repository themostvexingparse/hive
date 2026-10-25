---
title: Running this computer's agents (the daemon)
keywords: daemon, run agents, headless, background, wake, wakes, logs, log, daemon.log, orchestrator terminal
see: chat, agents, machines, watch
---
# `hive up`

`hive up` runs every slot (agent) on this computer, through the **daemon**: a background
program that asks the server every few seconds whether anything concerns its agents, starts
an agent's CLI when it has work, runs its git work (commit, rebase, checks, push), extends its
file leases and uploads its live output.

```sh
hive up                       # the orchestrator, if it lives here, opens in this terminal
hive up --headless            # run the orchestrator without a terminal too
hive up --orchestrator orch   # which orchestrator to open, if this computer has several
hive up -v                    # detailed logging in ~/.hive/logs/daemon.log
```

- **On the orchestrator's computer,** the terminal becomes the orchestrator's own CLI
  session; you can type to it directly (press Esc first while it waits for news). Workers on
  the same computer run in the background. When the session ends, Hive asks whether to
  relaunch it.
- **On a computer with only workers,** the terminal shows what every agent does as it works.
- **On a paired computer,** it also picks up agents placed on it, every 30 seconds, and takes
  off those moved away (`hive help machines`).
- **Slots of several servers or projects** run together; the table it prints at the start
  shows each slot's server.

Ctrl+C stops everything on this computer. While the daemon is stopped its agents aren't
woken, and their file leases expire after their lifetime (600 seconds by default) unless it
comes back.

## How a worker is started

A worker isn't running all the time. When something concerns it (a task assigned, a review
requested, a message for it), the daemon **wakes** it: it starts the CLI headless with a
short prompt saying what happened, continuing the agent's conversation, and the turn ends
when the agent stops. Settings in `[sessions]` decide when it starts a fresh conversation
instead (`hive help config`).

An agent whose account isn't logged in is paused with a message saying to run
`hive agents login NAME`; it is retried when you exit the login.

On this computer, `[hooks]` in your own settings run when the fleet does something
(`hive help hooks`).
