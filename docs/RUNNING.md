# Running the fleet

`hive up` runs every agent set up on a computer. (Each agent on a computer lives in a
**slot**: its own working folder of the repository and its own logged-in CLI account,
created with `hive join`; see [Setting up machines](MACHINES.md).) You start `hive up` on
each computer that has slots and leave it running. It starts the **daemon**, a background
program that asks the [server](SERVER.md) every few seconds whether anything concerns its
agents, starts them when it does, and handles their git work. What you see in the terminal
depends on which agents live on that computer:

- **On the orchestrator's computer,** the terminal becomes your chat with the orchestrator
  (the agent that plans and coordinates; it never writes code).
- **On computers with only workers** (all other agents), the terminal shows what the workers
  print as they work.

Workers run **headless**: without a terminal, given one prompt (set of instructions) at a
time. Each such run is a **turn**; a turn the daemon starts because something concerns the
worker is called a **wake**. This page explains both kinds of computer, how a wake is
launched, and how to take over an agent yourself with `hive chat`.

`hive up` only runs the agents. The fleet (the whole team) does no work until you give it a
goal with `hive start` ([The fleet lifecycle](LIFECYCLE.md)). Some details below, such as the
exact command lines, are for reference: you never type them, Hive does.

---

## The machine with the orchestrator (your computer)

```sh
hive up
```

1. **Daemon:**
   - It first asks the server who each slot's agent is and what [roles](ROLES.md) it has,
     retrying for up to 30 s. If the server can't be reached, `hive up` stops with a clear message instead of
     guessing.
   - It then starts in the background. Its log goes to `~/.hive/logs/daemon.log`, not the
     terminal.
