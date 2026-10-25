# Running the server

The server is the **fleet**'s single source of truth (the fleet is the team of AI agents
working on your repository; see [How Hive works](ARCHITECTURE.md)). It stores:
- the agents and their roles;
- messages between agents and you;
- tasks, the units of work;
- [leases](GLOSSARY.md#lease), the locks agents take on files before editing them;
- [decisions](GLOSSARY.md#decision), questions only you can answer;
- live output, what each agent prints while it works.

It decides who gets which task and lease, and serves the web dashboard. Every
[daemon](GLOSSARY.md#daemon) (the program `hive up` runs on each computer with agents) and
every admin command (the commands you use to control the fleet) talks to it over HTTP(S).
This page covers what the server needs, how to run it for a trial, where to go for a public
deployment, and how to log in to it as admin.

There is exactly one server per fleet. One server can also host
[several projects](PROJECTS.md): separate repositories, each with its own fleet.

---

## What the server needs

- an **admin token**, which is your password; keep it secret;
- a **database URL**, saying where to store data. The default, `sqlite:///hive.db`, is a
  single file named `hive.db` in the current folder. For MySQL or Postgres it takes the form
  `mysql+pymysql://user:password@host/dbname` ([database URL](GLOSSARY.md#sqlite-mysql-postgres-database-url));
- optionally, a GitHub token, only for [GitHub mode](INTEGRATION.md#github-mode-optional), in
  which the server itself updates your repository's `main` branch on GitHub.

| Environment variable | Required | Meaning |
|---|---|---|
| `HIVE_ADMIN_TOKEN` | yes | Admin (your) token. Anyone holding it controls the fleet. |
| `HIVE_DATABASE_URL` | no (default `sqlite:///hive.db`) | SQLAlchemy URL: SQLite, MySQL (`mysql+pymysql://…`), or Postgres. |
| `HIVE_GITHUB_TOKEN` | no | Lets the server move `main` itself through the GitHub API ([GitHub mode](INTEGRATION.md#github-mode-optional)). |
| `HIVE_PROJECT_DATABASE_URL` | no | Where new projects' databases go; `{slug}` in it is replaced by the project's name ([Several projects on one server](PROJECTS.md)). |
| `HIVE_DATA_DIR` | no | Folder for project databases and archives (default: next to a SQLite database). |

These are environment variables: settings you give a program through its environment, e.g.
`export HIVE_ADMIN_TOKEN=…` in bash or `$env:HIVE_ADMIN_TOKEN = "…"` in PowerShell before
starting it. The full list, including SQLite's journal mode (how it writes the file safely)
and the folder for saved run histories, is in the
[configuration reference](CONFIGURATION.md#environment-variables).

**About the admin token.** It is the only credential that can answer decisions, start the
fleet, read agents' live output and enroll (create) agents. Generate a long random one, store
it like a password, and keep it off computers that only run worker agents (agents other than
the orchestrator you chat with) ([Security model](SECURITY.md)). Agents get their own, much
weaker, tokens when you enroll them ([Enrolling agents](MACHINES.md)).

---

## Locally (trials, single machine)

```sh
python -c "import secrets; print(secrets.token_urlsafe(32))"     # generate an admin token
hive server --admin-token <that token> --db sqlite:///hive.db --port 8765
```

| Option | Default | Meaning |
|---|---|---|
| `--host` | `127.0.0.1` | Network address to listen on. `127.0.0.1` accepts connections from this computer only; `0.0.0.0` also from other computers on your local network. |
| `--port` | `8765` | Port. |
| `--db` | `sqlite:///hive.db` | Database URL (see above). |
| `--sqlite-journal` | `wal` | How SQLite writes its file. Use `delete` or `truncate` if the file is on a network drive, where the default doesn't work reliably. |
| `--admin-token` | `HIVE_ADMIN_TOKEN` | The admin token, if it isn't in the environment. |

The first command prints a random token; use it as `<that token>` in the second. Keep the
terminal open: the server runs in the foreground until you press Ctrl+C. Open
`http://127.0.0.1:8765/` in a browser to see the [dashboard](MONITORING.md#the-web-dashboard),
and sign in with the token.

This uses the simple web server built into Flask (the Python web framework Hive's server is
written with): fine for trying Hive and for a local network. For anything reachable from the
internet, run it under a production web server, as [DEPLOYMENT.md](DEPLOYMENT.md) describes.

---

## Public deployment (PythonAnywhere, VPS, containers)

**➜ See [DEPLOYMENT.md](DEPLOYMENT.md)** for the full guide:
- choosing a host;
- **capacity planning** with measured storage and request-load numbers;
- step-by-step PythonAnywhere and VPS (gunicorn + Caddy + systemd) setups;
- upgrades, backups, data pruning, monitoring, hardening and troubleshooting.

In short, the server is a standard Python web app ([WSGI](GLOSSARY.md#wsgi);
the entry point is `hive.server.wsgi:application`) that needs no background processes.
On [PythonAnywhere](GLOSSARY.md#pythonanywhere), a Python web host, the paid **Developer**
plan with its included MySQL database has a wide margin for a 7-agent fleet: well under 1 GB
of data per busy year. The **free plan** works for trials and small fleets, using a SQLite
file in the mode for network drives
([DEPLOYMENT.md §4.8](DEPLOYMENT.md#48-free-beginner-plan-with-sqlite)).

Computers running agents contact the server every few seconds, and everything the agents
print is uploaded to it, so use **HTTPS** (encrypted) for anything reachable from the
internet.

---

## Log in as admin

On your computer:

```sh
hive login --server https://USER.pythonanywhere.com --token <admin token>
```

Use your server's address: `http://127.0.0.1:8765` for a local trial, or for example
`https://USER.pythonanywhere.com` on PythonAnywhere, where `USER` is your PythonAnywhere
username.

This checks the token against the server and saves it in `~/.hive/admin.toml` (user-only
permissions). Every user-side command (`hive start`, `hive status`, `hive approve`, …) uses
it, so you log in once per computer. `HIVE_SERVER_URL` and `HIVE_ADMIN_TOKEN` environment
variables override the file (and are never written to it).

`hive server` on your own computer logs you in to it the same way. If you are already
logged in to another server, that login stays your default: the local one is saved beside
it, and `hive login --server http://127.0.0.1:8765` switches.

If the server hosts [several projects](PROJECTS.md), each project has its own address,
`https://<server>/p/<project>`, and its own admin token. You can log in to just one of them:
`hive login --server https://…/p/acme --token <project admin token>`, where `acme` is
an example project name. That login is kept beside the server's, and used only for that
project.

---

**Next:** [Preparing the repository](REPOSITORY.md) ·
**See also:** [Several projects on one server](PROJECTS.md), [DEPLOYMENT.md](DEPLOYMENT.md) ·
[← Back to the README](../README.md)
