---
title: Reading and changing settings, and where they come from
keywords: settings, setting, configuration, hive.toml, hive.local.toml, config.toml, layers, layer, local, user, --local, --user, explain, upload, sync, sessions
see: presets, hooks, permissions, roles, environment
---
# `hive config`

Settings are named `<section>.<name>`, as in the file: `integration.review`,
`sessions.max_wakes`, `roles.<role>.prompt`.

```sh
hive config                                   # settings that aren't at their default
hive config list --all                        # every setting, and where each comes from
hive config get integration.review
hive config explain integration.shell         # its value in every layer, and which wins
hive config set integration.review reviewer   # in hive.toml: the whole fleet
hive config set integration.checks "npm test" # a list setting given one value
hive config set integration.checks '["npm test", "npm run lint"]'
hive config set integration.shell powershell --local   # this computer only
hive config set sessions.max_wakes 20 --user  # you, in every repository on this computer
hive config unset integration.shell --local   # back to the value below it
hive config edit                              # open hive.toml in $EDITOR
```

Values are TOML (`true`, `90`, `["a", "b"]`); anything else is text. `set` and `unset` edit
the file in place, keeping its comments, and refuse a change that would make the
configuration invalid.

## The layers

Each layer overrides the one before it:

1. **default**: built into Hive;
2. **preset**: the bundle `fleet.preset` in `hive.toml` names (`hive help presets`);
3. **user**: `~/.hive/config.toml` (`--user`): you, in every repository on this computer;
4. **hive.toml**: committed in the repository: the whole fleet;
5. **hive.local.toml** (`--local`): next to `hive.toml` in your clone, git-ignored: this
   computer only, its daemon and its agents' worktrees.

Only `hive.toml` (over its preset) reaches the server and the other computers. The user and
local layers never leave this computer. Two safety rules: an agent's worktree reads the
`hive.local.toml` of the clone recorded when the agent was set up, never one its own `.git`
file points to; and a `hive.local.toml` that git tracks is ignored.

## Getting a change to everyone

After changing `hive.toml`, commit and push it: every agent reads its own copy. The server
keeps a copy too, because it enforces roles, permissions and reviews. You don't upload it
yourself: every admin command run inside your clone uploads `hive.toml` when it changed,
to the server (or project) `hive.toml` names in `[server] url`, and says so.
`hive admin push-config` uploads it explicitly; `hive start` always does.

## The sections

- `[server] url`: the server (or `…/p/project`) this repository belongs to.
- `[repo] main` (the branch work goes into), `github` (GitHub mode).
- `[integration] review` (`hive help review`), `checks` (commands that must pass before
  work reaches main), `shell` (the shell checks run in: `native`, which is cmd.exe on
  Windows and sh elsewhere, `bash`, `powershell` or `cmd`).
- `[orchestrator] standby` (who takes over), `debounce_seconds`, `heartbeat_hours`.
- `[sessions]` `per_task`, `max_context_tokens`, `max_wakes`,
  `orchestrator_fresh_after_hours`: when an agent starts a fresh conversation.
- `[permissions]` (`hive help permissions`), `[roles.<name>]` (`hive help roles`),
  `[fleet] preset` (`hive help presets`), `[hooks]` (`hive help hooks`).
