# Getting started

This walkthrough takes you from nothing to a working fleet on **one computer**: a local
server, an orchestrator you chat with, and one programmer that writes code. It takes about
fifteen minutes, plus however long the agents spend on your goal. Each step says what it
does and links to the page that explains it in full.

Once this works, growing the fleet means enrolling more agents and running `hive up` on more
machines. Nothing about the first setup changes.

**You need:**
- Hive installed ([Requirements and installation](INSTALLATION.md));
- git, and a repository with an `origin` remote you can push to;
- Claude Code (`claude`) installed, and **two** Claude accounts: one for the orchestrator,
  one for the programmer. Gemini CLI or Codex work the same way; change `--vendor`.

---

## 1. Start a server

```sh
python -c "import secrets; print(secrets.token_urlsafe(32))"      # → your admin token
hive server --admin-token <admin token>
```

Keep this terminal open. The server listens on `http://127.0.0.1:8765` and stores everything
in `hive.db` in the current folder. The admin token is your password for the whole fleet.

➜ [Running the server](SERVER.md). For a server other machines can reach, see
[DEPLOYMENT.md](DEPLOYMENT.md).

## 2. Log in as admin

In a second terminal:

```sh
hive admin login --server http://127.0.0.1:8765 --token <admin token>
```

Every admin command on this computer now uses this login.

## 3. Prepare the repository

```sh
cd ~/code/myapp
hive init
git add hive.toml .hive .gitignore && git commit -m "Wire for Hive" && git push
hive admin push-config
```

`hive init` writes `hive.toml` (the fleet's configuration) and `.hive/` (the bundles that
give agents their tools and instructions). They do nothing to your own CLI sessions. You may
want to set `[integration] checks` in `hive.toml` to your test command first, so nothing
reaches `main` without passing it.

➜ [Preparing the repository](REPOSITORY.md)

## 4. Enroll two agents

```sh
hive admin enroll orch --vendor claude --roles orchestrator
hive admin enroll dev  --vendor claude --roles programmer
```

Each command prints the agent's token (once) and a ready-made `hive join` line.

➜ [Enrolling agents](MACHINES.md#enroll-each-agent-on-your-computer), [Roles](ROLES.md)

## 5. Add both as slots on this computer

```sh
hive join orch --vendor claude --token <orch token> --repo ~/code/myapp
hive join dev  --vendor claude --token <dev token>  --repo ~/code/myapp
```

Each slot gets its own git worktree next to your clone (`~/code/myapp-orch`,
`~/code/myapp-dev`) and its own private CLI config folder.

➜ [Joining a slot](MACHINES.md#join-each-slot-on-the-machine-that-will-run-it)

## 6. Log each slot's account in once

`hive join` printed the exact command for your shell. In bash it looks like this:

```sh
cd ~/code/myapp-orch && CLAUDE_CONFIG_DIR=~/.hive/accounts/orch claude   # log in, then exit
cd ~/code/myapp-dev  && CLAUDE_CONFIG_DIR=~/.hive/accounts/dev  claude   # the other account
```

➜ [Logging in](MACHINES.md#log-each-slots-account-in-once)

## 7. Check the setup

```sh
cd ~/code/myapp
hive overview
```

It should end with no problems. If it lists any, each comes with the command that fixes it.

➜ [Checking a project's setup](REPOSITORY.md#checking-a-projects-setup-hive-overview)

## 8. Run the fleet

```sh
hive up
```

The orchestrator's Claude Code session opens in this terminal. That is where you chat with
it. The programmer runs in the background, idle until it gets work.

➜ [Running the fleet](RUNNING.md)

## 9. Give it a goal and approve the plan

In another terminal:

```sh
hive start "Add CSV export to the reports page"
```

The orchestrator studies the repository and drafts a plan as tasks. It may ask you
questions in its terminal. When the plan is ready it raises a decision:

```sh
hive decisions             # read the plan
hive decide 1 approve      # or: hive decide 1 reject --note "split the UI work out"
```

Nobody writes code until you approve.

➜ [The fleet lifecycle](LIFECYCLE.md)

## 10. Watch it work

```sh
hive watch                 # the programmer's live output
hive status                # tasks, leases, the orchestrator's latest updates
```

Or open `http://127.0.0.1:8765/` and sign in with the admin token for the dashboard.

The programmer leases the files it needs, edits them, and releases its leases. Hive then
commits, rebases, runs your checks and pushes to `main`. The orchestrator checks the
programmer's report and tells you what changed. If anything needs your decision, it asks,
and the fleet waits for your answer.

➜ [Watching the fleet](MONITORING.md),
[Leases, git integration and reviews](INTEGRATION.md)

---

## Where to go next

- **Add more agents:** enroll them and `hive join` on any machine
  ([Enrolling agents and setting up machines](MACHINES.md)).
- **Require reviews:** set `[integration] review = "reviewer"` and enroll a reviewer
  ([Reviews](INTEGRATION.md#reviews)).
- **Tell the agents your conventions:** `.hive/prompts/`
  ([Customising what agents are told](PROMPTS.md)).
- **Put the server online:** [DEPLOYMENT.md](DEPLOYMENT.md).
- **Every command, with examples:** [CLI.md](CLI.md).

---

[← Back to the README](../README.md)
