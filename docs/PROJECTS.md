# Several projects on one server

One server can run several projects at once. Each project is a separate repository with its
own fleet, agents, tasks, decisions, config and archives. Use this when you want Hive working
on more than one codebase without paying for, or maintaining, more than one server. If you
only ever work on one repository, you can skip this page: a server starts with one project,
`default`, and everything else in this documentation works on it unchanged.

---

## How projects are separated

| | The default project | Any other project |
|---|---|---|
| URL (API and dashboard) | `https://host/` | `https://host/p/<project>/` |
| Database | `HIVE_DATABASE_URL` | its own (see below) |
| Admin token | the server's `HIVE_ADMIN_TOKEN` | its own, or the server's |

**Projects are fully separate.** Each project has its own database, so nothing in one
project's data refers to another, and isolation doesn't depend on filtering queries.
- **Agent tokens** work only in their own project, so the same agent name can exist in two
  projects.
- **A project's admin token** is admin there and nowhere else. You can hand it to someone who
  runs that project without giving them the rest of the server.
- **The server's admin token** works in every project, and is the only one that can create
  or change projects.
- **Existing servers:** a server that ran a single project before multi-project support keeps
  working unchanged as the `default` project.

**A project's URL is used wherever a server URL is.** Clients need no special handling: you
pass `https://host/p/acme` to `hive admin login`, `hive init` and `hive join` exactly as you
would pass `https://host`.

Project names follow the same rules as agent names (`^[a-z][a-z0-9-]{1,31}$`). `default`,
`api`, `admin` and `static` are reserved.

---

## Creating a project

With the server's admin token:

```sh
hive admin project create acme --name "Acme Corp"
```

This prints the project's URL and its admin token (shown once). Then use the project's URL
wherever you'd use the server's:

```sh
cd acme-repo
hive init --server https://host/p/acme      # hive.toml now names the project
hive admin push-config                      # inside the repo: acts on the project it names
hive admin enroll dev --vendor claude --roles programmer   # prints `hive join … --server https://host/p/acme`
```

From here on, setup is the same as for a single project: [enroll agents and join
machines](MACHINES.md), then [run the fleet](RUNNING.md).

---

## Which project an admin command acts on

1. `--project NAME` anywhere on the command line (or `HIVE_PROJECT`), on your admin server.
2. Otherwise, the project the current repository's `hive.toml` names (`[server] url = "…/p/NAME"`).
3. Otherwise, the server you logged in to (`default`).

So inside a project's repository, commands act on that project without any flag. Outside it,
pass `--project`. [CLI.md §10](CLI.md#10-several-projects-on-one-server) has examples.

---

## Where a project's database lives

- **SQLite server:** each project gets a SQLite file, `projects/<project>/hive.db`, next to
  the server's database (or under `HIVE_DATA_DIR`). Nothing to prepare.
- **MySQL or Postgres server:** create the project's database first. Then pass
  `--db-url mysql+pymysql://…`, or set `HIVE_PROJECT_DATABASE_URL` on the server with a
  `{slug}` placeholder (e.g. `mysql+pymysql://user:pw@host/user$hive_{slug}`), so that only
  the database has to be created. On PythonAnywhere, databases are created on the
  *Databases* tab.
- **Failure:** if the database can't be opened, the project isn't created.

Each project's dashboard archives live in `projects/<project>/hive-archives/` under the data
folder ([Files and directories](CONFIGURATION.md#files-and-directories)).

---

## Managing projects

These need the server's admin token:

| Command | Effect |
|---|---|
| `hive admin project list` | List the server's projects. |
| `hive admin project rotate NAME` | Give the project a new admin token; the old one stops working. |
| `hive admin project set NAME --name "…"` | Rename it. |
| `hive admin project set NAME --github-token-env VAR` | Use the token in the server's environment variable `VAR` for [GitHub mode](INTEGRATION.md#github-mode-optional). Hive stores only the variable's name. |
| `hive admin project set NAME --archive` / `--unarchive` | Stop or resume serving the project. Archiving keeps its data. |

---

## Machines with slots of several projects

A machine can run slots of several projects. Each slot remembers its own server or project
URL ([`hive join`](MACHINES.md#join-each-slot-on-the-machine-that-will-run-it)), and
`hive up` runs them all.

Slot names must be unique on a machine: if two projects both have a `dev`, run them on
different machines or enroll one under another name. `hive join` refuses to silently take
over a slot that belongs to another project; `--replace` moves it deliberately.

---

**Next:** [Preparing the repository](REPOSITORY.md) ·
**See also:** [DEPLOYMENT.md §15](DEPLOYMENT.md#15-hosting-several-projects),
[CLI.md §10](CLI.md#10-several-projects-on-one-server) ·
[← Back to the README](../README.md)
