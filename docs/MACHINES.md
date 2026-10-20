# Enrolling agents and setting up machines

An **agent** is one AI coding assistant in your Hive fleet: Claude Code, Gemini CLI or Codex,
logged in to its own account. It exists in two places:
- on the [server](SERVER.md), as a name with [roles](ROLES.md) (what it's for) and a
  **token** (its password for talking to the server);
- on a **machine** (any computer that runs agents), as a **slot**: its own
  [git worktree](GLOSSARY.md#worktree) (a separate working folder of your repository, on its
  own branch) and its own private CLI settings folder, where its account is logged in.

Setting one up takes three steps:

1. **Enroll** it on the server, from your computer (`hive admin enroll`). This creates the
   identity and prints its token.
2. **Join** it on the machine that will run it (`hive join`). This creates the slot: the
   worktree, the private CLI config folder and the git hook.
3. **Log in** its CLI account once, interactively, into that private config folder.

After that, `hive up` on the machine runs the slot ([Running the fleet](RUNNING.md)).

You need to have [started a server](SERVER.md), logged in to it as admin
(`hive admin login`), and [prepared the repository](REPOSITORY.md) with `hive init` first.

A fleet needs at least **one orchestrator** (the agent that plans and that you chat with;
it never writes code) and **one programmer** (an agent that writes code) to start
([Roles](ROLES.md)). The orchestrator usually runs on your own computer, because you chat
with it in a terminal.

**The examples on this page** create five agents: `orch` (the orchestrator), `claude-2` and
`claude-3` (Claude Code programmers), `gemini-1` (a Gemini CLI reviewer and planner) and
`codex-1` (a Codex programmer). The names are your choice.

---

## Enroll each agent (on your computer)

```sh
hive admin enroll orch     --vendor claude --roles orchestrator
hive admin enroll claude-2 --vendor claude --roles programmer
hive admin enroll claude-3 --vendor claude --roles programmer,tester
hive admin enroll gemini-1 --vendor gemini --roles reviewer,planner
hive admin enroll codex-1  --vendor codex  --roles programmer
```

- **Names** are 2 to 32 characters: lowercase letters, digits and hyphens, starting with a
  letter (`^[a-z][a-z0-9-]{1,31}$`). `user`, `system` and `admin` are reserved. Choose names
  you'll recognise in logs and the dashboard; the vendor in the name is a useful habit, not a
  requirement.
- **Vendor** is the CLI the slot runs: `claude`, `gemini` or `codex`.
- **Roles** are comma-separated. The built-in ones are `orchestrator`, `programmer`,
  `planner` (helps plan), `reviewer` (checks others' work) and `tester` ([Roles](ROLES.md)).
  `orchestrator` can't be combined with a role that writes code. You can change roles later
  with `hive admin roles`.
- Each command prints the agent's **token once**, plus a ready-made `hive join` command. The
  server stores only a hash, so copy the token now.
- **Lost token:** have the server issue a new one (the old one stops working) by sending an
  HTTP `POST` request to `/api/v1/agents/<name>/token` on the server with the admin token,
  then re-join with the new one. [CLI.md §13](CLI.md#13-common-recipes) has the exact
  `curl` command.

---

## Join each slot on the machine that will run it

```sh
hive join claude-2 --vendor claude --token <token> --server <url> --repo /path/to/clone
```

Run this on the computer that will run the agent. Use the token and server address
`hive admin enroll` printed; `/path/to/clone` is where the repository is cloned on this
computer (clone it first with `git clone …` if needed). The slot's worktree is created next
to it.

What `hive join` does:
1. **Checks** the token against the server and that it belongs to `claude-2`.
2. **Creates a git worktree** for the slot next to your clone:
   `/path/to/<clone-name>-claude-2`, on a new branch `hive/claude-2`, starting from
   `origin/main` (the latest `main` on the shared copy of the repository).
   Use `--worktree PATH` to choose the location.
   - **Re-joining:** if a branch `hive/claude-2` already exists with commits that aren't in
     `main`, `hive join` refuses rather than resetting it. Integrate those commits or delete
     the branch first.
3. **Installs a git `pre-commit` hook** in the clone: a script git runs before every commit.
   Worktrees share it.
   - **Your own commits:** the hook exits immediately outside agent sessions, so they never
     need `hive` on PATH.
   - **Agent sessions:** it refuses commits of files the agent hasn't leased (locked for
     itself first; see [Leases](INTEGRATION.md#leases)).
   - **Existing hooks:** a foreign `pre-commit` hook is never overwritten; Hive tells you
     which line to add.
4. **Creates the slot's private CLI settings folder**, by default `~/.hive/accounts/claude-2`
   (`--config-dir` to change it). The agent's account login will live here, separate from
   your own.
5. **Saves the slot** to `~/.hive/machine.toml`, this computer's list of slots
   ([`machine.toml`](CONFIGURATION.md#hivemachinetoml)).

Why separate worktrees? Each agent edits, commits and rebases (replays its commits on top of
the newest `main`) in its own folder on its own branch, so agents never see each other's
half-finished changes, and your own clone stays yours. Agents can't edit files in another slot's worktree or in your clone
([Leases](INTEGRATION.md#leases)).

| `hive join` option | Meaning |
|---|---|
| `--repo PATH` | Local clone; the worktree is created next to it. |
| `--worktree PATH` | Explicit worktree path (use with or instead of `--repo`). |
| `--config-dir PATH` | The account's CLI config folder (default `~/.hive/accounts/<name>`). |
| `--permission-mode MODE` | Each CLI's own setting for how much it may do without asking. Claude: `acceptEdits` (default for workers: file edits allowed, other actions follow Hive's rules), `bypassPermissions` (everything allowed), …; Gemini: `auto_edit` (default, the same idea), `yolo` (everything allowed); Codex: `workspace-write` (default for workers: may write only inside its worktree, in Codex's sandbox), `read-only`, `danger-full-access`, `yolo`. See the [Security model](SECURITY.md#permissions). |
| `--model MODEL` | The AI model the slot's CLI uses; the names are the vendor's own. Default: the account's default model. |
| `--server URL` | The Hive server's address, or a [project](PROJECTS.md)'s (`https://host/p/<project>`), saved for this slot only. Default: the slot's current one if re-joining, else this computer's default (set by the first `hive join`). |
| `--replace` | Move an existing slot of the same name to another server or project. What the slot remembers locally (which events it has seen, its cached messages, its CLI conversation) is reset. Without it, `hive join` refuses, so another project's agent of the same name can't silently take over the slot. |
| `--hive-cmd CMD` | How the git hook calls `hive` (default `hive`; use a full path if `hive` isn't on [PATH](GLOSSARY.md#path)). |

Rather than `bypassPermissions` or `yolo`, allow the extra commands an agent needs in the
`[permissions]` section of `hive.toml`. Those modes let the agent run any command on the
computer and skip the step where the orchestrator approves dangerous commands
([Shell-command permissions](PERMISSIONS.md)).

---

## Log each slot's account in once

Each slot uses its own settings folder, so log the matching account into it once, by
starting the CLI by hand and following its login steps. Each CLI reads an environment
variable naming its settings folder (`CLAUDE_CONFIG_DIR` for Claude Code, `GEMINI_CLI_HOME`
for Gemini CLI, `CODEX_HOME` for Codex); setting it points the CLI at the slot's folder
instead of your personal one:

**bash / zsh (macOS, Linux, Git Bash):**
```sh
cd /path/to/clone-claude-2
CLAUDE_CONFIG_DIR=~/.hive/accounts/claude-2 claude      # log in, then exit
cd /path/to/clone-gemini-1
GEMINI_CLI_HOME=~/.hive/accounts/gemini-1 gemini        # log in, then exit
cd /path/to/clone-codex-1
CODEX_HOME=~/.hive/accounts/codex-1 codex login         # on a remote computer (SSH), add --device-auth
```

**PowerShell (Windows):**
```powershell
cd C:\path\to\clone-claude-2
$env:CLAUDE_CONFIG_DIR = "$HOME\.hive\accounts\claude-2"; claude; Remove-Item Env:CLAUDE_CONFIG_DIR
cd C:\path\to\clone-gemini-1
$env:GEMINI_CLI_HOME = "$HOME\.hive\accounts\gemini-1"; gemini; Remove-Item Env:GEMINI_CLI_HOME
cd C:\path\to\clone-codex-1
$env:CODEX_HOME = "$HOME\.hive\accounts\codex-1"; codex login; Remove-Item Env:CODEX_HOME
```

**cmd.exe (Windows):** `set "CLAUDE_CONFIG_DIR=%USERPROFILE%\.hive\accounts\claude-2" && claude`.
Close the window afterwards, so the variable doesn't leak into other commands.

`hive join` prints these commands for your platform with the real paths filled in.

`--device-auth` makes Codex show a code to enter in a browser on any device, for computers
where it can't open a browser itself.

Use a different account per slot. That is the point of separate folders. `hive overview`
([Checking a project's setup](REPOSITORY.md#checking-a-projects-setup-hive-overview))
reports slots whose account has never been logged in.

---

## A typical layout

| Machine | Slots | Notes |
|---|---|---|
| Your laptop | `orch` (orchestrator) | You chat with it in the terminal where `hive up` runs. |
| Desktop | `claude-2`, `claude-3`, `codex-1` | Three accounts side by side, three worktrees. |
| Spare machine | `gemini-1` (reviewer, planner) | |

Any split works, including everything on one computer. Machines can run Windows, macOS or
Linux in any mix.

---

**Next:** [Running the fleet](RUNNING.md) ·
**See also:** [Roles](ROLES.md), [CLI.md §7](CLI.md#7-adding-agents) ·
[← Back to the README](../README.md)
