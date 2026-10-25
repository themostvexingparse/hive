# Development

This page is for working on Hive itself: running the tests, finding your way around the
source, and the documents that record its design and reviews. If you're new to how Hive's
parts fit together (server, daemon, agents), read [How Hive works](ARCHITECTURE.md) first;
the [glossary](GLOSSARY.md) explains the terms.

---

## Setting up

In a clone of Hive's source, with [uv](GLOSSARY.md#uv) installed:

```sh
uv sync                      # create a private Python environment with Hive and its dependencies
uv run pytest -q             # run the test suite (pytest)
uv run hive --version        # run the development copy, without installing it
```

To try a change with real agents, re-install it on each computer with the installer scripts
([Installation](INSTALLATION.md)); `install.bat /test` or `install.sh --test` runs the suite
first and installs only if it passes.

---

## Tests

| Test file | Covers |
|---|---|
| `tests/test_server.py` | roles and fleet validation, the approval gate, scheduling, leases and fencing, freeze and thaw, message audiences, reviews, role-change decisions |
| `tests/test_e2e.py` | a real server, daemon, bare git origin and two worktrees. A scripted fake agent CLI (`tests/fake_agent.py`) plans, gets approved, is denied an unleased write, leases, integrates into `main`, completes, reports and streams output. Also covers an interactive orchestrator in `await_events` and the freeze on a stuck agent. |
| `tests/test_init_and_launch.py` | bundle generation and legacy cleanup, launch commands, tool-name matching, stream formatting |
| `tests/test_codex.py` | Codex CLI support: bundle, launch commands, the slot's private `CODEX_HOME`, output, hooks |
| `tests/test_agy.py` | Antigravity CLI (agy) support: bundle, the slot's private home, launch commands, output and token counts, hooks, the daemon's approvals for agy, the 3-minute limit on MCP calls. Events and hook payloads are recorded from agy 1.4.4. |
| `tests/test_codex_e2e.py` | end to end with the real Codex CLI and a scripted model server; runs only when `codex` is installed |
| `tests/test_gitops.py` | git status parsing and the lease release transaction against a real git repository |
| `tests/test_permissions.py` | shell-command tiers and the generated permission files |
| `tests/test_shells.py` | running commands in bash, PowerShell and cmd.exe |
| `tests/test_prompts.py` | project prompt files in `.hive/prompts/`, and that agents can't change them |
| `tests/test_join.py` | `hive join`: each slot keeps the server it was joined to |
| `tests/test_overview.py` | `hive overview`, offline and against a live server |
| `tests/test_projects.py` | several projects on one server: routing, isolation, the two kinds of admin token |
| `tests/test_archives.py` | archiving a fleet's history, clearing it, and reading archives back |
| `tests/test_sqlite.py` | SQLite deployment, including the PythonAnywhere free-tier setup |
| `tests/test_daemon.py` | daemon and CLI robustness |
| `tests/test_ui.py` | coloured terminal output |

The end-to-end tests run the real server, daemon and git, and use `tests/fake_agent.py`, a
scripted stand-in for an AI coding CLI, so they need no accounts and use none of an
account's allowance.

---

## Source layout

| Path | Contents |
|---|---|
| `src/hive/server/` | Flask app (`app.py`), business logic (`service.py`), schema (`db.py`), project routing (`router.py`, `projects.py`), archives, the dashboard (`dashboard.html`) |
| `src/hive/daemon/` | daemon core, local API, git operations (`gitops.py`), CLI launching and stream formatting (`wake.py`), shells, local state |
| `src/hive/mcp_server.py`, `hooks.py`, `local.py` | the pieces the agent CLIs run |
| `src/hive/init.py`, `prompts.py` | bundles, skills and project prompt files |
| `src/hive/permissions.py` | shell-command parsing and the permission tiers |
| `src/hive/overview.py` | `hive overview` |
| `src/hive/cli.py` | the `hive` command |
| `src/hive/help/`, `helpdocs.py` | the pages `hive help TOPIC` shows (below), and finding and showing them |
| `src/hive/roles.py`, `globs.py`, `config.py`, `ui.py` | shared logic and terminal output |
| `scripts/simulate_load.py` | storage and request-cost simulation behind [DEPLOYMENT.md §3](DEPLOYMENT.md#3-capacity-planning-how-much-data-and-load-to-expect) |
| `scripts/audit/` | probes used by the code audits |

### Help pages (`hive help TOPIC`)

Each file in `src/hive/help/` is one page, `NAME.md`, shown by `hive help NAME`. They ship
inside the package, so editing a page needs no code change. A page starts with a header:

```markdown
---
title: Hooks: run your own commands when the fleet does something
keywords: hook, notify, on_decision
see: config, up
---
# Hooks on fleet events
…
```

- `title` is its line in `hive help topics`; `keywords` are other words that find it (each
  keyword on one page only); `see` names related pages, listed at the end.
- The body is Markdown: `#`/`##` headings, ```` ``` ```` code blocks, `- ` lists, `` `code` ``
  and `**bold**` are styled in the terminal; keep lines under 92 characters.
- `hive help WORDS` finds the page named `WORDS` (spaces as hyphens), else the page with
  that keyword, else the page of the first word (`agents add` → `agents`), else the pages
  that mention it. When the words are a command, its options follow the page.
- `tests/test_help.py` checks every page: its header, its links, the commands it shows, and
  that no keyword is on two pages.

---

## Design and review documents

| Document | Contents |
|---|---|
| [SPEC.md](../SPEC.md) | The design rationale and full specification: server API, state machine, protocols. Source code docstrings refer to its sections (`SPEC §13`). |
| [AUDIT.md](AUDIT.md) | A code audit of the whole codebase (2026-10-20). |
| [MULTI_PROJECT_AUDIT.md](MULTI_PROJECT_AUDIT.md) | The audit behind several projects on one server. |

---

[← Back to the README](../README.md)
