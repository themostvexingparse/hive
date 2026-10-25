# Using the `hive` command

`hive` is the one command for everything in Hive: running the server, setting up a
repository, adding agents, running the computers that host them, and steering the **fleet**
(the team of AI agents working on your repository). This guide shows each command with
examples. [§16](#16-command-summary) is the one-table summary of every command and option;
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
12. [Scripts and extensions](#12-scripts-and-extensions)
13. [Commands the agent CLIs run](#13-commands-the-agent-clis-run)
14. [Common recipes](#14-common-recipes)
15. [Error messages and what to do](#15-error-messages-and-what-to-do)
16. [Command summary](#16-command-summary)
17. [Coloured output](#17-coloured-output)

---

## 1. Where each command runs

**Not sure what to run?** `hive` on its own looks at your setup and prints the one next
step. `hive help` lists the common commands, `hive help --all` every one, and
`hive help TOPIC` explains one thing in detail: a command (`hive help agents add`, with its
options) or a subject (`hive help hooks`, `hive help permissions`, `hive help leases`);
`hive help topics` lists them all. The design behind
this interface is in [CLI_REDESIGN.md](CLI_REDESIGN.md).

| Where | Who | Commands |
|---|---|---|
| The server | the host (you) | `hive server` (for trials; online, the server runs as a Python web app on a web host instead, see [DEPLOYMENT.md](DEPLOYMENT.md)) |
| Your computer | you, as admin (the person controlling the fleet) | `hive login`, `hive init`, `hive agents …`, `hive config …`, `hive doctor`, `hive prompts`, `hive start`, `hive status`, `hive decisions`, `hive approve`, `hive reject`, `hive answer`, `hive say`, `hive watch`, `hive pause`, `hive resume`, `hive reset`, `hive admin …` |
| Each computer that runs agents | you, once per agent | `hive agents add` (or `hive join` for an agent created elsewhere), `hive agents login`, `hive up`, `hive chat` |
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
# 1. Start a server (keep this terminal open). It makes an admin token and logs you in.
hive server

# 2. In another terminal: prepare the repository, commit, push
cd ~/code/myapp
hive init
git add hive.toml .hive .gitignore && git commit -m "Set up Hive" && git push

# 3. Add two agents to this computer; each offers to log its CLI account in
hive agents add claude                # claude-1: the first agent is the orchestrator
hive agents add claude programmer     # claude-2

# 4. Run the machine: the orchestrator opens in this terminal
hive up

# 5. In another terminal: give the fleet a goal, then approve the plan
hive start "Add CSV export to the reports page"
hive decisions          # shows the plan
hive approve
```

From here, follow along with `hive status` and `hive watch`, and answer decisions (questions
the orchestrator asks you) as they come. The same setup with the precise, older commands
(`hive admin enroll`, `hive join` with every flag) still works; §7 shows both.

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
([DEPLOYMENT.md](DEPLOYMENT.md)). The admin token is the password for the whole fleet. Without
`--admin-token` or `$HIVE_ADMIN_TOKEN`, `hive server` uses the one it saved in
`~/.hive/server.toml`, making one up the first time. When the server listens on this
computer, it also logs this computer in as admin (§5), so admin commands work straight
away. `$HIVE_ADMIN_TOKEN` and `$TOKEN` below are shell variables holding a token.

```sh
# Local trial: SQLite file in the current folder, port 8765, token made and saved for you
hive server

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
| `--admin-token` | `HIVE_ADMIN_TOKEN`, else `~/.hive/server.toml` | Made up and saved there if none is given. |

On start it prints the address, the dashboard URL, the database and the projects it serves.

---

## 5. Logging in as admin

```sh
hive login [--server URL] [--token TOKEN]
```

Every admin command needs a login. `hive server` logs in the computer it runs on by itself;
log in to any other server with `hive login`. It checks the admin token against the server
and saves it in `~/.hive/admin.toml` (readable only by you). In a terminal it asks for the
server (default: the one `hive.toml` names) and for the token, without showing what you
type; in scripts, pass both. `hive admin login` is the same command. On a server that hosts
several [projects](PROJECTS.md), each project has its own address (`…/p/<project>`) and its
own admin token, besides the server's own.

```sh
# The server's admin token: admin of the whole server, every project
hive login --server https://hive.example.com
# ? Admin token for https://hive.example.com (hidden):
# → Logged in to https://hive.example.com as the server's admin (all projects).

# A single project's admin token (§10); acme is an example project
hive login --server https://hive.example.com/p/acme --token <acme's admin token>
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
([Preparing the repository](REPOSITORY.md)). It also adds `hive.local.toml` and the
orchestrator's private memory to `.gitignore`. The main branch defaults to `origin`'s default
branch. It prints what to do next.

```sh
cd ~/code/myapp
hive init                                         # server from your admin login
hive init --main develop                          # finished work goes into `develop`
hive init --server https://hive.example.com/p/acme   # a project on a shared server (§10)
hive init ../other-repo                           # another folder
hive init --hive-cmd /opt/hive/bin/hive           # agents' tools must call this exact `hive`
```

Commit and push what it wrote (`hive.toml`, `.hive/`), so every agent's copy of the
repository gets it.

### Uploading `hive.toml` (automatic)

The server keeps its own copy of `hive.toml`, because it enforces the [roles](ROLES.md), the
[shell-command permissions](PERMISSIONS.md) and the [review](INTEGRATION.md#reviews) setting
in it. You don't have to upload it: every admin command run inside your clone uploads
`hive.toml` when it changed since this computer last sent it, and says so in one line.
It goes only to the server (or project) `hive.toml` names in `[server] url`: a command aimed
elsewhere, with `--project`, `$HIVE_PROJECT` or another default login, leaves that server's
copy alone. (Commands run inside an agent's worktree never upload, since it may hold an older
`main`.) `hive admin push-config` uploads it explicitly, and `hive start` always does.

### `hive config`: read and change settings

```sh
hive config                                       # the settings that aren't at their default
hive config list --all                            # every setting, and where it comes from
hive config get integration.review
hive config set integration.review reviewer       # in hive.toml: the whole fleet
hive config set integration.checks "npm test"     # a list setting given one value
hive config set integration.checks '["npm test", "npm run lint"]'
hive config set integration.shell powershell --local   # in hive.local.toml: this computer only
hive config unset integration.shell --local       # back to hive.toml's value
hive config explain integration.shell             # its value in every layer, and which wins
hive config edit                                  # open hive.toml in $EDITOR
hive config set fleet.preset strict               # start from a bundle of settings
hive config set sessions.max_wakes 20 --user      # your default, in every repository here
hive config set hooks.on_decision "notify-send Hive \"\$HIVE_SUMMARY\"" --local
```

Settings are named `<section>.<name>`, as in the file (`roles.<role>.<name>` for
[custom roles](ROLES.md#custom-roles-and-overrides)). They come from five layers, each
overriding the one before:

| Layer | File | Applies to |
|---|---|---|
| `default` | built into Hive | everything not set below |
| `preset` | the bundle `fleet.preset` in `hive.toml` names | the whole fleet (`hive.toml`'s own settings override it) |
| `user` | `~/.hive/config.toml` (`--user`) | you, in every repository on this computer; never uploaded |
| `hive.toml` | committed in the repository | the whole fleet; uploaded to the server |
| `hive.local.toml` | next to `hive.toml` in your clone, git-ignored (`--local`) | this computer only: its daemon and its agents' worktrees (read from the clone each agent was set up from); never uploaded, and ignored if it is ever committed |

**Presets** (`fleet.preset`): `custom` (the default: nothing extra), `solo` (no reviews),
`team` (reviews by an agent with the `reviewer` role), `strict` (reviews, and the `strict`
[permission preset](PERMISSIONS.md)). The server gets `hive.toml` over its preset.

**Hooks** (`hooks.*`) are shell commands this computer's daemon (`hive up`) runs when the
fleet does something: `on_decision` (a decision waits for you), `on_task_done`, `on_main`
(`main` moved), `on_frozen`, `on_state` (the fleet's state changed) and `on_message` (an
agent wrote to you in `#user`). Each gets `HIVE_EVENT`, `HIVE_SUMMARY` (one line for a
person) and `HIVE_EVENT_JSON` in its environment, and is stopped after 2 minutes. They are
read only from `hive.local.toml` or `~/.hive/config.toml`, never from the committed
`hive.toml`, so a repository can't run commands on every computer; `hive config set hooks.…`
without `--local` or `--user` is refused, and `hive doctor` flags any in `hive.toml`. For the
same reason, a `hive.local.toml` that git tracks (committed with `git add -f`) is ignored,
and `hive doctor` says so; and an agent's worktree reads the `hive.local.toml` of the clone
recorded in `machine.toml` when the agent was set up, never one its own `.git` file points
to.

`set` and `unset` edit the file in place, keeping its comments, check that the result is a
valid configuration (otherwise nothing changes), and upload a changed `hive.toml`. Values
are TOML (`true`, `90`, `["a", "b"]`); anything else is text. After changing `hive.toml`,
commit and push it so every agent's copy has it; each agent's next launch builds its CLI's
permission files from it. The committed copy `hive init` writes to `.hive/agent/` is built
from `hive.toml` and its preset only, never from your own or this computer's settings.

### `hive doctor`: check the whole setup

```sh
hive doctor                # this repository
hive doctor ../myapp       # another one
hive doctor --offline      # don't contact the server, or the local daemon (the program hive up runs)
```

`hive overview` is the same command, under its older name.

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

An agent exists on the server (its name, CLI, roles and token) and, on the computer that
runs it, as a **slot** (its worktree and CLI account folder; [Setting up machines](MACHINES.md)).
`hive agents add` does both in one go; `hive admin enroll` and `hive join` do one step each.

### `hive agents add`: create agents and set them up here

```sh
hive agents add [N] CLI [ROLES] [--name NAME] [--elsewhere] [--model M] [--permission-mode P] [--no-login]
```

```sh
cd ~/code/myapp
hive agents add claude                       # claude-1; the fleet's first agent is its orchestrator
hive agents add claude programmer            # claude-2
hive agents add 3 codex programmers          # codex-1, codex-2, codex-3
hive agents add gemini reviewer,planner --name gemini-review
hive agents add agy tester --elsewhere       # runs on another computer: prints the line to run there
```

Run it inside your clone. The count, the CLI (`claude`, `codex`, `gemini`, `agy`) and the
roles can come in any order; plural role names work. Names default to `<cli>-<n>`. Without
roles, the fleet's first agent becomes its orchestrator and later ones programmers. For each
agent it creates the agent on the server, creates its worktree next to your clone (as
`hive join` does), and, in a terminal, offers to log its account in. If setting up the slot
fails, the agent is removed from the server again, so nothing is left half-made. Tokens of
agents set up here are never printed.

With `--elsewhere`, nothing is set up here. Instead it prints one line per agent, such as
`hive join agy-1 --token hv_… --server https://hive.example.com`, to run on the other
computer inside its clone. The token in it is shown only once.

### `hive agents`: list, log in, change, remove

```sh
hive agents                                  # every agent: CLI, roles, state, where, logged in
hive agents login claude-2                   # log its CLI account in (once per agent)
hive agents login codex-1 --device           # Codex over SSH: log in with a code on another device
hive agents edit claude-2 --roles programmer,tester
hive agents edit codex-1 --model <model> --permission-mode read-only   # this computer's slot
hive agents edit codex-1 --model default     # back to the account's default model
hive agents rm claude-3                      # asks first; --yes in scripts
hive agents rotate claude-2                  # new token; a slot here is updated automatically
```

- `login` opens the agent's CLI in its worktree with its private settings folder, without
  Hive attached, for you to log in and exit. It works the same in every shell. (agy keeps its
  login in the computer's keyring, so it's once per computer.) While you're logged in, the
  daemon doesn't start that agent in the background.
- `edit --roles` replaces the roles (as `hive admin roles`); `--model` and
  `--permission-mode` change this computer's slot and apply from the agent's next turn.
- `rm` removes the agent from the server: its token stops working, its unfinished tasks go
  back on the board (work waiting for review included, which starts again), reviews it was
  picked for go to another reviewer, its leases are revoked and its pending command approvals
  cancelled, and the orchestrator is told. Its slot on this computer goes too; the
  worktree is kept if it has uncommitted changes, and the account folder is kept so adding it
  back needs no new login. An agent that is the orchestrator of a run in progress can't be
  removed: give another agent the orchestrator role first.
- `rotate` replaces the token. For an agent set up on another computer it prints the
  `hive join` line to run there.

### `hive machines`: pair other computers, then place agents on them

```sh
# On your computer
hive machines invite                     # a one-time code, and the line to run on the other computer
hive machines                            # paired computers: state, CLIs, agents
hive agents add claude programmer --on desk
hive agents edit claude-3 --on laptop    # move it (`--on none`: off every paired computer)
hive machines rm desk                    # unpair (its agents stay, unplaced)

# On the other computer, inside its clone
hive join K7Q4-M2XD --server http://192.168.1.20:8765 [--as desk]
hive up
```

A code works once, for 15 minutes. `hive join CODE` (no `--token`) pairs the computer: it is
named after its host name unless you pass `--as`, and remembers the clone you ran it in.
From then on its daemon (`hive up`) asks the server every 30 seconds which agents are placed
on it, claims new ones (the server gives that computer a fresh token for each), sets up their
slots next to the clone, and takes off those removed or placed elsewhere, without a restart.
`hive up` and `hive join` offer to log new agents in. When your server listens only on your
own computer (`127.0.0.1`), `invite` says how to make it reachable and prints your network
address instead. Full walkthrough: [Setting up machines](MACHINES.md#on-another-computer-pair-it-once).

**Join keys, for scripts** that set up many computers:

```sh
hive machines invite --reusable --days 7 --label ci   # prints hk_…, shown only once
hive machines keys                                    # keys that still work, and how often used
hive machines revoke 3f9a1c2b07                       # stop it pairing more (paired ones stay)
# then on each computer, inside its clone:
hive join hk_… --server https://hive.example.com --as build-07
```

A join key pairs any number of computers until it expires or is revoked; keep it like a
password.

### `hive plan` and `hive apply`: the fleet as a file

For a fleet you want to keep the same, or rebuild elsewhere, describe it in
`hive.fleet.toml` (next to `hive.toml`; any other file can be named on the command line):

```toml
[[agents]]
name = "orch"
vendor = "claude"
role = "orchestrator"            # leave `on` out: the computer you run hive apply on

[[agents]]
vendor = "claude"
roles = ["programmer", "tester"]
count = 3                        # claude-1, claude-2, claude-3
on = "desk"                      # a paired computer (hive machines)
model = "<model>"                # optional, as `hive agents edit` sets them
permission_mode = "acceptEdits"

[[agents]]
name = "rev"
vendor = "gemini"
role = "reviewer"
on = "elsewhere"                 # exists; Hive leaves where it runs alone
```

```sh
hive agents export > hive.fleet.toml     # start from the fleet as it is
hive plan                                # what applying the file would change
hive apply                               # make those changes (asks first; --yes in scripts)
hive apply --prune                       # also remove agents the file doesn't list
```

Without `name`, agents are numbered `<vendor>-<n>` through the file in order; with a `name`
and a `count`, `<name>-1`, `<name>-2`, …. `plan` lists each change: agents created, roles
changed, agents moved (between paired computers and this one), slot settings changed, and,
with `--prune`, agents removed. Agents the file doesn't list are kept unless you add
`--prune`. An agent's CLI can't change: rename one in the file to replace it. The server
stays the source of truth: `hive agents add` and the rest keep working beside the file, and
`hive plan` shows any difference they made.

### `hive admin enroll`: create an agent (precise form)

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

### `hive join`: set up an agent created elsewhere

```sh
hive join NAME --token T [--server URL] [--vendor V] [--repo PATH | --worktree PATH]
          [--config-dir PATH] [--permission-mode MODE] [--model MODEL] [--hive-cmd CMD] [--replace]
```

```sh
# The usual form, inside your clone: worktree created next to it, as ~/code/myapp-claude-2
cd ~/code/myapp
hive join claude-2 --token hv_… --server https://hive.example.com

# The same from anywhere
hive join claude-2 --token hv_… --server https://hive.example.com --repo ~/code/myapp

# Choose the worktree's location
hive join claude-2 --vendor claude --token hv_… --repo ~/code/myapp --worktree /fast-disk/myapp-c2

# A Codex agent on a specific AI model, allowed to write only inside its own folder
hive join codex-1 --vendor codex --token hv_… --repo ~/code/myapp \
                  --model <model-name> --permission-mode workspace-write

# Re-join with a new token (after losing or replacing the old one): server and worktree are kept
hive join claude-2 --vendor claude --token hv_new… --repo ~/code/myapp
```

What it does: checks the token, asks the server which CLI the agent runs (`--vendor` is
only needed if it can't say, and is refused if it contradicts it), creates the agent's
working folder (a git worktree, next to your clone, by default the one you're in) on branch
`hive/NAME`, installs a git `pre-commit` hook (a check git runs before each commit, refusing
agents' commits of files they haven't locked), creates the agent's private CLI settings
folder, and saves the slot to this computer's list, `~/.hive/machine.toml`. At the end, in
a terminal, it offers to **log the agent's CLI account in**. Otherwise it prints
`hive agents login NAME`, which does that later.

| Option | Meaning |
|---|---|
| `--server URL` | The server, or a project's URL (`…/p/NAME`). Saved for **this slot only**. Default: the slot's current one when re-joining, else the machine's default (set by the first join). |
| `--repo PATH` | Your clone; the worktree goes next to it. |
| `--worktree PATH` | Explicit worktree path. |
| `--config-dir PATH` | The CLI account folder (default `~/.hive/accounts/NAME`). |
| `--permission-mode MODE` | The CLI's own setting for how much it may do without asking. Claude: `acceptEdits` (default), `bypassPermissions`, …; Gemini: `auto_edit` (default), `yolo`; Codex: `workspace-write` (default), `read-only`, `danger-full-access`, `yolo`; agy: `accept-edits` (default), `auto`, `plan`. The defaults allow file edits; the others allow less or everything ([details](MACHINES.md#on-another-computer-without-pairing)). |
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

### `hive`: the chat with the team

Once the team exists (and, for agents on this computer, `hive up` runs), `hive` in a terminal
opens the chat. `hive chat` opens it too; `hive next` shows the next-step screen instead.

```
> Add CSV export to the reports page
? Start the team on this goal? [Y/n]
• the fleet is planning
claude-1: I'll split this into the exporter and the button; one question first…

┌ Decision #1 · start_approval · freezes the fleet
│ Approve the 2-task plan?
│ …
└ y = approve · n = send back with a note
> y
√ Decision #1 answered: approve
> /agents add codex reviewer
```

- **Messages:** a line goes to the orchestrator in `#user` (in setup, it becomes the goal once
  you confirm). Its replies, status updates and the fleet's state appear as they happen.
- **Decisions** appear as prompts: `y` approves, `n` asks what to change and sends the plan
  back; other questions take `/answer …`. Hive answers them on your keypress, never the
  AI.
- **Slash commands are the `hive` commands:** `/agents add codex` runs `hive agents add codex`,
  `/status`, `/plan`, `/config …` and so on; `/watch AGENT` follows an agent's live output in
  the chat (`/watch off` stops); `/help`; `/quit` leaves (the team keeps working).
  Commands that take over a terminal (`server`, `up`, `join`) run in their own.

It works wherever the orchestrator runs: headless under `hive up --headless`, or in its own
terminal under `hive up`.

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

### `hive decisions`, `hive approve`, `hive reject`, `hive answer`

```sh
hive decisions          # open and queued decisions, each with its answer commands
hive decisions --all    # also the ones already answered
```

```sh
hive approve                                            # the plan, or a yes/no question
hive approve "go ahead, but keep the old endpoint"     # with a note
hive reject "Split the migration into its own task first"
hive answer "Use PostgreSQL; we already run it"         # any other question
hive approve 3                                          # when several are open: its number
```

With one decision waiting, no number is needed. With several, add the number `hive decisions`
shows, or, in a terminal, pick one from the list. `approve` and `reject` refuse a decision
that isn't a yes/no question and show how to answer it. `hive reject` without a note asks for
one in a terminal. `hive decisions` prints the ready answer lines for each decision.

`hive decide ID ANSWER [--note TEXT]` is the precise form, for scripts:

```sh
hive decide 3 approve
hive decide 3 reject --note "Split the migration into its own task first"
hive decide 7 "Use PostgreSQL; we already run it"
```

In the orchestrator's own terminal you can answer without leaving it: `! hive approve` (in
Claude Code and Gemini CLI, `!` runs a shell command).

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

## 12. Scripts and extensions

### `hive api`: the server's API with your admin login

```sh
hive api GET /fleet
hive api GET /tasks -f state=open
hive api POST /messages -d '{"channel": "#user", "body": "Prioritise the API"}'
hive api PATCH /agents/claude-2 -d - < roles.json
```

It prints the JSON the server answers, for anything the commands don't cover (the endpoints
are in `src/hive/server/app.py`). Agents may never run it.

### Plugins: `hive NAME` runs `hive-NAME`

A program called `hive-NAME` on your PATH becomes `hive NAME …`, as with git, kubectl and
cargo. It gets the remaining arguments, plus `HIVE_HOME` and (with `--project`)
`HIVE_PROJECT`, and can call `hive api` for anything on the server. Hive's own commands
always win over a plugin of the same name. Neither plugins nor `hive api` run inside an
agent's session.

## 13. Commands the agent CLIs run

You don't run these yourself. They are listed so you recognise them in configuration files and
lists of running programs.

| Command | Run by |
|---|---|
| `hive mcp [--slot S]` | Each agent CLI, to get Hive's tools. It's an [MCP server](GLOSSARY.md#mcp-mcp-server-mcp-tool) (the standard way to give these CLIs extra tools), talking to the CLI over its standard input and output. |
| `hive hook EVENT [--vendor V]` | The CLIs' [hooks](GLOSSARY.md#hook), automatic checks at fixed moments: they deliver messages and refuse edits the agent isn't allowed to make. |
| `hive hook git-pre-commit` | The git pre-commit hook installed by `hive join`. |

---

## 14. Common recipes

**Check everything after a change.**
```sh
hive doctor
```

**Move a slot to another server or project.**
```sh
hive join claude-2 --vendor claude --token <new token> --server https://new.example.com --replace
```

**An agent's token was lost.** Give it a new one (the old one stops working). If the agent
is set up on this computer, its slot is updated; otherwise it prints the `hive join` line to
run where it is:
```sh
hive agents rotate claude-2
```

**Remove an agent.**
```sh
hive agents rm claude-3
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

## 15. Error messages and what to do

| Message | Meaning and fix |
|---|---|
| `not logged in to a Hive server …` | No admin login on this computer. Start a local server with `hive server` (it logs you in), or run `hive login --server URL`. |
| `can't reach the Hive server …` | The server isn't running or the address is wrong. Locally: `hive server`. `hive doctor` checks the rest. |
| `no admin token for https://…/p/NAME` | You're logged in, but not to that server or project. Log in to it, or use the server's admin token. |
| `managing projects needs the server's own admin token` | Project commands need `HIVE_ADMIN_TOKEN`, not a project's token. |
| `token belongs to 'x', not 'y'` | The token is for another agent; check the name you passed to `hive join`. |
| `this machine already has a slot named 'dev', for …` | Another server or project's slot has that name. Use another name, or `--replace` to move the slot. |
| `no slots on this machine; run hive join first` | `hive up` has nothing to run. |
| `could not reach the Hive server for …` | `hive up` couldn't check with the server which agent a slot is. Check the server is up and the tokens in `~/.hive/machine.toml` are valid; `hive doctor` helps. |
| `no project named 'x' on this server` | Wrong project name in the URL, or the project was archived (`hive admin project list`). |
| `no hive.toml found (run hive init)` | Run the command inside a repository prepared with `hive init`. |

For anything else, `hive doctor` lists problems with their fixes, and [Troubleshooting](TROUBLESHOOTING.md) covers the rest.

---

## 16. Command summary

Every command and option in one place. The sections above explain each one with examples.
The commands the agent CLIs run are in [§13](#13-commands-the-agent-clis-run).

### Everyday

| Command | Description |
|---|---|
| `hive` | Where things stand and the next step; once the team runs, the chat with it (§9). |
| `hive chat` / `hive next` | The chat; the next-step screen. |
| `hive api METHOD PATH [-d JSON] [-f K=V]` | The server's API with your admin login (§12). |
| `hive NAME …` | Runs the plugin `hive-NAME` from PATH (§12). |
| `hive machines invite --reusable [--days N] [--label L]` / `keys` / `revoke ID` | Join keys for scripts. |
| `hive config set KEY VALUE --user` | A setting of yours, in every repository on this computer. |
| `hive help [--all] [TOPIC…]` | The common commands; `--all` every command; `TOPIC` (a command or a subject) explained in detail, with a command's options; `topics` lists every topic. |
| `hive login [--server URL] [--token T]` | Log in as admin; asks for what's missing, the token without echoing it (§5). |
| `hive agents add [N] CLI [ROLES] [--name N] [--elsewhere] [--model M] [--permission-mode P] [--no-login]` | Create agents and set them up on this computer, or print the `join` line for another (§7). |
| `hive agents [list]` | Every agent: CLI, roles, state, where it runs, login. |
| `hive agents login NAME [--device]` | Log an agent's CLI account in. |
| `hive agents edit NAME [--roles R] [--model M] [--permission-mode P]` | Change an agent. |
| `hive agents rm NAME [--yes]` | Remove an agent: server and this computer. |
| `hive agents rotate NAME` | Give an agent a new token. |
| `hive agents add … --on MACHINE` / `hive agents edit NAME --on MACHINE\|none` | Place an agent on a paired computer, or move it. |
| `hive machines [list]` / `invite` / `rm NAME [--yes]` | Paired computers; a one-time pairing code; unpair one. |
| `hive plan [FILE] [--prune]` / `hive apply [FILE] [--prune] [--yes]` | Compare the fleet with `hive.fleet.toml`; make it match. |
| `hive agents export [-o FILE]` | Write the fleet as a file for `plan` and `apply`. |
| `hive join CODE --server URL [--as NAME] [--repo PATH]` | Pair this computer (on the other computer, inside its clone). |
| `hive config [list [--all]]` | Settings in force and where each comes from (§6). |
| `hive config get\|explain KEY` | One setting's value; its value in every layer. |
| `hive config set KEY VALUE [--local]` / `unset KEY [--local]` | Change a setting in `hive.toml` (or `hive.local.toml`). |
| `hive config edit [--local]` | Open the file in your editor. |
| `hive approve [ID] [NOTE]` / `hive reject [ID] [NOTE]` / `hive answer [ID] TEXT` | Answer a decision; the ID only when several are open (§9). |
| `hive doctor [PATH] [--offline]` | Check the whole setup, with fixes (`hive overview` is the same). |

### User / admin

| Command | Description |
|---|---|
| `hive server [--host H] [--port P] [--db URL] [--sqlite-journal wal\|delete\|truncate] [--admin-token T]` | Run the server (development); without a token it makes and saves one, and logs this computer in. |
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
| `hive join NAME --token T [--server URL] [--vendor V] [--repo PATH] [--worktree PATH] [--config-dir PATH] [--permission-mode MODE] [--model MODEL] [--hive-cmd CMD] [--replace]` | Set up an agent created elsewhere on this machine; vendor and clone are inferred ([Setting up machines](MACHINES.md#on-another-computer-without-pairing)). |
| `hive up [-v] [--headless]` | Run this machine's slots ([Running the fleet](RUNNING.md)). |
| `hive chat SLOT [--fresh]` | Attach interactively to a slot ([Taking over a slot interactively (`hive chat`)](RUNNING.md#taking-over-a-slot-interactively-hive-chat)). |

---

## 17. Coloured output


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
for them itself, and in any macOS or Linux terminal. Its palette is vivid 24-bit colour
(honey headings, sky-blue commands, amber placeholders, emerald success, coral errors) on
terminals that show it: Windows consoles, and terminals that set `COLORTERM=truecolor` or
are known to (VS Code, iTerm2, WezTerm, …). Elsewhere it uses the nearest of the 256
standard colours (`TERM=…-256color`), else the basic 16. Set `HIVE_COLOR_DEPTH` to
`truecolor`, `256` or `16` to choose. If a console font can't show a symbol, or
the output's character encoding can't represent it, Hive uses plain ASCII characters
instead.

Colour is used only when the output is a terminal. Output sent to another program (a pipe),
a file, a git hook or an agent's CLI is plain text, and what Hive's tools and hooks send to
the agents is never coloured. To force colour on or off,
these settings are checked in order and the first one set wins:

1. `--color always|never|auto`, anywhere on the line (`hive --color never status`, `hive status --color never`);
2. `HIVE_COLOR=always|never`;
3. `NO_COLOR=1` (off);
4. `FORCE_COLOR=1` (on).

The terminal window Git Bash opens by default (called mintty) doesn't present itself to
Windows programs as a console, so set `HIVE_COLOR=always` there. The installers (`install.bat`, `install.sh`) are coloured
too and respect `NO_COLOR`; `install.sh` uses no colour when its output isn't a terminal.

---

[← Back to the README](../README.md)
