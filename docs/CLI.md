# Using the `hive` command

`hive` is the one command for everything in Hive: running the server, setting up a
repository, adding agents, running the computers that host them, and steering the **fleet**
(the team of AI agents working on your repository). This guide shows each command with
examples. [§15](#15-command-summary) is the one-table summary of every command and option;
the [configuration reference](CONFIGURATION.md) has every config file and environment
variable. If Hive is new to you, read [How Hive works](ARCHITECTURE.md) first; the
[glossary](GLOSSARY.md) explains every term.

**Names used in the examples:**
- `~/code/myapp`: your local copy (clone) of the repository the agents work on;
- `orch`, `dev`, `claude-2`, `gemini-1`, `codex-1`: example agent names (`orch` is the
  orchestrator, the agent that plans and that you chat with; the others are workers);
- `hive.example.com`: your Hive server's address;
- `acme`, `beta`: example [projects](PROJECTS.md) on a server that hosts several
  repositories; `~/code/acme-app` is `acme`'s repository;
- `<…>` and `…`: a value you fill in, such as a token. Agent tokens (an agent's password,
  printed when you create it) start with `hv_`; project admin tokens with `hp_`.

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
15. [Command summary](#15-command-summary)
16. [Coloured output](#16-coloured-output)

---

## 1. Where each command runs

| Where | Who | Commands |
|---|---|---|
| The server | the host (you) | `hive server` (for trials; online, the server runs as a Python web app on a web host instead, see [DEPLOYMENT.md](DEPLOYMENT.md)) |
| Your computer | you, as admin (the person controlling the fleet) | `hive admin …`, `hive init`, `hive overview`, `hive prompts`, `hive start`, `hive status`, `hive decisions`, `hive decide`, `hive say`, `hive watch`, `hive pause`, `hive resume`, `hive reset` |
| Each computer that runs agents | you, once per agent | `hive join`, `hive up`, `hive chat` |
| Inside agents' sessions | the agents' CLIs (Claude Code, Gemini CLI, Codex, agy) | `hive mcp`, `hive hook` (you never run these) |

On a computer, each agent lives in a **slot**: its own working folder of the repository (a
[git worktree](GLOSSARY.md#worktree)) and its own logged-in CLI account
([Setting up machines](MACHINES.md)). Your computer can run agents too. That's the usual setup
for the orchestrator.

Examples below are written for bash or zsh (macOS, Linux, Git Bash on Windows). They work the
same in PowerShell and cmd, except where a section shows Windows variants.

---

## 2. A first run, start to finish

A complete trial on one computer: one server, one orchestrator, one programmer (an agent
that writes code). [Getting started](GETTING_STARTED.md) walks through the same steps with
explanations.

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

# 4. Create (enroll) two agents (each prints a token and a ready-made `hive join` line)
hive admin enroll orch --vendor claude --roles orchestrator
hive admin enroll dev  --vendor claude --roles programmer

# 5. Add both to this computer (as slots), using the tokens from step 4
hive join orch --vendor claude --token <orch token> --repo ~/code/myapp
hive join dev  --vendor claude --token <dev token>  --repo ~/code/myapp

# 6. Log each slot's CLI account in once (hive join prints the exact command)

# 7. Run the machine: the orchestrator opens in this terminal
hive up

# 8. In another terminal: give the fleet a goal, then approve the plan
hive start "Add CSV export to the reports page"
hive decisions          # shows the plan as decision number 1
hive decide 1 approve
```

From here, follow along with `hive status` and `hive watch`, and answer decisions (questions
the orchestrator asks you) as they come.

---

## 3. Options every command accepts

| Option | Meaning |
|---|---|
| `--version` | Print Hive's version. |
| `--color auto\|always\|never` | Colour the output. `auto` (default) colours only on a terminal. Also `HIVE_COLOR`, `NO_COLOR`, `FORCE_COLOR`. |
| `--project NAME` | Which [project](PROJECTS.md) admin commands act on, when a server hosts several (§10). Can go anywhere on the line. Also the `HIVE_PROJECT` environment variable. |
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

This is a simple built-in web server: good for trials and a local network. For a server on
the internet, deploy Hive as a web app on a proper host instead
([DEPLOYMENT.md](DEPLOYMENT.md)). The admin token is the password for the whole fleet; you make
it up (see §2). `$HIVE_ADMIN_TOKEN` and `$TOKEN` below are shell variables holding it.

```sh
# Local trial: SQLite file in the current folder, port 8765
hive server --admin-token "$HIVE_ADMIN_TOKEN"

# Reachable from other computers on your local network, with the database file somewhere specific
hive server --host 0.0.0.0 --port 9000 --db sqlite:////srv/hive/hive.db --admin-token "$TOKEN"

# A MySQL database instead of a SQLite file (user hive, password secret, server db.local)
hive server --db "mysql+pymysql://hive:secret@db.local/hive" --admin-token "$TOKEN"
```

| Option | Default | Notes |
|---|---|---|
| `--host` | `127.0.0.1` | `127.0.0.1` accepts only this computer; `0.0.0.0` accepts other computers too. |
| `--port` | `8765` | |
| `--db` | `sqlite:///hive.db` | Where to store data ([database URL](GLOSSARY.md#sqlite-mysql-postgres-database-url)). Ignored if `HIVE_DATABASE_URL` is set. On Windows write SQLite paths as `sqlite:///C:/path/hive.db`. |
| `--sqlite-journal` | `wal` | How SQLite writes its file. Use `delete` if the file is on a network drive. |
| `--admin-token` | `HIVE_ADMIN_TOKEN` | Required one way or the other. |

On start it prints the address, the dashboard URL, the database and the projects it serves.

---

## 5. Logging in as admin

```sh
hive admin login --server URL --token TOKEN
```

Every admin command needs a login. It checks the admin token against the server and saves it
in `~/.hive/admin.toml` (readable only by you). On a server that hosts several
[projects](PROJECTS.md), each project has its own address (`…/p/<project>`) and its own admin
token, besides the server's own.

```sh
# The server's admin token: admin of the whole server, every project
hive admin login --server https://hive.example.com --token <HIVE_ADMIN_TOKEN>
# → Logged in to https://hive.example.com as the server's admin (all projects).

# A single project's admin token (§10); acme is an example project
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

Run once in a clone of the repository (and again after upgrading Hive). It writes `hive.toml`
(the project's Hive settings) and, under `.hive/agent/`, the *bundles*: for each CLI, the
tools, automatic checks and instructions Hive attaches to agents
([Preparing the repository](REPOSITORY.md)). It prints what to do next.

```sh
cd ~/code/myapp
hive init                                         # server from your admin login
hive init --main develop                          # finished work goes into `develop`, not `main`
hive init --server https://hive.example.com/p/acme   # a project on a shared server (§10)
hive init ../other-repo                           # another folder
hive init --hive-cmd /opt/hive/bin/hive           # agents' tools must call this exact `hive`
```

Commit and push what it wrote (`hive.toml`, `.hive/`), so every agent's copy of the
repository gets it.

### `hive admin push-config`: upload `hive.toml`

```sh
cd ~/code/myapp
hive admin push-config
```

The server keeps its own copy of `hive.toml`, because it enforces the [roles](ROLES.md), the
[shell-command permissions](PERMISSIONS.md) and the [review](INTEGRATION.md#reviews) setting
in it. Upload it after every change. `hive start` also uploads it when run inside the
repository.

### `hive overview`: check the whole setup

```sh
hive overview              # this repository
hive overview ../myapp     # another one
hive overview --offline    # don't contact the server, or the local daemon (the program hive up runs)
```

It describes the repository, configuration, permissions, roles, prompt files (your additions
to the agents' instructions), this computer's slots and the fleet. It ends with a list of **problems, each with the command that fixes it**.
Run it whenever something seems off.

### `hive prompts`: see what agents are told

```sh
hive prompts                                         # what a programmer is told
hive prompts --role orchestrator
hive prompts --role programmer,tester --slot dev --vendor gemini   # as agent dev, on Gemini CLI
hive prompts --role reviewer --full                  # include the built-in instructions (skill)
hive prompts --path ../myapp
```

Use it after editing `.hive/prompts/` ([Customising what agents are told](PROMPTS.md)) to check your additions and placeholders.

---

## 7. Adding agents

Adding an agent has two steps: **enroll** (create) it on the server, from your computer, then
**join** it on the computer that will run it ([Setting up machines](MACHINES.md)).

### `hive admin enroll`: create an agent

```sh
hive admin enroll NAME --vendor claude|gemini|codex|agy --roles ROLE[,ROLE…]
```

```sh
hive admin enroll orch     --vendor claude --roles orchestrator
hive admin enroll claude-2 --vendor claude --roles programmer
hive admin enroll gemini-1 --vendor gemini --roles reviewer,planner
hive admin enroll codex-1  --vendor codex  --roles programmer,tester
```

`--vendor` is the CLI the agent runs; `--roles` says what it does ([Roles](ROLES.md)). It
prints the agent's **token** (its password) **once**, and the full `hive join` command to run
on the agent's computer (with the right `--server`, including the project if there is one).
Names are 2 to 32 lowercase letters, digits and hyphens, starting with a letter
(`^[a-z][a-z0-9-]{1,31}$`). `orchestrator` can't be combined with a role that writes code.

### `hive admin roles`: change an agent's roles

```sh
hive admin roles claude-2 programmer,reviewer
```

This replaces the agent's roles. While the fleet is active, some changes become a decision
(a question for you) to approve; Hive prints the `hive decide` command for it.

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

# A Codex agent on a specific AI model, allowed to write only inside its own folder
hive join codex-1 --vendor codex --token hv_… --repo ~/code/myapp \
                  --model <model-name> --permission-mode workspace-write

# Re-join with a new token (after losing or replacing the old one): server and worktree are kept
hive join claude-2 --vendor claude --token hv_new… --repo ~/code/myapp
```

What it does: checks the token, creates the agent's working folder (a git worktree, next to
your clone) on branch `hive/NAME`, installs a git `pre-commit` hook (a check git runs before
each commit, refusing agents' commits of files they haven't locked), creates the agent's
private CLI settings folder, and saves the slot to this computer's list,
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
| `--permission-mode MODE` | The CLI's own setting for how much it may do without asking. Claude: `acceptEdits` (default), `bypassPermissions`, …; Gemini: `auto_edit` (default), `yolo`; Codex: `workspace-write` (default), `read-only`, `danger-full-access`, `yolo`; agy: `accept-edits` (default), `auto`, `plan`. The defaults allow file edits; the others allow less or everything ([details](MACHINES.md#join-each-slot-on-the-machine-that-will-run-it)). |
| `--model MODEL` | The AI model this agent's CLI uses (default: the account's). |
| `--hive-cmd CMD` | How the git hook calls `hive`. |
| `--replace` | Move an existing slot of this name to another server or project. What it remembers locally (messages seen, its conversation) is reset. |

---

## 8. Running a machine

### `hive up`: run every slot on this machine

```sh
hive up                # the orchestrator (if it lives here) opens in this terminal for you to chat with
hive up --headless     # run the orchestrator without a terminal too, like the workers (e.g. on a remote computer)
hive up -v             # detailed logging in ~/.hive/logs/daemon.log
```

- **On the orchestrator's machine,** the terminal becomes the orchestrator's chat. Workers on
  the same machine run in the background. When the session ends, Hive asks whether to
  relaunch it.
- **On a computer with only workers,** the terminal shows what every agent does as it works.
  Stop with Ctrl+C.
- **Slots of several servers or projects** run together; the table `hive up` prints when it
  starts then shows each slot's server.

### `hive chat`: take over a slot interactively

```sh
hive chat claude-2            # resume the slot's current session in its native CLI
hive chat claude-2 --fresh    # start a new session
```

This opens the agent's own CLI in your terminal, continuing its conversation. Useful to log
a CLI in, to look over an agent's shoulder, or to help it by hand. While you're attached,
Hive doesn't start that agent in the background. `hive up` should be running on that
computer, otherwise Hive's tools and automatic checks won't work in the session.

---

## 9. Steering the fleet

### `hive start`: give the fleet a goal

```sh
cd ~/code/myapp
hive start "Add CSV export to the reports page, with tests"
```

It uploads `hive.toml` (when run inside the repository) and moves the fleet to the
*planning* state ([The fleet lifecycle](LIFECYCLE.md)). The orchestrator plans, then asks you
to approve the plan as a decision.

### `hive status`: what's going on

```sh
hive status           # fleet state, waiting decision, agents, open tasks, leases (file locks), recent updates
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

The number is the decision's, as `hive decisions` shows it. `hive decisions` prints a ready
`hive decide` line for each option. In the orchestrator's own terminal you can answer without
leaving it: `! hive decide 3 approve` (in Claude Code and Gemini CLI, `!` runs a shell command).

### `hive say`: message the orchestrator

```sh
hive say "Prioritise the API; the UI can wait until tomorrow"
```

It posts to `#user`, the channel between you and the orchestrator, with high priority, which
gets the orchestrator's attention within seconds.

### `hive watch`: follow live output

```sh
hive watch                # every agent, following (Ctrl+C to stop)
hive watch claude-2       # one agent
hive watch -n 1000        # start with the last 1000 lines
hive watch --no-follow    # print and exit
```

Live output is what agents print while they work. Only admins can read it; agents can't, not
even the orchestrator ([Watching the fleet](MONITORING.md)).

### `hive pause`, `hive resume`, `hive reset`

```sh
hive pause     # stop every agent; the clocks on file locks (leases) pause too
hive resume    # back to the state before the pause
hive reset     # emergency: back to the setup state; keeps agents, tasks and history
```

To end a run and start over cleanly, use the web dashboard's *Archives… → Archive & clear…*
instead of `reset` ([Monitoring](MONITORING.md#archives)).

---

## 10. Several projects on one server

One server can host several projects (separate repositories), each with its own fleet,
agents and database. A project lives at `https://host/p/NAME` (`host` being your server's
address). The server's own address is the `default` project. The examples use two made-up
projects, `acme` and `beta`. See
[Several projects on one server](PROJECTS.md) for how it works.

### Managing projects (server admin only)

```sh
hive admin project create acme --name "Acme Corp"          # prints its URL and admin token
hive admin project create beta --db-url "mysql+pymysql://u:pw@db/u\$hive_beta"   # its own MySQL database
hive admin project list
hive admin project set acme --name "Acme Corporation"
hive admin project set acme --github-token-env HIVE_GITHUB_TOKEN_ACME   # GitHub mode, see INTEGRATION.md
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

# On each computer that runs agents (enroll printed these lines, with --server …/p/acme)
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

Give them the project's admin token (printed by `hive admin project create` or
`hive admin project rotate`; it starts with `hp_`). They log in with it:

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

This makes a copy of a **SQLite** database file, taken safely while the server keeps running,
and downloads it to your computer. For MySQL or Postgres, use the database's own backup
(dump) tool
([DEPLOYMENT.md §10](DEPLOYMENT.md#10-backups-and-restore)).

---

## 12. Commands the agent CLIs run

You don't run these yourself. They are listed so you recognise them in configuration files and
lists of running programs.

| Command | Run by |
|---|---|
| `hive mcp [--slot S]` | Each agent CLI, to get Hive's tools. It's an [MCP server](GLOSSARY.md#mcp-mcp-server-mcp-tool) (the standard way to give these CLIs extra tools), talking to the CLI over its standard input and output. |
| `hive hook EVENT [--vendor V]` | The CLIs' [hooks](GLOSSARY.md#hook), automatic checks at fixed moments: they deliver messages and refuse edits the agent isn't allowed to make. |
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

**An agent's token was lost.** Ask the server for a new one (*rotate* it; the old one stops
working), then re-join with the new one. There's no `hive` command for this yet, so the first
line calls the server's web API directly with `curl`, using your admin token; it prints the
new token:
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

**End a run and keep its history.** Dashboard → ⋯ menu → *Archives…* → *Archive & clear…*
([Archives](MONITORING.md#archives)), then `hive start "<next goal>"`.

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
| `could not reach the Hive server for …` | `hive up` couldn't check with the server which agent a slot is. Check the server is up and the tokens in `~/.hive/machine.toml` are valid; `hive overview` helps. |
| `no project named 'x' on this server` | Wrong project name in the URL, or the project was archived (`hive admin project list`). |
| `no hive.toml found (run hive init)` | Run the command inside a repository prepared with `hive init`. |

For anything else, `hive overview` lists problems with their fixes, and [Troubleshooting](TROUBLESHOOTING.md) covers the rest.

---

## 15. Command summary

Every command and option in one place. The sections above explain each one with examples.
The commands the agent CLIs run are in [§12](#12-commands-the-agent-clis-run).

### User / admin

| Command | Description |
|---|---|
| `hive server [--host H] [--port P] [--db URL] [--sqlite-journal wal\|delete\|truncate] [--admin-token T]` | Run the server (development). |
| `hive admin backup [-o FILE]` | Download a consistent copy of the project's SQLite database. |
| `hive admin login --server URL --token T` | Save admin credentials to `~/.hive/admin.toml`. `URL` may be a project's (`…/p/NAME`), with that project's admin token. |
| `hive admin project create NAME [--name TEXT] [--db-url URL] [--github-token-env VAR]` | Add a project (server admin only); prints its URL and admin token ([§10](#10-several-projects-on-one-server)). |
| `hive admin project list` | List the server's projects. |
| `hive admin project set NAME [--name TEXT] [--github-token-env VAR] [--archive\|--unarchive]` | Rename, archive or change a project's GitHub token variable. |
| `hive admin project rotate NAME` | Replace a project's admin token. |
| `hive admin enroll NAME --vendor claude\|gemini\|codex\|agy --roles r1,r2` | Create an agent; prints its token and a `hive join` line. |
| `hive admin roles NAME r1,r2` | Change an agent's roles. |
| `hive admin push-config` | Upload `hive.toml` from the current repository. |
| `hive init [PATH] [--server URL] [--main BRANCH] [--hive-cmd CMD]` | Prepare a repository ([Preparing the repository](REPOSITORY.md)). |
| `hive overview [PATH] [--offline]` | Describe this project's Hive setup: repository, configuration, permissions, roles, prompts, this machine's slots, the fleet, and problems with their fixes ([Preparing the repository](REPOSITORY.md)). |
| `hive prompts [--role R1,R2] [--slot NAME] [--vendor claude\|gemini\|codex\|agy] [--full] [--path DIR]` | Show what Hive tells an agent with those roles, including your additions ([Customising what agents are told](PROMPTS.md)). |
| `hive start "GOAL"` | Start the fleet; uploads `hive.toml` if run in the repo. |
| `hive status [--all] [-n N] [-v]` | Fleet state, waiting decision, agents, tasks (`--all` includes finished ones), leases, last N status entries (`-v` with bodies). |
| `hive decisions [--all]` | Open and queued decisions with context (`--all`: also answered ones). |
| `hive decide ID ANSWER [--note TEXT]` | Answer a decision. |
| `hive say "TEXT"` | Message the orchestrator in `#user`. |
| `hive watch [AGENT] [-n N] [--no-follow]` | Follow live output ([Following live output in the terminal (`hive watch`)](MONITORING.md#following-live-output-in-the-terminal-hive-watch)). |
| `hive pause` / `hive resume` / `hive reset` | Fleet control ([Pausing, resuming, resetting](LIFECYCLE.md#pausing-resuming-resetting)). |

Every admin command takes `--project NAME` (anywhere on the line; also `HIVE_PROJECT`) to act
on that project; inside a repository whose `hive.toml` names a project, that project is the
default ([§10](#10-several-projects-on-one-server)).

### Machine

| Command | Description |
|---|---|
| `hive join NAME --vendor V --token T [--server URL] [--repo PATH] [--worktree PATH] [--config-dir PATH] [--permission-mode MODE] [--model MODEL] [--hive-cmd CMD] [--replace]` | Add a slot to this machine ([Join each slot on the machine that will run it](MACHINES.md#join-each-slot-on-the-machine-that-will-run-it)). |
| `hive up [-v] [--headless]` | Run this machine's slots ([Running the fleet](RUNNING.md)). |
| `hive chat SLOT [--fresh]` | Attach interactively to a slot ([Taking over a slot interactively (`hive chat`)](RUNNING.md#taking-over-a-slot-interactively-hive-chat)). |

---

## 16. Coloured output


Everything Hive prints for a person is coloured and laid out the same way on Windows, macOS
and Linux:

- **Message markers:** `√` done, `!` warning, `×` error, `»` step, `•` info, `→` note.
- **Sections:** `── Title ──` headings and aligned tables.
- **Colour by meaning:** states are coloured by what they mean (green running/online/done,
  yellow waiting or needing you, red frozen/failed/offline). Each agent name keeps its own
  colour everywhere.
- **Agent output:** in `hive up` and `hive watch`, tool calls, results, errors and session
  notes each get their own colour.

Hive's colours (standard terminal colour codes, called ANSI codes) work in Windows Terminal,
PowerShell and cmd.exe on Windows 10 and later, where Hive switches on the console's support
for them itself, and in any macOS or Linux terminal. If a console font can't show a symbol, or
the output's character encoding can't represent it, Hive uses plain ASCII characters
instead.

Colour is used only when the output is a terminal. Output sent to another program (a pipe),
a file, a git hook or an agent's CLI is plain text, and what Hive's tools and hooks send to
the agents is never coloured. To force colour on or off,
these settings are checked in order and the first one set wins:

1. `hive --color always|never|auto ...` (before the subcommand);
2. `HIVE_COLOR=always|never`;
3. `NO_COLOR=1` (off);
4. `FORCE_COLOR=1` (on).

The terminal window Git Bash opens by default (called mintty) doesn't present itself to
Windows programs as a console, so set `HIVE_COLOR=always` there. The installers (`install.bat`, `install.sh`) are coloured
too and respect `NO_COLOR`; `install.sh` uses no colour when its output isn't a terminal.

---

[← Back to the README](../README.md)
