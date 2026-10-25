---
title: The fleet as a file: hive plan, hive apply, hive agents export
keywords: plan, apply, export, hive.fleet.toml, fleet file, declarative, prune, infrastructure as code, rebuild
see: agents, machines
---
# `hive plan` and `hive apply`

For a fleet you want to keep the same, or rebuild elsewhere, describe it in
`hive.fleet.toml` next to `hive.toml` (or name another file on the command line):

```toml
[[agents]]
name = "orch"
vendor = "claude"
role = "orchestrator"          # no `on`: the computer you run hive apply on

[[agents]]
vendor = "claude"
roles = ["programmer", "tester"]
count = 3                      # claude-1, claude-2, claude-3
on = "desk"                    # a paired computer (hive help machines)
model = "<model>"              # optional, as hive agents edit sets them
permission_mode = "acceptEdits"

[[agents]]
name = "rev"
vendor = "gemini"
role = "reviewer"
on = "elsewhere"               # it exists; Hive leaves where it runs alone
```

```sh
hive agents export > hive.fleet.toml     # start from the fleet as it is now
hive plan                                # what applying the file would change
hive apply                               # make those changes (asks first; --yes in scripts)
hive apply --prune                       # also remove agents the file doesn't list
```

- **Names:** without `name`, agents are numbered `<vendor>-<n>` through the file in order;
  with a `name` and a `count`, `<name>-1`, `<name>-2`, ….
- **What `plan` lists:** agents to create, roles to change, agents to move (between paired
  computers and this one), slot settings to change and, with `--prune`, agents to remove.
- Agents the file doesn't list are kept unless you add `--prune`.
- An agent's CLI can't change: rename it in the file to replace it.

The server stays the source of truth: `hive agents add` and the rest keep working beside the
file, and `hive plan` shows any difference they made.
