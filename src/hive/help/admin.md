---
title: The precise low-level commands: hive admin
keywords: enroll, admin enroll, admin roles, push-config, backup, backups, low-level, precise, restore
see: agents, join, projects, config
---
# `hive admin`

The everyday commands (`hive agents`, `hive config`, …) do several steps at once. The
`hive admin` commands each do exactly one, for scripts and unusual setups:

```sh
hive admin enroll codex-1 --vendor codex --roles programmer,tester   # create an agent
hive admin roles claude-2 programmer,reviewer                        # replace its roles
hive admin push-config                       # upload hive.toml to the server now
hive admin backup                            # download the SQLite database
hive admin backup -o ~/backups/hive.db
hive admin login --server URL --token T      # the same as hive login
hive admin project …                         # several projects (hive help projects)
```

- **enroll** creates the agent on the server only and prints its **token once**, with the
  `hive join` line to run on the computer that will run it (`hive help join`). Names are 2 to
  32 lowercase letters, digits and hyphens, starting with a letter. `orchestrator` can't be
  combined with a role that codes. `hive agents add` does this and the `hive join` in one go.
- **roles** replaces an agent's roles, like `hive agents edit NAME --roles`. While the fleet
  is active, some changes become a decision for you to approve.
- **push-config** uploads `hive.toml` (over its preset) to the server. Admin commands run in
  your clone already do this when it changed; this does it explicitly, to the server the
  command targets.
- **backup** copies a SQLite database safely while the server keeps running, and downloads
  it (`--project NAME` for one project's). For MySQL or Postgres, use the database's own dump
  tool.
