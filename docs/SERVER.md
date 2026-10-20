# Running the server

The server is the fleet's single source of truth. It stores agents, messages, tasks, leases,
decisions and live output, decides who gets which task and lease, and serves the web
dashboard. Every daemon and every admin command talks to it over HTTP(S). This page covers
what the server needs, how to run it for a trial, where to go for a public deployment, and
how to log in to it as admin.

There is exactly one server per fleet. One server can also host
[several projects](PROJECTS.md), each with its own fleet.

---

## What the server needs

- an **admin token**, which is your password; keep it secret;
- a **database URL**;
- optionally, a GitHub token (see [GitHub mode](INTEGRATION.md#github-mode-optional)).

| Environment variable | Required | Meaning |
|---|---|---|
| `HIVE_ADMIN_TOKEN` | yes | Admin (your) token. Anyone holding it controls the fleet. |
| `HIVE_DATABASE_URL` | no (default `sqlite:///hive.db`) | SQLAlchemy URL: SQLite, MySQL (`mysql+pymysql://…`), or Postgres. |
| `HIVE_GITHUB_TOKEN` | no | Lets the server move `main` itself through the GitHub API ([GitHub mode](INTEGRATION.md#github-mode-optional)). |
| `HIVE_PROJECT_DATABASE_URL` | no | Where new projects' databases go, with a `{slug}` placeholder ([Several projects on one server](PROJECTS.md)). |
| `HIVE_DATA_DIR` | no | Folder for project databases and archives (default: next to a SQLite database). |

The full list, including SQLite journal mode and the archive folder, is in the
[configuration reference](CONFIGURATION.md#environment-variables).

**About the admin token.** It is the only credential that can answer decisions, start the
fleet, read agents' live output and enroll agents. Generate a long random one, store it like
a password, and keep it off machines that host only worker agents
([Security model](SECURITY.md)). Agents get their own, much weaker, tokens when you enroll
them.

---

## Locally (trials, single machine)

```sh
python -c "import secrets; print(secrets.token_urlsafe(32))"     # generate an admin token
hive server --admin-token <that token> --db sqlite:///hive.db --port 8765
```

| Option | Default | Meaning |
|---|---|---|
| `--host` | `127.0.0.1` | Interface to listen on. Use `0.0.0.0` to accept connections from your LAN. |
| `--port` | `8765` | Port. |
| `--db` | `sqlite:///hive.db` | Database URL. |
| `--sqlite-journal` | `wal` | `delete` or `truncate` for SQLite files on network filesystems. |
| `--admin-token` | `HIVE_ADMIN_TOKEN` | The admin token, if it isn't in the environment. |

Keep the terminal open: the server runs in the foreground. Open `http://127.0.0.1:8765/` in a
browser to see the [dashboard](MONITORING.md#the-web-dashboard).

This is Flask's built-in server: fine for trying Hive and for a LAN. Use a proper WSGI server
for anything public.

---

## Public deployment (PythonAnywhere, VPS, containers)

**➜ See [DEPLOYMENT.md](DEPLOYMENT.md)** for the full guide:
- choosing a host;
- **capacity planning** with measured storage and request-load numbers;
- step-by-step PythonAnywhere and VPS (gunicorn + Caddy + systemd) setups;
- upgrades, backups, data pruning, monitoring, hardening and troubleshooting.

In short, the server is a WSGI app (`hive.server.wsgi:application`) that needs no background
workers. A PythonAnywhere **Developer** plan with its included MySQL has a wide margin for a
7-agent fleet: well under 1 GB of data per busy year. The **free plan** works for trials and
small fleets, using a SQLite file in Hive's network-filesystem mode
([DEPLOYMENT.md §4.8](DEPLOYMENT.md#48-free-beginner-plan-with-sqlite)).

Agent machines poll the server every few seconds, and worker output is uploaded to it, so
use **HTTPS** for anything reachable from the internet.

---

## Log in as admin

On your computer:

```sh
hive admin login --server https://USER.pythonanywhere.com --token <admin token>
```

This checks the token against the server and saves it in `~/.hive/admin.toml` (user-only
permissions). Every user-side command (`hive start`, `hive status`, `hive decide`, …) uses
it, so you log in once per computer. `HIVE_SERVER_URL` and `HIVE_ADMIN_TOKEN` environment
variables override the file.

You can also log in to a single project with its own admin token
([Several projects on one server](PROJECTS.md)):
`hive admin login --server https://…/p/acme --token <project admin token>`. That login is
kept beside the server's, and used only for that project.

---

**Next:** [Preparing the repository](REPOSITORY.md) ·
**See also:** [Several projects on one server](PROJECTS.md), [DEPLOYMENT.md](DEPLOYMENT.md) ·
[← Back to the README](../README.md)
