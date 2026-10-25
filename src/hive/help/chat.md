---
title: The chat with the team, and taking over one agent's session
keywords: talk, conversation, slash commands, slash, messages, attach, take over, interactive
see: next, decisions, say, watch, up
---
# The chat: `hive` and `hive chat`

Once the team exists and its agents run, `hive` (or `hive chat`) opens a chat with the
orchestrator in this terminal:

```
> Add CSV export to the reports page
? Start the team on this goal? [Y/n]
• the fleet is planning
claude-1: I'll split this into the exporter and the button; one question first…
┌ Decision #1 · start_approval · freezes the fleet
│ Approve the 2-task plan?
└ y = approve · n = send back with a note
> y
```

- **A line you type** goes to the orchestrator in `#user`, the channel between you and it,
  with high priority. Before there is a goal, a line becomes the goal once you confirm.
- **Its replies**, its status updates, and changes of the fleet's state appear as they
  happen.
- **Decisions** (questions only you answer) appear as prompts. For a yes/no decision, `y`
  approves and `n` asks what to change and sends it back. Other questions take
  `/answer TEXT`. Hive records the answer on your keypress, never the AI.
- **Slash commands are the hive commands:** `/agents add codex` runs
  `hive agents add codex`; `/status`, `/decisions`, `/plan`, `/config get …`, `/pause` and
  the rest work the same. They act on the project the chat shows (`--project` included).
- `/watch AGENT` follows one agent's live output inside the chat; `/watch off` stops it.
- `/help` lists the commands; `/quit` (or Ctrl+C) leaves. The team keeps working.

Commands that take over a terminal (`server`, `up`, `join`, `chat`) aren't run from the
chat: run them in their own terminal.

The chat works wherever the orchestrator runs: headless under `hive up --headless`, on
another computer, or interactively in the terminal of `hive up` (its own instructions tell it
to answer you in `#user`).

## Taking over one agent: `hive chat NAME`

```sh
hive chat claude-2            # open claude-2's own CLI here, continuing its conversation
hive chat claude-2 --fresh    # start a new conversation instead
```

This opens the agent's own CLI (Claude Code, Codex, …) in your terminal, in its worktree,
with its account. Use it to look over an agent's shoulder or to help it by hand. While you
are attached, Hive doesn't start that agent in the background. `hive up` must be running on
this computer, or Hive's tools and checks won't work inside the session.
