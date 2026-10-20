# Shell-command permissions

Agents run shell commands all the time: `git status`, the test suite, a build, a formatter.
Headless agents can't answer "may I run this command?" prompts, so an unapproved command is
simply refused. Hive therefore decides ahead of time which commands an agent may run, and
sorts every command into one of three tiers:

| Tier | Examples | What happens |
|---|---|---|
| **1. Allowed** | reading, tests, git history work, builds | Runs directly. |
| **2. Needs approval** | `git push`, `git reset --hard`, `rm -rf`, `sudo` | A worker asks; **the orchestrator** approves or denies, and the daemon runs the exact command once. |
| **3. Never** | `hive decide`, `hive admin`, pushing to `main` | Refused, with no way to approve. |

The aim is to need as little human intervention as possible while staying safe: routine work
never waits for anyone, dangerous commands wait for the orchestrator, not for you, and the
few commands that would let an agent act as you are impossible.

---

## Where the rules live

`hive init` generates per-session permission files for each CLI from `[permissions]` in
`hive.toml`:

| CLI | Generated file | Attached with |
|---|---|---|
| Claude Code | `.hive/agent/claude/settings/{orchestrator,worker}.json` | `--settings` |
| Gemini CLI | `.hive/agent/gemini/policies/{orchestrator,worker}.toml` | `--policy` |
| Codex | `.hive/agent/codex/rules/{orchestrator,worker}.rules` (execpolicy `prefix_rule`s) | copied to the slot's `CODEX_HOME/rules/hive.rules` |

- **Which file:** the orchestrator gets the `orchestrator` set; every other role gets the
  `worker` set.
- **Scope:** nothing is added to your own CLI settings, so your normal sessions are unaffected.
- **Hive's own tools:** always allowed.

