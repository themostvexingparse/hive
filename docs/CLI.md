# Using the `hive` command

`hive` is the one command for everything in Hive: running the server, setting up a
repository, adding agents, running machines, and steering the fleet. This guide shows each
command with examples. The README's [command reference](../README.md#19-command-reference) is
the one-table summary; [§20](../README.md#20-configuration-reference) has every config file
and environment variable.

---

## Contents

1. [Where each command runs](#1-where-each-command-runs)
2. [A first run, start to finish](#2-a-first-run-start-to-finish)
3. [Options every command accepts](#3-options-every-command-accepts)
4. [Running the server](#4-running-the-server)
5. [Logging in as admin](#5-logging-in-as-admin)
6. [Preparing a repository](#6-preparing-a-repository)
7. [Adding agents](#7-adding-agents)
8. [Running a machine](#8-running-a-machine)
9. [Steering the fleet](#9-steering-the-fleet)
10. [Several projects on one server](#10-several-projects-on-one-server)
11. [Backups](#11-backups)
12. [Commands the agent CLIs run](#12-commands-the-agent-clis-run)
13. [Common recipes](#13-common-recipes)
14. [Error messages and what to do](#14-error-messages-and-what-to-do)

---

## 1. Where each command runs

| Where | Who | Commands |
|---|---|---|
| The server | the host (you) | `hive server` (for trials; production uses a WSGI host) |
| Your computer | you, as admin | `hive admin …`, `hive init`, `hive overview`, `hive prompts`, `hive start`, `hive status`, `hive decisions`, `hive decide`, `hive say`, `hive watch`, `hive pause`, `hive resume`, `hive reset` |
| Each agent machine | you, once per slot | `hive join`, `hive up`, `hive chat` |
| Inside agent sessions | the agent CLIs | `hive mcp`, `hive hook` (you never run these) |

Your computer can be an agent machine too. That's the usual setup for the orchestrator.

Examples below use POSIX shell syntax. They work the same in PowerShell and cmd, except
where a section shows Windows variants.

---

## 2. A first run, start to finish

A complete trial on one machine: one server, one orchestrator, one programmer.

```sh
# 1. Start a server (keep this terminal open)
python -c "import secrets; print(secrets.token_urlsafe(32))"      # → your admin token
hive server --admin-token <admin token>

# 2. In another terminal: log in as admin
hive admin login --server http://127.0.0.1:8765 --token <admin token>

# 3. Prepare the repository, commit, push, upload its config
cd ~/code/myapp
hive init
git add hive.toml .hive && git commit -m "Wire for Hive" && git push
hive admin push-config

# 4. Enroll two agents (each prints a token and a ready-made `hive join` line)
hive admin enroll orch --vendor claude --roles orchestrator
hive admin enroll dev  --vendor claude --roles programmer

# 5. Add both as slots on this machine
hive join orch --vendor claude --token <orch token> --repo ~/code/myapp
hive join dev  --vendor claude --token <dev token>  --repo ~/code/myapp

# 6. Log each slot's CLI account in once (hive join prints the exact command)

# 7. Run the machine: the orchestrator opens in this terminal
hive up

# 8. In another terminal: give the fleet a goal, then approve the plan
hive start "Add CSV export to the reports page"
hive decisions
hive decide 1 approve
```

From here, follow along with `hive status` and `hive watch`, and answer decisions as they
come.

---

## 3. Options every command accepts

| Option | Meaning |
|---|---|
| `--version` | Print Hive's version. |
| `--color auto\|always\|never` | Colour the output. `auto` (default) colours only on a terminal. Also `HIVE_COLOR`, `NO_COLOR`, `FORCE_COLOR`. |
| `--project NAME` | Which project admin commands act on (§10). Can go anywhere on the line. Also `HIVE_PROJECT`. |
| `-h`, `--help` | Help for any command or subcommand, e.g. `hive join --help`. |

```sh
hive --version
hive status --color never > status.txt
hive admin project --help
```

---

## 4. Running the server

```sh
hive server [--host H] [--port P] [--db URL] [--sqlite-journal wal|delete|truncate] [--admin-token T]
```

This is the development server: good for trials and a LAN. For a public server, deploy the
WSGI app instead ([DEPLOYMENT.md](DEPLOYMENT.md)).

```sh
# Local trial: SQLite file in the current folder, port 8765
hive server --admin-token "$HIVE_ADMIN_TOKEN"

# Reachable from other machines on your LAN, with the database somewhere specific
hive server --host 0.0.0.0 --port 9000 --db sqlite:////srv/hive/hive.db --admin-token "$TOKEN"

# MySQL instead of SQLite
hive server --db "mysql+pymysql://hive:secret@db.local/hive" --admin-token "$TOKEN"
```

| Option | Default | Notes |
|---|---|---|
| `--host` | `127.0.0.1` | `0.0.0.0` to accept other machines. |
| `--port` | `8765` | |
| `--db` | `sqlite:///hive.db` | Ignored if `HIVE_DATABASE_URL` is set. On Windows write SQLite paths as `sqlite:///C:/path/hive.db`. |
| `--sqlite-journal` | `wal` | Use `delete` on network filesystems. |
| `--admin-token` | `HIVE_ADMIN_TOKEN` | Required one way or the other. |

On start it prints the address, the dashboard URL, the database and the projects it serves.

---

## 5. Logging in as admin

```sh
hive admin login --server URL --token TOKEN
```

Every admin command needs a login. It checks the token against the server and saves it in
`~/.hive/admin.toml` (readable only by you).

```sh
# The server's admin token: admin of the whole server, every project
hive admin login --server https://hive.example.com --token <HIVE_ADMIN_TOKEN>
# → Logged in to https://hive.example.com as the server's admin (all projects).

# A single project's admin token (§10)
hive admin login --server https://hive.example.com/p/acme --token <acme's admin token>
# → Logged in to https://hive.example.com/p/acme as admin of project acme.
```

- **Logins accumulate.** A project login is kept beside your server login. Logging in to a
  different server makes it the default and keeps the previous one too.
- **Environment overrides:** `HIVE_SERVER_URL` and `HIVE_ADMIN_TOKEN` override the file, for
  scripts:

  ```sh
  HIVE_SERVER_URL=https://hive.example.com HIVE_ADMIN_TOKEN=… hive status
  ```

---

## 6. Preparing a repository

### `hive init`: wire the repository for Hive

```sh
hive init [PATH] [--server URL] [--main BRANCH] [--hive-cmd CMD]
```

Run once in a clone of the project (and again after upgrading Hive). It writes `hive.toml`
and the agent bundles under `.hive/agent/`, and prints what to do next.

```sh
cd ~/code/myapp
hive init                                         # server from your admin login
hive init --main develop                          # the integration branch isn't `main`
hive init --server https://hive.example.com/p/acme   # a project on a shared server (§10)
hive init ../other-repo                           # another folder
hive init --hive-cmd /opt/hive/bin/hive           # agents' tools must call this exact `hive`
```

Commit and push what it wrote (`hive.toml`, `.hive/`), so every machine gets it.

### `hive admin push-config`: upload `hive.toml`

```sh
cd ~/code/myapp
hive admin push-config
```

The server keeps its own copy of `hive.toml` (roles, permissions, review policy). Upload it
after every change. `hive start` also uploads it when run inside the repository.

### `hive overview`: check the whole setup

```sh
hive overview              # this repository
hive overview ../myapp     # another one
hive overview --offline    # don't contact the server or the local daemon
```

It describes the repository, configuration, permissions, roles, prompt files, this machine's
slots and the fleet. It ends with a list of **problems, each with the command that fixes it**.
Run it whenever something seems off.

### `hive prompts`: see what agents are told

```sh
hive prompts                                         # a programmer
hive prompts --role orchestrator
hive prompts --role programmer,tester --slot dev --vendor gemini
hive prompts --role reviewer --full                  # include the built-in skill text
hive prompts --path ../myapp
```

Use it after editing `.hive/prompts/` (README §17.5) to check your additions and placeholders.

---

## 7. Adding agents

Adding an agent has two steps: **enroll** it on the server (your computer), then **join**
it on the machine that will run it.

### `hive admin enroll`: create an agent

```sh
hive admin enroll NAME --vendor claude|gemini|codex --roles ROLE[,ROLE…]
```

```sh
hive admin enroll orch     --vendor claude --roles orchestrator
hive admin enroll claude-2 --vendor claude --roles programmer
hive admin enroll gemini-1 --vendor gemini --roles reviewer,planner
hive admin enroll codex-1  --vendor codex  --roles programmer,tester
```

It prints the agent's **token, once**, and the full `hive join` command to run on the agent's
machine (with the right `--server`, including the project if there is one). Names must match
`^[a-z][a-z0-9-]{1,31}$`. `orchestrator` can't be combined with a coding role.

### `hive admin roles`: change an agent's roles

```sh
hive admin roles claude-2 programmer,reviewer
```

While the fleet is active, some changes become a decision for you to approve; Hive prints the
`hive decide` command for it.

### `hive join`: add a slot to a machine

```sh
hive join NAME --vendor V --token T [--server URL] [--repo PATH | --worktree PATH]
          [--config-dir PATH] [--permission-mode MODE] [--model MODEL] [--hive-cmd CMD] [--replace]
```

```sh
# The usual form: worktree created next to your clone, as ~/code/myapp-claude-2
hive join claude-2 --vendor claude --token hv_… --server https://hive.example.com --repo ~/code/myapp

# Choose the worktree's location
hive join claude-2 --vendor claude --token hv_… --repo ~/code/myapp --worktree /fast-disk/myapp-c2

# A Codex slot on a specific model, sandboxed to its worktree
hive join codex-1 --vendor codex --token hv_… --repo ~/code/myapp \
                  --model <model-name> --permission-mode workspace-write

# Re-join to replace a lost or rotated token: server and worktree are kept
hive join claude-2 --vendor claude --token hv_new… --repo ~/code/myapp
```

What it does: checks the token, creates the worktree on branch `hive/NAME`, installs the git
pre-commit hook, creates the slot's private CLI config folder, and saves the slot to
`~/.hive/machine.toml`. At the end it prints how to **log the slot's CLI account in once**,
for your shell. For example, on Windows:

```powershell
cd "C:\code\myapp-claude-2"; $env:CLAUDE_CONFIG_DIR = "C:\Users\you\.hive\accounts\claude-2"; claude; Remove-Item Env:CLAUDE_CONFIG_DIR
```

| Option | Meaning |
|---|---|
| `--server URL` | The server, or a project's URL (`…/p/NAME`). Saved for **this slot only**. Default: the slot's current one when re-joining, else the machine's default (set by the first join). |
| `--repo PATH` | Your clone; the worktree goes next to it. |
| `--worktree PATH` | Explicit worktree path. |
| `--config-dir PATH` | The CLI account folder (default `~/.hive/accounts/NAME`). |
| `--permission-mode MODE` | Claude: `acceptEdits` (default), `bypassPermissions`, …; Gemini: `auto_edit` (default), `yolo`; Codex: `workspace-write` (default), `read-only`, `danger-full-access`, `yolo`. |
| `--model MODEL` | The model this slot's CLI uses (default: the account's). |
| `--hive-cmd CMD` | How the git hook calls `hive`. |
| `--replace` | Move an existing slot of this name to another server or project. Its local state is reset. |

---

## 8. Running a machine

### `hive up`: run every slot on this machine

```sh
hive up                # orchestrator slot (if any) opens interactively in this terminal
hive up --headless     # run the orchestrator headless too (e.g. on a server box)
hive up -v             # debug logging in ~/.hive/logs/daemon.log
```

- **On the orchestrator's machine,** the terminal becomes the orchestrator's chat. Workers on
  the same machine run in the background. When the session ends, Hive asks whether to
  relaunch it.
- **On a worker machine,** the terminal streams every slot's live output. Stop with Ctrl+C.
- **Slots of several servers or projects** run together; the start-up table then shows each
  slot's server.

### `hive chat`: take over a slot interactively

```sh
hive chat claude-2            # resume the slot's current session in its native CLI
hive chat claude-2 --fresh    # start a new session
```

Useful to log a CLI in, to look over an agent's shoulder, or to help it by hand. While you're
attached, the daemon doesn't wake that slot headlessly. `hive up` should be running on that
machine, otherwise Hive's tools and hooks won't work in the session.

---

## 9. Steering the fleet

### `hive start`: give the fleet a goal

```sh
cd ~/code/myapp
hive start "Add CSV export to the reports page, with tests"
```

It uploads `hive.toml` (when run inside the repository) and moves the fleet to *planning*.
The orchestrator plans, then asks you to approve the plan as a decision.

### `hive status`: what's going on

```sh
hive status           # fleet, waiting decision, agents, open tasks, leases, recent updates
hive status --all     # include finished and cancelled tasks
hive status -n 30     # the last 30 status updates (default 5)
hive status -v        # status updates with their full bodies
```

### `hive decisions` and `hive decide`: answer what the fleet asks

```sh
hive decisions          # open and queued decisions, each with its answer commands
hive decisions --all    # also the ones already answered
```

```sh
hive decide 3 approve
hive decide 3 reject --note "Split the migration into its own task first"
hive decide 7 "Use PostgreSQL; we already run it"     # free-text answer
```

`hive decisions` prints a ready `hive decide` line for each option. In the orchestrator's own
terminal you can answer without leaving it: `! hive decide 3 approve`.

### `hive say`: message the orchestrator

```sh
hive say "Prioritise the API; the UI can wait until tomorrow"
```

It posts to `#user` with high priority, which wakes the orchestrator within seconds.

### `hive watch`: follow live output

```sh
hive watch                # every agent, following (Ctrl+C to stop)
hive watch claude-2       # one agent
hive watch -n 1000        # start with the last 1000 lines
hive watch --no-follow    # print and exit
```

Only admins can read live output; agents can't, not even the orchestrator.

### `hive pause`, `hive resume`, `hive reset`

```sh
hive pause     # stop every agent; lease timers pause too
hive resume    # back to the state before the pause
hive reset     # emergency: back to setup; keeps agents, tasks and history
```

To end a run and start over cleanly, use the dashboard's *Archives… → Archive & clear…*
instead of `reset` (README §18).

---

## 10. Several projects on one server

One server can host several projects, each with its own fleet, agents and database. A project
lives at `https://host/p/NAME`. The server's own address is the `default` project. See README
[§4.4](../README.md#44-several-projects-on-one-server) for how it works.

### Managing projects (server admin only)

```sh
hive admin project create acme --name "Acme Corp"          # prints its URL and admin token
hive admin project create beta --db-url "mysql+pymysql://u:pw@db/u\$hive_beta"
hive admin project list
hive admin project set acme --name "Acme Corporation"
hive admin project set acme --github-token-env HIVE_GITHUB_TOKEN_ACME
hive admin project set acme --archive        # stop serving it; data is kept
hive admin project set acme --unarchive
hive admin project rotate acme               # new admin token; the old one stops working
```

### Which project a command acts on

Admin commands pick their project in this order:
1. `--project NAME` (anywhere on the line), or `HIVE_PROJECT`.
2. The project the current repository's `hive.toml` names (`[server] url = "…/p/NAME"`).
3. The server you logged in to: its `default` project.

```sh
hive --project acme status
hive status --project acme                 # same thing
HIVE_PROJECT=acme hive decisions
cd ~/code/acme-app && hive status          # hive.toml names acme, so no flag needed
```

### Setting up a project, end to end

```sh
# Server admin
hive admin project create acme --name "Acme Corp"

# In the project's repository
cd ~/code/acme-app
hive init --server https://hive.example.com/p/acme
git add hive.toml .hive && git commit -m "Wire for Hive (acme)" && git push
hive admin push-config                          # goes to acme: hive.toml names it
hive admin enroll orch --vendor claude --roles orchestrator
hive admin enroll dev  --vendor codex  --roles programmer

# On each agent machine (enroll printed these lines, with --server …/p/acme)
hive join orch --vendor claude --token hv_… --server https://hive.example.com/p/acme --repo ~/code/acme-app
hive up
```

### One machine, several projects

A machine can run slots of different projects; `hive up` runs them all. Slot names must be
unique on a machine, so if two projects both have an agent named `dev`, enroll one under
another name. `hive join` refuses a second `dev` rather than replacing the first:

```sh
hive join dev --vendor claude --token hv_… --server https://hive.example.com/p/beta --repo ~/code/beta
# ✗ this machine already has a slot named 'dev', for https://hive.example.com/p/acme. …
```

### Handing a project to someone else

Give them the project's admin token (from `create` or `rotate`). They log in with it:

```sh
hive admin login --server https://hive.example.com/p/acme --token hp_…
```

That token works for that project only: it can't see other projects or manage projects. The
dashboard is at `https://hive.example.com/p/acme/`.

---

## 11. Backups

```sh
hive admin backup                           # → hive-backup-<time>.db in this folder
hive admin backup -o ~/backups/hive.db
hive admin backup --project acme -o acme.db # one project's database
```

This makes a consistent copy of a **SQLite** database inside the server process and
downloads it. For MySQL or Postgres, use the database's own dump tool
([DEPLOYMENT.md §10](DEPLOYMENT.md#10-backups-and-restore)).

---

## 12. Commands the agent CLIs run

You don't run these yourself. They are listed so you recognise them in configs and process
lists.

| Command | Run by |
|---|---|
| `hive mcp [--slot S]` | Each agent CLI, to get Hive's MCP tools (stdio). |
| `hive hook EVENT [--vendor V]` | The CLIs' hooks: message delivery and write enforcement. |
| `hive hook git-pre-commit` | The git pre-commit hook installed by `hive join`. |

---

## 13. Common recipes

**Check everything after a change.**
```sh
hive overview
```

**Move a slot to another server or project.**
```sh
hive join claude-2 --vendor claude --token <new token> --server https://new.example.com --replace
```

**An agent's token was lost.** Rotate it with the admin token, then re-join with the new one:
```sh
curl -X POST -H "Authorization: Bearer $HIVE_ADMIN_TOKEN" https://hive.example.com/api/v1/agents/claude-2/token
hive join claude-2 --vendor claude --token <new token> --repo ~/code/myapp
```

**Script against several projects.**
```sh
for p in acme beta; do hive --project "$p" status --color never; done
```

**The orchestrator is waiting and you want to talk to it now.** In its terminal press **Esc**,
then type. From anywhere else: `hive say "…"`.

**End a run and keep its history.** Dashboard → ⋯ → *Archives…* → *Archive & clear…*, then
`hive start "<next goal>"`.

---

## 14. Error messages and what to do

| Message | Meaning and fix |
|---|---|
| `not logged in as admin; run hive admin login …` | No admin login on this computer. Run `hive admin login --server URL --token TOKEN`. |
| `no admin token for https://…/p/NAME` | You're logged in, but not to that server or project. Log in to it, or use the server's admin token. |
| `managing projects needs the server's own admin token` | Project commands need `HIVE_ADMIN_TOKEN`, not a project's token. |
| `token belongs to 'x', not 'y'` | The token is for another agent; check the name you passed to `hive join`. |
| `this machine already has a slot named 'dev', for …` | Another server or project's slot has that name. Use another name, or `--replace` to move the slot. |
| `no slots on this machine; run hive join first` | `hive up` has nothing to run. |
| `could not reach the Hive server for …` | `hive up` couldn't confirm a slot's identity. Check the server is up and the tokens in `~/.hive/machine.toml` are valid; `hive overview` helps. |
| `no project named 'x' on this server` | Wrong project name in the URL, or the project was archived (`hive admin project list`). |
| `no hive.toml found (run hive init)` | Run the command inside a repository prepared with `hive init`. |

For anything else, `hive overview` lists problems with their fixes, and README
[§24](../README.md#24-troubleshooting) covers troubleshooting.
