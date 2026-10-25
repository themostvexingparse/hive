---
title: hive on its own: the next step, or the chat
keywords: hive, home, next step, what now, what next, where am i
see: getting-started, chat, doctor
---
# `hive` and `hive next`

`hive` with no command looks at your setup and tells you the one thing to do next, with the
command for it. It goes through the setup in order and stops at the first step missing:

- no admin login: start a server (`hive server`) or log in to one (`hive login`);
- not inside a clone of a git repository: `cd` into one;
- the repository has no `hive.toml`: `hive init`;
- the server isn't answering: start it, or `hive doctor`;
- the fleet has no orchestrator, or no programmer: `hive agents add …`;
- an agent on this computer isn't logged in: `hive agents login NAME`;
- this computer's agents aren't running: `hive up`;
- a decision waits for you: the commands that answer it;
- no goal yet: `hive start "GOAL"`.

Once the team is set up and running, `hive` in a terminal opens **the chat** with the team
instead (`hive help chat`). `hive next` always shows the next-step screen, also when the chat
would open; in a script, or with its output redirected, `hive` shows the screen too.

On a computer that is only paired with a project (it runs agents but you don't administer
the fleet from it), `hive` shows which project it is paired with, its agents, and whether
its agents need a login or `hive up`.

`hive` also uploads `hive.toml` when it changed, as every admin command does
(`hive help config`).
