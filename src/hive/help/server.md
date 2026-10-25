---
title: Running a Hive server on this computer
keywords: local server, database, sqlite, mysql, postgres, port, host, network, lan
see: login, projects, security, admin
---
# `hive server`

The **server** is the fleet's single source of truth: agents, tasks, messages, file leases,
decisions, reports, live output and the web dashboard. Every computer that runs agents, and
every admin command, talks to it. `hive server` runs one on this computer, for a trial or a
local network. For a server on the internet, deploy Hive as a web app on a host instead
(docs/DEPLOYMENT.md).

```sh
hive server                                   # this computer only, port 8765, ./hive.db
hive server --host 0.0.0.0 --port 9000        # reachable from other computers
hive server --db sqlite:///C:/hive/hive.db    # the database file somewhere specific
hive server --db "mysql+pymysql://hive:secret@db.local/hive"
```

Keep its terminal open; Ctrl+C stops it. The dashboard is at the address it prints.

## The admin token

The admin token is the password for the whole fleet. `hive server` uses, in order:
`--admin-token`, the `HIVE_ADMIN_TOKEN` environment variable, or the token it saved in
`~/.hive/server.toml` (made up and saved the first time).

When the server listens on this computer, it also **logs this computer in** as admin, so
admin commands work straight away without `hive login`. If you were already logged in to
another server, that login stays your default; the local one is saved beside it, and
`hive login --server http://127.0.0.1:8765` makes it the default.

## Options

- `--host`: `127.0.0.1` (default) accepts only this computer; `0.0.0.0` other computers too
  (allow the port through the firewall). Pairing other computers needs this.
- `--port`: default `8765`.
- `--db`: where the data lives, as a database URL (default `sqlite:///hive.db` in the current
  folder). `HIVE_DATABASE_URL` overrides it. On Windows, write SQLite paths as
  `sqlite:///C:/path/hive.db`.
- `--sqlite-journal wal|delete|truncate`: how SQLite writes its file; use `delete` on a
  network drive.

One server can host several projects (separate repositories, each with its own fleet):
`hive help projects`.
