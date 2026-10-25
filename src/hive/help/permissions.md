---
title: Which shell commands agents may run
keywords: permission, allow, deny, approval, approvals, commands, shell commands, tiers, forbidden, dangerous, request_approval, git push, rm -rf, sudo, standard, none
see: presets, roles, config, security
---
# Shell-command permissions

Most agents run headless, with nobody to ask "may I run this?". So Hive sorts every shell
command an agent runs into one of three tiers ahead of time:

- **Allowed: runs directly.** Reading (`git status`, `ls`, `grep`, …), tests and checks
  (`pytest`, `npm test`, your `integration.checks`), web reads, and for workers git history
  work (`add`, `commit`, `rebase`, …), building, running and installing packages. The
  orchestrator only reads and runs tests: it never codes.
- **Needs approval.** Commands that publish, discard or rewrite history, or delete
  recursively (`git push`, `git reset --hard`, `rm -rf`, `sudo`, sending data with `curl`),
  and **any command on no list**. The command waits while a reviewer, or the orchestrator
  when no reviewer takes it, approves or denies it; then it runs as if a person had clicked
  "allow". You aren't asked, unless the orchestrator escalates to you.
- **Never.** The `hive` commands that act as you (`hive approve`, `decide`, `admin`,
  `agents`, `config`, `api`, …), pushing to `main`, and anything in your `deny` list. No
  approval is possible.

Work reaches `main` through Hive's own `lease_release` tool, which needs no approval.

## Customising

In `hive.toml` (commit and push it; each agent's next launch uses it):

```toml
[permissions]
preset = "standard"      # standard | strict (no build/run tools) | none (only your lists)
allow = ["docker compose ps", "npm run e2e"]
approval = ["npm publish", "terraform apply"]
deny = ["kubectl delete"]
```

```sh
hive config set permissions.allow '["docker compose ps", "npm run e2e"]'
hive config set permissions.allow '["make deploy-preview"]' --local   # this computer only
```

Each entry is the beginning of a command, without wildcards: `"git diff"` covers
`git diff --stat` too. The stricter tier wins, and the built-in dangerous commands always
need approval whatever the preset. If agents keep asking for a command your project needs
routinely, add it to `allow`.

## How it is checked

Hive's own hook, run by the CLI before every command, analyses the command itself: commands
joined with `;`, `&&` or `|` are checked one by one, `bash -c`, `cmd /c` and
`powershell -Command` are unwrapped, `C:\…\git.exe` counts as `git`, and dangerous flags are
found in any position. The same rules are written into each CLI's own permission format as a
first layer.

Approvals are single-use and expire after an hour; an agent can't approve its own request.
Only the requesting agent and you see an approved command's output. You can decide pending
requests yourself in the dashboard's Approvals panel. `hive doctor` shows how many entries
each tier has.
