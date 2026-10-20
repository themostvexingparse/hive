# Leases, git integration and reviews

In a Hive fleet, several AI agents edit one git repository at the same time. Hive keeps them
out of each other's way with **leases**: an agent must lock every path it wants to edit, and
nobody else can lock an overlapping path until it's released. Releasing the lease is also how
work gets into the repository's `main` branch: the **daemon** (the background program
`hive up` runs on each computer with agents) commits exactly the leased files, **rebases**
them (replays them on top of the newest `main`), runs your **checks** (the test commands you
list in `hive.toml`) and pushes. With reviews switched on, another agent approves the work
first.

Each agent works in its own **slot** on its computer, which includes its own
[worktree](GLOSSARY.md#worktree): a separate working folder of the repository on its own
branch ([Setting up machines](MACHINES.md)).

This page explains leases, how worktrees and branches are laid out, every step of a release,
the optional GitHub mode, and reviews.

---

## Leases

- **Acquire before editing:** an agent must lease every path it edits, with Hive's
  `lease_acquire` tool. Hive's [hooks](AGENT_INTERFACE.md#hooks) (checks that run inside the
  agent's CLI before each action) refuse edits to anything else.
- **All or nothing:** it gets every requested path or none. That rules out deadlocks, where
  two agents each hold something the other is waiting for. On a conflict, the response lists
  who holds what and until when, so the agent can wait, ask the holder, or tell the
  orchestrator (the agent that coordinates).
- **Path patterns** ([globs](GLOSSARY.md#glob)) are written relative to the repository's top
  folder, with forward slashes: `src/app.py`, `src/api/**`, `docs/*.md`.
  - `*` matches any characters within one folder level, `**` any number of levels, `?` one
    character. `[` has no special meaning.
  - A path without wildcards also covers everything beneath it: `src/api` covers
    `src/api/users.py`.
- **Overlap checks are cautious:** two leases are refused if the parts of their patterns
  before the first wildcard overlap, even when the files they match are actually different. For example, `src/*.py` and
  `src/app.js` overlap because both start with `src/`. The checks ignore case, because
  `src/App.py` and `src/app.py` are the same file on Windows and macOS.
- **Other folders are off-limits:** agents can't edit files in another agent's worktree or
  in your own clone of the repository.
- **Lifetime:** 600 seconds by default (at least 30, at most 3600). The daemon extends leases
  automatically while it runs, so they expire only if a computer dies or loses contact with
  the server. Expired and revoked leases are announced to the holder and the orchestrator.
- **Fencing:** each lease carries a serial number (its *fencing number*). Once a lease is
  revoked or expires, its number is no longer valid, so it can never be used to merge work
  into `main`, even by a computer that comes back online late.
- **Revoking:** the orchestrator (with its `lease_force_release` tool) and you (the
  dashboard's *Leases* panel, [The web dashboard](MONITORING.md#the-web-dashboard)) can take
  a lease away, for example from a computer that died.

---

## Worktrees and branches

- Every agent works in its own worktree on a branch named after it, `hive/<agent name>`
  (e.g. `hive/claude-2`), created by `hive join`
  ([Enrolling agents and setting up machines](MACHINES.md#join-each-slot-on-the-machine-that-will-run-it)).
- Agents never commit to or push `main`. Only the release steps below move it.
- `.hive/orchestrator/`, where the orchestrator keeps its private notes, is never committed
  (it's listed in `.gitignore`).

---

## What `lease_release(commit_message, task_id)` does

When an agent has finished a piece of work, it calls Hive's `lease_release` tool, with a
commit message and the number of the task the work is for. Its daemon then runs these steps:

1. **Commit:** commits only the changed files covered by the agent's leases, or by its role's
   `write_globs` (paths the role may edit without a lease; [Roles](ROLES.md#custom-roles-and-overrides)),
   even if the agent staged other files. Lines recording the agent, the task and the fencing
   number (`Hive-Agent`, `Hive-Task`, `Hive-Fence`) are added at the end of the commit
   message.
   - **Other changed files** are set aside with `git stash` and restored afterwards; the
     result lists them. Hive restores only what it set aside; anything the agent stashed
     itself is left alone.
   - **Nothing covered (`uncovered_changes`):** if nothing would be integrated but there *are*
     changed files outside the leases, the release stops and **keeps the leases**. The agent
     must lease those files (or revert them) and release again.
   - **Earlier commits (`uncovered_commits`):** if commits already on the agent's branch touch
     files its leases don't cover, the release stops the same way.
2. **Rebase:** fetches the latest `main` from `origin` (the shared copy of the repository)
   and rebases onto it. If the agent's changes conflict with someone else's:
   - the rebase is aborted;
   - the commit and leases are kept;
   - the orchestrator is told;
   - the agent must resolve the conflict and call `lease_release` again.
3. **Checks:** runs every command listed in `checks` in the `[integration]` section of
   `hive.toml` (usually your test suite). If one fails, the leases are kept and the last part
   of its output is returned to the agent.
   - **Which shell:** by default checks run in the computer's usual shell: cmd.exe on
     Windows and `/bin/sh` elsewhere.
   - **Fleets on several operating systems:** one `hive.toml` serves every computer, so write
     commands that work everywhere (`python -m pytest -q`, `npm test`), or set `shell` in
     `[integration]` to `bash` ([Git Bash](GLOSSARY.md#git-bash) on Windows) or
     `powershell` so every computer uses the same shell.
   - **Time limit:** each check may run for up to 30 minutes.
4. **Push:** pushes the agent's branch (`hive/<agent name>`) to `origin`.
5. **Integrate** (merge into `main`):
   - **Local mode** (default):
     1. The server checks the agent still holds valid leases and gives it a **one-time
        permission** to integrate, tied to this agent, this commit, and those leases.
     2. The daemon pushes the commit to `main` with an ordinary push (`git push origin
        HEAD:main`, never a forced one). That push fails if someone else changed `main`
        meanwhile; then the daemon fetches again and retries from step 2, up to 3 times.
     3. It then confirms to the server with the permission. Without a valid one, nobody can
        release leases or change which commit the server records as `main`.

     **If the server can't be reached for the confirmation,** the daemon retries. The result
     is `integrated_unconfirmed`, and the daemon keeps resending the confirmation in the
     background until it gets through.
   - **GitHub mode** ([below](#github-mode-optional)): the server moves `main` forward itself,
     through GitHub's web API.
6. **Done:**
   - the leases are released;
   - the server records the new `main` and announces it;
   - every other agent whose worktree has no uncommitted changes rebases onto it at once;
   - agents with uncommitted work get a notice and rebase at their next release.

With reviews switched on, step 5 becomes "submit for review" instead ([Reviews](#reviews)).
The task number is then required, and it must be the agent's own task.

Calling `lease_release` with `abandon=true` frees the leases without integrating anything.

### Finishing a task in one call

`lease_release` can also take the task's `result` and the agent's report (`report_summary`,
`report_body`). Once the work is integrated, Hive completes the task with that result and
files the report on the agent's behalf. With review on, it keeps them until the reviewer
approves, and finishes the task then. The agent needs no further calls and isn't woken again
just to hear the news. The worker skill tells agents to finish this way: each extra call or
turn would re-read the agent's whole conversation.

### What the agent gets back

| Status | Meaning | What happens next |
|---|---|---|
| `integrated` | The work is on `main`. | With a result and report given, the task is completed and the report filed (the result lists what was done); otherwise the agent does both itself. |
| `integrated_unconfirmed` | Pushed to `main`, server not yet told. | The daemon keeps confirming in the background. |
| `review_pending` | Review is on; the branch is pushed and the task is in review. | The reviewer is woken ([Reviews](#reviews)); the agent ends its turn. |
| `nothing` | No changes to integrate. | – |
| `conflict` | The rebase onto `origin/main` failed. | The agent merges and releases again; the orchestrator is notified. |
| `checks_failed` | One of the checks failed. | The agent fixes it, using the output, and releases again. |
| `uncovered_changes` / `uncovered_commits` | Changes outside the leases. | The agent leases or reverts them and releases again. |
| `error` | Something else failed, e.g. a push. | The message says what. |

---

## GitHub mode (optional)

If your repository is on GitHub, you can have the server, rather than the agents' computers,
move `main`:
1. In `hive.toml`, in the `[repo]` section, set `github = "owner/repo"` (your repository's
   GitHub name, e.g. `octocat/hello-world`).
2. Create a [GitHub token](GLOSSARY.md#github-token) (in GitHub: *Settings → Developer
   settings → Fine-grained tokens*) with the *Contents: write* permission on that repository,
   and give it to the server in the `HIVE_GITHUB_TOKEN` environment variable.

The server then moves `main` only forward, to a commit that already contains it (a
*fast-forward*), so nobody's work on `main` can ever be overwritten. For extra safety, use
GitHub's *branch protection* so that only that token can push to `main`, and give the agents'
computers permission to push only `hive/*` branches. Then even a misbehaving agent computer
can't change `main` directly.

If the server hosts [several projects](PROJECTS.md), `HIVE_GITHUB_TOKEN` applies to the
`default` project only. Give each other project its own token: put it in another environment
variable on the server, then name that variable for the project, e.g. for a project called
`acme`: `hive admin project set acme --github-token-env HIVE_GITHUB_TOKEN_ACME`. Hive stores
only the variable's name.

This mode is implemented but not yet tested against GitHub
([Known limitations](LIMITATIONS.md)).

---

## Reviews

A review means another agent must approve a programmer's work before it reaches `main`. Set
`review` in the `[integration]` section of `hive.toml`, then upload the settings with
`hive admin push-config`:

| Value | Behaviour |
|---|---|
| `off` (default) | Work integrates as soon as checks pass. |
| `orchestrator` | The orchestrator reviews every integration. |
| `reviewer` | An agent with the `reviewer` [role](ROLES.md) reviews. If no reviewer other than the author exists, the orchestrator does. |

**Flow with review on:**
1. The programmer's `lease_release` pushes its branch and the task enters **review**. The
   programmer **keeps its leases**, so nobody else can change the files under review. The
   task number must be given and must be the programmer's own task, so review can't be
   skipped.
2. The reviewer is started (woken) with the commit to inspect; it sees the changes with
   `git diff origin/main...<commit>`. It gives its verdict (approve or not, with notes) with
   the `review_verdict` tool. Nobody can review their own task.
3. **Approve:** the programmer's daemon re-runs the release automatically (rebase, checks,
   integrate). If the programmer left its result and report with `lease_release`, Hive
   completes the task and files the report, and the programmer isn't woken; otherwise it's
   woken to do both.
   - **If the programmer is in the middle of a run,** the daemon waits for it to finish
     first, so half-finished edits are never committed.
   - **The approval is single-use:** it only counts for that review.
4. **Changes requested:** the task returns to `in_progress`, and the programmer is started
   with the reviewer's notes.

---

**Next:** [What the agents see](AGENT_INTERFACE.md) ·
**See also:** [Tasks, messages and reports](COORDINATION.md),
[`hive.toml` reference](CONFIGURATION.md#hivetoml) ·
[← Back to the README](../README.md)
