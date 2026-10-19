# Hive — Multi-Agent Harness Specification

Version 0.1 · Status: draft, implementation in progress

Hive connects several subscription-based coding agents (Claude Code, Gemini/Antigravity CLI), each
running as its official CLI on a user-owned machine, into one collaborating fleet that shares a git
repository, a message board, a task board and a lease (lock) system.

Hive never extracts or proxies account credentials. Every agent is the vendor's own CLI, logged in
with its own subscription, driven through documented features (headless mode, MCP, hooks).

---

## 1. Goals and non-goals

**Goals**
- Agents on different machines communicate (public channels, DMs, threads, priorities, interrupts).
- Work is split into tasks, routed by role, and coordinated by one non-coding orchestrator.
- Concurrent edits are prevented by path-scoped leases; releasing a lease integrates the work into
  `main` and every other machine syncs automatically.
- The user stays in control: the fleet does not start coding until the user approves the plan, and
  when an agent is stuck the fleet freezes until the user decides.
- One-command integration: `hive init` in a repo, `hive join` per agent slot, `hive up` per machine.
- The server runs on cheap/simple hosting, including PythonAnywhere (WSGI, no WebSockets).

**Non-goals (v1)**
- Using vendor APIs or API keys. Running agents in the cloud. Real-time streaming of agent output.

---

## 2. Glossary

| Term | Meaning |
|---|---|
| **Server** | Public `hive-server` (Flask/WSGI). Single source of truth. |
| **Machine** | A computer running one `hived` daemon. |
| **Slot / Agent** | One agent identity (e.g. `claude-1`) = one CLI account config dir + one git worktree on a machine. |
| **Role** | A named set of permissions and duties (orchestrator, programmer, …). |
| **Lease** | A time-limited exclusive claim on a set of path globs, carrying a fencing token. |
| **Fleet state** | Global state machine gating what agents may do (§7). |
| **Decision** | A question the orchestrator asks the user; may freeze the fleet. |
| **Wake** | A headless CLI turn started by the daemon in response to events. |

---

## 3. Architecture

```
            ┌──────────────────── hive-server (WSGI) ─────────────────────┐
            │ REST /api/v1 · auth · events log · messages · tasks · leases │
            │ fleet state · decisions · status feed · dashboard           │
            │ GitHub ref compare-and-swap for main                         │
            │ DB: SQLite (dev) / MySQL / Postgres via SQLAlchemy Core       │
            └───────────────▲──────────────────────────────▲──────────────┘
                 HTTPS poll │                              │ HTTPS poll
        ┌───────────────────┴─────────┐        ┌───────────┴─────────────────┐
        │ hived (machine A)            │        │ hived (machine B)            │
        │  local API 127.0.0.1:<port>  │        │                              │
        │  slot claude-1 → wt/claude-1 │        │  slot gemini-1 → wt/gemini-1 │
        │   ├ hive-mcp (stdio)         │        │   ├ hive-mcp (stdio)         │
        │   └ hooks (hive hook …)      │        │   └ hooks                    │
        └──────────────────────────────┘        └──────────────────────────────┘
```

- Agents never talk to the server directly. The CLI spawns `hive mcp --slot <name>` (stdio MCP)
  and `hive hook <event> --slot <name>` (hooks). Both talk to the local daemon over
  `127.0.0.1` HTTP authenticated with a per-machine local secret.
- The daemon owns: server session, polling, local inbox cache, git operations, wake scheduling,
  process supervision.
- Transport is pluggable (`poll` in v1; `sse`/`ws` later). Polling: one `GET /events?since=`
  per slot every `poll_interval` (default 3 s).

---

## 4. Identity and authentication

- **Admin token**: `HIVE_ADMIN_TOKEN` env on the server. Used by the user/dashboard/CLI for
  enrollment, decisions, fleet control.
