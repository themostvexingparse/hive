---
title: Messaging the orchestrator
keywords: message, tell, #user, write to orchestrator, priority
see: chat, decisions
---
# `hive say`

```sh
hive say "Prioritise the API; the UI can wait until tomorrow"
```

It posts your message to `#user`, the channel between you and the orchestrator, with high
priority, which gets the orchestrator's attention within seconds, even while it waits for
news. It answers in `#user` too: the chat (`hive`) and the dashboard show its reply.

The chat is the same thing, interactively: every line you type there is a `hive say`.

Messages are for steering: priorities, extra detail, corrections. A question the team needs
answered before it can go on comes to you as a decision instead (`hive help decisions`).
