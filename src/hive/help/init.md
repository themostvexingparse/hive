---
title: Preparing a repository for Hive
keywords: repository, repo, bundle, bundles, .hive, gitignore, set up repository, upgrade
see: config, prompts, permissions, doctor
---
# `hive init`

Run once in your clone of the repository, and again after upgrading Hive:

```sh
cd ~/code/myapp
hive init
git add hive.toml .hive .gitignore && git commit -m "Set up Hive" && git push
```

It writes:

- **`hive.toml`**: the project's Hive settings, shared by the whole fleet
  (`hive help config`). An existing one is kept.
- **`.hive/agent/`**: for each CLI, the package Hive attaches to the sessions it starts:
  its tools (an MCP server), its automatic checks (hooks), its instructions (skills) and
  the shell-command rules. It is built from `hive.toml` and its preset only, never from one
  computer's own settings.
- **`.hive/prompts/`**: commented-out starter files for your additions to the agents'
  instructions (`hive help prompts`). Existing files are never touched.
- **`.gitignore`** lines for `hive.local.toml` (this computer's own settings) and
  `.hive/orchestrator/` (the orchestrator's private notes).

Commit and push them: each agent works in its own worktree, often on another computer, and
gets these files through git. Nothing here changes your own CLI sessions: a `claude` you
start yourself never loads them.

Each agent's launch builds its package afresh from the installed Hive and its worktree, so
changing `[permissions]` or a prompt file reaches the next session without `hive init`. The
committed copy is what older Hive versions use, and the fallback if building fails.

## Options

```sh
hive init --main develop                    # finished work goes into develop
hive init --server https://hive.example.com/p/acme   # the server (project) it belongs to
hive init ../other-repo                     # another folder
hive init --hive-cmd /opt/hive/bin/hive     # how agents' tools call hive
```

`--main` defaults to `origin`'s default branch, else the current one. `--hive-cmd` is
needed only when `hive` isn't on the PATH of the agents' CLIs.
