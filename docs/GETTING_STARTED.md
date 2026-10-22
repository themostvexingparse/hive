# Getting started

This walkthrough takes you from nothing to a working Hive **fleet** (a team of AI coding
agents working on one repository) on **one computer**. You'll set up:

- a **server**: the program that holds everything the agents share (tasks, messages, file
  locks) and that you control the fleet through;
- an **orchestrator**: an agent that plans the work and that you chat with, but that never
  writes code;
- one **programmer**: an agent that writes the code.

It takes about ten minutes, plus however long the agents spend on your goal. Each step says
what it does and links to the page that explains it in full. The [glossary](GLOSSARY.md)
explains every term.

**If you get lost at any point, run `hive` on its own.** It looks at your setup and prints
the one next step.

Once this works, growing the fleet means adding more agents and running them on more
computers. Nothing about the first setup changes.

**You need:**
- Hive installed ([Requirements and installation](INSTALLATION.md));
- git, and a repository whose `origin` remote (the shared copy you `git push` to, such as a
  GitHub repository) you can push to;
- Claude Code (`claude`) installed, and **two** Claude accounts: one for the orchestrator,
  one for the programmer. Codex (`codex`), Antigravity CLI (`agy`) or Gemini CLI (`gemini`)
  work the same way: write `codex`, `agy` or `gemini` instead of `claude` below. (agy has
  one login per computer, so two agy agents on one computer share an account:
  [Accounts](MACHINES.md#log-each-agents-account-in-once).)

**In the examples,** `~/code/myapp` is the folder where you cloned your repository; use your
own path.

---

## 1. Start a server

```sh
hive server
```

Keep this terminal open: the server runs until you stop it. It listens on
`http://127.0.0.1:8765` (your own computer, port 8765) and stores everything in a file,
`hive.db`, in the current folder.

The first time, it makes up an **admin token** (the password that controls the whole fleet),
saves it in `~/.hive/server.toml`, and logs this computer in with it. You never have to type
it. (To choose your own: `hive server --admin-token <token>`.)

➜ [Running the server](SERVER.md). For a server other computers can reach, see
[DEPLOYMENT.md](DEPLOYMENT.md). If someone else runs the server, log in to it instead with
`hive login --server <its address>`: it asks for the token without showing it.

## 2. Prepare the repository

In a second terminal:

```sh
cd ~/code/myapp
hive init
git add hive.toml .hive .gitignore && git commit -m "Set up Hive" && git push
```

- `hive init` writes two things into the repository:
  - `hive.toml`, the fleet's settings;
  - `.hive/`, a folder holding, for each supported CLI, a package (a *bundle*) of the tools,
    automatic checks and instructions Hive gives agents.

  They have no effect on `claude` sessions you open yourself; Hive attaches them only to
  agents it launches.
- The `git` line commits and pushes them, so the agents' copies of the repository get them.

Hive uploads `hive.toml` to the server by itself whenever it changed, the next time any
`hive` command talks to the server.

Recommended: tell Hive your test command, so it runs it before any agent's work reaches
`main`, and refuses work that fails:

```sh
hive config set integration.checks "python -m pytest -q"
```

➜ [Preparing the repository](REPOSITORY.md), [`hive.toml` reference](CONFIGURATION.md#hivetoml)

## 3. Add the two agents

```sh
hive agents add claude
hive agents add claude programmer
```

Each command does everything an agent needs:
- creates it on the server, named `claude-1`, `claude-2` and so on. **The fleet's first
  agent is its orchestrator**; without a role, later ones are programmers. Add several at
  once with `hive agents add 3 claude programmers`;
- sets up its home on this computer, its **slot**:
  - its own **git worktree**: a second working folder of your repository, next to your clone
    (`~/code/myapp-claude-1`, `~/code/myapp-claude-2`), each on its own branch
    (`hive/claude-1`, …). Agents work there, so they never touch your own folder or each
    other's half-finished edits;
  - its own private Claude Code settings folder (`~/.hive/accounts/claude-1`, …), where its
    account will be logged in;
- offers to **log its account in** right away: Claude Code opens; log in with that agent's
  account, then exit it. Use a different account for each agent.

To log an agent in later (or again): `hive agents login claude-2`.

➜ [Enrolling agents and setting up machines](MACHINES.md), [Roles](ROLES.md)

## 4. Check the setup

```sh
hive doctor
```

It reports on the repository, the configuration, this computer's agents and the server, and
should end with no problems. If it lists any, each comes with the command that fixes it.

➜ [Checking a project's setup](REPOSITORY.md#checking-a-projects-setup-hive-doctor)

## 5. Run the fleet

```sh
hive up
```

`hive up` starts a background program (the *daemon*) that keeps in touch with the server and
starts agents when there's work for them. The orchestrator's Claude Code session opens in
this terminal: that is where you chat with it. The programmer waits in the background, using
nothing, until it gets work.

➜ [Running the fleet](RUNNING.md)

## 6. Give it a goal and approve the plan

In another terminal, run `hive` again: now that the team runs, it opens a **chat** with it.
Type the goal (Hive asks once to confirm it), and approve the plan with `y` when it appears.
Everything below can also be done with single commands, as shown:

```sh
hive start "Add CSV export to the reports page"
```

The text in quotes is the **goal**: what you want done. The orchestrator studies the
repository and drafts a plan, split into tasks. It may ask you questions in its terminal.
When the plan is ready it asks for your approval as a **decision**: a question that only you
can answer.

```sh
hive decisions             # shows the plan
hive approve               # approves it
```

To send the plan back instead: `hive reject "split the UI work out"`. The orchestrator gets
your note and revises. Other questions are answered with `hive answer "<your answer>"`. When
several decisions are open at once, add the decision's number: `hive approve 3`.

Nobody writes code until you approve.

➜ [The fleet lifecycle](LIFECYCLE.md), [Answering decisions](ORCHESTRATOR.md#answering-decisions)

## 7. Watch it work

```sh
hive watch                 # the programmer's live output, as it works (Ctrl+C to stop)
hive status                # tasks, file locks, and the orchestrator's latest updates
```

Or open `http://127.0.0.1:8765/` in a browser and sign in with the admin token (it's in
`~/.hive/server.toml`) to see the dashboard.

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

- **Add more agents:** `hive agents add …` on this computer. To use another computer, pair
  it once: `hive machines invite` prints a line to run there (`hive join CODE …`), then
  `hive agents add codex --on <its name>` places agents on it from here
  ([Adding agents and setting up machines](MACHINES.md)).
- **See and manage agents:** `hive agents` lists them; `hive agents edit`, `rm` and
  `rotate` change, remove them or give them a new token.
- **Require reviews:** `hive config set integration.review reviewer`, and add an agent with
  the `reviewer` role (`hive agents add gemini reviewer`), so another agent checks work
  before it reaches `main` ([Reviews](INTEGRATION.md#reviews)).
- **Change settings for this computer only:** `hive config set … --local` writes
  `hive.local.toml`, which is never committed or uploaded; `hive config explain <setting>`
  shows where a value comes from.
- **Tell the agents your conventions:** write them in `.hive/prompts/`
  ([Customising what agents are told](PROMPTS.md)).
- **Put the server online:** [DEPLOYMENT.md](DEPLOYMENT.md).
- **Every command, with examples:** [CLI.md](CLI.md).

---

[← Back to the README](../README.md)
