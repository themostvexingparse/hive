---
title: Answering the team's questions: decisions, approve, reject, answer
keywords: decision, approve, reject, answer, decide, plan approval, start approval, question, questions, stuck, yes, no
see: chat, start, lifecycle
---
# Decisions

A **decision** is a question only you can answer. The orchestrator raises them; agents never
answer them, and can't run the commands below. Kinds:

- **start_approval**: approve the plan before anyone codes;
- **stuck**: an agent is stuck on something only you can resolve. The whole fleet
  **freezes** until you answer, so nobody builds on a guess;
- **role_change**: a change to required roles, for you to approve;
- **question**: anything else; it never freezes the fleet.

## Seeing and answering them

```sh
hive decisions                    # open and queued decisions, with ready answer commands
hive decisions --all              # also the ones already answered
hive approve                      # the plan, or the yes/no question waiting
hive approve "go ahead, but keep the old endpoint"     # with a note
hive reject "Split the migration into its own task first"
hive answer "Use PostgreSQL; we already run it"        # any other question
hive approve 3                    # when several are open: its number
```

- With one decision waiting no number is needed. With several, give the number
  `hive decisions` shows, or pick one from the list in a terminal.
- `approve` and `reject` refuse a decision that isn't a yes/no question, and show how to
  answer it. `reject` without a note asks for one in a terminal.
- Rejecting the plan sends the fleet back to planning with your note; the orchestrator
  revises and asks again.
- In the chat (`hive`), a waiting decision appears as a prompt: `y` approves, `n` sends it
  back with a note, `/answer TEXT` answers anything else.
- In the orchestrator's own terminal you can answer without leaving it: `! hive approve`
  (in Claude Code and Gemini CLI, `!` runs a shell command).
- The web dashboard's Decisions panel answers them too.

`hive decide ID ANSWER [--note TEXT]` is the precise form, for scripts:

```sh
hive decide 3 approve
hive decide 3 reject --note "Split the migration into its own task first"
hive decide 7 "Use PostgreSQL; we already run it"
```

Only one freezing decision is open at a time; others queue and open in order.
