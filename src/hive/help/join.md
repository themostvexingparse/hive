---
title: Joining: pair this computer, or set up an agent created elsewhere
keywords: hv_, pair this computer, set up slot, --replace, re-join, rejoin, unpaired, permission modes
see: machines, agents, up
---
# `hive join`

`hive join` has two forms, both run inside this computer's clone of the repository.

## With a pairing code: pair this computer

```sh
hive join K7Q4-M2XD --server http://192.168.1.20:8765 --as desk
hive join hk_… --server https://hive.example.com --as build-07    # a reusable join key
```

The code comes from `hive machines invite` on the admin's computer. This computer becomes a
machine of that project; agents placed on it are then set up automatically while `hive up`
runs (`hive help machines`).

## With an agent's token: set up one agent

For an agent created with `hive agents add … --elsewhere` (or `hive admin enroll`), which
prints this line:

```sh
hive join claude-2 --token hv_… --server https://hive.example.com
```

It checks the token, asks the server which CLI the agent runs, creates its worktree next to
the clone you're in (on branch `hive/NAME`), installs the git `pre-commit` hook, creates its
private CLI settings folder and saves the slot in `~/.hive/machine.toml`. In a terminal it
then offers to log the account in.

## Options

- `--server URL`: the server, or a project's URL (`…/p/NAME`); saved for this slot only.
- `--repo PATH`: the clone (default: the one you're in); `--worktree PATH`: an exact place.
- `--config-dir PATH`: the account folder (default `~/.hive/accounts/NAME`).
- `--model M`: the AI model the CLI uses (default: the account's).
- `--permission-mode P`: the CLI's own setting for what it may do without asking. Claude
  `acceptEdits` (default), `bypassPermissions`; Gemini `auto_edit` (default), `yolo`; Codex
  `workspace-write` (default), `read-only`, `danger-full-access`, `yolo`; agy
  `accept-edits` (default), `auto`, `plan`. Rather than allowing everything, allow the
  commands agents need in `[permissions]` (`hive help permissions`).
- `--vendor V`: only a check; the server knows which CLI the agent runs.
- `--hive-cmd CMD`: how the git hook calls hive, if `hive` isn't on PATH.
- `--replace`: move an existing slot of the same name to another server or project. Its
  local state (events seen, messages, its CLI conversation) is reset. Without it, `hive join`
  refuses, so another project's agent of the same name can't take over the slot.

Re-joining with a new token keeps the slot's server and worktree. If branch `hive/NAME`
already has commits that aren't in `main`, Hive refuses rather than resetting it.
