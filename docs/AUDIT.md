# Hive code audit

**Date:** 2026-10-20
**Scope:** the whole codebase as of this date, including uncommitted changes. That covers
`src/hive/**` (about 6,100 lines: server, daemon, permissions, CLI, init, MCP server, hooks,
dashboard) and the test suite.
**Status:** **all 36 findings fixed** on 2026-10-20 (see [§8 Resolution](#8-resolution)).
Both probe scripts now report every finding as "not reproduced". The test suite grew from 93
to 165 tests and passes repeatedly.

---

## 1. Method

1. **Baseline:** ran the full test suite three times: **93 passed, 1 skipped** (a
   POSIX-only test, skipped on Windows) each time. No flaky failures appeared.
2. **Line-by-line review** of every source file, looking for:
   - correctness;
   - authorization gaps;
   - state-machine dead ends;
   - concurrency and ordering;
   - cross-platform behaviour (Windows, macOS, Linux);
   - input validation.
3. **Reproduction:** every suspect that could be exercised locally was run against the real
   code with a probe script:
   - [`scripts/audit/probe_server.py`](../scripts/audit/probe_server.py): server and API
     suspects (`P*`);
   - [`scripts/audit/probe_daemon.py`](../scripts/audit/probe_daemon.py): daemon, git and
     command-parsing suspects (`D*`).

   Run them with `uv run python scripts/audit/probe_server.py` (and `probe_daemon.py`). Each
   prints `REPRODUCED` or `not reproduced`. One deliberate **control probe** (P23) checks
   correct behaviour and correctly reported "not reproduced", which shows the harness doesn't
   just report everything as a bug.
4. **Classification:**

   | Label | Meaning |
   |---|---|
   | Reproduced | Demonstrated by a probe on this machine. |
   | Reasoned | Follows from the code but wasn't run, usually because it needs MySQL or Postgres, a live CLI, or precise timing. |

**Not covered by this audit:**
- live Claude Code or Gemini CLI sessions (they would use account quota);
- MySQL or Postgres back ends;
- macOS and Linux hosts;
- an end-to-end run on PythonAnywhere.

---

## 2. Summary

| ID | Severity | Area | Finding | Status |
|---|---|---|---|---|
| H1 | **High** | Server · integration | `/integrate/confirm` has no authorization: any agent can release other agents' leases, move the recorded `main`, and complete approved tasks | Reproduced (P1) |
| H2 | **High** | Server · review | The mandatory review gate can be bypassed: omit `task_id`, or quote any already-approved task | Reproduced (P2a, P2b) |
| H3 | **High** | Daemon · git | `lease_release` commits everything in the git index, including files no lease covers | Reproduced (D2) |
| M1 | Medium | Server · fleet state | Answering a freezing decision while paused leaves the fleet frozen forever | Reproduced (P6) |
| M2 | Medium | Server · tasks | A draft task created with an `assignee` is never started after approval | Reproduced (P3) |
| M3 | Medium | Permissions | `git -C … push origin main` / `git -c k=v push …` evade the push-to-`main` hard limit; the orchestrator can approve them | Reproduced (P7, D9) |
| M4 | Medium | Permissions | Recursive deletes via `find -delete`, `find -exec rm`, `xargs rm -rf` aren't detected; `find` is even pre-allowed | Reproduced (D1) |
| M5 | Medium | Server · review | An agent holding `programmer`+`reviewer` can approve its own work | Reproduced (P11) |
| M6 | Medium | Daemon · startup | If the server is unreachable when `hive up` starts, wake-ups are silently dropped and the orchestrator falls back to headless mode | Reproduced (D4) + reasoned |
| M7 | Medium | Daemon · Windows | The wake timeout doesn't stop a CLI launched through a `.cmd` shim (Gemini on Windows) | Reproduced (D7) |
| M8 | Medium | CLI · orchestrator | Startup race: a headless orchestrator turn can start alongside the interactive session, on the same session id | Reasoned |
| M9 | Medium | Server · config | Uploading a config that removes a custom role still held by an agent makes that agent's requests fail with 500 | Reproduced (P5) |
| M10 | Medium | Server · review | Review mode `reviewer` with no eligible reviewer leaves the task in review, assigned to nobody, forever | Reproduced (P20) |
| M11 | Medium | Daemon · git | A failed `/integrate/confirm` after `main` was pushed leaves the leases active and the other machines unsynced | Reasoned |
| M12 | Medium | Init · git hook | The shared pre-commit hook blocks *your own* commits wherever `hive` isn't on PATH | Reproduced (D8) |
| M13 | Medium | Server · MySQL/Postgres | Lock-order inversion (heartbeat vs. tick) can deadlock; the deadlock error surfaces as 500, not a retryable 503 | Reasoned |
| L1 | Low | Daemon · git | `lease_release` can pop an unrelated, older stash | Reproduced via simulated stash failure (D3) |
| L2 | Low | Server · input | Malformed input returns 500 instead of 400: `..` path hints, non-numeric `ttl`/`ids`/`until`, non-ASCII bearer token | Reproduced (P4, P13, P14) |
| L3 | Low | Server · privacy | Every agent can read every approved command's captured output | Reproduced (P15) |
| L4 | Low | Leases | Paths differing only in case don't conflict (same file on Windows/macOS) | Reproduced (P16) |
| L5 | Low | Server · reports | Any agent can answer a report request addressed to another agent | Reproduced (P18) |
| L6 | Low | Server · config | Invalid `[permissions]` config is accepted by `PUT /config`, then every approval request fails with 500 | Reproduced (P21) |
| L7 | Low | Server · tasks | A task depending on a cancelled or failed task stays open forever, with no notice | Reproduced (P22) |
| L8 | Low | Leases | Globs containing `[...]` count as wildcards but are matched literally | Reproduced (D5) |
| L9 | Low | Daemon · wakes | Wake reasons are consumed before the CLI starts; if it fails to launch, the work is lost | Reasoned |
| L10 | Low | Daemon · review | Auto-integration after approval can commit edits the agent is still making | Reasoned |
| L11 | Low | MCP server | The tool list is fixed at session start; a standby promoted by failover lacks orchestrator tools | Reasoned |
| L12 | Low | Daemon · config | The daemon reads `hive.toml` once at startup | Reasoned |
| L13 | Low | Server · fleet state | Approving the plan while paused silently unpauses the fleet | Reasoned |
| L14 | Low | Daemon · wakes | All output of a wake is kept in memory for up to an hour | Reasoned |
| L15 | Low | Daemon · Gemini | A failed `gemini extensions link` is silent; every later Gemini wake fails | Reasoned |
| L16 | Low | Daemon · join | Re-joining a slot resets its branch with `worktree add -B`, orphaning unintegrated commits | Reasoned |
| L17 | Low | Prompting | Message bodies can contain `</hive-message>` and break out of the untrusted-content wrapper | Reasoned |
| L18 | Low | Server · failover | Failover drops the old orchestrator's non-coding roles | Reasoned |
| L19 | Low | Writes | Writes outside every slot worktree, including your own main clone, aren't lease-checked | Reasoned |
| L20 | Low | Performance | `GET /approvals/<id>` is a write transaction; waiting workers poll it every 2 s | Reasoned |

**Totals:** 36 findings: 3 high, 13 medium, 20 low. 21 were reproduced by probes; 15 are
reasoned from the code.

---

## 3. High severity

### H1 · `/integrate/confirm` has no authorization checks
**Where:** `src/hive/server/service.py:715` (`confirm_integration`), route
`src/hive/server/app.py` `/integrate/confirm`.

**What happens:**
1. It releases **any** lease ids passed in the request, without checking the holder or the
   fencing numbers.
2. It overwrites `fleet.main_sha` with any sha and broadcasts `main.advanced`.
3. It marks a task `done` if it's in review and approved.

There's no `_require_tool`, holder check or fleet-state check.

**Repro (P1):**
- `prog2` releases `prog1`'s active lease by posting its id to `/integrate/confirm`.
- The orchestrator, which has no `lease_release` tool, gets 200 and sets `main_sha` to
  `eeee…`.

**Impact:**
- **Lease protection is bypassed:** another agent can then lease and edit the same files.
- **Machines rebase for nothing:** every daemon fetches and rebases on a forged
  `main.advanced`.
- **Tasks are completed falsely:** approved tasks can be marked done.

**Direction:**
- Bind confirmation to an integration the server granted in `integrate()`, e.g. a one-time
  token tied to the sha, lease ids and fencing.
- Check the holder, the fencing and the `lease_release` permission.

### H2 · The mandatory review gate can be bypassed
**Where:** `service.py:689` (`if task_id and self._review_mode(f) != "off":`).

**What happens:**
- **No task id:** review only applies when a `task_id` is supplied, so
  `lease_release(task_id=None)` integrates straight into `main`.
- **Borrowed task id:** the `approved` flag stays `True` after a task is done, and
  `integrate()` doesn't check that the task belongs to the caller. So any agent can quote any
  previously approved task id and skip review.

**Repro:**
- **P2a:** with `review = "orchestrator"`, an integrate without `task_id` returns
  `push_main`.
- **P2b:** a second agent integrates unreviewed work by quoting a finished, approved task.

**Impact:** `[integration] review` becomes advisory.

**Direction:**
- Require `task_id` whenever review is on.
- Require the task to be assigned to the caller and in a reviewable state.
- Bind the approval to the reviewed sha (`pending_sha`) and clear it after integration.

### H3 · `lease_release` commits the whole index, not just leased files
**Where:** `src/hive/daemon/gitops.py:112`. `git commit` commits everything staged. The
daemon's commit also runs without `HIVE_SLOT`, so the lease-checking pre-commit hook is
skipped.

**What happens:**
1. `git add` is now pre-allowed for workers, so an agent can stage a file no lease covers.
2. The release transaction stages the covered files and commits.
3. That commit includes the uncovered staged file, which is then integrated into `main`.

**Repro (D2):** with only `a.txt` leased, a staged change to `secret.txt` reached
`origin/main`.

**Impact:** silently defeats the lease system, which is Hive's core concurrency guarantee.

**Direction:**
- Commit only the covered paths (`git commit -- <paths>`), or verify the index (`git diff
  --cached --name-only`) contains only covered paths before committing.
- Also re-check commits already ahead of `main`.

---

## 4. Medium severity

### M1 · Answering a freezing decision while paused leaves the fleet frozen forever
**Where:** `service.py` `answer_decision` (`target = f["prev_state"] or "running"`) together
with `fleet_control` pause, which stores `prev_state = "frozen"`.

**Sequence:**
1. The fleet is frozen; you pause it.
2. You answer the decision. The state becomes `frozen` (the stored previous state), with no
   blocking decision.
3. `resume` then fails ("fleet is not paused"), and nothing can unfreeze the fleet.

**Repro (P6):** the final state is `frozen` with `blocking_decision = None`; resume returns
409. The only escape is `hive reset`.

**Direction:** when answering while paused, keep `paused` and set its stored previous state
to the post-answer target, or refuse to answer while paused.

### M2 · Pre-assigned draft tasks never start
**Where:** `service.py:427` (`_assign` keeps drafts as drafts and emits nothing), and
`answer_decision`, which turns drafts into `open` while keeping the assignee. The scheduler
only picks tasks with no assignee.

**Repro (P3):** after approval the task is `open` with `assignee = prog1`, and no
`task.assigned` event is ever emitted. The agent is never woken and the task never runs.

**Direction:** on approval, move assigned drafts to `claimed` and emit `task.assigned`.

### M3 · The push-to-`main` limit is evaded by `git -C`/`git -c`
**Where:** `src/hive/permissions.py:349`. Only `words[1:3]` are inspected for `push`, so
`git -C . push origin main` and `git -c a=b push origin main` aren't seen as pushes. The
approval prefix `git push` doesn't match them either.

**Repro:**
- **P7:** the server accepts an approval request for `git -C . push origin main`, and the
  orchestrator can approve it (200).
- **D9:** confirms both variants are missed. `git push origin HEAD:main` is correctly caught.

**Impact:** an approved command can push straight to `main`, bypassing integration and
review.

**Direction:** parse git's global options (`-C <path>`, `-c <k=v>`, `--git-dir`,
`--work-tree`, …) before locating the subcommand, in both `pushes_to` and the prefix
matcher.

### M4 · Recursive deletes via `find`/`xargs` aren't detected
**Where:** `permissions.py` (`dangerous()`, `_WRAPPERS`; `find` is in `READ_ONLY`).

**Repro (D1):** none of these is flagged:
- `find . -name '*.o' -delete`
- `find build -exec rm -rf {} +`
- `ls | xargs rm -rf`
- `git ls-files -z | xargs -0 rm -f`

`find` is on the allow list, so Gemini's policy pre-approves `find . -delete`. Claude has its
own built-in protection for `find -delete`/`-exec`.

**Direction:**
- Treat `find` with `-delete`, `-exec` or `-execdir` as needing approval.
- Unwrap `xargs` (with or without flags) and check the inner command.

### M5 · Self-review
**Where:** `service.py:520` (`review_verdict`) doesn't check that the reviewer isn't the
task's assignee. `_pick_reviewer` avoids self-assignment, but the verdict endpoint doesn't.

**Repro (P11):** an agent holding `programmer`+`reviewer` approved its own task.

**Direction:** reject verdicts where the caller is the assignee; optionally require the caller
to be the assigned reviewer.

### M6 · The daemon misbehaves when the server is unreachable at startup
**Where:** `src/hive/daemon/core.py:207` (`trigger()` filters by `rt.me["wake_on"]`) and
`src/hive/cli.py:221`.

**What happens:**
- **Wake-ups are dropped:** `refresh()` fails, so `rt.me` stays `{}`. `wake_on` then falls
  back to the built-in roles of `rt.roles`, which is empty, so every trigger is discarded,
  including `task.assigned`. That lasts until some fleet-state or role-change event happens to
  trigger a successful refresh.
- **The orchestrator goes headless:** `cmd_up` decides orchestrator mode from
  `rt.is_orchestrator`, which is false when the refresh failed, so it silently starts the
  orchestrator in headless mode.

**Repro (D4):** after `task.assigned`, no pending trigger exists.

**Direction:**
- Retry `refresh()` (for example in the heartbeat loop) until it succeeds.
- Queue triggers instead of dropping them while the agent's identity is unknown.
- Make `hive up` wait for the identity, or fail loudly.

### M7 · The wake timeout doesn't work through `.cmd` shims on Windows
**Where:** `src/hive/daemon/wake.py:301`. `proc.kill()` kills only the `cmd.exe` wrapper;
the grandchild keeps the stdout pipe open, so the read loop blocks until it exits.

**Repro (D7):** with `timeout=2`, `run_cli` returned only after 15.1 s, when the child finished
by itself. npm installs Gemini CLI as `gemini.cmd`, so a hung Gemini wake on Windows is never
killed and blocks that slot.

**Direction:** kill the whole process tree on Windows (`taskkill /T /F /PID`, or a Job
Object), and stop reading once the timeout fires.

### M8 · The orchestrator's interactive session can race a headless turn
**Where:** `cli.py:220` vs `cli.py:245`. `d.start_background()` starts the wake loop before
`orch.attached = True` is set.

**What happens:**
1. Wake reasons from an earlier session (for example, you closed the CLI while events were
   queued) are already older than the 3-second coalescing window.
2. So the first wake-loop pass can start a **headless** orchestrator turn on the stored
   session id.
3. At the same moment, the interactive CLI resumes that same session.

**Direction:** mark the slot attached before starting the background loops, or tell the
daemon which slots are interactive when it's constructed.

### M9 · Removing a custom role that an agent still holds
**Where:** `service.py:180` (`set_config` validates only that the roles parse), and every
`role_set(actor.roles, catalog)` call.

**Repro (P5):** after uploading a config without the role, that agent's `whoami` returns 500.
So do all of its role-checked calls, and `hive start` reports `invalid_fleet`. The maintenance
tick isn't affected (D6, not reproduced): the scheduler never evaluates that agent.

**Direction:** reject configs that drop roles still held by agents, or treat unknown roles as
empty.

### M10 · No eligible reviewer means the task waits forever
**Where:** `service.py:742` (`_pick_reviewer` returns `None`). For example, the only reviewer
is the assignee.

**Repro (P20):** `integrate` returns `{"status": "review_pending", "reviewer": null}`. No
daemon is woken: the orchestrator only reacts when it's the named reviewer.

**Direction:** fall back to the orchestrator and notify it.

### M11 · A confirmation failure after pushing `main` leaves things half-done
**Where:** `gitops.py:182`. The daemon pushes `HEAD:main`, then calls `/integrate/confirm`;
an exception there isn't handled.

**What happens:**
1. The server is down or busy at that moment, so the call fails.
2. `main` has moved, but the leases stay active, `main_sha` is stale and no `main.advanced`
   is sent, so other machines don't sync.
3. A retried `lease_release` finds nothing ahead of `main` and releases the leases.
4. The confirmation itself is never sent.

**Direction:** retry the confirmation persistently, for example with a queued confirmation
in the local store that a background loop resends.

### M12 · The pre-commit hook blocks your own commits without `hive` on PATH
**Where:** `src/hive/init.py:428`. The hook is installed in the clone's shared hooks
directory and `exec`s `hive`.

**Repro (D8):** with `hive` missing from PATH, a normal user commit fails (`exec: … not
found`). This happens in GUI git clients with a minimal PATH, after uninstalling Hive, or in
other environments.

**Direction:** make the hook a no-op unless `HIVE_SLOT` is set, checked in the shell script
itself before calling `hive`, and tolerate a missing `hive`.

### M13 · Possible deadlock on MySQL or Postgres (reasoned)
**Where:** `service.py:281–287`, `heartbeat()`. It updates the `agents` row, then locks the
`fleet` row (when the agent was offline). `tick()` and most writers do the reverse: they lock
`fleet` first, then update `agents`.

**What happens:** InnoDB or Postgres resolves the lock-order inversion by aborting one
transaction. The resulting `OperationalError` ("Deadlock found…") doesn't match the
`locked`/`busy` check in `app.py`, so the client gets 500 instead of a retryable 503. SQLite
isn't affected: `BEGIN IMMEDIATE` serializes writers.

**Direction:**
- Always lock `fleet` first.
- Also treat deadlock and serialization errors as 503.

---

## 5. Low severity

| ID | Finding | Where | Notes / direction |
|---|---|---|---|
| L1 | **`stash pop` without a matching `stash push`** | `gitops.py:134,188` | `stash push` runs with `check=False`; `stash pop` runs whenever there were outside files. If the push stored nothing or failed, the pop applies an older, unrelated stash, now plausible since `git stash` is pre-allowed. D3 shows this with a simulated push failure. Pop only the stash created, by its ref. |
| L2 | **500 instead of 400 on bad input** | `service.py:387` (`G.normalize` `ValueError`), `app.py` `int()`/`float()` casts, `app.py:53` (`hmac.compare_digest` raises `TypeError` on non-ASCII) | P4, P13, P14. Validate and return 400/401. |
| L3 | **Approved-command output visible to all agents** | `GET /approvals` | P15. Command output can contain secrets, which was the reason streams are admin-only. Restrict `output` to the requester, the orchestrator and the admin, or to the admin only. |
| L4 | **Case-insensitive file systems** | `globs.py` | P16: `src/App.py` and `src/app.py` can be leased by two agents. Normalise case for overlap checks (at least on Windows and macOS). |
| L5 | **Report requests answerable by anyone** | `service.py` `submit_report` | P18. Require `actor == request.target`. |
| L6 | **`[permissions]` not validated on upload** | `service.py` `set_config` | P21: a bogus preset is accepted; every approval request then fails with 500. Validate with `permissions.build_rules` in `set_config`. |
| L7 | **Dead dependencies** | `service.py` `_schedule` | P22: a task whose dependency was cancelled or failed stays open forever. Notify the orchestrator, or mark it `blocked`. |
| L8 | **`[ ]` in globs** | `globs.py:12,49` | D5: `[` makes a glob non-literal, but the regex escapes it, so `src/[ab].py` matches nothing. Support character classes or treat `[` as literal. |
| L9 | **Lost wake on launch failure** | `core.py:407` | Wake reasons are consumed before the CLI starts. If it can't start (missing on PATH, bad config dir), the work is never retried. Consume after a successful start, or re-queue on failure. |
| L10 | **Auto-release race** | `core.py:267` | After a review approval, `auto_release` runs in a thread while the same agent may be mid-turn. Its in-progress edits under the leases get committed and integrated. Skip or defer while `rt.running`. |
| L11 | **Stale MCP tool list** | `src/hive/mcp_server.py:26` | Tools are registered once. After failover, the new orchestrator's session lacks `await_events`, `approval_decide`, etc. until restarted. Register all tools and rely on the server's checks, or restart sessions on role change. |
| L12 | **Daemon config not reloaded** | `core.py` `SlotRuntime.__init__` | Permissions, `main`, debounce and checks come from the worktree's `hive.toml` read once. `config.updated` doesn't reload them. Reload on `config.updated` and after syncing the worktree. |
| L13 | **Approval while paused** | `service.py` `answer_decision` | Approving the plan while paused sets `running` directly, silently unpausing the fleet. Same remedy as M1. |
| L14 | **Unbounded memory per wake** | `wake.py:316` | `raw` keeps every output line for up to an hour; only the last 400 are used. Use a bounded `deque`. |
| L15 | **Silent extension-link failure** | `wake.py` `ensure_gemini_extension` | The result of `gemini extensions link` is ignored; every later `-e hive` wake then fails. Check the return code and log or surface it. |
| L16 | **Branch reset on re-join** | `gitops.py:47` | `worktree add -B` resets an existing `hive/<slot>` branch. Unintegrated commits are left only in the reflog. Refuse, or warn, when the branch is ahead of `main`. |
| L17 | **Message framing escape** | `wake.py` `fmt_message` | A body containing `</hive-message>` can break out of the untrusted-content wrapper in prompts. Escape or encode the closing tag. |
| L18 | **Failover drops roles** | `service.py` `_failover` | The old orchestrator receives only the standby's roles; its own non-coding roles (e.g. `planner`) are lost. Merge the role sets. |
| L19 | **Unprotected writes outside worktrees** | `core.py` `_relpath` returns `None` → allowed | Edits to your main clone or other paths aren't lease-checked. Treat the main clone as protected; consider denying writes outside the slot worktree by default. |
| L20 | **Write transaction for a read** | `service.py` `get_approval` | A write transaction is used to flip expired approvals. Waiting workers poll every 2 s, adding SQLite write load (notable on PythonAnywhere). Expire lazily in a read path, or poll less often. |

---

## 6. Verified working

These were explicitly exercised and behave as intended:
- **Fleet lifecycle:** the start gate (planning, awaiting approval, running) and freeze/thaw
  with lease extension and queued decisions (test suite).
- **Approvals:**
  - one-time use, expiry, no self-approval, forbidden commands refused (test suite);
  - claims refused while frozen (P23 control);
  - dangerous commands across bash, PowerShell and cmd, including nested and encoded forms;
    no false positives on quoted text (test suite).
- **Integration:** lease release of unstaged edits, paths with spaces or Unicode, and the
  pre-commit hook path parsing (test suite, after the earlier `ndex.html` fix).
- **SQLite:** reads never wait for the write lock; idle ticks don't write; in-process writers
  are serialized; consistent backups through the API; `503` plus client retry on a locked
  database (test suite).
- **Shells on Windows:** PowerShell and cmd.exe run commands with embedded quotes; Git Bash
  runs POSIX commands; timeouts are reported (test suite).
- **CLI compatibility:**
  - the generated Gemini policies load in Gemini CLI 0.41;
  - the generated Claude plugin passes `claude plugin validate`;
  - the Gemini extension links only into the slot's private home.

---

## 7. Suggested order of work

1. **H1, H2, H3:** restore the integrity of leases and review. They're independent and small.
2. **M1, M2, M5, M10:** state-machine dead ends and self-review.
3. **M3, M4:** command-parsing gaps in the hard limits.
4. **M6, M7, M8, M11:** daemon robustness (startup, Windows timeouts, races, retries).
5. **M9, M12, M13**, then the low-severity items.

Each fix should come with a regression test. The probes in `scripts/audit/` are written so
they can be turned into tests directly.

---

## 8. Resolution

All findings were fixed in four batches:
1. server integrity;
2. command parsing;
3. git;
4. daemon and CLI.

Each fix has a regression test, and the full suite ran after every batch. After the last
batch it passed three times in a row.

- **Test suite:** **165 passed, 1 skipped** (a POSIX-only test on Windows).
- **Audit probes:** `probe_server.py` and `probe_daemon.py` report **0 reproduced**. The
  control probe still behaves correctly.
- **Real CLIs:** the regenerated Gemini policies load in Gemini CLI 0.41, and both bundles
  pass `claude plugin validate` and `gemini extensions validate`.

| ID | Fix | Regression test |
|---|---|---|
| H1 | **Grant-based confirmation.** `integrate()` issues a one-time **grant** (new `integrations` table), bound to the agent, the sha, and the leases with their fencing numbers. `confirm` requires that grant, re-checks that the leases are still held, and can be used once. Integration also requires naming the leases. | `test_server.py::test_confirm_requires_a_matching_one_time_grant`, `::test_confirm_refused_after_lease_revoked` |
| H2 | **Review is enforced.** With review on, `task_id` is required, the task must be the caller's and in an active state, and an approval only counts while the task is in review. It's cleared on integration. | `test_server.py::test_review_gate_cannot_be_bypassed`, `::test_review_flow` |
| H3 | **Only covered paths are committed.** The release commits just the covered paths (`git commit --only -- <paths>`). Renames report both paths. Branches whose commits touch unleased files are refused (`uncovered_commits`). | `test_gitops.py::test_release_never_commits_staged_files_outside_leases`, `::test_release_refuses_earlier_commits_touching_unleased_files`, `::test_release_integrates_renames_with_both_paths_leased`, `::test_release_integrates_a_deleted_leased_file` |
| M1 | **Pausing over a decision works.** Pause keeps the state from before the freeze. Answering while paused stays paused, and `resume` enters frozen or awaiting approval if a decision is still open. Queued decisions activate correctly while paused. | `test_server.py::test_answering_while_paused_keeps_pause_and_resume_restores`, `::test_queued_decision_activates_while_paused` |
| M2 | **Pre-assigned drafts start.** On approval they become `claimed` and emit `task.assigned`. | `test_server.py::test_pre_assigned_draft_is_handed_over_on_approval` |
| M3 | **Git global options are skipped.** `-C`, `-c`, `--git-dir`, `--no-pager`, … are skipped before matching rules or detecting pushes. | `test_permissions.py::test_git_global_options_*` |
| M4 | **`find` and `xargs` deletes need approval.** `find -delete` and `find -exec <delete>` need approval; `xargs` is unwrapped and its command checked. | `test_permissions.py::test_find_and_xargs_deletes_need_approval`, `::test_harmless_find_and_xargs_not_flagged` |
| M5 | **No self-review.** Verdicts from the task's assignee are refused. | `test_server.py::test_no_self_review_and_reviewer_fallback` |
| M6 | **The daemon waits for the server.** It keeps wake reasons while the agent's identity is unknown and retries the identity lookup in the heartbeat loop. `hive up` waits up to 30 s for the server and otherwise fails with a clear message, never falling back to headless mode. | `test_daemon.py::test_triggers_are_kept_while_identity_is_unknown`, `::test_wait_for_identity_reports_unreachable_slots`, `::test_hive_up_fails_loudly_when_server_unreachable` |
| M7 | **Timeouts stop everything.** They kill the whole process tree (`taskkill /T` on Windows, process groups on POSIX), for both CLI wakes and approved commands. | `test_daemon.py::test_wake_timeout_kills_the_whole_process_tree`, `::test_approved_command_timeout_kills_grandchildren` |
| M8 | **No startup race.** `hive up` marks the orchestrator attached before any background loop starts. | `test_daemon.py::test_hive_up_marks_orchestrator_attached_before_loops_start` |
| M9 | **Configs that don't fit current agents are refused.** A config removing roles that agents hold gets 409 (`roles_in_use`); invalid role definitions get 400. | `test_server.py::test_config_is_validated_against_agents_and_permissions` |
| M10 | **Reviewer fallback.** With no eligible reviewer, the orchestrator reviews. | `test_server.py::test_no_self_review_and_reviewer_fallback` |
| M11 | **Confirmations aren't lost.** Confirmation is retried; if the server stays unreachable, the release returns `integrated_unconfirmed` and the daemon resends from its heartbeat loop. | `test_gitops.py::test_confirm_is_retried_then_queued`, `::test_daemon_resends_queued_confirmations` |
| M12 | **Your own commits are unaffected.** The git hook exits immediately unless `HIVE_SLOT` is set, so your commits never need `hive`. `hive up` refreshes old hooks and keeps a custom `--hive-cmd`. | `test_daemon.py::test_git_hook_ignores_user_commits_and_fails_closed_for_agents` |
| M13 | **No lock-order deadlock.** `heartbeat` locks the fleet row first, and deadlock or serialization errors map to a retryable 503. | `test_server.py::test_deadlock_errors_are_retryable` |
| L1 | **Only Hive's own stash is popped.** It's tracked by the stash ref before and after the push. | `test_gitops.py::test_release_pops_only_its_own_stash`, `::test_release_does_not_pop_when_nothing_was_stashed` |
| L2 | **Bad input gets 400.** It returns 400, not 500 (`num()`/`nums()` helpers, path hints), and a non-ASCII token gets 401. | `test_server.py::test_bad_input_is_400_not_500` |
| L3 | **Output stays private.** Approved-command output is visible only to the requester and the user. | `test_server.py::test_command_output_only_for_requester_and_user` |
| L4 | **Leases ignore case.** Lease overlap checks are case-insensitive. | `test_permissions.py::test_globs_case_and_brackets` |
| L5 | **Only the addressee answers a report request.** | `test_server.py::test_only_the_addressee_answers_a_report_request` |
| L6 | **`[permissions]` is validated on upload.** | `test_server.py::test_config_is_validated_against_agents_and_permissions` |
| L7 | **Dead dependencies are reported.** Dependents of a cancelled or failed task are blocked, and the orchestrator is told. | `test_server.py::test_dependents_of_cancelled_task_are_blocked_and_reported` |
| L8 | **`[` in globs is literal.** | `test_permissions.py::test_globs_case_and_brackets` |
| L9 | **Failed launches are retried.** Wake reasons and messages are consumed only once the CLI has started. | `test_daemon.py::test_failed_cli_launch_keeps_the_work_pending` |
| L10 | **No auto-integration mid-turn.** Auto-integration waits for the running turn and blocks new wakes meanwhile. | `test_daemon.py::test_auto_release_waits_for_the_running_turn`, `::test_maybe_wake_does_not_start_while_releasing` |
| L11 | **Interactive sessions hear about role changes,** and are told to relaunch for the new tools. | `test_daemon.py::test_role_change_notice_for_interactive_sessions` |
| L12 | **The daemon reloads `hive.toml`** on `config.updated` and after rebasing onto a new `main`. | `test_daemon.py::test_config_reload_on_update` |
| L13 | **Approving the plan while paused keeps the fleet paused;** tasks are scheduled on resume. | `test_server.py::test_plan_approved_while_paused_stays_paused` |
| L14 | **Bounded wake memory.** Wake output is held in a bounded `deque(maxlen=400)`. | covered by the wake tests |
| L15 | **Extension problems are reported.** `ensure_gemini_extension` reports a missing bundle or a failed link (logged, streamed, printed). | `test_daemon.py::test_gemini_extension_problems_are_reported` |
| L16 | **Re-join protects unintegrated work.** It refuses to reset a branch that has unintegrated commits. | `test_gitops.py::test_rejoin_refuses_to_reset_a_branch_with_unintegrated_commits` |
| L17 | **Message framing is safe.** `</hive-message` in a message body is escaped (case- and whitespace-insensitive). | `test_daemon.py::test_message_bodies_cannot_close_the_wrapper` |
| L18 | **Failover keeps roles.** The old orchestrator keeps its other roles. | `test_server.py::test_failover_keeps_old_orchestrators_other_roles` |
| L19 | **Other checkouts are protected.** Writes into any other checkout of the repository (other slots, the user's clone, nested worktrees) are denied. | `test_daemon.py::test_writes_into_the_users_clone_are_denied` |
| L20 | **Reads stay reads.** `GET /approvals/<id>` is a read; expiry is recorded only when someone tries to use the approval. | `test_server.py::test_reading_an_expired_approval_does_not_write` |

**Found and fixed along the way:**
- **Approval race:** a race in the daemon could queue a redundant wake after a blocked
  `request_approval` call had already consumed the decision. Consumed decisions are now
  remembered (`consumed_approvals`). Covered by
  `test_e2e.py::test_dangerous_command_runs_after_orchestrator_approval`.
- **Test-harness race:** the end-to-end tests' fake agents appended to one shared log file,
  so a read could catch a half-written line. Each fake agent now writes its own file, and the
  reader skips an incomplete final line. This was the source of the one-off unexplained test
  failure seen earlier.

**Compatibility:** `/integrate/confirm` now requires the grant returned by `/integrate`.
**Upgrade the server and every agent machine together.** An older daemon's integration would
reach `main` but leave its leases held until it's upgraded.
