---
title: Hooks: run your own commands when the fleet does something
keywords: hook, notify, notification, notifications, on_decision, on_task_done, on_main, on_frozen, on_state, on_message, events, event, HIVE_EVENT, HIVE_SUMMARY, alert
see: config, up, security
---
# Hooks on fleet events

A hook is a shell command this computer's daemon (`hive up`) runs when something happens in
the fleet: a desktop notification when a decision waits for you, a chat message to your
phone, a log line.

```sh
hive config set hooks.on_decision 'notify-send "Hive" "$HIVE_SUMMARY"' --local
hive config set hooks.on_task_done 'echo "$HIVE_SUMMARY" >> ~/hive-done.log' --user
```

The events:

- `on_decision`: a decision waits for you;
- `on_task_done`: a task finished;
- `on_main`: `main` moved (work was merged);
- `on_frozen`: the fleet froze on a stuck agent;
- `on_state`: the fleet's state changed;
- `on_message`: an agent wrote to you in `#user`.

Each hook runs in a shell, from the worktree of one of this computer's agents, with:

- `HIVE_EVENT`: the event's type, e.g. `decision.requested`;
- `HIVE_SUMMARY`: one line for a person, e.g. `Decision #4 waits for you: Ship it?`;
- `HIVE_EVENT_JSON`: the whole event, as JSON.

It is stopped after 2 minutes. Each event runs a hook once; starting `hive up` doesn't replay
old events.

## Where hooks may be set

Only in this computer's own files: `hive.local.toml` (`--local`) or `~/.hive/config.toml`
(`--user`). Hive ignores `[hooks]` in the committed `hive.toml`, refuses
`hive config set hooks.…` without `--local` or `--user`, and `hive doctor` flags any there:
a repository must never be able to run commands on every computer that runs it. For the same
reason, a `hive.local.toml` committed to git is ignored, and an agent's worktree reads the
`hive.local.toml` of the clone recorded when the agent was set up.
