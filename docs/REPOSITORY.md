# Preparing the repository

Hive's agents are ordinary AI coding CLIs (Claude Code, Gemini CLI, Codex). Before they can
work on a repository as a Hive **fleet**, the repository needs two things:
- **Hive's settings** for this project, in a file called `hive.toml`;
- a **bundle** for each CLI: a package, in the folder `.hive/agent/`, of what Hive attaches
  to an agent's session:
  - **tools** the agent can call (send a message, lock files, finish a task, …), offered
    through an [MCP server](GLOSSARY.md#mcp-mcp-server-mcp-tool), the standard way these CLIs
    accept extra tools;
  - **[hooks](GLOSSARY.md#hook)**: automatic checks the CLI runs before and after each
    action, which deliver urgent messages and refuse edits the agent isn't allowed to make;
  - **[skills](GLOSSARY.md#skill)**: written procedures the agent follows;
  - **permission rules** saying which shell commands the agent may run
    ([Shell-command permissions](PERMISSIONS.md)).

`hive init` writes all of that. You run it once, commit the result, and re-run it after
upgrading Hive or changing your prompt files (your own additions to the agents'
instructions; [Customising what agents are told](PROMPTS.md)). This page explains what
`hive init` creates, why none of it affects your own CLI sessions, and how `hive overview`
checks the whole setup afterwards.

---

## Running `hive init`

Run once in a clone (your local copy) of the repository the agents will work on.
`your-project` stands for its folder:

```sh
cd your-project
hive init                 # options: --server URL  --main main  --hive-cmd hive
```

| Option | Meaning |
|---|---|
| `--server URL` | The address of your Hive [server](SERVER.md) (or of a [project](PROJECTS.md) on it) to record in a new `hive.toml`. |
| `--main BRANCH` | The integration branch: the branch finished work is merged into (default `main`). |
| `--hive-cmd CMD` | How the bundles call `hive`. Use a full path if the agent CLIs can't find `hive` by name ([Installation](INSTALLATION.md#hive-must-be-on-path)). |

### What it creates

| Path | Purpose | Commit it? |
|---|---|---|
| `hive.toml` | The fleet's settings, in [TOML](GLOSSARY.md#files) format: whether work needs a [review](INTEGRATION.md#reviews), which test commands (*checks*) must pass before work reaches `main`, which agent takes over if the orchestrator is unavailable, which shell commands agents may run, and [roles](ROLES.md) ([`hive.toml` reference](CONFIGURATION.md#hivetoml)). Created only if missing. | yes |
| `.hive/agent/claude/` | The bundle for Claude Code, in the form Claude Code calls a **plugin**: `.claude-plugin/plugin.json` (the plugin's description), `.mcp.json` (starts Hive's MCP server), `hooks/hooks.json`, the two skills `skills/hive-orchestrator/SKILL.md` and `skills/hive-worker/SKILL.md`, and the permission rules for the orchestrator and for everyone else, `settings/{orchestrator,worker}.json`. | yes |
| `.hive/agent/gemini/` | The bundle for Gemini CLI, which calls it an **extension**: `gemini-extension.json` (starts Hive's MCP server and adds the instructions in `HIVE.md`), `hooks/hooks.json`, the same skills, and permission rules (Gemini calls them policies) `policies/{orchestrator,worker}.toml`. | yes |
| `.hive/agent/codex/` | The bundle for Codex, which calls it a **profile**: `hive.config.toml` (starts Hive's MCP server and sets the hooks), the same skills, and permission rules in Codex's own rule format (*execpolicy*) `rules/{orchestrator,worker}.rules`. | yes |
| `.hive/plans/` | Where the orchestrator (the agent that plans the work) may write plans. | yes |
| `.hive/prompts/` | **Your additions to the agents' instructions** ([Customising what agents are told](PROMPTS.md)): `README.md`, plus commented-out starters `orchestrator.md`, `worker.md` and `roles/<role>.md` for every role. Each is created only if missing; your files are never overwritten. Until you write something outside the comments, they add nothing. | yes |
| `.hive/README.md` | Explains the folder. | yes |
| `.gitignore` | Adds `.hive/orchestrator/`, where the orchestrator keeps its private notes, so they're never committed. | yes |

Everything is committed because every agent works in its own
[worktree](GLOSSARY.md#worktree) (a separate working folder of the repository, often on
another computer): the bundles reach the agents through git, not through Hive.

### The bundles are inert

None of the CLIs loads `.hive/agent/` on its own:
- **Claude Code** gets the plugin only when `hive` passes `--plugin-dir .hive/agent/claude`.
- **Gemini** gets the extension only through the agent's own **private** Gemini settings
  folder, never your personal `~/.gemini`
  ([How an agent is launched](RUNNING.md#how-an-agent-is-launched-for-a-headless-turn-wake)).
- **Codex** gets the profile, rules and skills only through the agent's own **private**
  settings folder (Codex's `CODEX_HOME`), and only when Hive starts it with `-p hive` (use
  the `hive` profile)
  ([How an agent is launched](RUNNING.md#how-an-agent-is-launched-for-a-headless-turn-wake)).

Opening `claude`, `gemini` or `codex` in this repository yourself loads nothing from Hive. You
can keep using your own CLI sessions in the same repository as before.

### Cleanup of older versions

The first version of Hive (0.1) wrote its settings into files the CLIs load automatically,
so they affected every session. `hive init` removes those Hive entries:
`.mcp.json`, `.claude/settings.json`, `.gemini/settings.json`, and the
`<!-- hive:begin -->` blocks in `CLAUDE.md`, `GEMINI.md`, `AGENTS.md`. Your own entries in
those files are kept. A file is deleted only if Hive's content was all it had.

### Commit, push and upload

```sh
git add -A
git commit -m "Add Hive"
git push
hive admin push-config        # uploads hive.toml to the server (run inside the repo)
```

The server keeps its own copy of `hive.toml`, because it enforces the roles, review policy
and permissions in it. `hive admin push-config` uploads it; it needs your admin login
([Log in as admin](SERVER.md#log-in-as-admin)). `hive start`, which gives the fleet its goal
([Starting](LIFECYCLE.md#starting)), also uploads it automatically when run inside the
repository.

### When to re-run it

Re-run `hive init`, then commit, push and `hive admin push-config`, after:
- upgrading Hive, so the bundles match the new version;
- changing the `[permissions]` section of `hive.toml`, since the CLIs' permission files are
  generated from it;
- changing `.hive/prompts/orchestrator.md` or `worker.md`, which are built into the skills
  ([Customising what agents are told](PROMPTS.md)).

`hive overview` tells you when the bundles are stale, and why.

---

## Checking a project's setup (`hive overview`)

Run `hive overview` anywhere inside the repository (or `hive overview PATH`) for a full
report on its Hive setup, ending with a list of problems and the command that fixes each one.
It never changes anything, so run it whenever something seems off. The table below uses
terms explained elsewhere: **slots** are agents set up on this computer
([Setting up machines](MACHINES.md)), **leases** are agents' locks on files
([Leases](INTEGRATION.md#leases)), and the **fleet state** is the phase the team is in
([The fleet lifecycle](LIFECYCLE.md)).

| Section | What it shows |
|---|---|
| **Repository** | Current branch; `origin` (the shared copy every computer pushes to); the integration branch and whether this clone has fetched it; whether [GitHub mode](INTEGRATION.md#github-mode-optional) is on; whether Hive's git commit check (installed by `hive join`) is in place; and whether `.hive/agent/` matches what `hive init` would write now. Changes to Hive's version, `[permissions]` or `.hive/prompts/` all make it stale. |
| **Configuration** | From `hive.toml`: server address, review policy, checks and the shell they run in, standby orchestrators (agents that take over if the orchestrator is unavailable), and timing settings. |
| **Shell-command permissions** | The preset (a starting set of rules) and how many commands are allowed, need approval, or are never allowed, for the orchestrator and for other agents; your extra lists ([Shell-command permissions](PERMISSIONS.md)). |
| **Roles** | Every [role](ROLES.md): built-in, built-in but changed in `hive.toml`, or custom; required or optional; how many agents hold it; what it may edit; which kinds of task it takes; where its instructions come from (built-in, `hive.toml`, `.hive/prompts/roles/`). |
| **Project prompts** | Each file in `.hive/prompts/`: active (with line count), comments only, or too large; and where it goes. |
| **This machine** | This computer's Hive settings (`~/.hive/machine.toml`), whether `hive up` (which runs the agents) is running, whether you're logged in as admin, and this repository's slots: CLI, what each is doing now, its branch, whether its account has been logged in, and its worktree. |
| **Fleet** | Asked from the server: fleet state, goal, orchestrator, the latest commit on `main`, agents (online, and which run on this computer), open tasks and active leases. |
| **Problems** | Everything above that needs attention. |

### Problems it reports

- no `origin` remote, or the integration branch not fetched;
- stale agent bundles, with the reason;
- `.hive/orchestrator/` not git-ignored;
- errors in `hive.toml`'s `[roles]` or `[permissions]` sections;
- warnings about your prompt files in `.hive/prompts/` ([Customising what agents are told](PROMPTS.md));
- slots with a missing worktree, the wrong branch, a never-logged-in account, or a worktree
  shared with another slot;
- a server URL that differs between `hive.toml` and this machine;
- an unreachable server, or credentials it refuses;
- a server whose review policy differs from `hive.toml`, meaning the copy uploaded with
  `hive admin push-config` is out of date;
- slots set up here but not created on the server (`hive admin enroll`);
- required roles nobody holds (a fleet needs an orchestrator and a programmer).

### Which server it asks

The one this project really uses. `hive.toml`'s `[server] url` is informational only
([`hive.toml` reference](CONFIGURATION.md#hivetoml)), so the overview picks, in this order:
1. the server this project's slots on this computer connect to (recorded in
   `~/.hive/machine.toml` by `hive join --server`);
2. the server your admin login points at (`~/.hive/admin.toml`, set by `hive admin login`);
3. `hive.toml`'s value, only if neither exists.

The Fleet section names the source. It also lists the other addresses whenever they differ,
and reports:
- agents and admin commands pointing at different servers, which means two different fleets;
- an out-of-date `hive.toml` value, which is harmless for connecting but misleading for
  anyone setting up a new machine.

`hive init` records this machine's server in a new `hive.toml` when you don't pass `--server`
and aren't logged in as admin.

### How it gets the fleet data

It uses your admin login if it points at that server. Otherwise it uses the token (password)
of one of this computer's agents for the repository, which can read the fleet but nothing
admin-only. It asks the [daemon](GLOSSARY.md#daemon), the background program `hive up`
starts, whether it's running.

**Offline:** `--offline` skips both the server and the daemon, so it works with no network.
Without it, each check gives up after a few seconds.

---

**Next:** [Enrolling agents and setting up machines](MACHINES.md) ·
**See also:** [Customising what agents are told](PROMPTS.md),
[Configuration reference](CONFIGURATION.md) ·
[← Back to the README](../README.md)
