# Token efficiency audit

**Date:** 2026-10-20
**Scope:** everything Hive puts into an agent's context: tool schemas, skills, wake prompts,
hook injections, tool results, `await_events` digests, and how sessions are reused. `master`
at `d832045`, unmodified.
**Method:** measurement on the real code, then a request-by-request model of a fleet's work.
No code was changed.

---

## Summary

| | Now | Achievable | How |
|---|---|---|---|
| Tokens a programmer uses per task, vs the same work in a plain CLI session | **3.5×** | **1.1–1.3×** | Fresh session per task; finish a task in one call |
| Orchestrator, per batch of events | 49k (cache-weighted) | 21k (−58%) | Summary lists, less frequent `state.md` and status writes, rotate its session using `state.md` as memory |
| Busy fleet day (7 agents, 30 tasks), cache-weighted input | **≈ 11.9M** | **≈ 4.2M (−65%)** | All of the above |
| Share of tokens spent on the actual coding work | **≈ 19%** | **≈ 55%** | |

Two findings matter more than the rest:

1. **Workers keep one session for 40 wakes** (`SESSION_ROTATE_AFTER = 40`). Every request in a
   task then re-reads the previous tasks' history, up to the CLI's compaction limit. This one
   design choice accounts for most of the overhead: switching to a fresh session per task cuts
   a programmer's per-task input by **62%** on its own (§4).
2. **`task_list` has no limit and returns full specs and notes.** It's 24k tokens after one
   busy day, 166k after a week and **714k after 30 days**. The orchestrator's skill calls it
   at the start of every session. After about a week, that single call **no longer fits in a
   200k context window**. This is a correctness problem as much as an efficiency one (§3.2).

The static parts (tool schemas, skills, wake prompts) are already modest. Trimming them
saves about 7%: worth doing, but it isn't where the tokens go.

---

## 1. How tokens are counted here

- **Every model request re-reads the whole context:** the CLI's own system prompt and
  tools, Hive's tool schemas, the session's history, and the turn so far. A task with 28
  requests reads Hive's 2.9k tokens of tool schemas 28 times. So a cost per request matters
  in proportion to how many requests carry it, and *anything that stays in the history* is
  paid again on every later request.
