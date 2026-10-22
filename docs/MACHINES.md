# Adding agents and setting up machines

An **agent** is one AI coding assistant in your Hive fleet: Claude Code, Codex, Antigravity
CLI (`agy`) or Gemini CLI, logged in to its own account. It exists in two places:
- on the [server](SERVER.md), as a name with [roles](ROLES.md) (what it's for) and a
  **token** (its password for talking to the server);
- on a **machine** (any computer that runs agents), as a **slot**: its own
  [git worktree](GLOSSARY.md#worktree) (a separate working folder of your repository, on its
  own branch) and its own private CLI settings folder, where its account is logged in.

Hive sets up both for you. You need to have [started a server](SERVER.md) (or logged in to
one) and [prepared the repository](REPOSITORY.md) with `hive init` first.

A fleet needs at least **one orchestrator** (the agent that plans and that you chat with;
it never writes code) and **one programmer** (an agent that writes code) to start
([Roles](ROLES.md)). The orchestrator usually runs on your own computer, because you chat
with it in a terminal.

There are three ways to add agents, from simplest to most manual:

| Where the agent runs | How |
|---|---|
| This computer | `hive agents add claude` |
| Another computer, paired once with a code | `hive agents add claude --on desk`, from your computer |
| Another computer, without pairing | `hive agents add claude --elsewhere`, then the printed `hive join` line there |

---

## On this computer

```sh
cd ~/code/myapp
hive agents add claude                    # claude-1: the fleet's first agent is the orchestrator
hive agents add claude programmer         # claude-2
hive agents add 2 codex programmers       # codex-1, codex-2
hive agents add gemini reviewer,planner
```

For each agent, Hive:
1. **creates it on the server**: named `<cli>-<n>` unless you pass `--name`; without roles,
   the fleet's first agent is its orchestrator and later ones are programmers;
2. **creates its git worktree** next to your clone (`~/code/myapp-claude-2`) on a new branch
   `hive/claude-2`, starting from `origin/main` (the latest `main` on the shared copy of the
   repository);
3. **installs a git `pre-commit` hook** in the clone: a script git runs before every commit.
   Your own commits pass straight through (it exits immediately outside agent sessions); in
   agent sessions it refuses commits of files the agent hasn't leased (see
   [Leases](INTEGRATION.md#leases)). A foreign `pre-commit` hook is never overwritten; Hive
   tells you which line to add;
4. **creates its private CLI settings folder**, `~/.hive/accounts/claude-2`, where its
   account login will live, separate from your own;
5. **saves the slot** to `~/.hive/machine.toml`, this computer's list of slots
   ([`machine.toml`](CONFIGURATION.md#hivemachinetoml));
6. in a terminal, **offers to log its account in** (below).

Why separate worktrees? Each agent edits, commits and rebases (replays its commits on top of
the newest `main`) in its own folder on its own branch, so agents never see each other's
half-finished changes, and your own clone stays yours. Agents can't edit files in another
slot's worktree or in your clone ([Leases](INTEGRATION.md#leases)).

Options: `--name NAME`, `--model MODEL` (the AI model its CLI uses; default: the account's),
`--permission-mode MODE` (below), `--no-login`. Change them later with
`hive agents edit NAME --roles … --model … --permission-mode …`.

---

## On another computer: pair it once

Pairing makes a computer part of the project. From then on you place agents on it from your
own computer, and it sets them up by itself. You never copy a token.

**1. On your computer**, get a one-time code:

```sh
hive machines invite
# √ Pairing code K7Q4-M2XD (works once, for 15 minutes)
# ── On the other computer, inside its clone of the repository, run ──
#   hive join K7Q4-M2XD --server http://192.168.1.20:8765
```

If your server only listens on your own computer (`hive server` without `--host`), Hive
says so: other computers need it started as `hive server --host 0.0.0.0` (and its port
allowed through the firewall), or a [server online](DEPLOYMENT.md). The printed line then
uses your computer's network address.

**2. On the other computer**, clone the repository (`git clone …`), install Hive and the
agent CLIs, then, inside the clone, run the printed line:

```sh
cd ~/code/myapp
hive join K7Q4-M2XD --server http://192.168.1.20:8765
# √ This computer is now desk in http://192.168.1.20:8765.
hive up
```

It is named after its host name; choose another with `--as NAME`. `hive up` runs its agents
and keeps asking the server (every 30 seconds) for agents placed on it, so it can stay
running while you add more.

**3. On your computer**, place agents on it:

```sh
hive agents add claude programmer --on desk
hive agents add 2 codex programmers --on desk
hive machines                      # paired computers, their CLIs, and their agents
```

Within a minute the other computer's daemon **claims** each agent: the server gives it a
fresh token (which only that computer ever sees), and it sets up the slot as above, next to
its clone. Log each agent's account in once, on that computer: `hive agents login claude-3`
(`hive up` there offers it too).

- **Move** an agent: `hive agents edit claude-3 --on laptop`. The old computer takes it off
  (unless it's in the middle of a turn: then on its next check); the new one claims it, which
  replaces the token, so the old slot stops working anyway.
- **Unpair** a computer: `hive machines rm desk`. Its token stops working, and so do the
  tokens of the agents placed on it, so it is cut off entirely. The agents stay on the
  server, unplaced: place them elsewhere (`hive agents edit NAME --on …`) and they get new
  tokens there.
- **Refuse placements** on a computer: set `accept_placements = "never"` in its
  `~/.hive/machine.toml`.

A pairing code is a password that works once for 15 minutes: share it like one. A paired
computer's token (`hm_…`, in its `machine.toml`) can only ask which agents are placed on it
and claim them: it can't act as an agent or as you ([Security](SECURITY.md)).

---

## On another computer, without pairing

For a one-off agent, or a computer you'd rather not pair:

```sh
hive agents add agy tester --elsewhere
# √ Created agy-1 (agy, tester). On the computer that will run it, inside its clone, run:
#     hive join agy-1 --token hv_… --server https://hive.example.com
```

Run that line on the other computer, inside its clone. `hive join` checks the token, asks
the server which CLI the agent runs, and sets up the slot as above. The token is shown only
once. `hive agents rotate agy-1` makes a new one (and prints a new line) if it's lost.

The precise forms of these two steps still work, for scripts:

```sh
hive admin enroll codex-1 --vendor codex --roles programmer
hive join codex-1 --vendor codex --token <token> --server <url> --repo /path/to/clone
```

| `hive join` option | Meaning |
|---|---|
| `--repo PATH` | Local clone; the worktree is created next to it (default: the clone you're in). |
| `--worktree PATH` | Explicit worktree path (use with or instead of `--repo`). |
| `--vendor V` | Only a check: the server already knows which CLI the agent runs. |
| `--config-dir PATH` | The account's CLI config folder (default `~/.hive/accounts/<name>`). |
| `--permission-mode MODE` | Each CLI's own setting for how much it may do without asking (below). |
| `--model MODEL` | The AI model the slot's CLI uses; the names are the vendor's own. |
| `--server URL` | The Hive server's address, or a [project](PROJECTS.md)'s (`https://host/p/<project>`), saved for this slot only. Default: the slot's current one if re-joining, else this computer's default. |
| `--replace` | Move an existing slot of the same name to another server or project. What the slot remembers locally (which events it has seen, its cached messages, its CLI conversation) is reset. Without it, `hive join` refuses, so another project's agent of the same name can't silently take over the slot. |
| `--hive-cmd CMD` | How the git hook calls `hive` (default `hive`; use a full path if `hive` isn't on [PATH](GLOSSARY.md#path)). |

**Re-joining:** if a branch `hive/<name>` already exists with commits that aren't in `main`,
Hive refuses rather than resetting it. Integrate those commits or delete the branch first.

### Permission modes

`--permission-mode` is each CLI's own setting for how much it may do without asking. Claude:
`acceptEdits` (default for workers: file edits allowed, other actions follow Hive's rules),
`bypassPermissions` (everything allowed), …; Gemini: `auto_edit` (default, the same idea),
`yolo` (everything allowed); Codex: `workspace-write` (default for workers: may write only
inside its worktree, in Codex's sandbox), `read-only`, `danger-full-access`, `yolo`; agy:
`accept-edits` (default for workers), `auto`, `plan`. See the
[Security model](SECURITY.md#permissions).

Rather than `bypassPermissions` or `yolo`, allow the extra commands an agent needs in the
`[permissions]` section of `hive.toml`. Those modes let the agent run any command on the
computer and skip the step where the orchestrator approves dangerous commands
([Shell-command permissions](PERMISSIONS.md)).

---

## Log each agent's account in once

Each agent uses its own settings folder, so log the matching account into it once:

```sh
hive agents login claude-2
hive agents login codex-1 --device     # Codex on a computer without a browser (e.g. over SSH)
```

This opens the agent's CLI in its worktree, pointed at the agent's private settings folder
and without Hive attached; log in, then exit it. It works the same in bash, PowerShell and
cmd. `hive agents add`, `hive join` and `hive up` offer it when an agent is new. While you're
logged in, the daemon doesn't start that agent in the background, and an agent paused
because it wasn't logged in is retried when you exit.

What it does underneath, if you'd rather do it by hand: each CLI reads an environment
variable naming its settings folder (`CLAUDE_CONFIG_DIR` for Claude Code, `GEMINI_CLI_HOME`
for Gemini CLI, `CODEX_HOME` for Codex), for example
`CLAUDE_CONFIG_DIR=~/.hive/accounts/claude-2 claude`.

**agy (Antigravity CLI)** is different: it keeps its Google login in the computer's keyring
(Windows Credential Manager, macOS Keychain), not in its settings folder. Log in once on that
computer; every agy agent there then uses that account and shares its usage limits. To run
agy agents on several accounts, put them on different computers (or different user accounts
of one computer). Each agy slot still gets its own folder for its settings, Hive's plugin and
its conversations; Hive passes that folder to agy as its home directory
([how](RUNNING.md#agy-home-folder)).

Use a different account per agent. That is the point of separate folders. `hive agents`
shows which agents on this computer are logged in, and `hive doctor`
([Checking a project's setup](REPOSITORY.md#checking-a-projects-setup-hive-overview))
reports those that have never been.

---

## A typical layout

| Machine | Agents | How they got there |
|---|---|---|
| Your laptop | `claude-1` (orchestrator) | `hive agents add claude`; you chat with it where `hive up` runs |
| Desktop | `claude-2`, `claude-3`, `codex-1` | paired as `desk`; `hive agents add … --on desk` |
| Spare machine | `gemini-1` (reviewer, planner) | paired as `spare` |

Any split works, including everything on one computer. Machines can run Windows, macOS or
Linux in any mix. The [sample setup diagram](ARCHITECTURE.md#a-sample-setup) shows a
three-machine fleet like this one, with what runs on each computer and how they connect.

---

**Next:** [Running the fleet](RUNNING.md) ·
**See also:** [Roles](ROLES.md), [CLI.md §7](CLI.md#7-adding-agents) ·
[← Back to the README](../README.md)
