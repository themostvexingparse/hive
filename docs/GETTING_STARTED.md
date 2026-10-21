# Getting started

This walkthrough takes you from nothing to a working Hive **fleet** (a team of AI coding
agents working on one repository) on **one computer**. You'll set up:

- a **server**: the program that holds everything the agents share (tasks, messages, file
  locks) and that you control the fleet through;
- an **orchestrator**: an agent that plans the work and that you chat with, but that never
  writes code;
- one **programmer**: an agent that writes the code.

It takes about fifteen minutes, plus however long the agents spend on your goal. Each step
says what it does and links to the page that explains it in full. The
[glossary](GLOSSARY.md) explains every term.

Once this works, growing the fleet means adding more agents and running them on more
computers. Nothing about the first setup changes.

**You need:**
- Hive installed ([Requirements and installation](INSTALLATION.md));
- git, and a repository whose `origin` remote (the shared copy you `git push` to, such as a
  GitHub repository) you can push to;
- Claude Code (`claude`) installed, and **two** Claude accounts: one for the orchestrator,
  one for the programmer. Codex (`codex`), Antigravity CLI (`agy`) or Gemini CLI (`gemini`)
  work the same way; change `--vendor claude` to `--vendor codex`, `--vendor agy` or
  `--vendor gemini`. (agy has one login per computer, so two agy agents on one computer
  share an account: [Accounts](MACHINES.md#log-each-slots-account-in-once).)

**In the examples,** `~/code/myapp` is the folder where you cloned your repository; use your
own path. `orch` and `dev` are the names given to the two agents; you can choose others.

---

## 1. Start a server

First, make up an **admin token**: a long random password that controls the whole fleet.
This command prints one:

```sh
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Then start the server with it, putting the printed text in place of `<admin token>`:

```sh
hive server --admin-token <admin token>
```

Keep this terminal open: the server runs until you stop it. It listens on
`http://127.0.0.1:8765` (your own computer, port 8765) and stores everything in a file,
`hive.db`, in the current folder.

➜ [Running the server](SERVER.md). For a server other computers can reach, see
[DEPLOYMENT.md](DEPLOYMENT.md).

## 2. Log in as admin

In a second terminal:

```sh
hive admin login --server http://127.0.0.1:8765 --token <admin token>
```

This saves the server address and token in `~/.hive/admin.toml`, so every admin command on
this computer (`hive admin …`, `hive start`, `hive decide`, …) works without them.

## 3. Prepare the repository

```sh
cd ~/code/myapp
hive init
git add hive.toml .hive .gitignore && git commit -m "Wire for Hive" && git push
hive admin push-config
```

- `hive init` writes two things into the repository:
  - `hive.toml`, the fleet's settings;
  - `.hive/`, a folder holding, for each supported CLI, a package (a *bundle*) of the tools,
    automatic checks and instructions Hive gives agents.

  They have no effect on `claude` sessions you open yourself; Hive attaches them only to
  agents it launches.
- The `git` line commits and pushes them, so the agents' copies of the repository get them.
- `hive admin push-config` uploads `hive.toml` to the server, which enforces it.

Recommended: before committing, open `hive.toml` and set `checks` in its `[integration]`
section to your test command, for example `checks = ["python -m pytest -q"]`. Hive then runs
it before any agent's work reaches `main`, and refuses work that fails.

➜ [Preparing the repository](REPOSITORY.md), [`hive.toml` reference](CONFIGURATION.md#hivetoml)

## 4. Create the two agents on the server

```sh
hive admin enroll orch --vendor claude --roles orchestrator
hive admin enroll dev  --vendor claude --roles programmer
```

*Enrolling* creates an agent on the server: a name, the CLI it runs (`--vendor`) and what
it's for (`--roles`). Each command prints the agent's own **token** (its password, shown only
once, so copy it) and a ready-made `hive join` command for the next step.

➜ [Enrolling agents](MACHINES.md#enroll-each-agent-on-your-computer), [Roles](ROLES.md)

## 5. Add both agents to this computer

```sh
hive join orch --vendor claude --token <orch token> --repo ~/code/myapp
hive join dev  --vendor claude --token <dev token>  --repo ~/code/myapp
```

Use the tokens printed in step 4. *Joining* sets up a **slot**, an agent's home on this
computer:
- its own **git worktree**: a second working folder of your repository, next to your clone
  (`~/code/myapp-orch`, `~/code/myapp-dev`), each on its own branch (`hive/orch`,
  `hive/dev`). Agents work there, so they never touch your own folder or each other's
  half-finished edits;
- its own private Claude Code settings folder (`~/.hive/accounts/orch`, `…/dev`), where its
  account will be logged in.

➜ [Joining a slot](MACHINES.md#join-each-slot-on-the-machine-that-will-run-it)

## 6. Log each agent's account in once

Each slot needs its own Claude account logged in to its own settings folder. `hive join`
printed the exact commands for your shell. In bash they look like this:

```sh
cd ~/code/myapp-orch && CLAUDE_CONFIG_DIR=~/.hive/accounts/orch claude   # log in, then exit
cd ~/code/myapp-dev  && CLAUDE_CONFIG_DIR=~/.hive/accounts/dev  claude   # log in with the other account
```

`CLAUDE_CONFIG_DIR` tells Claude Code which settings folder to use, so this login goes to the
slot's folder instead of your personal one.

➜ [Logging in](MACHINES.md#log-each-slots-account-in-once)

## 7. Check the setup

```sh
cd ~/code/myapp
hive overview
```

It reports on the repository, the configuration, this computer's slots and the server, and
should end with no problems. If it lists any, each comes with the command that fixes it.

➜ [Checking a project's setup](REPOSITORY.md#checking-a-projects-setup-hive-overview)

## 8. Run the fleet

```sh
hive up
```

`hive up` starts a background program (the *daemon*) that keeps in touch with the server and
starts agents when there's work for them. The orchestrator's Claude Code session opens in
this terminal: that is where you chat with it. The programmer waits in the background, using
nothing, until it gets work.

➜ [Running the fleet](RUNNING.md)

## 9. Give it a goal and approve the plan

In another terminal:

```sh
hive start "Add CSV export to the reports page"
```

The text in quotes is the **goal**: what you want done. The orchestrator studies the
repository and drafts a plan, split into tasks. It may ask you questions in its terminal.
When the plan is ready it asks for your approval as a **decision**: a numbered question that
only you can answer.

```sh
hive decisions             # shows the open decision, its number and the plan
hive decide 1 approve      # 1 is that number
```

To send the plan back instead: `hive decide 1 reject --note "split the UI work out"`. The
orchestrator gets your note and revises.

Nobody writes code until you approve.

➜ [The fleet lifecycle](LIFECYCLE.md), [Answering decisions](ORCHESTRATOR.md#answering-decisions)

## 10. Watch it work

```sh
hive watch                 # the programmer's live output, as it works (Ctrl+C to stop)
hive status                # tasks, file locks, and the orchestrator's latest updates
```

Or open `http://127.0.0.1:8765/` in a browser and sign in with the admin token to see the
dashboard.

What happens next:
1. The programmer **leases** (locks) the files it needs, so no other agent can edit them, and
   edits them in its worktree.
2. It **releases** its leases. Hive then commits the changes, **rebases** them (replays them
   on top of the newest `main`), runs your checks, and pushes to `main`.
3. The programmer files a **report**. The orchestrator checks it against the repository and
   tells you what changed.
4. If anything needs your decision, the orchestrator asks, and the fleet waits for your
   answer.

➜ [Watching the fleet](MONITORING.md),
[Leases, git integration and reviews](INTEGRATION.md)

---

## Where to go next

- **Add more agents:** enroll them and `hive join` on any computer
  ([Enrolling agents and setting up machines](MACHINES.md)).
- **Require reviews:** set `review = "reviewer"` in `hive.toml`'s `[integration]` section and
  enroll an agent with the `reviewer` role, so another agent checks work before it reaches
  `main` ([Reviews](INTEGRATION.md#reviews)).
- **Tell the agents your conventions:** write them in `.hive/prompts/`
  ([Customising what agents are told](PROMPTS.md)).
- **Put the server online:** [DEPLOYMENT.md](DEPLOYMENT.md).
- **Every command, with examples:** [CLI.md](CLI.md).

---

[← Back to the README](../README.md)
