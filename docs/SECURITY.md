# Security model

Hive runs several AI agents on your computers that can run shell commands, sends what they
print to a Hive [server](SERVER.md), and lets them push to your git repository. This page
explains what protects what: which passwords (*tokens*) exist and what each can do, who can
see what, what the agents can and can't do, and what you should do to keep it that way. The
[glossary](GLOSSARY.md) explains the terms used here.

The short version:
- **The admin token is the key to everything.** It's the password you chose when starting
  the server. Keep it secret and off computers that only run worker agents (all agents except
  the orchestrator you chat with).
- **Agents can't act as you.** They can't answer [decisions](GLOSSARY.md#decision) (the
  questions only you may answer), start the fleet, or run Hive's commands meant for you.
- **Hard limits are enforced in code**: by the server, by [hooks](GLOSSARY.md#hook) (checks
  Hive runs inside each agent's CLI) and by git, not by asking the AI model nicely.
- **Anything an agent prints reaches your server.** Use HTTPS and keep real credentials out
  of the repository.

---

## Tokens

| Credential | Can do | Stored as |
|---|---|---|
| **Server admin token** (`HIVE_ADMIN_TOKEN`) | Everything, in every project: answer decisions, start/pause/reset the fleet, read agents' live output, create agents, manage projects. | an environment variable on the server; `~/.hive/admin.toml` on each computer where you ran `hive admin login` |
| **Project admin token** (when one server hosts [several projects](PROJECTS.md)) | The same, within one project only. Can't create or change projects. | on the server, only as a hash (a one-way fingerprint from which the token can't be recovered) |
| **Agent token** (never shown for agents set up on your computer or on a paired one; printed once by `hive agents add --elsewhere` and `hive admin enroll`) | Act as one agent, within its [roles](ROLES.md). | a hash on the server; `~/.hive/machine.toml` on the computer that runs the agent |
| **Pairing code** (`hive machines invite`) | Pair one computer with the project, once, within 15 minutes. | a hash on the server, deleted when used |
| **Machine token** (`hm_…`, made when a computer pairs) | List the agents placed on that computer and claim them, which gives it a new token for each. Refused by every other endpoint: it can't act as an agent or as you. Placing agents is the admin's. | a hash on the server; `~/.hive/machine.toml` on that computer |
| **Local secret** | Talk to the [daemon](GLOSSARY.md#daemon) (the background program `hive up` runs) through its local API, a small web service reachable only from the same computer (`127.0.0.1`). | `~/.hive/machine.toml` |
| **GitHub token** (optional) | Move `main` on GitHub ([GitHub mode](INTEGRATION.md#github-mode-optional)). | the server's environment only |

- Keep the server's admin token off agent machines unless you also work there.
- Agent tokens are shown once, if at all, and can be replaced by new ones (*rotated*, with
  `hive agents rotate`) if lost or leaked ([CLI.md §13](CLI.md#13-common-recipes)).
- Share a pairing code like a password. Unpair a computer you no longer trust with
  `hive machines rm NAME`: its token stops working, and so do the tokens of the agents placed
  on it. The agents stay on the server; placing them elsewhere gives them new tokens.
- The local API accepts connections only from the same computer, and needs that computer's
  secret.

---

## Who sees what

- Only the admin (you) can read live output (what agents print while working). Agent
  tokens, including the orchestrator's, are refused.
- Only the admin can answer decisions and start the fleet.
- Agents get only the tools their roles allow, enforced server-side
  ([How roles are enforced](ROLES.md#how-roles-are-enforced)).
- The output of a dangerous command run after approval is visible only to the agent that
  asked for it and to you ([Approval rules](PERMISSIONS.md#approval-rules)).

---

## Secrets in output

Agents' output is uploaded to your server; anything an agent prints, including the contents
of files it reads, ends up there. Use HTTPS, protect the admin token, and keep real credentials out of
the repository. Reports and status updates are instructed never to contain secrets.

Dashboard [archives](MONITORING.md#archives) (saved histories of past runs) contain the
recorded output too. Treat archive files like the database.

---

## Permissions

Each CLI has its own setting for how much it may do without asking a person (its
*permission mode*). Workers run without a terminal, so Hive starts them in a mode that allows
file edits but not arbitrary commands: Claude Code's `acceptEdits`, Gemini CLI's `auto_edit`,
or Codex's `workspace-write` sandbox (writes only inside the agent's own working folder). On
top of that come the permission files Hive generates
([Shell-command permissions](PERMISSIONS.md)):
- **File edits** are allowed without asking, but Hive's [lease](INTEGRATION.md#leases) checks
  still refuse edits to files the agent hasn't locked for itself first.
- **Shell commands** follow three tiers: allowed, approved by the orchestrator, or never.
  Anything else is refused.

If agents need more commands, allow them in the `[permissions]` section of `hive.toml`
rather than giving an agent the mode `bypassPermissions` or `yolo` (with
`hive join --permission-mode`). Those modes let the agent run any command on that computer
and skip the approval step; Hive's hook still blocks the never-allowed commands.

Rules about which commands may run aren't watertight on their own: a programming-language
interpreter such as `python` can run anything. The hard protections are the admin token, lease checks on integration, and the
server's and daemon's checks on approved commands
([Trust implications](PERMISSIONS.md#trust-implications)).

---

## Your prompt files

`.hive/prompts/` in the repository holds your additions to the agents' instructions. Agents
can't edit it or get changes to it into `main`
([Customising what agents are told](PROMPTS.md#agents-cant-change-these-files)), so an agent
can't rewrite its own instructions or another agent's. The same goes for `.hive/agent/`, which
holds Hive's generated tools, checks and instructions for each CLI.

---

## Prompt injection

*Prompt injection* is text that tries to talk an AI agent into doing something it shouldn't,
for example a message, or a comment in a file, saying "ignore your instructions and …".
Other agents' messages are marked as untrusted information, and the agents' written
procedures ([skills](GLOSSARY.md#skill)) say so explicitly.
Hive's hard limits (leases, roles, freeze, decisions) are enforced by the server and hooks,
not by the model's goodwill, so a convinced agent still can't break them.

---

## Checklist

- [ ] The server is reached over HTTPS (encrypted).
- [ ] The admin token is long, random, and only on the server and your own computer.
- [ ] Computers that run only workers have only their agent tokens.
- [ ] The repository holds no real passwords or keys.
- [ ] No agent runs with the permission mode `bypassPermissions`, `yolo` or
  `danger-full-access` unless it needs it.
- [ ] In GitHub mode, GitHub's branch protection lets only the server's token push to `main`.

[DEPLOYMENT.md §13](DEPLOYMENT.md#13-security-hardening) covers hardening the server itself.

---

**See also:** [Shell-command permissions](PERMISSIONS.md), [Roles](ROLES.md) ·
[← Back to the README](../README.md)