The CLIs' own permission files hold the common prefix forms as a first layer. **Hive's
PreToolUse hook is the authority**: it parses every command itself (see
[below](#how-hive-recognises-dangerous-commands)).

---

## 1. Allowed: run directly

With the `standard` preset:

| Group | Examples | Orchestrator | Workers |
|---|---|---|---|
| Reading | `git status/diff/log/show/blame/fetch/reflog`, `ls`, `cat`, `grep`, `rg`, `find`, `tree`, PowerShell `Get-ChildItem`/`Get-Content`/`Select-String` | ✓ | ✓ |
| Tests and checks | `pytest`, `python -m pytest`, `npm test`, `cargo test`, `go test`, `ruff check`, `mypy`, `tsc --noEmit`, plus every command in `[integration] checks` | ✓ | ✓ |
| Git history work | `git add`, `commit`, `merge`, `rebase`, `cherry-pick`, `revert`, `checkout`, `switch`, `restore`, `stash`, `tag`, `mv`, `rm`, `am`, `apply` | – | ✓ |
| Building and running | `python`, `node`, `uv run`, `npm run`, `npm install`, `npx`, `make`, `cargo build`, `go build`, formatters and linters, `mkdir`, `touch`, `cp`, `mv`, plain `rm`, `Copy-Item`/`Move-Item`/`New-Item`/`Remove-Item` | – | ✓ |

The orchestrator only reads and runs tests: it never codes.

---

## 2. Needs approval: dangerous commands

These publish, discard or rewrite history, or delete recursively:

| Group | Commands |
|---|---|
| Git | `git push`, `git reset --hard`, `git clean`, `git checkout --`/`-f`, `git restore --source`, `git branch -D`, `git update-ref`, `git filter-branch`/`filter-repo`, `git reflog expire`, `git gc --prune`, `git stash drop`/`clear`, `git rebase --root` |
| Recursive or forced deletes | POSIX `rm -r`/`-rf`/`-f`/`--recursive`, `find -delete`, `find -exec rm …`, `… \| xargs rm -rf`; cmd.exe `del /s /q /f`, `rd /s`, `rmdir /s`; PowerShell `Remove-Item -Recurse`/`-Force` (any abbreviation, any position) and its aliases `rm`, `del`, `ri`, `rd` |
| Elevation and permissions | `sudo`, `su`, `doas`, `runas`, `gsudo`, `Start-Process -Verb RunAs`; `chmod -R`, `chown -R`, `takeown`, `Set-Acl`, `icacls` with `/grant`, `/deny`, `/t`, … |
| Machine-level | `dd`, `mkfs`, `shred`, `format`, `diskpart`, `Format-Volume`, `Clear-Disk`, `reg delete`, `Remove-ItemProperty`, `shutdown`, `Stop-Computer`, `Restart-Computer`, `Set-ExecutionPolicy` |

Agents rarely need any of these: integration (`git push` included) goes through
`lease_release`, which doesn't need approval.

### How approval works for workers

**The orchestrator approves, not you.**
1. The worker's CLI blocks the command, and the hook tells it to call
   `request_approval(command, reason)`.
2. The orchestrator is woken within seconds (`approval.requested`). It approves or denies with
   a note, following criteria in its skill:
   - the command is needed for the task;
   - it's limited to the agent's own worktree and branch;
   - it's the narrowest command that works.

   It escalates to you with a question only when consequences are unclear or reach outside
   the repository.
3. **The Hive daemon runs the exact approved command once** in the worker's worktree, with a
   10-minute limit, and returns the output to the agent. It uses the shell the command was
   written for:
   - `bash`: Git Bash on Windows;
   - `powershell`: pwsh if installed, otherwise Windows PowerShell;
   - `cmd`: Windows only.

   The hook tells the agent which shell it used. `auto` means PowerShell on Windows and bash
   elsewhere.

**Why the worker usually doesn't need a new turn:** `request_approval` waits for the decision
(up to 10 minutes, using no tokens), so the worker usually gets the result within the same
turn. If no decision arrives, it ends its turn, is woken when the decision lands, and calls
`run_approved(id)`.

You can approve or deny pending requests yourself in the dashboard's *Approvals* panel, just
as the orchestrator can ([The web dashboard](MONITORING.md#the-web-dashboard)).

### Approval rules

- An approval is single-use and expires after an hour.
- The requesting agent can't approve itself.
- Everything is recorded on the server: who asked, why, who decided, the exit code and the
  output.
- **Who sees the output:** only the requesting agent and you. Other agents, including the
  orchestrator, see the request and the decision but not the output, which can contain
  secrets.
- **Git options don't hide a command:** git global options (`git -C dir …`, `git -c k=v …`)
  are looked through, so `git -C . push origin main` is still recognised as a push to `main`.

The **orchestrator's own** dangerous commands prompt *you* in its interactive session, since
there's nobody else to approve them.

---

## 3. Never: no approval possible

- **Hive's user commands:** `hive decide`, `admin`, `start`, `say`, `pause`, `resume`,
  `reset`, `init`, `join`, `up`. An agent must never answer your decisions or speak as you,
  even on a machine where you're logged in as admin.
- **Pushing to `main`,** including `--all` and `--mirror`. Integration goes through
  `lease_release`.
- Anything you add to `deny` ([below](#customising)).

These are checked by the hook, by the server when a request is made, and by the daemon again
just before it runs an approved command.

---

## Anything else

Commands not covered by these lists are refused in headless sessions. In an interactive
session (the orchestrator, or `hive chat`), you're asked instead.

If agents keep hitting refusals for a command your project needs, add it to `allow` or
`approval`.

---

## How Hive recognises dangerous commands

It parses each command itself instead of matching text prefixes only, so the tiers hold in
bash, PowerShell and cmd.exe alike:
- **Compound commands** are split, while separators inside quotes are left alone. So
  `git commit -m "fix; rm -rf x"` is just a commit.
- **Nested shells are unwrapped:** `bash -c`, `cmd /c`, `powershell -Command` and
  `-EncodedCommand`.
- **Program names are normalised:** `C:\…\git.exe` → `git`, `/bin/rm` → `rm`.
- **Dangerous flags are found in any position:** `Remove-Item build -Recurse` is caught just
  like `rm -rf build`.

---

## Codex specifics

- Codex workers run in its `workspace-write` sandbox by default: they can write in their
  worktree, and commands without a matching rule run sandboxed, without network access.
  Commands in the *allowed* tier run outside the sandbox, as with the other CLIs. Give a slot
  `--permission-mode danger-full-access` (or `yolo`) if its work needs more.
- Codex hooks can't ask for confirmation, so for the interactive orchestrator a dangerous
  command in the *approval* tier is refused with a note to ask you to run it yourself. The
  `.rules` file's prompts only apply when the Hive daemon can't be reached.
- `.hive/agent/` (like `.hive/prompts/`) is protected: agents can't edit or commit it, since
  Codex loads the hooks and rules there without its own hook-trust check.

---

## Customising

Edit `[permissions]` in `hive.toml`, then re-run `hive init` and commit:

```toml
[permissions]
preset = "standard"     # standard | strict (no build/run tools) | none (only your lists)
allow = ["docker compose ps", "npm run e2e"]
approval = ["npm publish", "terraform apply"]   # extra commands the orchestrator must approve
deny = ["kubectl delete"]                       # extra commands agents may never run
```

| Preset | Allowed tier |
|---|---|
| `standard` | Everything in the [table above](#1-allowed-run-directly). |
| `strict` | The same without the building and running tools (interpreters, package managers, `make`). |
| `none` | Only your `allow` list. |

- **Prefix format:** entries are command prefixes without wildcards. Hive translates each one
  per CLI: `"git diff"` becomes `Bash(git diff *)` and `PowerShell(git diff *)` for Claude, and
  `commandPrefix = "git diff"` for Gemini.
- **Stricter tier wins:** a command can't be allowed if it's also listed for approval or
  never-allowed, and the built-in dangerous commands always need approval, whatever the
  preset.

`hive overview` shows the preset and how many prefixes each tier has, for the orchestrator and
for workers.

---

## Trust implications

- **Interpreters run anything.** `python`, `node`, `npx`, `uv run`, `npm run` and `make` can
  run arbitrary code. That's why `strict` leaves them out.
- **Pattern rules aren't a security boundary.** The CLIs' own docs say so: an agent could reach
  the same program another way, e.g. `sh -c '…'`. The workers' skill forbids working around a
  block. The hard protections are:
  - the admin token, needed for decisions;
  - lease checks on integration;
  - the server's and the daemon's checks on approved commands.

  Keep the admin token off machines that host worker agents.

See the [Security model](SECURITY.md) for the whole picture.

---

**Next:** [Customising what agents are told](PROMPTS.md) ·
**See also:** [What the agents see](AGENT_INTERFACE.md), [Security model](SECURITY.md) ·
[← Back to the README](../README.md)
