# Security model

Hive runs several AI agents with shell access on your machines, sends their output to a
server, and lets them push to your repository. This page explains what protects what: which
credentials exist and what each can do, who can see what, what the agents can and can't do,
and what you should do to keep it that way.

The short version:
- **The admin token is the key to everything.** Keep it secret and off machines that host
  only workers.
- **Agents can't act as you.** They can't answer decisions, start the fleet, or run Hive's
  user commands.
- **Hard limits are enforced in code** (server, hooks, git), not by asking the model nicely.
- **Anything an agent prints reaches your server.** Use HTTPS and keep real credentials out
  of the repository.

---

## Tokens

| Credential | Can do | Stored as |
|---|---|---|
| **Server admin token** (`HIVE_ADMIN_TOKEN`) | Everything, in every project: answer decisions, start/pause/reset, read live output, enroll agents, manage projects. | an environment variable on the server; `~/.hive/admin.toml` where you logged in |
| **Project admin token** ([Several projects](PROJECTS.md)) | The same, within one project only. Can't create or change projects. | a hash on the server |
| **Agent token** | Act as one agent, within its roles. | a hash on the server; `~/.hive/machine.toml` on its machine |
| **Local secret** | Call the daemon's local API on `127.0.0.1`. | `~/.hive/machine.toml` |
| **GitHub token** (optional) | Move `main` ([GitHub mode](INTEGRATION.md#github-mode-optional)). | the server's environment only |

- Keep the server's admin token off agent machines unless you also work there.
- Agent tokens are shown once at enrollment and can be rotated.
- The local API binds to `127.0.0.1` and needs the per-machine secret.

---

## Who sees what

- Only the admin can read live output. Agent tokens, including the orchestrator's, are
  refused.
- Only the admin can answer decisions and start the fleet.
- Agents get only the tools their roles allow, enforced server-side
  ([How roles are enforced](ROLES.md#how-roles-are-enforced)).
- The output of an approved command is visible only to the agent that asked for it and to
  you ([Approval rules](PERMISSIONS.md#approval-rules)).

---

## Secrets in output

Worker output is uploaded to your server; anything an agent prints, including file contents
it reads, ends up there. Use HTTPS, protect the admin token, and keep real credentials out of
the repository. Reports and status updates are instructed never to contain secrets.

Dashboard archives contain the recorded output too. Treat archive files like the database.

---

## Permissions

Headless workers run in `acceptEdits` (Claude) or `auto_edit` (Gemini) mode, or Codex's
`workspace-write` sandbox, with the session permission files described in
[Shell-command permissions](PERMISSIONS.md).
- **File edits** are auto-approved, while lease enforcement still blocks edits outside leases.
- **Shell commands** follow the three tiers: allowed, approved by the orchestrator, or never.
  Anything else is refused.

Widen `[permissions]` rather than joining with `--permission-mode bypassPermissions` / `yolo`.
That flag lets the agent run any command on that machine and skips the approval step; Hive's
hook still blocks the never-allowed commands.

Pattern rules for shell commands aren't a security boundary on their own: an interpreter can
run anything. The hard protections are the admin token, lease checks on integration, and the
server's and daemon's checks on approved commands
([Trust implications](PERMISSIONS.md#trust-implications)).

---

## Your prompt files

Agents can't edit `.hive/prompts/` or get changes to it into `main`
([Customising what agents are told](PROMPTS.md#agents-cant-change-these-files)), so an agent
can't rewrite its own instructions or another agent's. The same goes for `.hive/agent/`.

---

## Prompt injection

Other agents' messages are marked as untrusted information, and the skills say so explicitly.
Hive's hard limits (leases, roles, freeze, decisions) are enforced by the server and hooks,
not by the model's goodwill, so a convinced agent still can't break them.

---

## Checklist

- [ ] The server is served over HTTPS.
- [ ] The admin token is long, random, and only on the server and your own computer.
- [ ] Worker machines have only their agent tokens.
- [ ] The repository holds no real credentials.
- [ ] No slot runs with `bypassPermissions`, `yolo` or `danger-full-access` unless it needs it.
- [ ] In GitHub mode, branch protection lets only the server's token push `main`.

[DEPLOYMENT.md §13](DEPLOYMENT.md#13-security-hardening) covers hardening the server itself.

---

**See also:** [Shell-command permissions](PERMISSIONS.md), [Roles](ROLES.md) ·
[← Back to the README](../README.md)
