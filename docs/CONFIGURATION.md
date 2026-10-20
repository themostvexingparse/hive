# Configuration reference

This page lists every configuration file, environment variable, file location, timing and
limit in one place. The other pages explain what each setting is for. This one is for looking
them up.

| Where | What | Written by |
|---|---|---|
| [`hive.toml`](#hivetoml) | The project's fleet configuration, committed to the repository. | `hive init`, then you |
| [`~/.hive/machine.toml`](#hivemachinetoml) | One machine's slots and their tokens. Private. | `hive join` |
| `~/.hive/admin.toml` | Your admin logins. Private. | `hive admin login` |
| [Environment variables](#environment-variables) | Server settings and overrides. | you |

---

## `hive.toml`

In the repository, committed. Upload it to the server with `hive admin push-config` after
every change (`hive start` also uploads it). Changes to `[permissions]` also need
`hive init` to regenerate the CLIs' permission files.

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
standby = ["claude-2"]       # failover candidates, in order
debounce_seconds = 90        # batching window for routine events
heartbeat_hours = 4          # headless orchestrator only: periodic check-in

[permissions]                # shell-command tiers for agents
preset = "standard"          # standard | strict | none
allow = ["docker compose ps"]          # run directly
approval = ["npm publish"]             # run once the orchestrator approves
deny = ["kubectl delete"]              # never

[roles.docs-writer]          # custom roles / overrides
can_code = true
task_kinds = ["docs"]
```

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
| `[permissions]` | [Shell-command permissions](PERMISSIONS.md#customising) |
| `[roles.<name>]` | [Custom roles and overrides](ROLES.md#custom-roles-and-overrides) |

---

## `~/.hive/machine.toml`

Per machine, private, written by `hive join`. You rarely edit it by hand.

```toml
server_url = "https://USER.pythonanywhere.com"   # default for slots that name none
local_port = 8766            # daemon's local API (127.0.0.1 only)
local_secret = "…"           # generated; protects the local API
poll_interval = 3.0          # seconds between server polls

[[slots]]
name = "claude-2"
vendor = "claude"
token = "hv_…"
worktree = "/path/to/proj-claude-2"
config_dir = "/home/you/.hive/accounts/claude-2"
permission_mode = ""         # empty = default (claude: acceptEdits, gemini: auto_edit,
                             # codex: workspace-write)
model = ""                   # empty = the account's default model
server_url = "https://USER.pythonanywhere.com/p/acme"   # this slot's server or project
command = []                 # optional custom headless command, see below
```

### Custom headless command

Use `command` if a CLI's flags differ on your system. Hive replaces these placeholders and
passes the prompt on stdin:

| Placeholder | Replaced with |
|---|---|
| `{session}` | the session id |
| `{session_flag}` | `--session-id` or `--resume` |
| `{mode}` | the permission mode |
| `{bundle}` | the slot's `.hive/agent/<vendor>` folder |

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
| `HIVE_COLOR`, `NO_COLOR`, `FORCE_COLOR` | all commands | Colour on/off ([Coloured output](CLI.md#16-coloured-output)). |
| `HIVE_SLOT`, `HIVE_DAEMON_URL`, `HIVE_LOCAL_SECRET` | set by Hive for agent CLIs | Identify the slot and reach the daemon. |
| `CLAUDE_CONFIG_DIR` / `GEMINI_CLI_HOME` / `CODEX_HOME` | set by Hive per slot | Keep each account's CLI config separate. |
| `MCP_TOOL_TIMEOUT` | set by Hive | Lets `await_events` block for up to 30 minutes. |

---

## Files and directories

**On each machine (`~/.hive/`, or `$HIVE_HOME`):**

| Path | Contents |
|---|---|
| `machine.toml` | default server URL, local API port and secret, slots with their tokens and server URLs (keep private) |
| `admin.toml` | admin server URL and token, plus `[servers]`: other servers' or projects' admin tokens (only where you ran `hive admin login`) |
| `state.db` | daemon state: event cursors, inbox cache, pending wake reasons, session ids |
| `accounts/<slot>/` | each slot's private Claude, Gemini or Codex config, including its login |
| `logs/daemon.log` | daemon log |
| `logs/<slot>.log` | raw output of every headless turn of that slot |

**In the repository:** `hive.toml`, `.hive/agent/{claude,gemini,codex}/`, `.hive/plans/`,
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
| Heartbeat / agent shown offline after | every 20 s / 90 s without one |
| Orchestrator failover after | 5 min unseen, or out of quota |
| Urgent events delivered (wake or `await_events`) | ≈ 3 s after arrival |
| Routine events batched for the orchestrator | `debounce_seconds` (90 s) |
| Minimum gap between wakes of one slot | 10 s |
| Maximum wakes per slot | 20 per hour |
| Headless turn timeout | 1 hour |
| Pause after a usage-limit error | 1 hour |
| New CLI session after | 40 turns |
| Lease TTL | 600 s default, 30–3600 s; auto-extended when < 5 min remain |
| Stop-hook pushbacks per turn | 3 |
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