2. **CLI launch:** opens the orchestrator's CLI (Claude Code, Codex, agy or Gemini CLI) **in your
   terminal**, as a normal interactive session, with Hive attached for this session only.
   Hive's tools, automatic checks ([hooks](GLOSSARY.md#hook)) and instructions come from the
   [bundle](GLOSSARY.md#bundle-plugin-extension-profile) `hive init` wrote to `.hive/agent/`.
   The commands, for reference:
   - Claude: `claude --plugin-dir <worktree>/.hive/agent/claude --settings <allow hive tools>
     --session-id|--resume <id> "<start prompt>"` (load Hive's plugin, pre-approve its tools,
     and start or resume a conversation)
   - Gemini: `gemini -e hive --session-id|--resume <id> -i "<start prompt>"` (use Hive's
     extension)
   - Codex: `codex [resume <id>] -p hive --dangerously-bypass-hook-trust "<start prompt>"`
     (use Hive's profile; [why hook trust is bypassed](#codex-profile))
   - agy: `agy [--conversation <id>] -i "<start prompt>"` (Hive's plugin is in the slot's
     [home folder](#agy-home-folder))
3. **Session:** a session is one continuing conversation with the AI model. The first launch
   creates a new one. Later launches **resume the same conversation**, so the orchestrator
   remembers earlier work, unless the last session was idle for longer than
   `orchestrator_fresh_after_hours` ([`[sessions]`](CONFIGURATION.md#hivetoml), 12 h by
   default). Then it starts fresh and recovers from its notes, rather than re-reading a long,
   stale conversation with every request. (Codex and agy pick their own session ids; Hive
   reads the id back from the slot's settings folder when the session ends.)
4. **Start prompt:** tells the agent to follow the `hive-orchestrator`
   [skill](GLOSSARY.md#skill) (its written operating procedure). It then:
   1. reads its notes from earlier sessions (`.hive/orchestrator/state.md` in its worktree);
   2. checks the fleet state and what changed since its notes (task and report summaries);
   3. acts on the [fleet state](LIFECYCLE.md) (for example, starts planning);
   4. waits in Hive's `await_events` tool, which returns as soon as something needs its
      attention and uses none of the account's model usage while waiting.
5. **On exit:** when you quit the CLI, `hive up` asks *"Relaunch? [Y/n]"*. Answering `n`, or
   pressing Ctrl+C, stops everything on this machine.

**Other slots on the same machine:** they keep running headless in the background. Their
output isn't printed here (the terminal belongs to the CLI); watch them with `hive watch`
from another terminal ([Watching the fleet](MONITORING.md)).

**Headless orchestrator:** `hive up --headless` runs the orchestrator like a worker: the
daemon starts a headless turn whenever something happens, plus a check-in every few hours
(`heartbeat_hours` in [`hive.toml`](CONFIGURATION.md#hivetoml), 4 by default). Use it only if
you don't want an interactive session; you then talk to the orchestrator through `hive say`
and the [dashboard](MONITORING.md#the-web-dashboard).

How to talk to the orchestrator once it's running is covered in
[Working with the orchestrator](ORCHESTRATOR.md).

---

## Worker machines

```sh
hive up            # -v for debug logging
```

1. The daemon starts and prints what each agent does as it works, prefixed with its name
   (`claude-2` in this example, a Claude Code programmer).
   `·` lines are Hive's notes (a wake started, why), `→` lines are the agent calling a tool,
   with its input, and `←` lines are the tool's result. `mcp__plugin_hive_hive__lease_acquire`
   is how Claude Code names Hive's `lease_acquire` tool, which locks files for the agent
   ([Leases](INTEGRATION.md#leases)):
   ```
   [claude-2] · wake 20261019-213012
   [claude-2] · why: Task #4 was assigned to you: Add login endpoint
   [claude-2]   I'll lease the auth module first.
   [claude-2] → mcp__plugin_hive_hive__lease_acquire {"globs": ["src/auth/**"], "task_id": 4}
   [claude-2] ← {"id": 7, "holder": "claude-2", "fencing": 12, …}
   [claude-2] → Edit {"file_path": "src/auth/login.py", …}
   [claude-2] · turn done (14 turns)
   [claude-2] · tokens: 412k in, 6.3k out over 15 requests, context 38k
   ```

   The `tokens` line is the turn's usage as the CLI reports it: input tokens summed over all
   its requests (most are re-read from the cache), output tokens, and the size of the
   conversation at the end. Claude Code and agy report all three; Codex and Gemini CLI
   report totals only.
2. The same lines are uploaded to the server about once a second, so you can follow them
   from any computer with `hive watch` or the dashboard ([Watching the fleet](MONITORING.md)).
3. Ctrl+C stops the daemon.

A worker computer needs no attention while it runs. Workers don't run at all, so use none of
their accounts' model usage, until the daemon wakes them.

---

## How an agent is launched for a headless turn ("wake")

When something concerns a worker (a task is assigned to it, a message mentions it, a review
of its work comes back, the fleet unfreezes, …), its daemon starts one headless turn. The
command lines, for reference (`--output-format stream-json`, `-o stream-json` and `--json`
make the CLI report each step as a line of JSON, which Hive turns into the live output; the
permission mode is the slot's `--permission-mode` from `hive join`):

| CLI | Command (prompt goes on stdin) |
|---|---|
| Claude | `claude -p --output-format stream-json --verbose --plugin-dir <bundle> --settings <allow hive tools> --permission-mode <mode> --session-id/--resume <id>` |
| Gemini | `gemini -p "Follow the Hive wake notice…" -o stream-json -e hive --approval-mode <mode> --session-id/--resume <id>` |
| Codex | `codex exec --json -p hive --dangerously-bypass-hook-trust -c sandbox_mode="<mode>" -c approval_policy="never" [resume <id>] -` |
| agy | `agy --input-format stream-json --output-format stream-json --mode <mode> [--conversation <id>]` (the prompt goes on stdin as one JSON message) |

If a CLI's options differ on your system, give the slot a custom command
([`machine.toml`](CONFIGURATION.md#hivemachinetoml)).

### Environment for every launch

Hive sets these environment variables for every CLI it starts. Hive's tools and hooks, which
run inside the CLI, use them to reach the daemon:

| Variable | Value |
|---|---|
| `HIVE_SLOT` | the slot name |
| `HIVE_DAEMON_URL` | the address of the daemon's local API (a small web service on `127.0.0.1`, reachable only from this computer) |
| `HIVE_LOCAL_SECRET` | the password for that local API |
| `MCP_TOOL_TIMEOUT` | 30 minutes: how long the CLI lets one of Hive's tools run, so `await_events` can wait |
| `CLAUDE_CONFIG_DIR` | the slot's config folder (Claude slots) |
| `GEMINI_CLI_HOME` | the slot's config folder (Gemini slots) |
| `CODEX_HOME` | the slot's config folder (Codex slots) |
| `HIVE_HOME` | Hive's own folder (`~/.hive`), so Hive's tools and hooks find it even when the CLI's home folder is changed (agy) |
| `USERPROFILE`, `HOME` | the slot's config folder (agy slots; [why](#agy-home-folder)) |
| `GIT_CONFIG_GLOBAL` | your `~/.gitconfig` (agy slots), so git keeps your name and settings |

A slot's `--model` is passed as `--model` (Claude, agy) or `-m` (Gemini, Codex).

### Gemini extension

Before a Gemini slot's first launch, `hive` registers Hive's extension with Gemini CLI by
running `gemini extensions link <worktree>/.hive/agent/gemini --consent` **inside the slot's
private settings folder** (`GEMINI_CLI_HOME`). Your personal `~/.gemini` is never touched.

### Codex profile

Before every Codex launch, `hive` copies into the slot's **private** `CODEX_HOME` (never
`~/.codex`):
- `.hive/agent/codex/hive.config.toml` as `hive.config.toml`, the profile selected with
  `-p hive`. It adds Hive's [MCP server](GLOSSARY.md#mcp-mcp-server-mcp-tool) (the program
  offering Hive's tools; it is given `HIVE_SLOT`, `HIVE_DAEMON_URL`, `HIVE_LOCAL_SECRET` and
  `HIVE_HOME`, and Hive's tools are pre-approved) and the hooks;
- the shell-command rules for the slot's role as `rules/hive.rules`
  ([Shell-command permissions](PERMISSIONS.md));
- the skills to `skills/hive-orchestrator/` and `skills/hive-worker/`.

The slot's own `config.toml` and login are left alone. If the repository was initialised by
an older Hive and has no `.hive/agent/codex/` yet, the files are generated on the fly.
The Hive MCP server is required: Codex waits for it before the first turn and fails startup
if it cannot initialize, so an agent cannot begin work before its Hive tools are ready.

Codex normally asks a person to review new hooks before it runs them. Nobody is there to
answer for a headless agent, and Hive generates the hooks itself (the slot's private settings
folder holds no others), so Hive launches Codex with `--dangerously-bypass-hook-trust`, which
skips that review. Codex reports this as a warning at the start of each turn;
`hive watch` hides that line.

Codex chooses its own session ids. The daemon reads the id from the first turn's output (the
`thread.started` line) and resumes that session with `codex exec resume <id>` afterwards.

### agy home folder

The Antigravity CLI (agy) keeps its settings, plugins and conversations in `~/.gemini` and has
no option to put them elsewhere. So for agy slots Hive sets the home directory itself
(`USERPROFILE` on Windows, `HOME` elsewhere) to the slot's private folder
(`~/.hive/accounts/<slot>`), and before every launch writes there (never in your own
`~/.gemini`):
- the plugin from `.hive/agent/agy/` (Hive's MCP server, hooks and skills) to
  `.gemini/config/plugins/hive/`, switched on in `.gemini/config/config.json`;
- the shell-command rules for the slot's role and the worktree as a trusted folder, in
  `.gemini/antigravity-cli/settings.json`. Other settings agy keeps there, such as the model
  you picked in `hive chat`, are kept.

If the repository was initialised by an older Hive and has no `.hive/agent/agy/` yet, the
files are generated on the fly.

What this means in practice:
- **One account per computer.** agy's login is in the computer's keyring, not in that folder,
  so every agy slot on a computer uses the same account
  ([Accounts](MACHINES.md#log-each-agents-account-in-once)).
- **Commands the agent runs see that home folder too.** Git keeps your identity through
  `GIT_CONFIG_GLOBAL`; other tools that keep settings or logins in your home folder (SSH keys,
  some package managers on macOS and Linux) don't find them. Hive's own git operations run
  in the daemon, which is unaffected.
- agy chooses its own conversation ids. The daemon reads the id from the first line of a turn's
  output (`init`) and resumes with `--conversation <id>`.
- agy cancels a call to an MCP tool after 3 minutes, so Hive's waiting tools return sooner
  for agy agents ([`await_events`](AGENT_INTERFACE.md#await_events)).

### The wake prompt

The prompt a worker receives contains:
- who the agent is and its roles;
- the fleet state;
- why it was woken;
- its current tasks, with their description (*spec*), the files they're expected to touch
  (*path hints*) and notes ([Tasks](COORDINATION.md#tasks)). A resumed session that has seen
  a task already gets only the notes added since;
- new messages;
- on the first turn of a session, its role's instructions and reminders of Hive's rules;
- when a fresh session continues a task begun in an earlier one, a short handoff: the
  uncommitted files and unmerged commits in its worktree. Its task notes say the rest, which
  is why agents record where they are before ending a turn with work unfinished.

### Wake rules

- **One turn at a time per slot.** Events that arrive during a turn reach the agent through
  [hooks](AGENT_INTERFACE.md#hooks) (automatic checks that run after each of its actions), or
  trigger a follow-up turn.
- **Sessions last one task.** Every request re-reads the whole session, so history that
  doesn't help the current work is pure cost. A worker resumes its session while it works on
  the same task (a review sending it back, a conflict, an approval coming in), and starts a
  fresh one when it moves to a new task. Any agent also starts fresh once a turn's context
  grew past `max_context_tokens` (120,000), after `max_wakes` turns (40), or after a failed
  resume. All three are set in [`[sessions]`](CONFIGURATION.md#hivetoml); `per_task = false`
  keeps one session across tasks.
- **Wakes are rate-limited**: at most 20 per slot per hour, at least 10 seconds apart.
- **Usage limits:** if the CLI fails because the account has used up its allowance, the slot
  pauses for an hour. The server is told, so it assigns no tasks to the agent meanwhile, and
  the orchestrator is notified.
- **Turn timeout:** a turn longer than one hour is killed.

All timings are listed in [Timings and limits](CONFIGURATION.md#timings-and-limits).

---

## Taking over a slot interactively (`hive chat`)

```sh
hive chat claude-2          # resume its session in your terminal
hive chat claude-2 --fresh  # start a new session
```

`claude-2` is an example agent name, as elsewhere on this page; use the name of the agent you
want to talk to (`hive status` lists them).

Use this to see what an agent was thinking, to guide it by hand, or to finish something
yourself with its context.
- The daemon stops headless turns for that slot while you're attached and resumes them when
  you exit.
- If a headless turn is running, it finishes first.
- Hive's bundle is attached exactly as for `hive up`, so leases, hooks and command
  permissions still apply. Shell commands Hive doesn't pre-approve ask you instead of being
  refused ([Shell-command permissions](PERMISSIONS.md)).

---

**Next:** [Working with the orchestrator](ORCHESTRATOR.md) ·
**See also:** [Watching the fleet](MONITORING.md), [CLI.md §8](CLI.md#8-running-a-machine) ·
[← Back to the README](../README.md)
