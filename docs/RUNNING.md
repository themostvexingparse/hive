# Running the fleet

`hive up` runs every slot on a machine. You start it on each machine that hosts slots and
leave it running. It starts the daemon, which polls the server, launches agents when
something concerns them, and handles their git work. What you see in the terminal depends on
which slots live on that machine:

- **On the orchestrator's machine,** the terminal becomes your chat with the orchestrator.
- **On worker machines,** the terminal shows the workers' live output.

This page explains both, how a worker's headless turn ("wake") is launched, and how to take
over a slot yourself with `hive chat`.

`hive up` only runs the agents. The fleet does no work until you give it a goal with
`hive start` ([The fleet lifecycle](LIFECYCLE.md)).

---

## The machine with the orchestrator (your computer)

```sh
hive up
```

1. **Daemon:**
   - It first fetches every slot's identity and roles from the server, retrying for up to
     30 s. If the server can't be reached, `hive up` stops with a clear message instead of
     guessing.
   - It then starts in the background. Its log goes to `~/.hive/logs/daemon.log`, not the
     terminal.
2. **CLI launch:** opens the orchestrator's **native CLI in your terminal** with Hive attached
   for this session:
   - Claude: `claude --plugin-dir <worktree>/.hive/agent/claude --settings <allow hive tools>
     --session-id|--resume <id> "<start prompt>"`
   - Gemini: `gemini -e hive --session-id|--resume <id> -i "<start prompt>"`
   - Codex: `codex [resume <id>] -p hive --dangerously-bypass-hook-trust "<start prompt>"`
3. **Session:** the first launch creates a new session. Later launches **resume the same
   conversation**. (Codex picks its own session ids; Hive reads the id back from the slot's
   `CODEX_HOME/sessions/` when the session ends.)
4. **Start prompt:** tells the agent to use the `hive-orchestrator` skill. It then:
   1. calls `whoami`;
   2. reads its memory (`.hive/orchestrator/state.md`);
   3. acts on the fleet state;
   4. waits in `await_events`.
5. **On exit:** when you quit the CLI, `hive up` asks *"Relaunch? [Y/n]"*. Answering `n`, or
   pressing Ctrl+C, stops everything on this machine.

**Other slots on the same machine:** they keep running headless in the background. Their
output isn't printed here (the terminal belongs to the CLI); watch them with `hive watch`
from another terminal ([Watching the fleet](MONITORING.md)).

**Headless orchestrator:** `hive up --headless` runs the orchestrator like a worker: the
daemon starts a headless turn per event, plus a check-in every `heartbeat_hours`. Use it only
if you don't want an interactive session; you then talk to the orchestrator through
`hive say` and the dashboard.

How to talk to the orchestrator once it's running is covered in
[Working with the orchestrator](ORCHESTRATOR.md).

---

## Worker machines

```sh
hive up            # -v for debug logging
```

1. The daemon starts and prints each agent's formatted live output, prefixed with its name:
   ```
   [claude-2] · wake 20261019-213012
   [claude-2] · why: Task #4 was assigned to you: Add login endpoint
   [claude-2]   I'll lease the auth module first.
   [claude-2] → mcp__plugin_hive_hive__lease_acquire {"globs": ["src/auth/**"], "task_id": 4}
   [claude-2] ← {"id": 7, "holder": "claude-2", "fencing": 12, …}
   [claude-2] → Edit {"file_path": "src/auth/login.py", …}
   [claude-2] · turn done (14 turns)
   ```
2. The same lines are uploaded to the server about once a second for `hive watch` and the
   dashboard.
3. Ctrl+C stops the daemon.

A worker machine needs no attention while it runs. Workers sit idle, using no tokens, until
the daemon wakes them.

---

## How an agent is launched for a headless turn ("wake")

When something concerns a worker (a task is assigned, a message mentions it, a review comes
back, the fleet unfreezes, …), its daemon starts one headless turn:

| CLI | Command (prompt goes on stdin) |
|---|---|
| Claude | `claude -p --output-format stream-json --verbose --plugin-dir <bundle> --settings <allow hive tools> --permission-mode <mode> --session-id/--resume <id>` |
| Gemini | `gemini -p "Follow the Hive wake notice…" -o stream-json -e hive --approval-mode <mode> --session-id/--resume <id>` |
| Codex | `codex exec --json -p hive --dangerously-bypass-hook-trust -c sandbox_mode="<mode>" -c approval_policy="never" [resume <id>] -` |

