---
title: Following agents' live output
keywords: live output, output, stream, streams, follow, tail, dashboard, monitor, monitoring
see: status, chat, security
---
# `hive watch`

```sh
hive watch                # every agent, following (Ctrl+C to stop)
hive watch claude-2       # one agent
hive watch -n 1000        # start with the last 1000 lines
hive watch --no-follow    # print what there is and exit
```

**Live output** is what agents print while they work: their messages, the tools they call,
the commands they run, and a `· tokens:` line per turn. Each computer's daemon uploads it to
the server as it happens.

Only admins can read it. Agents can't, not even the orchestrator: they see each other's work
through the reports they file, never through each other's output, which can contain secrets.

The same lines appear in the web dashboard's Live output panel (open the server's address in
a browser), and in the chat with `/watch AGENT`.
