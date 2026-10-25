---
title: Checking the whole setup, with a fix for every problem
keywords: check, checks setup, diagnose, troubleshoot, troubleshooting, problems, broken, not working, overview, drift
see: next, config, init
---
# `hive doctor`

```sh
hive doctor               # this repository
hive doctor ../myapp      # another one
hive doctor --offline     # without contacting the server or this computer's daemon
```

It describes the whole setup in one report:

- **the repository:** its branch, `origin`, the main branch, the git hook, and whether the
  committed `.hive/agent/` files match what this Hive would write;
- **the configuration:** the settings in force, `[hooks]` placed in `hive.toml` (which are
  ignored), a `hive.local.toml` committed to git (also ignored);
- **permissions:** the preset and how many entries each tier has, for the orchestrator and
  for workers;
- **roles:** every role, where its definition and prompt come from, and errors in `[roles]`;
- **prompt files:** your additions in `.hive/prompts/`, and problems in them;
- **this computer:** its slots, whether each agent's account is logged in, whether the daemon
  runs;
- **the server and fleet:** whether it answers, the fleet's state, its agents.

It ends with a list of **problems, each with the command that fixes it**. Run it whenever
something seems off. `hive overview` is the same command under its older name.