- **Agent token**: issued by `POST /api/v1/agents` (admin). Shown once; stored server-side as
  SHA-256. Daemon stores it in the machine config (`~/.hive/machine.toml`, user-only perms).
- Every agent request: `Authorization: Bearer <agent-token>`. Server resolves the agent and its roles.
- Agent names: `^[a-z][a-z0-9-]{1,31}$`. `user` and `system` are reserved author names.

---

## 5. Roles

### 5.1 Role definition

Roles are defined in `hive.toml` under `[roles.<name>]` (built-ins have defaults that can be
overridden). Fields:

| Field | Type | Meaning |
|---|---|---|
| `mandatory` | bool | Fleet cannot start without it. |
| `min`, `max` | int | Allowed number of holders. |
| `can_code` | bool | Whether the role may hold leases and edit source. |
| `write_globs` | [glob] | Paths editable *without* a lease (e.g. `.hive/**`). |
| `tools` | [name] or `"*"` | Hive MCP tools permitted (server-enforced). |
| `task_kinds` | [kind] | Task kinds this role accepts. |
| `fallback_to` | role | Who covers this role's task kinds when nobody holds it. |
| `wake_on` | [event-type] | Event types that wake an idle holder. |
| `prompt` | string | Role instructions injected into the agent's context. |

### 5.2 Built-in roles

