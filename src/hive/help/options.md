---
title: Options every command accepts, and coloured output
keywords: global options, --version, version, --color, color, colour, colors, no_color, --help, -h
see: environment, projects
---
# Options for every command

```sh
hive --version                         # Hive's version
hive status --color never > status.txt
hive --project acme status             # --project may come anywhere on the line
hive agents add --help                 # one command's options
```

- `--version`: print Hive's version.
- `--color auto|always|never`: colour the output. `auto` (the default) colours only on a
  terminal. The environment can decide it too: `HIVE_COLOR` (`auto`, `always`, `never`),
  `NO_COLOR` (any value turns colour off), `FORCE_COLOR` (turns it on). Hive uses vivid
  24-bit colour where the terminal shows it, else 256 or 16 colours; `HIVE_COLOR_DEPTH`
  (`truecolor`, `256`, `16`) chooses.
- `--project NAME`: which project admin commands act on, when a server hosts several
  (`hive help projects`). Also the `HIVE_PROJECT` environment variable.
- `-h`, `--help`: the options of any command or subcommand.

## Getting help

- `hive help`: the common commands.
- `hive help TOPIC`: how something works, in detail, e.g. `hive help agents`,
  `hive help hooks`, `hive help permissions`. For a command it also lists its options.
- `hive help topics`: every topic.
- `hive help --all`: every command, including `hive admin …`.
- `hive`: the next step for your setup, or the chat with the team.
