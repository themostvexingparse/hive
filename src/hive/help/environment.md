---
title: Environment variables and Hive's files
keywords: environment variables, env, variables, HIVE_HOME, HIVE_DATABASE_URL, machine.toml, server.toml, files, folders, ~/.hive
see: config, options, security
---
# Environment variables and files

## Environment variables

For admin commands:

- `HIVE_SERVER_URL`, `HIVE_ADMIN_TOKEN`: the server and admin token, overriding
  `~/.hive/admin.toml` (for scripts).
- `HIVE_PROJECT`: the project admin commands act on, like `--project`.
- `HIVE_HOME`: where Hive keeps its files on this computer (default `~/.hive`).
- `HIVE_COLOR`, `NO_COLOR`, `FORCE_COLOR`: colour on or off.

For the server (`hive server`, or a server deployed as a web app):

- `HIVE_ADMIN_TOKEN`: its admin token.
- `HIVE_DATABASE_URL`: its database.
- `HIVE_SQLITE_JOURNAL`: `wal` (default), or `delete`/`truncate` on network drives.
- `HIVE_GITHUB_TOKEN`: turns on GitHub mode (the server moves `main` through GitHub's API).
- `HIVE_DATA_DIR`, `HIVE_PROJECT_DATABASE_URL`, `HIVE_ARCHIVE_DIR`: where projects' databases
  and the dashboard's archives go.

Hive itself sets `HIVE_SLOT`, `HIVE_DAEMON_URL` and `HIVE_LOCAL_SECRET` for agents' CLIs, and
`CLAUDE_CONFIG_DIR`, `CODEX_HOME` or `GEMINI_CLI_HOME` so each agent uses its own account.

## Files on each computer (`~/.hive/`)

- `admin.toml`: your admin logins (`hive login`).
- `server.toml`: the admin token `hive server` made.
- `machine.toml`: this computer's slots (agents), their tokens, the servers it is paired
  with, and its daemon's local secret.
- `config.toml`: your own settings, in every repository (`hive config set … --user`).
- `accounts/NAME/`: each agent's private CLI settings folder, where its login lives.
- `bundles/NAME/`: the files Hive generates for an agent's sessions at each launch.
- `logs/daemon.log`: the daemon's log (`hive up -v` for more detail).
- `config-sync.json`: which `hive.toml` was last uploaded to which server.

## Files in the repository

- `hive.toml`: the fleet's settings (committed).
- `hive.local.toml`: this computer's own settings (git-ignored).
- `hive.fleet.toml`: the fleet as a file, if you use `hive plan` (`hive help fleet-file`).
- `.hive/agent/`, `.hive/prompts/`: written by `hive init` (`hive help init`).
