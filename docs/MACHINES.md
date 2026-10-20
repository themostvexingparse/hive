# Enrolling agents and setting up machines

An agent exists in two places: on the **server**, as an identity with roles and a token, and on
a **machine**, as a slot with its own git worktree and its own logged-in CLI account. Setting
one up takes three steps:

1. **Enroll** it on the server, from your computer (`hive admin enroll`). This creates the
   identity and prints its token.
2. **Join** it on the machine that will run it (`hive join`). This creates the slot: the
   worktree, the private CLI config folder and the git hook.
3. **Log in** its CLI account once, interactively, into that private config folder.

After that, `hive up` on the machine runs the slot ([Running the fleet](RUNNING.md)).

A fleet needs at least **one orchestrator** and **one programmer** to start
([Roles](ROLES.md)). The orchestrator usually runs on your own computer, because you chat
with it in a terminal.

---

## Enroll each agent (on your computer)

```sh
hive admin enroll orch     --vendor claude --roles orchestrator
hive admin enroll claude-2 --vendor claude --roles programmer
hive admin enroll claude-3 --vendor claude --roles programmer,tester
hive admin enroll gemini-1 --vendor gemini --roles reviewer,planner
hive admin enroll codex-1  --vendor codex  --roles programmer
```

- **Names** must match `^[a-z][a-z0-9-]{1,31}$`. `user`, `system` and `admin` are reserved.
  Choose names you'll recognise in logs and the dashboard; the vendor in the name is a
  useful habit, not a requirement.
- **Vendor** is the CLI the slot runs: `claude`, `gemini` or `codex`.
- **Roles** are comma-separated ([Roles](ROLES.md)). `orchestrator` can't be combined with a
  coding role. You can change roles later with `hive admin roles`.
- Each command prints the agent's **token once**, plus a ready-made `hive join` command. The
  server stores only a hash, so copy the token now.
- **Lost token:** `POST /api/v1/agents/<name>/token` with the admin token rotates it; then
  re-join with the new one ([CLI.md §13](CLI.md#13-common-recipes) has the exact commands).

---

## Join each slot on the machine that will run it

```sh
hive join claude-2 --vendor claude --token <token> --server <url> --repo /path/to/clone
```

The machine needs a clone of the repository first (`git clone …`). The slot's worktree is
created next to it.

What `hive join` does:
1. **Checks** the token against the server and that it belongs to `claude-2`.
2. **Creates a git worktree** for the slot next to your clone:
   `/path/to/<clone-name>-claude-2`, on branch `hive/claude-2`, starting from `origin/main`.
   Use `--worktree PATH` to choose the location.
   - **Re-joining:** if a branch `hive/claude-2` already exists with commits that aren't in
     `main`, `hive join` refuses rather than resetting it. Integrate those commits or delete
     the branch first.
3. **Installs a git `pre-commit` hook** in the clone; worktrees share it.
   - **Your own commits:** the hook exits immediately outside agent sessions, so they never
     need `hive` on PATH.
   - **Agent sessions:** it refuses commits of files the agent hasn't leased.
   - **Existing hooks:** a foreign `pre-commit` hook is never overwritten; Hive tells you
     which line to add.
4. **Creates the slot's private CLI config folder**, by default `~/.hive/accounts/claude-2`
   (`--config-dir` to change it).
5. **Saves the slot** to `~/.hive/machine.toml`.

Why separate worktrees? Each agent edits, commits and rebases in its own checkout on its own
branch, so agents never see each other's half-finished changes, and your own clone stays
yours. Agents can't edit files in another slot's worktree or in your clone
([Leases](INTEGRATION.md#leases)).

| `hive join` option | Meaning |
|---|---|
| `--repo PATH` | Local clone; the worktree is created next to it. |
| `--worktree PATH` | Explicit worktree path (use with or instead of `--repo`). |
| `--config-dir PATH` | The account's CLI config folder (default `~/.hive/accounts/<name>`). |
| `--permission-mode MODE` | Claude: `acceptEdits` (default for workers), `bypassPermissions`, …; Gemini: `auto_edit` (default), `yolo`; Codex: `workspace-write` (default for workers), `read-only`, `danger-full-access`, `yolo`. See the [Security model](SECURITY.md). |
| `--model MODEL` | The model the slot's CLI uses, e.g. `--model <model-name>`. Default: the account's default model. |
| `--server URL` | Server or project URL (`https://host/p/<project>`), saved for this slot only. Default: the slot's current one if re-joining, else the machine's default (set by the first `hive join`). |
| `--replace` | Move an existing slot of the same name to another server or project. Its local state (event cursor, inbox, session) is reset. Without it, `hive join` refuses, so another project's agent of the same name can't silently take over the slot. |
| `--hive-cmd CMD` | How the git hook calls `hive` (default `hive`). |

Prefer widening `[permissions]` in `hive.toml` over `bypassPermissions` or `yolo`: those modes
let the agent run any command on the machine and skip the orchestrator's approval step
([Shell-command permissions](PERMISSIONS.md)).

---

## Log each slot's account in once

Each slot uses its own config folder, so log the matching account into it once,
interactively. The environment variable points the CLI at the slot's folder instead of your
personal one:

**bash / zsh (macOS, Linux, Git Bash):**
```sh
cd /path/to/clone-claude-2
CLAUDE_CONFIG_DIR=~/.hive/accounts/claude-2 claude      # log in, then exit
cd /path/to/clone-gemini-1
GEMINI_CLI_HOME=~/.hive/accounts/gemini-1 gemini        # log in, then exit
cd /path/to/clone-codex-1
CODEX_HOME=~/.hive/accounts/codex-1 codex login         # add --device-auth over SSH
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

Use a different account per slot. That is the point of separate folders. `hive overview`
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