| Role | mandatory | min/max | can_code | task_kinds | fallback_to |
|---|---|---|---|---|---|
| orchestrator | yes | 1/1 | no | — | — |
| programmer | yes | 1/∞ | yes | code, test, docs | — |
| planner | no | 0/∞ | no | plan | orchestrator |
| reviewer | no | 0/∞ | no | review | orchestrator |
| tester | no | 0/∞ | yes (tests/**) | test | programmer |

Custom roles may be added freely. An agent may hold several roles, except that `orchestrator`
cannot be combined with any role where `can_code = true`.

### 5.3 Validation (`hive up`, `POST /fleet/start`)
- Exactly one orchestrator; ≥1 programmer; every role within `min/max`.
- Every task kind is routable (some holder exists or a `fallback_to` chain resolves to a held role).

### 5.4 Enforcement layers
1. **Server**: every tool call checked against the caller's `tools` list.
2. **Local hooks**: `PreToolUse` denies file writes outside (held leases ∪ `write_globs`); roles
   with `can_code = false` get only `write_globs`.
3. **Prompt**: role instructions in context.

---

## 6. Orchestrator

- Exactly one active orchestrator, optional `standby` list in `hive.toml`.
- Capabilities: everything except editing source code — tasks (create/assign/reassign/cancel),
  messages including `interrupt`, decisions, status feed, force lease release, role reassignment
  proposals, fleet pause/resume, read-only repo access.
- **Native interactive session.** On the orchestrator's machine, `hive up` launches the vendor
  CLI itself (`claude` / `gemini`) in the terminal, with Hive capabilities attached for that
  session only (§20), resuming the stored session when there is one. The user talks to it
  directly. The `hive-orchestrator` skill holds its operating procedure.
- **Event-driven, not always busy.** When idle it calls `await_events`, which blocks without
  using tokens until events are due, then returns a digest. The user can interrupt the wait at
  any time. Hooks also deliver queued events with the user's next prompt, and the Stop hook
  points it back to `await_events` when urgent events are pending. Delivery:

| Event | Delivery |
|---|---|
| `user.message`, `decision.answered`, `report.submitted` | immediate |
| `task.completed`, `task.failed`, `task.blocked`, `agent.stuck`, `review.verdict` | immediate |
| `agent.quota_exhausted`, `agent.offline`, `lease.expired`, `merge.conflict` | immediate |
| `task.progress`, normal messages | batched (debounce window, default 90 s) |

- `hive up --headless` keeps the older mode: the daemon wakes the orchestrator headlessly per
  event, plus a heartbeat every `heartbeat_hours`.
- **Reports, not streams.** The orchestrator never sees agents' live output; only the admin
  does (§20). Workers file reports (`report_submit`) on their own; the orchestrator can ask for
  one (`report_request`). It cross-checks reports against the repository and keeps only a
  summary in memory, which avoids context rot and keeps secrets out of its context.
- **Memory**: `.hive/orchestrator/state.md` in its worktree (git-ignored, rewritten after each
  batch of events) plus `task_list`, `status_read` and `report_list`.
- **User-facing**: after each batch of events it calls `status_publish` (level
  `fyi | decision-needed | alert`). Decisions are answered only by the user: via the dashboard,
  `hive decide`, or `! hive decide …` typed in the orchestrator's own session.
- **Failover**: if the orchestrator is quota-exhausted or offline > `failover_after`, the server
  promotes the first available standby and emits `orchestrator.failover`.

---

## 7. Fleet state machine

```
 setup ──start──▶ planning ──decision(start_approval) raised──▶ awaiting_approval
                     ▲                                              │approve
                     └───────────────reject (note)──────────────────┤
                                                                     ▼
          frozen ◀──decision(freeze=true) raised── running ──pause──▶ paused
            │                                        ▲  ▲               │
            └──────────decision answered─────────────┘  └────resume─────┘
```

| State | Orchestrator | Planner | Other roles |
|---|---|---|---|
| setup | idle | idle | idle |
| planning | active | active | idle (no wakes, cannot claim tasks or leases) |
| awaiting_approval | waits for answer | idle | idle |
| running | active | active | active |
| frozen | active (may only message/status/decide) | frozen | frozen |
| paused | frozen | frozen | frozen |

**Start gate.** After `hive up`/`POST /fleet/start` the fleet enters `planning`. The orchestrator
(with planner help) writes the plan to `.hive/plans/` and creates tasks in state `draft`, then calls
`decision_request(kind="start_approval", …)`. The fleet enters `awaiting_approval`. On
`approve`, all `draft` tasks become `open` and the fleet enters `running`. On `reject` the
answer's note is delivered to the orchestrator and the fleet returns to `planning`.

**Stuck → freeze.** Any agent may call `report_stuck(task_id, details)` → event `agent.stuck` →
orchestrator wake. If the orchestrator cannot resolve it, it calls
`decision_request(kind="stuck", freeze=true, …)` (stuck decisions default to `freeze=true`). Effects
of `frozen`:
- `PreToolUse` hook denies all tools for frozen roles with an explanation; `Stop` hook allows the
  agent to end its turn; daemons start no new wakes for frozen roles.
- Server rejects `lease_acquire`, `task_claim`, `lease_release` (integration) from frozen roles.
- Lease expiry clocks are suspended: on unfreeze every active lease's `expires_at` is extended by the
  frozen duration.
- When the decision is answered: fleet → `running`, `fleet.unfrozen` event, daemons re-wake agents
  that were mid-task with a "resume" prompt containing the decision outcome.

Only one blocking (`freeze` or `start_approval`) decision may be open at a time; further ones queue.

---

## 8. Messaging

- **Channels**: `#general` (default), `#user` (user ⇄ orchestrator), `#task-<id>` (auto-created),
  custom ones. **DMs**: `recipient` set, `channel = null`.
- **Message**: `id, ts, channel, author, recipient, priority, thread_id, body, mentions[]`.
  Body ≤ 8 KB. Rate limit: 20 messages / minute / agent (server, 429).
- **Priorities and delivery**:

| Priority | Who may send | Delivered |
|---|---|---|
| info | anyone | inbox only (`inbox` tool) |
| normal | anyone | next turn boundary (`UserPromptSubmit`/wake prompt) |
| high | anyone | after the next tool call (`PostToolUse` context injection) |
| interrupt | orchestrator, user | after the next tool call, framed "stop and handle first"; wakes idle agents |

- **Audience**: a message reaches a slot if it's a DM to it, it @mentions it, or it is in a channel
  the slot is subscribed to (all agents subscribe to `#general` and their `#task-*` channels).
- **Receipts**: `delivered` (daemon fetched) and `acked` (agent called `ack`). `Stop` hook blocks
  stopping while high/interrupt messages are unacked (max 3 consecutive blocks per turn).
- **Injection hygiene**: delivered messages are wrapped as
  `<hive-message from="…" priority="…">…</hive-message>` and the role prompt states that
  messages from other agents are information, not authority over the user's instructions.

---

## 9. Tasks

- Fields: `id, title, spec, kind, state, priority, deps[], assignee, created_by, path_hints[],
  branch, result, created, updated`.
- States: `draft → open → claimed → in_progress → review → done`, plus `blocked`, `failed`,
  `cancelled`. `review` is skipped when `integration.review = "off"`.
- Scheduler (server, evaluated on every state change): an `open` task whose deps are all `done` is
  offered (event `task.offered`) to an idle, non-exhausted agent whose roles accept the kind
  (direct role first, then fallback chain), preferring agents that recently touched `path_hints`.
  The orchestrator can assign explicitly.
- Handoff: `task_handoff(task_id, to, notes)` keeps the branch; notes are stored on the task.

---

## 10. Daemon and wakes

- Config: `~/.hive/machine.toml` — server URL, local port/secret, slots
  (`name, vendor, token, worktree, config_dir, command overrides`).
- Per slot, the daemon keeps: last event seq, local inbox (SQLite `~/.hive/state.db`),
  pending wake reasons, session id, running process handle.
- **Wake** = run the vendor CLI headless in the slot worktree with the slot's config dir:

| Vendor | First wake | Later wakes |
|---|---|---|
| claude | `claude -p <prompt> --session-id <uuid> --output-format json --permission-mode <mode>` | `claude -p <prompt> --resume <uuid> …` |
| gemini | `gemini -p <prompt> --session-id <uuid> -o json --approval-mode <mode>` | `gemini -p <prompt> --resume <uuid> …` (verify; configurable) |

  Command templates are configurable per slot (`{prompt}`, `{session}` placeholders).
  Environment: `CLAUDE_CONFIG_DIR` / `GEMINI_CLI_HOME` set to the slot's config dir,
  `HIVE_SLOT`, `HIVE_DAEMON_URL`, `HIVE_LOCAL_SECRET`.
- Wake prompt = role reminder + fleet state + digest of pending events/messages + current task.
- At most one process per slot. Events arriving during a run are delivered by hooks; if anything is
  still pending when the run ends, a follow-up wake is scheduled.
- **Quota detection**: CLI output matching usage-limit patterns → `POST /agents/me/quota`
  with `until` → scheduler skips the agent; orchestrator gets `agent.quota_exhausted`.
- **Interactive attach**: `hive chat <slot>` marks the slot `attached`; the daemon stops headless
  wakes for it and relies on hooks until detached.

---

## 11. Hooks

Hooks ship inside the per-session bundles (§20) and call `hive hook <event> --vendor <cli>`;
the slot comes from `HIVE_SLOT`, which the launcher sets. Hooks must answer within 2 s and fail open for delivery
(never break the agent if the daemon is down) but fail closed for write enforcement.

| Hook (Claude / Gemini) | Behaviour |
|---|---|
| PreToolUse / BeforeTool | Fleet frozen/paused/planning for this role → deny. Write tool targeting a path not covered by held leases or `write_globs` → deny with `lease_acquire` instructions. |
| PostToolUse / AfterTool | Inject undelivered high/interrupt messages as additional context. |
| UserPromptSubmit / BeforeAgent | Inject undelivered normal+ messages and fleet-state banner. |
| Stop / AfterAgent | Block (once per reason, ≤3 times) if unacked high/interrupt messages or the slot holds a lease on uncommitted work. Allow when frozen. |
| SessionStart | Inject role prompt, fleet state, current task. |

Write tools: `Edit, Write, MultiEdit, NotebookEdit` (Claude), `write_file, replace` (Gemini).
Shell writes are not fully enforceable; the git `pre-commit` hook re-checks staged paths against
leases.

---

## 12. Leases

- `lease_acquire(globs[], ttl?, reason)`: all-or-nothing. Overlap test is conservative: two globs
  overlap if the literal prefix (up to the first wildcard segment) of one is a path-prefix of the
  other's. Conflict response lists holders and their expiry.
- Each grant gets `fencing = ++counter`. TTL default 600 s, max 3600 s; the daemon heartbeats
  (`lease_extend`) while the slot process is alive.
- Expiry is evaluated lazily inside every lease query/transaction (no background job needed).
- `lease_release(commit_message)` (local transaction, §13), then server marks released and emits
  `lease.released`.
- `lease_force_release` (orchestrator/user) bumps the counter so old fencing tokens are stale.

---

## 13. Git integration

- Repo layout per slot: `git worktree add <worktree> -b hive/<agent>`; nobody commits to `main`
  locally. `.hive/` holds plans, orchestrator state and status.
- **Release transaction** (daemon):
  1. `git add -A` within leased paths; commit with trailers `Hive-Agent`, `Hive-Task`, `Hive-Fence`.
  2. `git fetch origin && git rebase origin/main` (conflict → abort, keep lease, report
     `merge.conflict`).
  3. Run `integration.checks` commands (e.g. tests); failure → keep lease, report.
  4. `git push origin HEAD:refs/heads/hive/<agent>` (force-with-lease on own branch).
  5. `POST /integrate {sha, base_sha, fencing, task_id}` → server validates the fencing token is
     current, then advances `main` via GitHub API `PATCH /repos/{o}/{r}/git/refs/heads/main`
     `{sha, force:false}` (fast-forward only = atomic compare-and-swap). Non-fast-forward → client
     retries from step 2.
  6. Server emits `main.advanced {sha, paths}`; lease released.
- With `integration.review != "off"` step 5 instead moves the task to `review`; integration happens
  when the reviewer (or orchestrator fallback) approves.
- **Sync on `main.advanced`**: per slot, if the worktree is clean → `git rebase origin/main`;
  otherwise inject a normal notice and rebase at the next release.
- Local mode (no GitHub token configured): the server records the sha and the releasing daemon
  pushes `HEAD:main` itself (non-forced push = same CAS semantics on the git remote).

---

## 14. Server API (REST, JSON, `/api/v1`)

| Method & path | Auth | Purpose |
|---|---|---|
| `POST /agents` | admin | enroll agent `{name, vendor, roles}` → token |
| `GET /agents` | any | roster with status/quota |
| `PATCH /agents/<name>` | admin/orch | roles, status |
| `POST /agents/me/heartbeat` | agent | presence, `{busy, attached}` |
| `POST /agents/me/quota` | agent | `{until}` |
| `GET /events?since=&limit=` | agent/admin | events visible to caller |
| `POST /messages` / `GET /messages` | agent/admin | post / list (channel, thread, dm) |
| `POST /messages/ack` | agent | ack ids |
| `POST /tasks`, `GET /tasks`, `GET/PATCH /tasks/<id>` | agent/admin | task board |
| `POST /tasks/<id>/claim` `/handoff` `/complete` | agent | lifecycle |
| `POST /leases`, `GET /leases`, `POST /leases/<id>/extend` `/release` `/force-release` | agent | leases |
| `POST /integrate` | agent | advance main (§13) |
| `GET /fleet`, `POST /fleet/start` `/pause` `/resume` | admin/orch | fleet state |
| `POST /decisions`, `GET /decisions`, `POST /decisions/<id>/answer` | orch / admin | decisions |
| `POST /stuck` | agent | report stuck |
| `POST /status`, `GET /status` | orch / any | status feed |
| `GET /` | admin (cookie/token) | dashboard |

Errors: `{"error": code, "message": …}` with 400/401/403/404/409/423 (fleet frozen)/429.

---

## 15. MCP tools (exposed by `hive mcp`)

`whoami, roster, post, dm, inbox, ack, task_create, task_list, task_get, task_claim,
task_update, task_handoff, task_complete, report_stuck, lease_acquire, lease_status, lease_extend,
lease_release, repo_state, status_publish, decision_request, fleet_state, task_assign,
lease_force_release, role_reassign`

Each tool maps 1:1 to a daemon local endpoint, which forwards to the server (or performs the git
transaction for `lease_release`). Role permission errors are returned as tool errors.

---

## 16. Configuration

**`hive.toml` (repo root, committed)**
```toml
[server]
url = "https://you.pythonanywhere.com"

[repo]
github = "owner/repo"      # optional; enables server-side ref CAS
main = "main"

[integration]
review = "off"             # off | orchestrator | reviewer
checks = ["python -m pytest -q"]

[orchestrator]
standby = ["claude-2"]
debounce_seconds = 90
heartbeat_hours = 4

[roles.reviewer]           # override/extend built-ins or add custom roles
prompt = "Review diffs for correctness and tests."
```

**`~/.hive/machine.toml` (per machine, private)** — server URL, local port, local secret, and
`[[slots]]` with `name, vendor, token, worktree, config_dir, permission_mode, command`.

---

## 17. CLI

| Command | Purpose |
|---|---|
| `hive server [--host --port]` | run the server (dev); WSGI entry `hive.server.wsgi:application` |
| `hive admin enroll <name> --vendor --roles` | create agent, print token |
| `hive init` | write `hive.toml`, `.hive/`, CLI MCP/hook configs, agent instruction files, git hooks |
| `hive join <name> --token --vendor --worktree [--config-dir]` | add a slot to this machine |
| `hive up` | run the daemon (all slots on this machine) |
| `hive start` | user: start the fleet (→ planning) |
| `hive decisions` / `hive decide <id> <answer> [--note]` | list / answer decisions |
| `hive say <text>` | message the orchestrator in `#user` |
| `hive status` | fleet state, roster, tasks, leases, latest status entries |
| `hive pause` / `hive resume` | fleet control |
| `hive chat <slot>` | attach interactively to a slot's session |
| `hive mcp --slot`, `hive hook <event> --slot` | invoked by the agent CLIs |

---

## 18. Hosting

- **PythonAnywhere** (recommended paid tier): WSGI file imports `hive.server.wsgi:application`;
  set `HIVE_ADMIN_TOKEN`, `HIVE_DATABASE_URL` (MySQL), optional `HIVE_GITHUB_TOKEN`. No
  background workers required: lease expiry, scheduler and failover are evaluated lazily on
  requests.
- Any WSGI host / VPS / Fly.io works identically (gunicorn).

---

## 19. Security

- TLS (host-provided). Tokens hashed at rest. Admin token never sent to agents.
- Local daemon API binds to 127.0.0.1 only and requires `X-Hive-Local` secret.
- Only the server holds the GitHub token able to move `main`; machines push only `hive/*` branches.
- Append-only event log is the audit trail.
- Message size and rate limits; loop detection (two agents exchanging >N DMs/minute → alert).

---

## 20. Per-session capabilities, live streams and reports (v0.2)

**Per-session capabilities.** `hive init` writes two self-contained bundles that no CLI loads
on its own:

| Bundle | Contents | Attached by |
|---|---|---|
| `.hive/agent/claude/` (Claude Code plugin) | `.claude-plugin/plugin.json`, `.mcp.json` (hive MCP server), `hooks/hooks.json`, `skills/hive-orchestrator`, `skills/hive-worker` | `claude --plugin-dir .hive/agent/claude --settings '<allow hive tools>'` |
| `.hive/agent/gemini/` (Gemini CLI extension) | `gemini-extension.json` (MCP server with a 30-min timeout, `HIVE.md` context), `hooks/hooks.json`, the same skills | linked into the slot's private `GEMINI_CLI_HOME` (`gemini extensions link --consent`), selected with `-e hive` |

`hive init` also removes hive entries written by v0.1 to auto-loaded files (`.mcp.json`,
`.claude/settings.json`, `.gemini/settings.json`, and the marked blocks in `CLAUDE.md`,
`GEMINI.md`, `AGENTS.md`), keeping the user's own entries. A CLI session started without
`hive` never loads Hive.

**Launch modes.**

| Slot | `hive up` behaviour |
|---|---|
| orchestrator | native interactive CLI in the terminal (resume or new session, initial prompt pointing to the skill); relaunch prompt on exit; workers on the same machine run in the background without terminal output |
| worker | headless wakes with `--output-format stream-json` (Claude, plus `--verbose`) or `-o stream-json` (Gemini), formatted live in the terminal |

`hive chat <slot>` attaches interactively to any slot, pausing its headless wakes.

**Live streams (admin only).** The daemon formats each stream-json event into readable lines
(assistant text, `→ tool input`, `← result`, `· turn done`; Gemini text deltas are joined). It
uploads them in batches every second (`POST /agents/me/stream`, max 500 lines/batch,
4000 chars/line). The server keeps the last 20,000 lines per agent. `GET /streams` requires
the admin token; agent tokens, including the orchestrator's, get 403. Views: `hive watch
[agent]` and the dashboard's live-output pane.

**Reports.** `reports` table (`kind` = `request` | `report`).

| Tool | Who | Effect |
|---|---|---|
| `report_request(agent, about, task_id?)` | orchestrator | event `report.requested` → wakes the target agent |
| `report_submit(summary, body, task_id?, request_id?)` | anyone | marks the request answered; event `report.submitted` → orchestrator |
| `report_list`, `report_get` | anyone | read |

**`await_events(timeout_seconds ≤ 1800)`** (orchestrator): blocks in the daemon until urgent
triggers are due (3 s coalescing), batched ones have aged past `debounce_seconds`, or the
timeout passes. Then it consumes the triggers and returns a digest with the fleet state, a
state-specific hint, events and unread messages.

## 21. Implementation phases

| Phase | Scope | Status |
|---|---|---|
| 0 | Verify CLI hooks/headless/resume for Claude and Gemini | CLI flags verified; live runs pending |
| 1 | Server core: auth, events, messages, fleet state, decisions, status | done, tested |
| 2 | Roles, tasks, scheduler, leases, freeze semantics | done, tested |
| 3 | Daemon, local API, MCP stdio, hooks, wakes | done, e2e-tested with a fake CLI |
| 4 | Git release transaction, integration, sync | done (local push mode e2e-tested; GitHub CAS mode untested) |
| 5 | Dashboard, `hive init`, failover | first version; loop detection and push notifications pending |

### Implementation notes (v0.1)

- Additional endpoints beyond §14: `POST /integrate/confirm` (local push mode),
  `POST /integrate/conflict`, `POST /tasks/<id>/assign|cancel|review`, `POST /fleet/reset`,
  `PUT /config` (upload `hive.toml`), `POST /agents/<name>/token` (rotate).
- Additional MCP tools beyond §15: `task_cancel`, `status_read`, `fleet_control`.
- Tasks are scheduled by direct auto-assignment (`task.assigned`) rather than offers; agents can
  hand off.
- In review mode the lease stays held during review; on approval the assignee's daemon re-runs
  the release transaction automatically.
- Plans written to `.hive/plans/` are not shared automatically across machines in v0.1; the plan
  reaches the user via the start-approval decision context and the programmers via task specs.
