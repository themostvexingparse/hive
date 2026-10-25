---
title: File leases and how work reaches main
keywords: lease, file lock, lock, locks, integration, integrate, merge, main, lease_release, lease_acquire, rebase, checks, branch, branches, worktrees, conflict, fencing
see: review, agents, config
---
# Leases, and getting work into main

## Leases

An agent must **lease** (lock) every path it edits, with Hive's `lease_acquire` tool. Hive's
checks inside its CLI refuse edits to anything else, and the git `pre-commit` hook refuses
commits of files it hasn't leased.

- **All or nothing:** it gets every requested path or none, so two agents never wait on each
  other. On a conflict it is told who holds what, until when.
- **Paths** are patterns relative to the repository's top folder: `src/app.py`, `src/api/**`
  (everything under it), `docs/*.md`. A path without wildcards covers everything beneath it.
- **Cautious overlap checks:** `src/*.py` and `src/app.js` count as overlapping, because both
  start with `src/`.
- **Lifetime:** 600 seconds by default; the daemon extends leases while it runs, so they
  expire only when a computer dies or loses contact.
- **Fencing:** a revoked or expired lease can never be used to merge work, even by a computer
  that comes back late.
- **Revoking:** the orchestrator, or you in the dashboard's Leases panel.

Agents can't edit files in another agent's worktree, or in your own clone.

## Worktrees and branches

Each agent works in its own git worktree, on its own branch `hive/NAME`, so agents never see
each other's half-finished changes and your clone stays yours.

## What `lease_release` does

When an agent finishes, it calls Hive's `lease_release` tool, and its daemon (not the AI)
runs these steps:

1. **commit** only the changed files its leases cover, on its branch;
2. **rebase** onto the newest `main`; on a conflict it stops, keeps the commit and the
   leases, and tells the orchestrator;
3. **checks:** run every `integration.checks` command (e.g. the test suite, up to 30 minutes
   each); if one fails, nothing is merged and the agent gets the end of its output;
4. **push** its branch `hive/NAME`;
5. **integrate:** with the server's one-time permission, push the commit to `main` (never
   forced; if `main` moved meanwhile it fetches and retries). With reviews on, this step
   asks for a review instead (`hive help review`);
6. **done:** the leases are released, the server records the new `main`, and every other
   agent rebases onto it.

```sh
hive config set integration.checks '["python -m pytest -q"]'
```
