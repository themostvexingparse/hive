---
title: Several projects (repositories) on one server
keywords: project, multi-project, --project, HIVE_PROJECT, /p/, hp_, project token, admin project, archive project
see: server, login, admin
---
# Projects

One server can host several projects: separate repositories, each with its own fleet,
agents and database. A project lives at `https://host/p/NAME`; the server's own address is
the `default` project.

## Which project a command acts on

1. `--project NAME` (anywhere on the line), or `HIVE_PROJECT`;
2. else the project the current repository's `hive.toml` names (`[server] url = "…/p/NAME"`);
3. else the server you logged in to: its `default` project.

```sh
hive --project acme status
hive status --project acme                 # the same
HIVE_PROJECT=acme hive decisions
cd ~/code/acme-app && hive status          # hive.toml names acme
```

## Managing projects (the server's own admin token)

```sh
hive admin project create acme --name "Acme Corp"    # prints its URL and admin token
hive admin project list
hive admin project set acme --name "Acme Corporation"
hive admin project set acme --archive                # stop serving it; data is kept
hive admin project set acme --unarchive
hive admin project rotate acme                       # a new admin token
```

`create` can give the project its own database (`--db-url`) and a GitHub token for GitHub
mode (`--github-token-env`, the name of an environment variable on the server).

## Setting one up

```sh
cd ~/code/acme-app
hive init --server https://hive.example.com/p/acme
git add hive.toml .hive .gitignore && git commit -m "Set up Hive" && git push
hive agents add claude
hive agents add claude programmer
```

A project's admin token (`hp_…`) works only for that project: give it to someone to hand
them the project. They log in with `hive login --server https://host/p/acme --token hp_…`.
Its dashboard is at `https://host/p/acme/`.

One computer can run agents of several projects; `hive up` runs them all. Agent names must
be unique on a computer, so `hive join` refuses a second `dev` from another project.
