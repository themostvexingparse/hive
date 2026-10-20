# Preparing the repository

Before any agent can work on a repository, the repository needs Hive's configuration and the
per-CLI bundles that give agents their tools, hooks and instructions. `hive init` writes all
of that. You run it once, commit the result, and re-run it after upgrading Hive or changing
your prompt files. This page explains what `hive init` creates, why none of it affects your
own CLI sessions, and how `hive overview` checks the whole setup afterwards.

---

## Running `hive init`

Run once in a clone of the project the agents will work on:

```sh
cd your-project
hive init                 # options: --server URL  --main main  --hive-cmd hive
```

| Option | Meaning |
|---|---|
| `--server URL` | The server or [project](PROJECTS.md) URL to record in a new `hive.toml`. |
| `--main BRANCH` | The integration branch (default `main`). |
| `--hive-cmd CMD` | How the bundles call `hive`. Use a full path if `hive` can't be on PATH for the agent CLIs ([Installation](INSTALLATION.md#hive-must-be-on-path)). |

### What it creates

| Path | Purpose | Commit it? |
|---|---|---|
| `hive.toml` | Fleet configuration: review policy, checks, standby orchestrator, shell-command permissions, roles ([`hive.toml` reference](CONFIGURATION.md#hivetoml)). Created only if missing. | yes |
| `.hive/agent/claude/` | **Claude Code plugin**: `.claude-plugin/plugin.json`, `.mcp.json` (hive MCP server), `hooks/hooks.json`, `skills/hive-orchestrator/SKILL.md`, `skills/hive-worker/SKILL.md`, plus per-role permission files `settings/{orchestrator,worker}.json` ([Shell-command permissions](PERMISSIONS.md)). | yes |
| `.hive/agent/gemini/` | **Gemini CLI extension**: `gemini-extension.json` (MCP server, `HIVE.md` context), `hooks/hooks.json`, the same skills, plus per-role policy files `policies/{orchestrator,worker}.toml` ([Shell-command permissions](PERMISSIONS.md)). | yes |
| `.hive/agent/codex/` | **Codex profile**: `hive.config.toml` (hive MCP server and hooks), the same skills, plus per-role execpolicy rules `rules/{orchestrator,worker}.rules` ([Shell-command permissions](PERMISSIONS.md)). | yes |
| `.hive/plans/` | Place for written plans. | yes |
| `.hive/prompts/` | **Your additions to the agents' instructions** ([Customising what agents are told](PROMPTS.md)): `README.md`, plus commented-out starters `orchestrator.md`, `worker.md` and `roles/<role>.md` for every role. Each is created only if missing; your files are never overwritten. Until you write something outside the comments, they add nothing. | yes |
| `.hive/README.md` | Explains the folder. | yes |
| `.gitignore` | Adds `.hive/orchestrator/` (the orchestrator's private memory). | yes |

Everything is committed because every agent works in its own worktree of the repository:
the bundles reach other machines through git, not through Hive.

### The bundles are inert

None of the CLIs loads `.hive/agent/` on its own:
- **Claude Code** gets the plugin only when `hive` passes `--plugin-dir .hive/agent/claude`.
- **Gemini** gets the extension only through the slot's **private** Gemini home
  ([How an agent is launched](RUNNING.md#how-an-agent-is-launched-for-a-headless-turn-wake)).
- **Codex** gets the profile, rules and skills only through the slot's **private**
  `CODEX_HOME`, selected with `-p hive`
  ([How an agent is launched](RUNNING.md#how-an-agent-is-launched-for-a-headless-turn-wake)).

Opening `claude`, `gemini` or `codex` in this repository yourself loads nothing from Hive. You
can keep using your own CLI sessions in the same repository as before.

### Cleanup of older versions

`hive init` removes Hive entries written by Hive 0.1 to files the CLIs load automatically:
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
and permissions in it. `hive admin push-config` uploads it. `hive start`
([Starting](LIFECYCLE.md#starting)) also uploads it automatically when run inside the
repository.

### When to re-run it

Re-run `hive init`, then commit, push and `hive admin push-config`, after:
- upgrading Hive, so the bundles match the new version;
- changing `[permissions]` in `hive.toml`, since the CLIs' permission files are generated
  from it;
- changing `.hive/prompts/orchestrator.md` or `worker.md`, which are built into the skills
  ([Customising what agents are told](PROMPTS.md)).

`hive overview` tells you when the bundles are stale, and why.

---

## Checking a project's setup (`hive overview`)

Run `hive overview` anywhere inside the project (or `hive overview PATH`) for a full report on
its Hive setup, ending with a list of problems and the command that fixes each one. It never
changes anything, so run it whenever something seems off.

| Section | What it shows |
|---|---|
| **Repository** | Branch, `origin`, the integration branch and whether it's fetched, GitHub mode, whether Hive's commit hook is installed, and whether `.hive/agent/` matches what `hive init` would write now. Changes to Hive's version, `[permissions]` or `.hive/prompts/` all make it stale. |
| **Configuration** | Server URL, review policy, checks and their shell, standby orchestrators, pacing (`hive.toml`). |
| **Shell-command permissions** | The preset and how many command prefixes are allowed, need approval, or are never allowed, for the orchestrator and for workers; your extra lists. |
| **Roles** | Every role: built-in, built-in but changed in `hive.toml`, or custom; required or optional; how many holders; what it may write; task kinds; where its prompt comes from (built-in, `hive.toml`, `.hive/prompts/roles/`). |
| **Project prompts** | Each file in `.hive/prompts/`: active (with line count), comments only, or too large; and where it goes. |
| **This machine** | The machine config, whether `hive up` is running, whether you're logged in as admin, and this repository's slots: CLI, what each is doing now, its branch, whether its account has been logged in, and its worktree. |
| **Fleet** | Asked from the server: state, goal, orchestrator, `main`, agents (online, and which run on this machine), open tasks and active leases. |
| **Problems** | Everything above that needs attention. |

### Problems it reports

- no `origin` remote, or the integration branch not fetched;
- stale agent bundles, with the reason;
- `.hive/orchestrator/` not git-ignored;
- errors in `hive.toml` (`[roles]`, `[permissions]`);
- prompt-file warnings ([Customising what agents are told](PROMPTS.md));
- slots with a missing worktree, the wrong branch, a never-logged-in account, or a worktree
  shared with another slot;
- a server URL that differs between `hive.toml` and this machine;
- an unreachable server, or credentials it refuses;
- a server whose review policy differs from `hive.toml`, meaning the uploaded config is stale;
- slots set up here but not enrolled on the server;
- required roles nobody holds.

### Which server it asks

The one this project really uses. `hive.toml`'s `[server] url` is informational only
([`hive.toml` reference](CONFIGURATION.md#hivetoml)), so the overview picks, in this order:
1. the server this project's slots on this machine connect to (`~/.hive/machine.toml`, set by
   `hive join --server`);
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

It uses your admin login if it points at that server. Otherwise it uses the token of one of
this machine's slots for the repository, which can read the fleet but nothing admin-only. It
asks the local daemon whether `hive up` is running.

**Offline:** `--offline` skips both the server and the daemon, so it works with no network.
Without it, each check gives up after a few seconds.

---

**Next:** [Enrolling agents and setting up machines](MACHINES.md) ·
**See also:** [Customising what agents are told](PROMPTS.md),
[Configuration reference](CONFIGURATION.md) ·
[← Back to the README](../README.md)
