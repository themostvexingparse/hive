# Leases, git integration and reviews

Several agents edit one repository at the same time. Hive keeps them out of each other's way
with **leases**: an agent must lock every path it wants to edit, and nobody else can lock an
overlapping path until it's released. Releasing the lease is also how work gets into `main`:
the daemon commits exactly the leased files, rebases, runs your checks and pushes. With
reviews on, another agent approves the work first.

This page explains leases, how worktrees and branches are laid out, every step of a release,
the optional GitHub mode, and reviews.

---

## Leases

- **Acquire before editing:** an agent must lease every path it edits with
  `lease_acquire(globs=[…])`. Hooks refuse writes to anything else
  ([Hooks](AGENT_INTERFACE.md#hooks)).
- **All or nothing:** it gets every requested path or none, which rules out deadlocks. On a
  conflict, the response lists who holds what and until when, so the agent can wait, ask the
  holder, or tell the orchestrator.
- **Glob syntax:** repo-relative POSIX. `src/app.py`, `src/api/**`, `docs/*.md`.
  - Wildcards: `**`, `*` and `?`. `[` is an ordinary character.
  - A path without wildcards also covers everything beneath it.
- **Overlap checks are conservative:** two leases are refused if their fixed prefixes
  overlap, even when their files are actually disjoint. For example, `src/*.py` and
  `src/app.js` overlap because both start with `src/`. The checks ignore case, because
  `src/App.py` and `src/app.py` are the same file on Windows and macOS.
- **Other checkouts are off-limits:** agents can't edit files in another slot's worktree or
  in your own clone of the repository.
- **Lifetime:** default 600 s (min 30, max 3600). The daemon extends leases automatically
  while it runs, so they expire only if a machine dies or loses contact. Expired and revoked
  leases are announced to the holder and the orchestrator.
- **Fencing:** each grant carries a fencing number. A revoked or expired lease can never be
  used to integrate, even by a machine that comes back late.
- **Revoking:** the orchestrator (`lease_force_release`) and you (the dashboard's *Leases*
  panel) can take a lease away, for example from a machine that died.

---

## Worktrees and branches

- Every slot works in its own worktree on branch `hive/<slot>`, created by `hive join`
  ([Enrolling agents and setting up machines](MACHINES.md#join-each-slot-on-the-machine-that-will-run-it)).
- Agents never commit to or push `main`. Only the release transaction below moves it.
- `.hive/orchestrator/` is git-ignored.

---

## What `lease_release(commit_message, task_id)` does

When an agent has finished a piece of work, it calls `lease_release`. Its daemon then runs
this transaction:

1. **Commit:** commits only the changed files covered by the agent's leases or `write_globs`,
   even if the agent staged other files. The commit message gets trailers: `Hive-Agent`,
   `Hive-Task`, `Hive-Fence`.
   - **Other changed files** are set aside (stashed) and restored afterwards; the result lists
     them. Only that stash entry is ever popped; the agent's own stashes are untouched.
   - **Nothing covered (`uncovered_changes`):** if nothing would be integrated but there *are*
     changed files outside the leases, the release stops and **keeps the leases**. The agent
     must lease those files (or revert them) and release again.
   - **Earlier commits (`uncovered_commits`):** if commits already on the agent's branch touch
     files its leases don't cover, the release stops the same way.
2. **Rebase:** `git fetch` and rebase onto `origin/main`. On a conflict:
   - the rebase is aborted;
   - the commit and leases are kept;
   - the orchestrator is told;
   - the agent must merge and call `lease_release` again.
3. **Checks:** runs every command in `[integration] checks`. On failure, the leases are kept
   and the output tail is returned to the agent.
   - **Which shell:** by default checks run in the machine's native shell, cmd.exe on
     Windows and `/bin/sh` elsewhere.
   - **Mixed fleets:** one `hive.toml` serves every machine, so write portable commands
     (`python -m pytest -q`, `npm test`), or set `[integration] shell` to `bash` (Git Bash on
     Windows) or `powershell` so every machine uses the same shell.
   - **Time limit:** each check may run for up to 30 minutes.
4. **Push:** pushes the agent's branch `hive/<slot>`.
5. **Integrate:**
   - **Local mode** (default):
     1. The server grants a **one-time integration grant**, bound to this agent, this commit,
        and its leases with their fencing numbers.
     2. The daemon pushes `HEAD:main` with a plain, non-forced push, which fails if `main`
        moved. In that case it re-fetches and retries from step 2, up to 3 times.
     3. It then confirms to the server with the grant. Without a valid grant, nobody can
        release leases or move the recorded `main`.

     **If the server can't be reached for the confirmation,** the daemon retries. The result
     is `integrated_unconfirmed`, and the daemon keeps resending the confirmation in the
     background until it gets through.
   - **GitHub mode** ([below](#github-mode-optional)): the server fast-forwards `main` through
     the GitHub API.
6. **Done:**
   - the leases are released;
   - the server records the new `main` and announces it;
   - every other slot whose worktree is clean rebases onto it immediately;
   - slots with uncommitted work get a notice and rebase at their next release.

With review enabled, step 5 becomes "submit for review" instead ([Reviews](#reviews)). The
`task_id` is then required, and it must be the agent's own task.

`lease_release(abandon=true)` frees the leases without integrating anything.

### What the agent gets back

| Status | Meaning | What happens next |
|---|---|---|
| `integrated` | The work is on `main`. | The agent completes its task and files a report. |
| `integrated_unconfirmed` | Pushed to `main`, server not yet told. | The daemon keeps confirming in the background. |
| `review_pending` | Review is on; the branch is pushed and the task is in review. | The reviewer is woken ([Reviews](#reviews)). |
| `nothing` | No changes to integrate. | – |
| `conflict` | The rebase onto `origin/main` failed. | The agent merges and releases again; the orchestrator is notified. |
| `checks_failed` | A command in `[integration] checks` failed. | The agent fixes it, using the output tail, and releases again. |
| `uncovered_changes` / `uncovered_commits` | Changes outside the leases. | The agent leases or reverts them and releases again. |
| `error` | Something else failed, e.g. a push. | The message says what. |

---

## GitHub mode (optional)

To let the server, rather than the machines, move `main`:
1. Set `[repo] github = "owner/repo"` in `hive.toml`.
2. Give the server `HIVE_GITHUB_TOKEN`, a fine-grained token with *Contents: write* on that
   repo.

The server then advances `main` with a fast-forward-only ref update, so `main` is never
force-moved. Combine this with branch protection so only that token can push `main`, and give
the machines push rights for `hive/*` branches only. Then even a misbehaving agent machine
can't change `main` directly.

With [several projects](PROJECTS.md), `HIVE_GITHUB_TOKEN` applies to the default project
only. Give each other project its own token: put it in an environment variable on the server,
then `hive admin project set NAME --github-token-env HIVE_GITHUB_TOKEN_ACME`. Hive stores only
the variable's name.

This mode is implemented but not yet tested against GitHub
([Known limitations](LIMITATIONS.md)).

---

## Reviews

Set `[integration] review` in `hive.toml`:

| Value | Behaviour |
|---|---|
| `off` (default) | Work integrates as soon as checks pass. |
| `orchestrator` | The orchestrator reviews every integration. |
| `reviewer` | An agent with the reviewer role reviews. If no reviewer other than the author exists, the orchestrator does. |

**Flow with review on:**
1. The programmer's `lease_release` pushes its branch and the task enters **review**. The
   programmer **keeps its leases**, so nobody else can change the files under review. The
   `task_id` must be passed and must be the programmer's own task, so review can't be
   skipped.
2. The reviewer is woken with the commit to inspect (`git diff origin/main...<sha>`) and calls
   `review_verdict(task_id, approve, notes)`. Nobody can review their own task.
3. **Approve:** the programmer's daemon re-runs the release automatically (rebase, checks,
   integrate) and the task becomes **done**.
   - **If the programmer is mid-turn,** the daemon waits for the turn to end first, so
     half-finished edits are never committed.
   - **The approval is single-use:** it only counts for that review.
4. **Changes requested:** the task returns to `in_progress`, and the programmer is woken with
   the notes.

---

**Next:** [What the agents see](AGENT_INTERFACE.md) ·
**See also:** [Tasks, messages and reports](COORDINATION.md),
[`hive.toml` reference](CONFIGURATION.md#hivetoml) ·
[← Back to the README](../README.md)