- **Two totals are reported:**
  - **Raw input**: every token read, every request.
  - **Cache-weighted input**: with prompt caching, a re-read prefix costs about a tenth of a
    new token; newly added tokens cost 1.25× (Anthropic's API pricing ratios). How
    subscription usage limits weigh cached tokens isn't published, so the real cost lies
    somewhere between the two. Every conclusion below holds under both.
- **Tokenizer:** OpenAI's `o200k_base` (via `tiktoken`) as a proxy; `cl100k_base` agreed within
  1%. Claude's tokenizer isn't available offline and typically yields somewhat more tokens for
  the same text, so absolute numbers may be 10–20% low. Ratios, which carry the conclusions,
  are unaffected.
- **Realistic text:** task specs, notes, reports and messages were filled with sentences from
  Hive's own documentation (English with code), not random letters, which tokenize much
  worse.

---

## 2. Static costs (measured)

What every session carries, from the generated bundles and the real MCP server
(`measure_static.py`):

| Item | Tokens | When it's in context |
|---|---|---|
| Tool schemas, programmer (26 tools) | **2,903** | every request |
| Tool schemas, reviewer (24 tools) | 2,587 | every request |
| Tool schemas, orchestrator (35 tools) | **4,121** | every request |
| … of which descriptions | 610 / 867 | |
| … trimmable boilerplate (`title` keys, `anyOf[X, null]`, `default: null`) | **−29%** (2,903 → 2,062; 4,121 → 2,943) | |
| `hive-worker` skill | 954 | once per session, after the agent opens it |
| `hive-orchestrator` skill | 1,238 | once per session |
| Role prompts | 19–144 | first wake of a session |
| Wake prompt, programmer (first / resumed) | 936 / 743 | every wake |
| `GUIDE` reminders | 103 | first wake of a session |
| SessionStart hook injection | 115 | **every wake** (see below) |
| UserPromptSubmit hook header | 19 | every wake |
| Gemini `HIVE.md` | 54 | every Gemini session |
| Permission files (`settings/*.json`, policies, `.rules`) | 1–5k | **never**: read by the CLI, not sent to the model |

**SessionStart fires on every resumed wake.** The hook is registered with `matcher: "*"`.
Claude Code fires SessionStart for `resume` as well as `startup` (per its hooks
documentation; not observed live, which would use account quota). `handle_hook` injects the
role prompt and project prompt unconditionally, while `build_prompt` deliberately adds them
only `first_in_session`. Small (115 tokens per wake) but unintended, and it grows with your
`.hive/prompts/roles/*.md` files (up to 32 KB each).

---

## 3. Dynamic costs (measured)

### 3.1 Per-task protocol calls

What each of a worker's protocol tool calls returns (`measure_protocol.py`). Each result joins
the session history:

| Call | Returns | Needed? |
|---|---|---|
| `task_get` | 527 | **No.** The wake prompt already contains the spec, path hints and notes, yet the skill's step 1 says to call it. |
| `task_update(in_progress)` | **528** | Echoes the full task row, spec included. An ack would be ~25. |
| `lease_acquire` | 79 | yes |
| `lease_release` | 46 | yes |
| `task_complete` | **687** | Echoes the full task again. |
| `report_submit` | 8 (+525 output to write it) | yes |

So the same 1.5 KB spec enters the context **four times per task**: wake prompt, `task_get`,
`task_update`, `task_complete`. Every tool result is also serialized with
`json.dumps(indent=1)`; compact JSON saves 5–33% per result (most on small, nested results
like `roster`: −33%).

### 3.2 List calls grow without bound

Measured on a fleet with 1, 7 and 30 busy days of history (30 tasks/day, 150 messages, 40
status updates; `measure_dynamic.py`):

| Tool result | Day 1 | Day 7 | Day 30 | Summary view, day 30 |
|---|---|---|---|---|
| `task_list` (no filter) | 24.0k | **166k** | **714k** | 35k (id, state, kind, assignee, title, deps) |
| `task_list` (open states only) | 2.7k | 18.8k | 80.6k | 5.8k |
| `report_list` (20 newest, full bodies) | 12.6k | 12.3k | 12.3k | 1.2k (summaries only) |
| `status_read(10)` (full bodies) | 4.7k | 5.0k | 4.6k | 0.6k |
| `task_get` | 0.5k | 0.5k | 0.5k | |
| `whoami` (programmer / orchestrator) | 0.5k / 0.7k | | | |
| `roster` (7 agents) | 0.7k | | | |
| `await_events` result (3 events, 2 messages) | 0.4k | | | |

- `task_list` returns every task ever created, each with its full spec and notes, and has no
  `limit`. The orchestrator's skill calls it at the start of every session ("`task_list`,
  `lease_status`, `status_read`, and `report_list(kind="report")`"). Past day 7 the result
  exceeds a 200k context on its own: depending on the CLI, the result is truncated, the
  session is compacted immediately, or the call fails. Either way the orchestrator can no
  longer see the task board reliably.
- `report_list` and `status_read` always return full bodies (2 KB reports, 1.5 KB status
  bodies) when the orchestrator mostly needs the one-line summaries.

---

## 4. The model: where the tokens actually go

`model.py` replays a programmer's tasks and the orchestrator's event batches **request by
request**, using the measured sizes above. Assumptions, varied in §4.4:

| Assumption | Default |
|---|---|
| The CLI's own system prompt and built-in tools | 18k tokens |
| Work per task | 20 tool calls × 1,200 tokens (reading files, editing, running tests) |
| Output per request (text and arguments) | 120 tokens |
| CLI auto-compaction | at 160k tokens, down to a 15k summary |
| Reviews | on (`review = "reviewer"`) |
| Orchestrator | 75 event batches per busy day; one action and one report check (3k) per batch; `state.md` 1.5k |

### 4.1 Programmer, per task

| Design | Raw input | Cache-weighted | Requests | vs now |
|---|---|---|---|---|
| **Now** | **2.71M** | **273k** | 28.1 | 100% |
| Quick wins: trimmed schemas, compact JSON, no `task_get`, ack-only `task_update`/`task_complete`, SessionStart on startup only | 2.53M | 255k | 27.1 | 93% |
| + fresh session per task | 1.01M | 103k | 28.0 | **38%** |
| + finish in one call (`lease_release` also completes the task and files the report), so no second wake after review | 0.86M | 87k | 25.0 | **32%** |
| *Reference: same work in a plain, fresh CLI session, no Hive at all* | *0.77M* | *77k* | | |

**Why session reuse dominates.** A task adds roughly 27k tokens to the session. With 40 wakes
per session (two per task with review on), the history quickly reaches the compaction limit
and then saw-tooths between 15k and 160k: about 80k on average. Every one of the task's ~28
requests re-reads that history. The tasks are independent (the wake prompt carries the spec),
so that history buys almost nothing. Starting a fresh session per task removes it, while the
CLI's own prefix (system prompt plus Hive's schemas) stays cacheable across sessions.

