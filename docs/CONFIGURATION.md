# Configuration reference

This page lists every configuration file, environment variable, file location, timing and
limit in one place. The other pages explain what each setting is for. This one is for looking
them up; the [glossary](GLOSSARY.md) explains the terms. The settings files are in
[TOML](GLOSSARY.md#files), a simple format of `key = value` lines under `[section]`
headings; `#` starts a comment. Environment variables are settings given to a program
through its environment (e.g. `export NAME=value` in bash, `$env:NAME = "value"` in
PowerShell).

| Where | What | Written by |
|---|---|---|
| [`hive.toml`](#hivetoml) | The project's settings for its fleet (its team of agents), committed to the repository. | `hive init`, then you or `hive config set` |
| `hive.local.toml` | This computer's overrides of `hive.toml` settings, next to it in your clone; git-ignored, never uploaded ([`hive config`](CLI.md#hive-config-read-and-change-settings)). | `hive config set --local`, or you |
| [`~/.hive/machine.toml`](#hivemachinetoml) | One computer's agents (*slots*) and their tokens (passwords). Private. | `hive agents add`, `hive join` |
| `~/.hive/admin.toml` | Your admin logins. Private. | `hive login`, `hive server` |
| [Environment variables](#environment-variables) | Server settings and overrides. | you |

---

## `hive.toml`

In the repository, committed. Admin commands run inside the repository upload it to the
server whenever it changed (`hive admin push-config` does it explicitly). Agents use a change once it is on `main`: each
launch builds the CLIs' permission files from it.

```toml
[server]
url = "https://USER.pythonanywhere.com"   # informational; machines use their own --server

[repo]
main = "main"                # integration branch
# github = "owner/repo"      # enables GitHub mode

[integration]
review = "off"               # off | orchestrator | reviewer
checks = ["python -m pytest -q"]   # run before every integration; must exit 0
shell = "native"             # native (cmd.exe on Windows, sh elsewhere) | bash | powershell | cmd

[orchestrator]
standby = ["claude-2"]       # agents that take over if the orchestrator is unavailable, in order
debounce_seconds = 90        # batching window for routine events
heartbeat_hours = 4          # headless orchestrator only: periodic check-in

[sessions]                   # when agents start a fresh CLI session instead of resuming
per_task = true              # workers start fresh for each new task
max_context_tokens = 120000  # ...or once a turn ended with more context than this
max_wakes = 40               # ...or after this many turns
orchestrator_fresh_after_hours = 12   # hive up starts the orchestrator fresh after this idle time

[permissions]                # shell-command tiers for agents
preset = "standard"          # standard | strict | none
allow = ["docker compose ps"]          # run directly
approval = ["npm publish"]             # run once the orchestrator approves
deny = ["kubectl delete"]              # never

[roles.docs-writer]          # custom roles / overrides
can_code = true
task_kinds = ["docs"]

[fleet]
preset = "custom"            # custom | solo | team | strict: a starting bundle of the above
```

The same settings can be given in two more files, which apply to one computer only and are
never uploaded ([`hive config`](CLI.md#hive-config-read-and-change-settings) explains the
order): `hive.local.toml` next to `hive.toml` in your clone (git-ignored), and
`~/.hive/config.toml`, yours in every repository on that computer. Those two are also where
**hooks** go, the commands this computer's daemon runs when the fleet does something; Hive
ignores a `[hooks]` section in `hive.toml`:

```toml
# hive.local.toml or ~/.hive/config.toml
[hooks]
on_decision = 'notify-send "Hive" "$HIVE_SUMMARY"'     # a decision waits for you
on_task_done = ""            # a task finished
on_main = ""                 # main moved
on_frozen = ""               # the fleet froze on a stuck agent
on_state = ""                # the fleet's state changed
on_message = ""              # an agent wrote to you in #user
```

Each hook runs in a shell with `HIVE_EVENT`, `HIVE_SUMMARY` and `HIVE_EVENT_JSON` set, from
the worktree of one of this computer's agents, and is stopped after 2 minutes.

| Key | Explained in |
|---|---|
| `[server] url` | [Checking a project's setup](REPOSITORY.md#which-server-it-asks) (it is informational only) |
| `[repo] main` | [What `lease_release` does](INTEGRATION.md#what-lease_releasecommit_message-task_id-does) |
| `[repo] github` | [GitHub mode](INTEGRATION.md#github-mode-optional) |
| `[integration] review` | [Reviews](INTEGRATION.md#reviews) |
| `[integration] checks`, `shell` | [What `lease_release` does](INTEGRATION.md#what-lease_releasecommit_message-task_id-does), step 3 |
| `[orchestrator] standby` | [Changing roles](ROLES.md#changing-roles) |
| `[orchestrator] debounce_seconds` | [Messages and priorities](COORDINATION.md#messages-and-priorities) |
| `[orchestrator] heartbeat_hours` | [Running the fleet](RUNNING.md#the-machine-with-the-orchestrator-your-computer) (headless orchestrator) |
| `[sessions]` | [Wake rules](RUNNING.md#wake-rules) and [the orchestrator's session](RUNNING.md#the-machine-with-the-orchestrator-your-computer). `max_context_tokens` uses the context size Claude Code reports; for Codex and Gemini CLI agents only `per_task` and `max_wakes` apply. `0` turns a limit off. |
| `[permissions]` | [Shell-command permissions](PERMISSIONS.md#customising) |
| `[fleet] preset` | [`hive config`](CLI.md#hive-config-read-and-change-settings): `solo` (no reviews), `team` (reviews by a `reviewer`), `strict` (reviews and the `strict` permission preset) |
| `[hooks]` | above; only in `hive.local.toml` or `~/.hive/config.toml` |
| `[roles.<name>]` | [Custom roles and overrides](ROLES.md#custom-roles-and-overrides) |

---

## `~/.hive/machine.toml`

One per computer that runs agents, private, written by `hive agents add`, `hive join` and,
on a paired computer, its daemon. You rarely edit it by hand. Each `[[slots]]` block
describes one agent on this computer: its name, CLI (`vendor`), token, working folder
(`worktree`), private CLI settings folder (`config_dir`), and its options
([Setting up machines](MACHINES.md)). `local_port` and `local_secret` are for the
[daemon](GLOSSARY.md#daemon)'s local API, a small web service reachable only from this
computer, which agents' tools use. `[machines]` holds this computer's pairings
([Pair it once](MACHINES.md#on-another-computer-pair-it-once)).

```toml
server_url = "https://USER.pythonanywhere.com"   # default for slots that name none
local_port = 8766            # daemon's local API (127.0.0.1 only)
local_secret = "…"           # generated; protects the local API
poll_interval = 3.0          # seconds between server polls
accept_placements = "auto"   # auto: set up agents a paired server places here | never

[machines."https://USER.pythonanywhere.com/p/acme"]   # set by `hive join CODE`
name = "desk"                # this computer's name in that project
token = "hm_…"               # its machine token: only lists and claims placed agents
repo = "/path/to/proj"       # the clone placed agents' worktrees go next to

[[slots]]
name = "claude-2"
vendor = "claude"
token = "hv_…"
worktree = "/path/to/proj-claude-2"
config_dir = "/home/you/.hive/accounts/claude-2"
permission_mode = ""         # empty = default (claude: acceptEdits, gemini: auto_edit,
                             # codex: workspace-write, agy: accept-edits)
model = ""                   # empty = the account's default model
server_url = "https://USER.pythonanywhere.com/p/acme"   # this slot's server or project
placed = false               # true: set up because the server placed it on this computer
command = []                 # optional custom headless command, see below
```

### Custom headless command

Normally Hive builds the command that starts an agent's headless run itself
([How an agent is launched](RUNNING.md#how-an-agent-is-launched-for-a-headless-turn-wake)).
Use `command` if a CLI's options differ on your system. Hive replaces these placeholders and
passes the prompt on stdin:

| Placeholder | Replaced with |
|---|---|
| `{session}` | the id of the agent's conversation (*session*) with its AI model |
| `{session_flag}` | `--session-id` (start that session) or `--resume` (continue it) |
| `{mode}` | the permission mode |
| `{bundle}` | the slot's `.hive/agent/<vendor>` folder (Hive's [bundle](GLOSSARY.md#bundle-plugin-extension-profile) for that CLI) |

```toml
command = ["gemini", "-p", "Follow the notice on stdin.", "-o", "stream-json", "-e", "hive",
           "--approval-mode", "{mode}", "--resume", "latest"]
```

The default commands are listed in
[How an agent is launched](RUNNING.md#how-an-agent-is-launched-for-a-headless-turn-wake).

---

## Environment variables

| Variable | Used by | Meaning |
|---|---|---|
| `HIVE_ADMIN_TOKEN` | server, admin CLI | Admin token (overrides `admin.toml` for the CLI). |
| `HIVE_DATABASE_URL` | server | Database URL. |
| `HIVE_GITHUB_TOKEN` | server | Enables GitHub mode. |
| `HIVE_SQLITE_JOURNAL` | server | SQLite journal mode: `wal` (default) or `delete`/`truncate` for network filesystems; auto-`delete` on PythonAnywhere. |
| `HIVE_ARCHIVE_DIR` | server | Folder for the default project's dashboard archives ([Archives](MONITORING.md#archives)). Default: `hive-archives/` next to a SQLite database file, otherwise `~/hive-archives`. Other projects keep theirs in `projects/<project>/hive-archives/` under the data folder. |
| `HIVE_PROJECT_DATABASE_URL` | server | Database URL for new projects, with a `{slug}` placeholder ([Several projects](PROJECTS.md)). |
| `HIVE_DATA_DIR` | server | Folder for project databases and archives. Default: next to a SQLite database file, otherwise `~/hive-data`. |
| `HIVE_SERVER_URL` | admin CLI | Overrides the server URL in `admin.toml`. |
| `HIVE_PROJECT` | admin CLI | The project admin commands act on, like `--project` ([Several projects](PROJECTS.md#which-project-an-admin-command-acts-on)). |
| `HIVE_HOME` | all local commands | Location of Hive's local files (default `~/.hive`). |
| `HIVE_COLOR`, `NO_COLOR`, `FORCE_COLOR` | all commands | Colour on/off ([Coloured output](CLI.md#17-coloured-output)). |
| `HIVE_SLOT`, `HIVE_DAEMON_URL`, `HIVE_LOCAL_SECRET` | set by Hive for agent CLIs | Identify the slot and reach the daemon. |
| `CLAUDE_CONFIG_DIR` / `GEMINI_CLI_HOME` / `CODEX_HOME` | set by Hive per slot | Keep each account's CLI config separate. |
| `USERPROFILE`, `HOME`, `GIT_CONFIG_GLOBAL` | set by Hive for agy slots | agy has no config-folder setting, so the slot's folder becomes its home directory; git keeps your `~/.gitconfig` ([agy home folder](RUNNING.md#agy-home-folder)). |
| `MCP_TOOL_TIMEOUT` | set by Hive | Lets `await_events` block for up to 30 minutes. |

---

## Files and directories

**On each machine (`~/.hive/`, or `$HIVE_HOME`):**

| Path | Contents |
|---|---|
| `machine.toml` | default server URL, local API port and secret, slots with their tokens and server URLs (keep private) |
| `admin.toml` | admin server URL and token, plus `[servers]`: other servers' or projects' admin tokens (only where you ran `hive login`, or `hive server`) |
| `server.toml` | the admin token `hive server` made up and uses when given none (keep private) |
| `config.toml` | your own settings, in every repository on this computer (`hive config set … --user`), including hooks |
| `bundles/<slot>/<cli>/` | the Hive plugin, extension or profile attached to that slot's sessions, rewritten at each launch from the installed Hive and the slot's worktree |
| `config-sync.json` | for each server, a fingerprint of the `hive.toml` last uploaded from here, so a changed one is uploaded automatically |
| `state.db` | what the daemon remembers between runs: which fleet events each slot has seen, a copy of its messages, why it's waiting to be started, and its conversation ids |
| `accounts/<slot>/` | each slot's private Claude, Gemini or Codex config, including its login; for agy, its settings, Hive's plugin and its conversations (agy's login is in the computer's keyring) |
| `logs/daemon.log` | daemon log |
| `logs/<slot>.log` | raw output of every headless turn of that slot |

**In the repository:** `hive.toml`, `.hive/agent/{claude,gemini,codex,agy}/`, `.hive/plans/`,
`.hive/prompts/` (yours; [Customising what agents are told](PROMPTS.md)), `.hive/README.md`,
and `.hive/orchestrator/state.md` (orchestrator worktree only, git-ignored). See
[Preparing the repository](REPOSITORY.md#what-it-creates).

**Next to your clone:** one worktree per slot, `<clone>-<slot>/`, on branch `hive/<slot>`.

**On the server:** the database: agents, events, messages, tasks, leases, decisions, status
feed, reports, live-output lines, and the list of projects. Dashboard archives are files in
`$HIVE_ARCHIVE_DIR`: one `<id>.json.gz` and a small `<id>.meta.json` each. Each other project
([Several projects](PROJECTS.md)) has its own database, by default `projects/<project>/hive.db`,
and archives in `projects/<project>/hive-archives/`, under the data folder.

---

## Timings and limits

| What | Value |
|---|---|
| Daemon polls the server | every 3 s (`poll_interval`) |
| Daemon tells the server a slot is alive / agent shown offline after | every 20 s / 90 s without hearing from it |
| A standby takes over from the orchestrator after | 5 min without contact, or its account out of usage |
| Urgent events delivered (by starting the agent, or ending the orchestrator's `await_events` wait) | ≈ 3 s after arrival |
| Routine events batched for the orchestrator (progress, ordinary messages, completed tasks, new reports, review verdicts) | `debounce_seconds` (90 s) |
| Minimum gap between wakes of one slot | 10 s |
| Maximum wakes per slot | 20 per hour |
| Headless turn timeout | 1 hour |
| Pause after a usage-limit error | 1 hour |
| New CLI session | for each new task (workers), or after a turn ending above 120k tokens of context, or after 40 turns (`[sessions]`) |
| Orchestrator starts fresh after | 12 h idle (`orchestrator_fresh_after_hours`) |
| `task_list` / `report_list` / `status_read` defaults | 50 / 20 / 5 items, as summaries |
| How long a lease (file lock) lasts | 600 s default, 30–3600 s; extended automatically when < 5 min remain |
| Times an agent is sent back when trying to end a run with urgent messages unhandled | 3 per run |
| Message size / rate | 8 KB / 20 per minute per agent |
| Live-output upload | every 1 s, ≤ 500 lines per batch, ≤ 4000 chars per line |
| Live output kept on server | last 20,000 lines per agent |
| `await_events` timeout | 600 s default, 1800 s max |
| Integration check time limit | 30 min per check |
| Approved command time limit | 10 min |
| Approval validity | single use, expires after 1 hour |
| Prompt file size | 32 KB of text after removing comments |

---

**See also:** [CLI.md](CLI.md), [DEPLOYMENT.md §7](DEPLOYMENT.md#7-configuration-reference) ·
[← Back to the README](../README.md)