If a CLI's flags differ on your system, give the slot a custom command
([`machine.toml`](CONFIGURATION.md#hivemachinetoml)).

### Environment for every launch

| Variable | Value |
|---|---|
| `HIVE_SLOT` | the slot name |
| `HIVE_DAEMON_URL` | the daemon's local API |
| `HIVE_LOCAL_SECRET` | the local API secret |
| `MCP_TOOL_TIMEOUT` | 30 minutes |
| `CLAUDE_CONFIG_DIR` | the slot's config folder (Claude slots) |
| `GEMINI_CLI_HOME` | the slot's config folder (Gemini slots) |
| `CODEX_HOME` | the slot's config folder (Codex slots) |

A slot's `--model` is passed as `--model` (Claude) or `-m` (Gemini, Codex).

### Gemini extension

Before a Gemini slot's first launch, `hive` runs
`gemini extensions link <worktree>/.hive/agent/gemini --consent` **inside the slot's private
`GEMINI_CLI_HOME`**. Your personal `~/.gemini` is never touched.

### Codex profile

Before every Codex launch, `hive` copies into the slot's **private** `CODEX_HOME` (never
`~/.codex`):
- `.hive/agent/codex/hive.config.toml` as `hive.config.toml`, selected with `-p hive`. It
  adds the `hive` MCP server (forwarding `HIVE_SLOT`, `HIVE_DAEMON_URL`, `HIVE_LOCAL_SECRET`
  and `HIVE_HOME`, with Hive's tools pre-approved) and the hooks;
- the rules for the slot's role as `rules/hive.rules`;
- the skills to `skills/hive-orchestrator/` and `skills/hive-worker/`.

The slot's own `config.toml` and login are left alone. If the repository was initialised by
an older Hive and has no `.hive/agent/codex/` yet, the files are generated on the fly.
The Hive MCP server is required: Codex waits for it before the first turn and fails startup
if it cannot initialize, so an agent cannot begin work before its Hive tools are ready.

Codex asks you to review new hooks before they run. Hive generates its hooks itself, and the
slot's private `CODEX_HOME` holds no others, so it launches Codex with
`--dangerously-bypass-hook-trust`. Codex reports this as a warning at the start of each turn;
`hive watch` hides that line.

Codex chooses its own session ids. The daemon reads the id from the `thread.started` event of
the first turn and resumes it with `codex exec resume <id>` afterwards.

### The wake prompt

The prompt a worker receives contains:
- who the agent is and its roles;
- the fleet state;
- why it was woken;
- its current tasks with spec, path hints and notes;
- new messages;
- on the first turn of a session, its role instructions and protocol reminders.

### Wake rules

- **One turn at a time per slot.** Events that arrive during a turn reach the agent through
  hooks, or trigger a follow-up turn.
- **Sessions persist** across turns, so an agent remembers what it did earlier. A new session
  starts every 40 turns, or after a failed resume, to keep the context from growing without
  limit.
- **Wakes are rate-limited**: at most 20 per slot per hour, at least 10 seconds apart.
- **Usage limits:** if the CLI fails with a usage-limit message, the slot pauses for an hour.
  The server is told, so the scheduler skips the agent and the orchestrator is notified.
- **Turn timeout:** a turn longer than one hour is killed.

All timings are listed in [Timings and limits](CONFIGURATION.md#timings-and-limits).

---

## Taking over a slot interactively (`hive chat`)

```sh
hive chat claude-2          # resume its session in your terminal
hive chat claude-2 --fresh  # start a new session
```

Use this to see what an agent was thinking, to guide it by hand, or to finish something
yourself with its context.
- The daemon stops headless turns for that slot while you're attached and resumes them when
  you exit.
- If a headless turn is running, it finishes first.
- Hive's plugin or extension is attached exactly as for `hive up`, so leases, hooks and
  permissions still apply. Commands outside the allowed list prompt you instead of being
  refused ([Shell-command permissions](PERMISSIONS.md)).

---

**Next:** [Working with the orchestrator](ORCHESTRATOR.md) ·
**See also:** [Watching the fleet](MONITORING.md), [CLI.md §8](CLI.md#8-running-a-machine) ·
[← Back to the README](../README.md)