**Why the quick wins barely register.** They remove about 2–3k tokens per request from the
prefix, but the prefix is about 100k of history. They become worth ~15% once sessions are
fresh, and are still worth doing.

**Hive's overhead factor** (tokens used, divided by the same work in a plain CLI session):
**3.5× now → 1.3× with fresh sessions → 1.1× with a one-call finish.** The remaining 1.1× is
the irreducible cost of coordination: tool schemas, the wake prompt, the lease and release
calls, and the report.

### 4.2 Orchestrator, per event batch

| Design | Raw input | Cache-weighted | Output | vs now |
|---|---|---|---|---|
| **Now** (one long session; `status_publish` and full `state.md` rewrite after every batch) | 493k | 49k | 2.0k | 100% |
| Summary lists, trimmed schemas | 469k | 47k | 2.0k | 95% |
| + `state.md` every 5 batches, status every 3 | 323k | 32k | 1.0k | 66% |
| + fresh session when history passes 60k, re-reading `state.md` | 205k | 21k | 1.0k | **42%** |

The skill tells the orchestrator to rewrite `state.md` in full and publish a status update
after **every** batch. That's ~1.9k **output** tokens per batch (the most expensive kind),
plus their copies in the history. Over a busy day: ~140k output tokens, mostly restating the
previous version.

The orchestrator's interactive session is never rotated. It already keeps `state.md` as
memory across sessions, so starting fresh when its history grows is cheap and loses little.
The summary-list change shows only 5% here because the model amortizes the session-start
cost over 75 batches, but it is what stops the orchestrator from breaking after a week
(§3.2).

### 4.3 A busy fleet day

30 tasks, 75 orchestrator batches, cache-weighted input:

| Design | Workers | Orchestrator | Total |
|---|---|---|---|
| **Now** | 8.2M | 3.7M | **11.9M** |
| Quick wins + summary lists | 7.6M | 3.5M | 11.2M |
| + fresh worker sessions, leaner orchestrator writes | 3.1M | 2.4M | 5.5M |
| + one-call finish, orchestrator session rotation | 2.6M | 1.5M | **4.2M** |

The coding work itself accounts for ≈ 2.3M (30 tasks × 77k). So today **≈ 19%** of the
fleet's input tokens go to the actual work, and **≈ 55%** after the changes. The orchestrator
remains the main coordination cost, which is what you'd expect from a design where one agent
reviews everything.

### 4.4 Sensitivity

Programmer, cache-weighted input per task, now → all worker changes:

| Assumption changed | Now | After | Saving |
|---|---|---|---|
| Defaults | 273k | 87k | 68% |
| Light tasks (10 steps × 800) | 167k | 41k | 76% |
| Heavy tasks (40 steps × 2,000) | 473k | 284k | 40% |
| Smaller CLI prompt (10k) | 254k | 67k | 74% |
| Larger CLI prompt (28k) | 284k | 112k | 60% |
| Later compaction (190k) | 315k | 87k | 72% |

