---
title: Calling the server's API from scripts
keywords: script, scripts, scripting, endpoint, endpoints, json, http, rest, automation, curl
see: plugins, login, security
---
# `hive api`

`hive api METHOD PATH` calls the server's API with your admin login and prints the JSON it
answers. Use it for anything the commands don't cover, in scripts:

```sh
hive api GET /fleet
hive api GET /agents
hive api GET /tasks -f state=open                   # query parameters
hive api POST /messages -d '{"channel": "#user", "body": "Prioritise the API"}'
hive api PATCH /agents/claude-2 -d - < roles.json   # the body from standard input
```

- `METHOD`: `GET`, `POST`, `PUT`, `PATCH` or `DELETE`.
- `PATH`: relative to the API (`/fleet`, `/agents`, `/tasks/3`, `/decisions`, …); the
  endpoints are in `src/hive/server/app.py`.
- `-d JSON`: the request body; `-d -` reads it from standard input.
- `-f KEY=VALUE`: a query parameter; repeat it for several.

It acts on the project chosen as for every admin command (`--project`, `hive.toml`, your
default; `hive help projects`). An error from the server ends it with a message and a
non-zero exit code.

Agents may never run `hive api`: it would let them act as you.
