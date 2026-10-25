---
title: Tokens, who can do what, and keeping a fleet safe
keywords: token, tokens, secret, secrets, safety, safe, password, admin token, machine token, agent token, local secret, hm_, https, prompt injection, trust
see: permissions, login, machines, hooks
---
# Security

## Tokens and what each can do

- **The server's admin token:** everything, in every project: answer decisions, start and
  pause the fleet, read live output, create agents, manage projects. Kept on the server, and
  in `~/.hive/admin.toml` wherever you ran `hive login` (or `~/.hive/server.toml` for
  `hive server`).
- **A project's admin token (`hp_…`):** the same, for one project only.
- **An agent's token (`hv_…`):** act as that one agent, within its roles. Never shown for
  agents set up on your computer or on a paired one; printed once by
  `hive agents add --elsewhere` and `hive admin enroll`. Kept in `~/.hive/machine.toml` on the
  computer that runs it. `hive agents rotate NAME` replaces it.
- **A machine's token (`hm_…`):** made when a computer pairs. It can only list the agents
  placed on that computer and claim them; every other request is refused.
- **Pairing codes and join keys (`hk_…`):** pair computers; a code works once for 15
  minutes, a key until it expires or is revoked. Share them like passwords.
- **The local secret:** lets Hive's tools on a computer talk to that computer's daemon,
  which listens only on `127.0.0.1`.

## What agents can't do

- Answer your decisions, speak as you or reconfigure Hive: the `hive` commands that act as
  you are refused for agents, even on a computer where you are logged in as admin
  (`hive help permissions`).
- Read each other's live output, or approved commands' output (only you and the requester).
- Edit Hive's own folder (`~/.hive`), except the notes their CLI keeps in its own account
  folder; edit another agent's worktree or your clone; edit `.hive/agent/` or
  `.hive/prompts/`.
- Push to `main` except through `lease_release`, or work in a fleet state their role isn't
  allowed to act in.

These limits are enforced by the server, by Hive's checks inside each CLI and by the git
hook, not by asking the AI nicely. Hooks on fleet events run only from your own computer's
files, never from the repository (`hive help hooks`).

## A short checklist

- Use HTTPS for any server reachable from the internet.
- Give each agent its own CLI account; never share your own.
- Prefer adding commands to `[permissions] allow` over `bypassPermissions` or `yolo` modes.
- Treat what agents read (issues, web pages, dependencies) as untrusted: it can contain
  instructions (prompt injection). Reviews (`hive help review`) and the approval tier limit
  what such text can make an agent do.
