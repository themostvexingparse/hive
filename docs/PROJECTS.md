# Several projects on one server

One Hive [server](SERVER.md) can run several projects at once. A **project** is one
repository together with everything Hive keeps for it: its **fleet** (the team of AI agents
working on it), their tasks, the [decisions](GLOSSARY.md#decision) waiting for you, its
uploaded settings (`hive.toml`) and its [archives](GLOSSARY.md#archive) (saved histories of
past runs). Use this when you want Hive working on more than one codebase without paying for,
or maintaining, more than one server. If you only ever work on one repository, you can skip
this page: a server starts with one project, `default`, and everything else in this
documentation works on it unchanged.

**The example on this page:** a company project called `acme` ("Acme Corp", the traditional
made-up company name), whose repository is cloned in a folder `acme-repo`, with one
programmer agent called `dev`. `host` stands for your server's address, e.g.
`hive.example.com` or `USER.pythonanywhere.com`. Replace all of these with your own.

---

## How projects are separated

| | The default project | Any other project |
|---|---|---|
| Address (for commands and the dashboard) | `https://host/` | `https://host/p/<project>/`, e.g. `https://host/p/acme/` |
| Database | the server's own (`HIVE_DATABASE_URL`) | its own (see below) |
| Admin token (the password for admin commands and the dashboard) | the server's (`HIVE_ADMIN_TOKEN`) | its own, or the server's |

**Projects are fully separate.** Each project has its own database, so nothing in one
project's data refers to another, and one project can't see another's data even through a
bug in a query.
- **Agent tokens** (the password each agent gets when you create it with
  `hive admin enroll`) work only in their own project, so the same agent name can exist in
  two projects.
- **A project's admin token** is admin there and nowhere else. You can hand it to someone who
  runs that project without giving them the rest of the server.
- **The server's admin token** works in every project, and is the only one that can create
  or change projects.
- **Existing servers:** a server that ran a single project before multi-project support keeps
  working unchanged as the `default` project.

**A project's address is used wherever a server address is.** You pass
`https://host/p/acme` to `hive admin login` (save your admin login), `hive init` (prepare the
repository) and `hive join` (add an agent to a computer) exactly as you would pass
`https://host`.

Project names (also called *slugs*, since they appear in addresses) follow the same rules as
agent names: 2 to 32 characters, lowercase letters, digits and hyphens, starting with a
letter (`^[a-z][a-z0-9-]{1,31}$`). `default`, `api`, `admin` and `static` are reserved.

---

## Creating a project

With the server's admin token:

```sh
hive admin project create acme --name "Acme Corp"
```

`acme` is the project's name in addresses; `--name` is a human-readable title. This prints
the project's address and its admin token (shown only once, so copy it). Then use the
project's address wherever you'd use the server's:

```sh
cd acme-repo
hive init --server https://host/p/acme      # hive.toml now names the project
hive admin push-config                      # inside the repo: acts on the project it names
hive admin enroll dev --vendor claude --roles programmer   # prints `hive join … --server https://host/p/acme`
```

- `hive init` prepares the repository for Hive and writes its settings file, `hive.toml`,
  which records the project's address ([Preparing the repository](REPOSITORY.md)).
- `hive admin push-config` uploads `hive.toml` to the project on the server.
- `hive admin enroll` creates an agent (here a Claude Code programmer called `dev`) and
  prints its token plus the `hive join` command to run on the computer that will host it.

From here on, setup is the same as for a single project: [enroll agents and join
machines](MACHINES.md), then [run the fleet](RUNNING.md).

---

## Which project an admin command acts on

Admin commands are the `hive` commands you use to control a fleet (`hive admin …`,
`hive start`, `hive status`, `hive decide`, …). They pick their project in this order:
1. `--project NAME` anywhere on the command line (or the `HIVE_PROJECT` environment
   variable), on the server you logged in to with `hive admin login`.
2. Otherwise, the project named by the `hive.toml` of the repository you're in (its
   `[server]` section has `url = "…/p/NAME"`).
3. Otherwise, the server you logged in to (its `default` project).

So inside a project's repository, commands act on that project without any flag. Outside it,
pass `--project`. [CLI.md §10](CLI.md#10-several-projects-on-one-server) has examples.

---

## Where a project's database lives

It depends on which database the server itself uses ([database URL](GLOSSARY.md#sqlite-mysql-postgres-database-url)):
- **SQLite** (a single file, the default): each project gets its own SQLite file,
  `projects/<project>/hive.db`, next to the server's database file (or under the folder named
  by the `HIVE_DATA_DIR` environment variable). Nothing to prepare.
- **MySQL or Postgres** (database servers): create an empty database for the project first,
  with your database's own tools. Then either pass its address to
  `hive admin project create … --db-url mysql+pymysql://…`, or set the
  `HIVE_PROJECT_DATABASE_URL` environment variable on the server to an address containing
  `{slug}`, which Hive replaces with the project's name (e.g.
  `mysql+pymysql://user:pw@host/user$hive_{slug}` gives `user$hive_acme`). Then only the
  database has to be created. On [PythonAnywhere](GLOSSARY.md#pythonanywhere), databases are
  created on the *Databases* tab.
- **Failure:** if the database can't be opened, the project isn't created.

Each project's [archives](MONITORING.md#archives) (saved run histories, made from the
dashboard) live in `projects/<project>/hive-archives/` in the same folder
([Files and directories](CONFIGURATION.md#files-and-directories)).

---

## Managing projects

These need the server's own admin token, not a project's:

| Command | Effect |
|---|---|
| `hive admin project list` | List the server's projects. |
| `hive admin project rotate NAME` | Give the project a new admin token; the old one stops working. |
| `hive admin project set NAME --name "…"` | Rename it. |
| `hive admin project set NAME --github-token-env VAR` | For [GitHub mode](INTEGRATION.md#github-mode-optional) (the server updates the repository's `main` branch on GitHub itself): use the [GitHub token](GLOSSARY.md#github-token) stored in the server's environment variable `VAR`. Hive stores only the variable's name, never the token. |
| `hive admin project set NAME --archive` / `--unarchive` | Stop or resume serving the project. Archiving keeps its data. |

---

## Machines with slots of several projects

On a computer, each agent lives in a **slot**: its own working folder of the repository and
its own logged-in CLI account, created by
[`hive join`](MACHINES.md#join-each-slot-on-the-machine-that-will-run-it). A computer can run
slots of several projects. Each slot remembers its own server or project address, and
`hive up` (which runs a computer's agents) runs them all.

Slot names must be unique on a computer: if two projects both have an agent called `dev`, run
them on different computers or enroll one under another name. `hive join` refuses to silently
take over a slot that belongs to another project; `hive join … --replace` moves it to the new
project deliberately.

---

**Next:** [Preparing the repository](REPOSITORY.md) ·
**See also:** [DEPLOYMENT.md §15](DEPLOYMENT.md#15-hosting-several-projects),
[CLI.md §10](CLI.md#10-several-projects-on-one-server) ·
[← Back to the README](../README.md)
