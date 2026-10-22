# Shell-command permissions

The AI agents in a Hive fleet run shell commands all the time: `git status`, the test suite,
a build, a formatter. Normally a coding CLI asks a person "may I run this command?". But most
Hive agents, the **workers** (everyone except the orchestrator), run **headless**: without a
terminal or anyone to answer, so an unapproved command is simply refused. Hive therefore
decides ahead of time which commands an agent may run, and sorts every command into one of
three tiers:

| Tier | Examples | What happens |
|---|---|---|
| **1. Allowed** | reading, tests, git history work, builds | Runs directly. |
| **2. Needs approval** | `git push`, `git reset --hard`, `rm -rf`, `sudo`, sending data with `curl`, and any command on no list | The command waits while **a reviewer**, or the orchestrator when no reviewer takes it, approves or denies it; once approved it runs, as if a person had clicked "allow". |
| **3. Never** | `hive decide`, `hive admin` (commands that would act as you), pushing to `main` | Refused, with no way to approve. |

The aim is to need as little human intervention as possible while staying safe: routine work
never waits for anyone, dangerous and unknown commands wait for a reviewer or the
orchestrator, not for you, and the few commands that would let an agent act as you are
impossible.

---

## Where the rules live

`hive init` generates a permission file for each CLI, in that CLI's own format, from the
`[permissions]` section of `hive.toml` (the project's Hive settings). Hive attaches it only to
the sessions it starts:

| CLI | Generated file | Attached with |
|---|---|---|
| Claude Code | `.hive/agent/claude/settings/{orchestrator,worker}.json` | `--settings` |
| Gemini CLI | `.hive/agent/gemini/policies/{orchestrator,worker}.toml` | `--policy` |
| Codex | `.hive/agent/codex/rules/{orchestrator,worker}.rules` (Codex's rule format, *execpolicy*) | copied into the agent's private Codex settings folder (`CODEX_HOME`) as `rules/hive.rules` |
| agy | `.hive/agent/agy/settings/{orchestrator,worker}.json` (`command(regex:…)` rules) | written into the `permissions` of the agent's private agy settings (`.gemini/antigravity-cli/settings.json` in its [home folder](RUNNING.md#agy-home-folder)) |

- **Which file:** the orchestrator gets the `orchestrator` set; every other role gets the
  `worker` set.
- **Scope:** nothing is added to your own CLI settings, so your normal sessions are unaffected.
- **Hive's own tools:** always allowed.

These files list commands by how they begin (e.g. anything starting with `git diff`), as a
first layer. **Hive's own check is the authority**: a [hook](AGENT_INTERFACE.md#hooks) that
the CLI runs before every tool call, which analyses each command itself (see
[below](#how-hive-recognises-dangerous-commands)).

---

## 1. Allowed: run directly

A *preset* is a ready-made starting set of rules; you choose it in `hive.toml`
([Customising](#customising)). With the default, `standard`:

| Group | Examples | Orchestrator | Workers |
|---|---|---|---|
| Reading | `git status/diff/log/show/blame/fetch/reflog`, `ls`, `cat`, `grep`, `rg`, `find`, `tree`, PowerShell `Get-ChildItem`/`Get-Content`/`Select-String`, read-only GitHub CLI (`gh repo view/list`, `gh pr view/list/diff/checks`, `gh issue view/list`, `gh search`, …) | ✓ | ✓ |
| Shell basics and pipelines | `cd`, `pushd`/`popd`, `test`, `sleep`, `date`, `printf`, `basename`/`dirname`/`realpath`, `tr`, `jq`, `xargs` (checked by the command it runs), checksums; PowerShell `Set-Location`, `Where-Object`, `Select-Object`, `Sort-Object`, `ForEach-Object`, `Group-Object`, `Format-Table`/`Format-List`, `ConvertTo-Json`/`ConvertFrom-Json`, `Join-Path`/`Split-Path` | ✓ | ✓ |
| Web reads | `curl`, `wget`, `Invoke-WebRequest`, `Invoke-RestMethod` for downloads and API reads. Sending data needs approval (below). | ✓ | ✓ |
| Docker, read-only | `docker ps/images/logs/inspect/version/info`, `docker compose ps/logs/config` | ✓ | ✓ |
| Tests and checks | `pytest`, `python -m pytest`, `npm test`, `cargo test`, `go test`, `ruff check`, `mypy`, `tsc --noEmit`, plus every test command you listed under `checks` in `hive.toml` ([Checks](INTEGRATION.md#what-lease_releasecommit_message-task_id-does)) | ✓ | ✓ |
| Git history work | `git add`, `commit`, `merge`, `rebase`, `cherry-pick`, `revert`, `checkout`, `switch`, `restore`, `stash`, `tag`, `mv`, `rm`, `am`, `apply` | – | ✓ |
| Building and running | `python`, `node`, `uv run`, `npm run`, `npm install`, `npx`, `make`, `cargo build`, `go build`, formatters and linters, `mkdir`, `touch`, `cp`, `mv`, plain `rm`, `Copy-Item`/`Move-Item`/`New-Item`/`Remove-Item`, browsers (`msedge`, `chrome`, `chromium`, `firefox`, e.g. headless screenshots of a page), `sed`, `awk`, `tee` | – | ✓ |
| Packages | `pip install/show/list/freeze`, `python -m pip`, `uv add`, `uv pip`, `uv lock`, `poetry`, `cargo add/update`, `go get`, `go mod`, `dotnet add/restore`, `npm ls/view/outdated`, `composer`, `bundle` | – | ✓ |

The orchestrator only reads and runs tests: it never codes. The `strict` preset leaves out
web reads, docker, building and packages.

**Web access** is allowed for every role: Claude Code's `WebFetch` and `WebSearch`, Gemini
CLI's `web_fetch` and `google_web_search`, and agy's `read_url` and browser actions
(`execute_url`). Codex keeps its own network setting (see [Codex specifics](#codex-specifics)).

**Hive approves allowed commands itself** (Claude Code and agy). The CLIs' own rules only match
the start of a command, so they would ask about forms Hive's checks understand, such as
`ls x 2>&1; git log`, or a program given by its full path. Hive's hook approves those when
every part is allowed; a headless turn couldn't answer the question.

---

## 2. Needs approval: dangerous commands

These publish, discard or rewrite history, or delete recursively:

| Group | Commands |
|---|---|
| Git | `git push`, `git reset --hard`, `git clean`, `git checkout --`/`-f`, `git restore --source`, `git branch -D`, `git update-ref`, `git filter-branch`/`filter-repo`, `git reflog expire`, `git gc --prune`, `git stash drop`/`clear`, `git rebase --root` |
| Recursive or forced deletes | POSIX `rm -r`/`-rf`/`-f`/`--recursive`, `find -delete`, `find -exec rm …`, `… \| xargs rm -rf`; cmd.exe `del /s /q /f`, `rd /s`, `rmdir /s`; PowerShell `Remove-Item -Recurse`/`-Force` (any abbreviation, any position) and its aliases `rm`, `del`, `ri`, `rd` |
| Elevation and permissions | `sudo`, `su`, `doas`, `runas`, `gsudo`, `Start-Process -Verb RunAs`; `chmod -R`, `chown -R`, `takeown`, `Set-Acl`, `icacls` with `/grant`, `/deny`, `/t`, … |
| Machine-level | `dd`, `mkfs`, `shred`, `format`, `diskpart`, `Format-Volume`, `Clear-Disk`, `reg delete`, `Remove-ItemProperty`, `shutdown`, `Stop-Computer`, `Restart-Computer`, `Set-ExecutionPolicy` |
| Sending data over the network | `curl` with `-d`/`--data…`/`--json`/`-F`/`-T`/`-X POST`/`PUT`/`PATCH`/`DELETE`; `wget --post-data`/`--post-file`/`--method` (other than GET); PowerShell `Invoke-WebRequest`/`Invoke-RestMethod` (and Windows PowerShell's `curl`/`wget` aliases) with `-Method Post`/`Put`/`Patch`/`Delete`, `-Body` or `-InFile` |
| Anything on no list | e.g. `docker run`, `ftp`, a tool your project uses that Hive doesn't know. Add it to `allow` if agents need it routinely ([Customising](#customising)). |

Agents rarely need any of these: getting work into `main` (pushing included) goes through
Hive's `lease_release` tool, which needs no approval
([What `lease_release` does](INTEGRATION.md#what-lease_releasecommit_message-task_id-does)).

### How approval works for workers

**A reviewer approves first, then the orchestrator; not you.**
1. **The command waits.** In Claude Code and agy the worker just runs the command. Hive's
   pre-command [hook](GLOSSARY.md#hook) holds it, using none of the account's model usage,
   and files an approval request with the command and its reason (the description the CLI
   gives the command, or the task it's for).
2. **Reviewers decide first.** Every online [reviewer](ROLES.md) (other than the requester) is
   woken within seconds and approves or denies with a note, following criteria in its
   [skill](GLOSSARY.md#skill) (written procedure):
   - the command is needed for the requesting agent's task;
   - it affects only that agent's own working folder of the repository (its
     [worktree](GLOSSARY.md#worktree)), its own branch, or files it has leased;
   - it's the narrowest command that works.
3. **The orchestrator gets what reviewers don't take:** at once when no reviewer is online,
   and after a minute and a half when none has decided. It applies the same criteria, and
   escalates to you with a question only when consequences are unclear or reach outside the
   repository.
4. **Approved: the command runs** in the worker's own session, as if a person had clicked
   "allow", and the worker sees its real output. Denied: the worker gets the note.
5. **No decision within four minutes:** the worker is told to record where it is and end its
   turn. It's woken with the decision, runs the same command again, and Hive lets it through
   once.

**Codex and Gemini CLI** can't be approved from a hook, so there the worker gets a refusal
saying to use Hive's `request_approval` tool. The same approvers decide, and then **the Hive
daemon runs the exact approved command once** in the worker's worktree, with a 10-minute
limit, and returns the output. It uses the shell the command was written for:
- `bash`: [Git Bash](GLOSSARY.md#git-bash) on Windows;
- `powershell`: PowerShell 7 (`pwsh`) if installed, otherwise Windows PowerShell;
- `cmd`: Windows only.

`request_approval` waits for the answer (using none of the account's model usage), so the
worker usually gets the result within the same run (*turn*); otherwise it's woken when the
answer comes and runs the command with the `run_approved` tool.

**In a session you're typing in** (an agent you took over with `hive chat`), the CLI asks
*you* instead, like any interactive session.

You can approve or deny pending requests yourself in the dashboard's *Approvals* panel, just
as reviewers and the orchestrator can ([The web dashboard](MONITORING.md#the-web-dashboard)).

### Approval rules

- An approval is single-use and expires after an hour.
- The requesting agent can't approve itself.
- Everything is recorded on the server: who asked, why, who decided, the command's exit code
  (whether it succeeded) and its output.
- **Who sees the output:** only the requesting agent and you. Other agents, including the
  orchestrator, see the request and the decision but not the output, which can contain
  secrets.
- **Git options don't hide a command:** options given before git's subcommand
  (`git -C dir …`, `git -c key=value …`) are looked through, so `git -C . push origin main`
  is still recognised as a push to `main`.

The **orchestrator's own** dangerous commands ask *you*, in its terminal, since there's nobody
else to approve them.

---

## 3. Never: no approval possible

- **Hive's own commands for you:** `hive decide`, `hive admin`, `hive start`, `hive say`,
  `hive pause`, `hive resume`, `hive reset`, `hive init`, `hive join`, `hive up`. An agent
  must never answer your questions ([decisions](GLOSSARY.md#decision)) or speak as you, even
  on a computer where you're logged in as admin.
- **Pushing to `main`,** including `git push --all` and `--mirror`, which push every branch.
  Work reaches `main` only through `lease_release`.
- Anything you add to `deny` ([below](#customising)).

These are checked by the hook, by the server when an approval is requested, and by the
daemon again just before it runs an approved command.

---

## Anything else

A worker's command that is on none of the lists is treated like a dangerous one: it waits for
a reviewer's or the orchestrator's approval (in Claude Code and agy; in Codex and Gemini CLI
it is refused, and the worker can use `request_approval`). In a session you're typing in
(the orchestrator's, or an agent you took over with `hive chat`;
[Running the fleet](RUNNING.md#taking-over-a-slot-interactively-hive-chat)), you're asked
instead.

If agents keep asking for a command your project needs, add it to `allow`.

---

## How Hive recognises dangerous commands

It analyses each command itself instead of only looking at how it begins, so the tiers hold
in bash, PowerShell and cmd.exe alike:
- **Several commands on one line** (joined with `;`, `&&`, `|`, …) are checked one by one,
  while those characters inside quotes are left alone. So `git commit -m "fix; rm -rf x"` is
  just a commit.
- **Commands run through another shell are unwrapped:** `bash -c "…"`, `cmd /c …`,
  `powershell -Command …`, and PowerShell's base64-encoded form `-EncodedCommand`.
- **Program names are normalised:** `C:\…\git.exe` → `git`, `/bin/rm` → `rm`.
- **Dangerous flags are found in any position:** `Remove-Item build -Recurse` is caught just
  like `rm -rf build`.

---

## Codex specifics

- Codex workers run in Codex's `workspace-write` sandbox by default: a restricted mode in
  which they can write only in their worktree, and commands without a matching rule run with
  no network access. Commands in the *allowed* tier run outside the sandbox, as with the other
  CLIs. If an agent's work needs more, set its mode with
  `hive join … --permission-mode danger-full-access` (or `yolo`)
  ([Setting up machines](MACHINES.md#join-each-slot-on-the-machine-that-will-run-it)).
- Codex hooks can't ask a person for confirmation, so when the orchestrator runs on Codex, a
  dangerous command in the *approval* tier is refused with a note to ask you to run it
  yourself. The confirmation prompts in the `.rules` file only apply when the Hive daemon
  can't be reached.
- `.hive/agent/` (like `.hive/prompts/`) is protected: agents can't edit or commit it. Hive
  starts Codex with its hook review switched off
  ([why](RUNNING.md#codex-profile)), so an agent able to change those files could change its
  own checks.

---

## agy specifics

- In a headless turn, agy refuses every tool call its own rules would ask about, and then
  ends the turn without an answer. So for agy agents Hive's hook makes the decision for every
  shell command: it approves commands in the *allowed* tier itself, refuses the *forbidden*
  tier, and holds every other command until a reviewer or the orchestrator decides
  ([How approval works](#how-approval-works-for-workers)). Hive's own tools are approved the
  same way. The rules in the settings file apply only when the daemon can't be reached; then
  agy refuses anything not allowed.
- The orchestrator's interactive session leaves commands outside the *allowed* tier to agy,
  which asks you.
- agy writes files inside the worktree without asking (`--mode accept-edits` is the default for
  workers); Hive's lease checks apply to `write_file` and `edit_file` as to the other CLIs'
  edit tools. An edit whose file Hive can't tell is refused.

---

## Customising

Edit the `[permissions]` section of `hive.toml`, then re-run `hive init` (which regenerates
the CLIs' permission files), commit, and push:

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

- **Format:** each entry is the beginning of a command, without wildcards: `"git diff"`
  matches `git diff`, `git diff --stat` and so on. Hive translates each entry into every
  CLI's own format (for Claude Code, `"git diff"` becomes `Bash(git diff *)` and
  `PowerShell(git diff *)`; for Gemini, `commandPrefix = "git diff"`).
- **Stricter tier wins:** a command can't be allowed if it's also listed for approval or
  never-allowed, and the built-in dangerous commands always need approval, whatever the
  preset.

`hive overview` ([Checking a project's setup](REPOSITORY.md#checking-a-projects-setup-hive-overview))
shows the preset and how many entries each tier has, for the orchestrator and for workers.

---

## Trust implications

- **Interpreters run anything.** `python`, `node`, `npx`, `uv run`, `npm run` and `make` can
  run any code at all, including a script that deletes files. That's why `strict` leaves
  them out.
- **Rules about command names aren't a watertight wall.** The CLIs' own documentation says
  so: an agent could reach the same program another way, e.g. through a script. The workers'
  skill forbids working around a block. The protections an agent can't get around are:
  - the admin token (your password for the server), needed to answer decisions;
  - the check, when work is merged into `main`, that the agent holds valid
    [leases](INTEGRATION.md#leases) on the files;
  - the server's and the daemon's checks on approved commands.

  Keep the admin token off computers that run worker agents.

See the [Security model](SECURITY.md) for the whole picture.

---

**Next:** [Customising what agents are told](PROMPTS.md) ·
**See also:** [What the agents see](AGENT_INTERFACE.md), [Security model](SECURITY.md) ·
[← Back to the README](../README.md)