Raw input savings are the same percentages to within 1%. The saving is smallest when tasks
are heavy, because then the task's own work dominates either way. It never drops below 40%.

---

## 5. Recommendations, by value

| # | Change | Saves | Effort | Where |
|---|---|---|---|---|
| 1 | **Fresh session per task** for workers. Rotate when a wake's task differs from the session's last task, or after `task_complete`, instead of every 40 wakes. Keep resuming within a task (review rounds, conflicts), where history does help. | **−62%** of worker input | small | `daemon/core.py` `run_wake`, `wake.SESSION_ROTATE_AFTER` |
| 2 | **Bound and slim the list tools.** `task_list` defaults to unfinished tasks with summary fields and a `limit`; full rows only through `task_get`. `report_list` and `status_read` return summaries; bodies via `report_get`. Update the orchestrator skill's session-start step. | prevents context overflow after ~1 week; −90–95% per call | small | `mcp_server.py`, `service.list_tasks`, skill text |
| 3 | **Orchestrator writes less.** `state.md`: append or patch, or rewrite every few batches. `status_publish`: only when something changed for the user. | −34% orchestrator input, −50% its output | skill text only | `init.py` `ORCHESTRATOR_SKILL` |
| 4 | **Rotate the orchestrator's session** once history passes a threshold (e.g. 60k), starting fresh from `state.md`. Needs `hive up` to relaunch the CLI fresh rather than `--resume` at that point. | −36% more | medium | `daemon/core.py`, `wake.py` launch |
| 5 | **One-call finish:** `lease_release(…, result=…, report=…)` completes the task and files the report. After an approved review, the daemon finishes it without waking the agent again. | −15% of worker input; one fewer wake per reviewed task | medium | `mcp_server.py`, daemon `auto_release` |
| 6 | **Ack-only results** for `task_update`, `task_complete`, `task_claim` (e.g. `{"id", "state"}`), and drop `task_get` from the worker skill's step 1 (the wake prompt has the spec). | ~1.7k tokens per task, kept in history | trivial | `mcp_server.call` / `daemon.call_tool`, skill text |
| 7 | **Trim tool schemas** (drop `title`, collapse `Optional`) and serialize results as compact JSON. | −29% schemas, −5–33% results | trivial | `mcp_server.py` |
| 8 | **SessionStart only on `startup`** (matcher), or have `handle_hook` skip the role and project prompt when the session is resumed. | 115+ tokens per wake, more with large `.hive/prompts/roles/` files | trivial | `init.py` `_hooks`, `core.handle_hook` |

Changes 6–8 matter little on their own (§4.1), but they compound once sessions are fresh,
and they're nearly free.

**Not worth changing:** the skills (≈1k, once per session), the wake prompt (≈0.75k per
wake), `await_events` digests (≈0.4k), message formatting. All are already lean.

**Further, not modelled:** the CLIs' own deferred tool loading (exposing tool names only and
loading schemas on demand) would remove most of the schema cost where a CLI supports it for
MCP servers. Splitting rarely used tools (`approval_list`, `report_list`, `roster`) behind a
single "more" tool would have a similar effect for all three CLIs.

---

## 5a. Status on the `token-optimization` branch

