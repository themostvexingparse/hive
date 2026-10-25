---
title: Adding and managing agents: add, list, login, edit, rm, rotate
keywords: agent, add agent, slot, slots, worktree, account, accounts, model, permission mode, remove agent, git hook, pre-commit, claude, codex, gemini, agy
see: machines, roles, join, fleet-file, up
---
# `hive agents`

An **agent** is one AI coding CLI (Claude Code, Codex, Gemini CLI or agy) logged in to its
own account. It exists in two places: on the server, as a name with roles and a token; and
on the computer that runs it, as a **slot**: its own git worktree (a separate working folder
of your repository, on branch `hive/NAME`) and its own CLI settings folder.

## Adding: `hive agents add`

Run it inside your clone. The count, the CLI and the roles come in any order:

```sh
hive agents add claude                     # claude-1: the fleet's first agent orchestrates
hive agents add claude programmer          # claude-2
hive agents add 3 codex programmers        # codex-1, codex-2, codex-3
hive agents add gemini reviewer,planner --name gemini-review
hive agents add claude programmer --on desk    # on the paired computer desk
hive agents add agy tester --elsewhere     # prints a hive join line for another computer
```

Without roles, the first agent becomes the orchestrator and later ones programmers. For each
agent on this computer Hive:

1. creates it on the server (named `<cli>-<n>` unless you pass `--name`);
2. creates its worktree next to your clone (`~/code/myapp-claude-2`), from the latest `main`;
3. installs a git `pre-commit` hook in the clone, which refuses agents' commits of files
   they haven't leased (your own commits pass straight through);
4. creates its private CLI settings folder, `~/.hive/accounts/NAME`;
5. saves the slot in `~/.hive/machine.toml`, with the clone it was made from;
6. in a terminal, offers to log its account in.

If setting up the slot fails, the agent is removed from the server again. Tokens of agents
set up here are never printed. Options: `--model M` (the AI model; default the account's),
`--permission-mode P` (the CLI's own setting for what it may do without asking; see
`hive help join`), `--no-login`.

## Listing, logging in, changing

```sh
hive agents                                   # every agent: CLI, roles, state, where, login
hive agents login claude-2                    # log its CLI account in, once per agent
hive agents login codex-1 --device            # Codex without a browser (over SSH)
hive agents edit claude-2 --roles programmer,tester
hive agents edit codex-1 --model <model> --permission-mode read-only
hive agents edit codex-1 --model default      # back to the account's default
hive agents edit claude-3 --on laptop         # move it to another paired computer
```

- **login** opens the agent's CLI in its worktree, pointed at its own settings folder and
  without Hive attached: log in, then exit. Use a different account per agent. agy keeps its
  login in the computer's keyring, so it is once per computer.
- **edit** `--roles` replaces the roles. `--model` and `--permission-mode` apply from the
  agent's next turn, on whichever computer runs it. `--on` moves it, keeping the model and
  permission mode you don't give again.

## Removing, and new tokens

```sh
hive agents rm claude-3          # asks first; --yes in scripts
hive agents rotate claude-2      # a new token; the old one stops working
hive agents export               # the fleet as a file (hive help fleet-file)
```

**rm** removes the agent from the server: its token stops working, its unfinished tasks go
back on the board (work waiting for review starts again), reviews it was picked for go to
another reviewer, its file leases are revoked, its pending command approvals are cancelled,
and the orchestrator is told. Its slot on this computer goes too; the worktree is kept if it
has uncommitted changes, and the account folder is kept so adding it back needs no login.
The orchestrator of a run in progress can't be removed.

**rotate** updates a slot on this computer by itself; an agent on a paired computer picks up
its new token there; for any other agent it prints the `hive join` line to run where it is.
