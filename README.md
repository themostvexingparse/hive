# Hive

Hive connects several subscription-based coding agents into one fleet. Each agent is the
vendor's official CLI (Claude Code or Gemini/Antigravity CLI), logged into its own account. The
fleet shares a git repository, a message board, a task board, and path leases (locks). A
non-coding **orchestrator** plans the work and keeps you informed. It asks you to approve the
plan before anyone codes, and it freezes the fleet whenever an agent is stuck and needs your
decision.

The full design is in [SPEC.md](SPEC.md).

```
hive-server (Flask/WSGI, e.g. PythonAnywhere)  ⇄  hived on each machine  ⇄  agent CLIs (MCP + hooks)
```

## Install

```sh
uv tool install --from . hive            # puts `hive` on PATH (needed by the CLI hooks/MCP config)
```

## 1. Run the server

**Locally (for development):**
```sh
hive server --admin-token <secret> --db sqlite:///hive.db --port 8765
```

**PythonAnywhere:**
1. Upload this repo.
2. Run `pip install --user '.[mysql]'`.
3. Create a MySQL database.
4. Create a manual WSGI web app whose WSGI file contains:
   ```python
   import os
   os.environ["HIVE_ADMIN_TOKEN"] = "<long random secret>"
   os.environ["HIVE_DATABASE_URL"] = "mysql+pymysql://USER:PASS@USER.mysql.pythonanywhere-services.com/USER$hive"
   # os.environ["HIVE_GITHUB_TOKEN"] = "..."   # optional: server advances main via GitHub API
   from hive.server.wsgi import application
   ```

The server needs no background workers. Lease expiry, scheduling, offline detection and
failover all run lazily on incoming requests. Daemons poll every few seconds, so WebSockets
aren't needed.

## 2. Wire your repository (once)

```sh
hive admin login --server https://you.pythonanywhere.com --token <secret>
cd your-repo && hive init
# edit hive.toml (review policy, checks, standby orchestrator, custom roles), then:
git add -A && git commit -m "Add Hive" && git push
hive admin push-config
```

`hive init` writes a Claude Code plugin (`.hive/agent/claude/`) and a Gemini extension
(`.hive/agent/gemini/`). Each contains the hive MCP server, hooks, and the
`hive-orchestrator` / `hive-worker` skills. They are **attached per session only**, when
`hive` launches a CLI (`--plugin-dir`, or the extension linked into the slot's private Gemini
home). Your own `claude` / `gemini` sessions never load Hive. Re-running `hive init` refreshes
the bundles and removes the auto-loaded wiring written by Hive 0.1.

## 3. Enroll agents and join machines

```sh
hive admin enroll claude-1 --vendor claude --roles orchestrator
hive admin enroll claude-2 --vendor claude --roles programmer
hive admin enroll gemini-1 --vendor gemini --roles reviewer,planner
```

On each machine, for each slot it hosts (one machine can host several accounts):

```sh
hive join claude-2 --vendor claude --token <token> --server <url> --repo <path-to-clone>
cd <worktree> && CLAUDE_CONFIG_DIR=~/.hive/accounts/claude-2 claude   # log this account in once
hive up                                                                # run the daemon
```

## 4. Run the fleet

`hive up` behaves differently depending on the slot's role:

- **Orchestrator machine:** `hive up` opens the native Claude Code or Gemini CLI in your
  terminal as the orchestrator. Talk to it like any session. When it's idle it waits in
  `await_events`, which uses no tokens and wakes it as soon as the fleet needs it; typing
  interrupts the wait.
- **Worker machines:** `hive up` runs agents headless and prints their live output.

```sh
hive start "Add OAuth login with GitHub and tests"   # orchestrator starts planning
hive decisions                                        # read the proposed plan
hive decide 1 approve                                 # coding starts only now
hive watch                                            # live output of all agents (admin only)
hive watch claude-2                                   # one agent
hive status                                           # agents, tasks, leases, status feed
hive decide 2 "Use the sandbox key in .env.example"   # unblock a frozen fleet
hive chat claude-2                                    # take over a worker interactively
```

In the orchestrator session you can also type `! hive decide 1 approve`. Decisions are always
answered by you, never by the orchestrator.

Live agent output is visible **only to the admin** (`hive watch`, and the dashboard's
live-output pane). The orchestrator gets **reports** instead: workers file them on their own,
or the orchestrator asks for one, and it cross-checks them against the repository.

The web dashboard at the server root `/` also shows decisions, the status feed, tasks and
leases, and has a chat box to the orchestrator.

## Roles

| Role | Required | Codes | Notes |
|---|---|---|---|
| orchestrator | exactly 1 | no | plans, assigns, monitors, reviews (fallback), talks to you; wakes on events only |
| programmer | ≥ 1 | yes | leases paths, integrates via `lease_release` |
| planner, reviewer, tester | optional | planner/reviewer no, tester `tests/**` | each falls back to orchestrator/programmer when absent |
| custom | optional | configurable | define under `[roles.<name>]` in `hive.toml` |

## Development

```sh
uv sync
uv run pytest -q
```

The end-to-end test (`tests/test_e2e.py`) starts a real server, a daemon, a bare git origin and
two worktrees. A scripted fake agent CLI (`tests/fake_agent.py`) stands in for Claude/Gemini.

## Verification status

**Verified locally:**
- The generated plugin passes `claude plugin validate` (one warning: no author).
- The generated extension passes `gemini extensions validate`.
- Linking the extension into a private `GEMINI_CLI_HOME` loads its context, MCP server and
  skills there and leaves `~/.gemini` untouched.
- Gemini's hook decision format matches its bundled docs.

**Not yet exercised with a live, logged-in session** (it would use account quota):
- Claude: `--plugin-dir` actually loading the plugin's hooks and MCP server, and the hive tool
  names under a plugin (`mcp__plugin_hive_hive__*`).
- Gemini: `--resume <uuid>` (Gemini documents `latest` or an index; override `command` per
  slot in `~/.hive/machine.toml` if needed) and the stream-json field names.
