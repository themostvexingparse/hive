---
title: Logging in to a Hive server as its admin
keywords: admin login, log in, logout, admin.toml, credentials, HIVE_SERVER_URL, HIVE_ADMIN_TOKEN, switch server
see: server, projects, security
---
# `hive login`

Every admin command (`hive status`, `hive agents`, `hive approve`, …) needs a login to a
server. `hive server` logs in the computer it runs on by itself; for a server someone else
runs, or one on the internet, log in with `hive login`.

```sh
hive login --server https://hive.example.com          # asks for the token, hidden
hive login --server https://hive.example.com --token <admin token>   # in scripts
hive login --server https://hive.example.com/p/acme --token hp_…     # one project only
```

It checks the token against the server and saves it in `~/.hive/admin.toml`, readable only
by you. Without `--server` it asks, suggesting the server `hive.toml` names.
`hive admin login` is the same command.

- **Logins add up.** Logging in to a server makes it your default and keeps the previous
  login too. A project login (`…/p/NAME`, with that project's `hp_…` token) is kept beside
  the server's and used only for that project.
- **Which server a command uses:** `--project NAME` (or `HIVE_PROJECT`) on your default
  server; else the project this repository's `hive.toml` names; else your default server
  (`hive help projects`).
- **For scripts,** `HIVE_SERVER_URL` and `HIVE_ADMIN_TOKEN` override the file, and are never
  written to it:

```sh
HIVE_SERVER_URL=https://hive.example.com HIVE_ADMIN_TOKEN=… hive status
```

To log out, delete `~/.hive/admin.toml` (or the line for that server in it).