| # | Change | Status |
|---|---|---|
| 1 | Fresh session per task; rotation by measured context size (`[sessions] max_context_tokens`, from Claude Code's per-request usage) and turn count; handoff note when a task continues in a fresh session | **done** (`daemon/core.py` `session_rotation`, `handoff_note`) |
| 2 | `task_list`: unfinished tasks by default, summary fields, `limit` (50) with a "more" note, `since_id`, `mine`. `report_list` and `status_read` return summaries (`report_get`, `status_read(full=true)` for text). Orchestrator skill's session start reads `state.md` first and only what changed. | **done**. Filtering happens in the daemon; the server still sends full rows over HTTP (no token cost, some bandwidth). |
| 3 | Orchestrator writes `state.md` by editing what changed, and publishes status only when the user should know | **done** (skill text and digest hint) |
| 4 | Orchestrator session rotation | **partly**: `hive up` starts it fresh after `orchestrator_fresh_after_hours` (12 h) idle. Rotating a long-running interactive session by size isn't implemented (the daemon can't see its token usage). |
| 5 | One-call finish: `lease_release(…, result, report_summary, report_body)`; after an approved review Hive completes the task and files the report without waking the agent | **done** |
| 6 | Ack-only results for task changes and `review_verdict`; worker skill no longer re-reads the task with `task_get`; slim `whoami` and `roster` | **done** |
| 7 | Trimmed tool schemas (titles and `default: null` dropped, `Optional` as a type list, still accepting `null`); compact JSON results | **done**: programmer schemas 2,903 → 2,302 tokens (−21%), orchestrator 4,121 → 3,145 (−24%). Less than the 29% estimated in §2, because nullability is kept. Gemini slots get plain types instead (no `anyOf`, no type lists), which Gemini's function calling is reported to require; not yet confirmed live. |
| 8 | SessionStart / UserPromptSubmit add nothing in headless turns (the wake prompt has it all) and skip the role prompt on `resume` | **done** |
| – | Resumed wakes send only task notes the session hasn't seen | **done** |
| – | Stop hook: a turn can't end with uncommitted leased changes until the agent recorded a progress note this turn, so a fresh session can pick the task up | **done** |
| – | Default `max_context_tokens` 120k (not 80k), so mid-task rotation stays rare and Claude Code's own compaction usually acts first | **done** |
| – | Routine events (completed tasks, new reports, review verdicts) batched for the orchestrator | **done** (`ORCH_BATCHED`) |
| – | Token telemetry: a `· tokens: …` line per headless turn in the live output and the slot's log | **done** (Claude Code: input, output, context; Codex and Gemini CLI: totals). Not yet aggregated per task on the server or shown on the dashboard. |
| – | `scripts/simulate_load.py` fixed for the integration grant (§6) | **done** |
| – | Regression budgets for schema size and result shapes (`tests/test_tokens.py`) | **done** |

Not implemented, with reasons:
- **Separate dispatcher for routine orchestrator events** (headless, from `state.md`) beside
  the interactive chat: the biggest remaining saving for the orchestrator, but it changes how
  the orchestrator works and needs a design decision first.
- **Auto-approving commands by policy:** a security decision, not an optimisation to make
  silently.
- **Deferring rarely used tools** behind one tool, or hiding tools by fleet state: depends on
  each CLI's support for tools appearing mid-session; worth a separate experiment.
- **Lower caps on report and message bodies**, **per-task token budgets**, **a budgeted repo
  map in wake prompts**: useful, but each changes behaviour users rely on and should be decided
  separately.
- **Server-side pagination of `/tasks`:** saves bandwidth, not tokens.

The cost model's "one-call finish" scenario (§4.1) corresponds to what the branch implements
for workers. That's about 68% less input per task in the default case. Real numbers can now be
read from the `tokens` lines in the live output.

---

## 6. Side finding

`scripts/simulate_load.py` no longer runs. It calls
`confirm_integration(who, sha, fences, task_id, paths)`, but since the one-time integration
grant was added the signature is `confirm_integration(actor, grant, sha, paths)`
(`TypeError` at line 73). The capacity numbers in DEPLOYMENT.md §3 can't be regenerated until
it passes the grant returned by `integrate()`. The measurement scripts for this audit use
the new signature.

---

## 7. Reproducing

The scripts live outside the repository (they changed nothing in it). Each runs with the
project's virtualenv (`.venv/Scripts/python` on Windows), with `tiktoken` reachable on the
path given as the first argument:

| Script | Measures |
|---|---|
| `measure_static.py` | bundle files, role prompts, wake guide, MCP tool schemas per role |
| `measure_schema_trim.py` | schema boilerplate and the trimmed equivalent |
| `measure_protocol.py` | what each per-task protocol call returns |
| `measure_dynamic.py` | list tools, wake prompts, digests and hook injections after 1, 7 and 30 busy days |
| `model.py` | the request-by-request model, scenarios and sensitivity table |

Measured values are the ones in §2–§3. Everything in §4 depends on the stated assumptions;
§4.4 shows how far.
