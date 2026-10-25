---
title: A first fleet on one computer, step by step
keywords: quickstart, quick start, tutorial, first run, begin, setup, set up, how to start, new
see: next, server, init, agents, up, start, chat
---
# Getting started

Hive turns several coding agents (Claude Code, Codex, Gemini CLI, agy) into one team that
works on one git repository. One agent, the **orchestrator**, plans and talks to you; the
others, the **workers**, write the code. You approve the plan and answer their questions.

You need git, a repository you can push to, and at least two logged-in accounts of an agent
CLI (one for the orchestrator, one for a programmer).

## The steps

```sh
hive server                          # 1. a local server; keep this terminal open
cd ~/code/myapp                      # 2. in another terminal, inside your repository
hive init
git add hive.toml .hive .gitignore && git commit -m "Set up Hive" && git push
hive agents add claude               # 3. the orchestrator (the first agent plans)
hive agents add claude programmer    #    and one programmer
hive up                              # 4. runs the agents; the orchestrator opens here
hive                                 # 5. in another terminal: the chat with the team
```

What each step does:

- **1.** `hive server` runs a server on this computer, makes an admin token, and logs this
  computer in with it (`hive help server`).
- **2.** `hive init` writes `hive.toml` (the project's Hive settings) and the files Hive
  attaches to agents. Commit and push them: every agent works in its own copy of the
  repository and gets them through git (`hive help init`).
- **3.** `hive agents add` creates each agent on the server, gives it its own working folder
  (a git worktree next to your clone) and offers to log its account in (`hive help agents`).
- **4.** `hive up` runs this computer's agents. Workers run in the background; the
  orchestrator opens in this terminal (`hive help up`).
- **5.** `hive` is the chat: type the goal, then answer the plan with `y` (approve) or `n`
  (send it back with a note) (`hive help chat`).

## When you're not sure what's next

Run `hive` on its own at any time. Until the team is set up, it shows the one next step and
the command for it; once the team runs, it opens the chat. `hive next` always shows the
step instead. `hive doctor` checks the whole setup and prints a fix for every problem.
