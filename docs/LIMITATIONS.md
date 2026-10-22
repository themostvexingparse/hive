# Known limitations and verification status

Hive's agents are the vendors' own command-line programs (CLIs): Claude Code, Codex, the
Antigravity CLI (agy) and Gemini CLI. Hive drives them through their documented features, and those CLIs change often. This
page records which of those features have been verified, against which versions and how,
which haven't been tried yet, and the limitations you should know about today.

The first sections are a technical record. The options and parts they mention are explained
elsewhere: how Hive starts each CLI, with which options, in
[Running the fleet](RUNNING.md#how-an-agent-is-launched-for-a-headless-turn-wake); the
plugin, extension and profile (Hive's package for each CLI) in
[Preparing the repository](REPOSITORY.md#what-it-creates); the MCP server (Hive's tools) and
hooks (Hive's automatic checks inside the CLI) in [What the agents see](AGENT_INTERFACE.md);
and the `.rules` files in [Shell-command permissions](PERMISSIONS.md). If you only want to
know what doesn't work yet, skip to [Current limitations](#current-limitations).

---

## Verified against the installed CLIs (Claude Code 2.1.x, Gemini CLI 0.41)

- `--plugin-dir`, `--settings`, `--session-id`, `--resume` and the stream-json flags exist.
- The generated plugin passes `claude plugin validate`.
- The generated extension passes `gemini extensions validate`.
- Linking the extension into a private `GEMINI_CLI_HOME` loads its context, MCP server and
  skills there and leaves `~/.gemini` untouched.
- Gemini's hook decision format matches its bundled documentation.

## Verified with Codex CLI 0.163

Driven end to end by an automated test (`tests/test_codex_e2e.py`, which runs when `codex` is
installed) in which a scripted stand-in replaces the real AI model, so no account is
needed:
- `codex exec --json` and `exec resume <id>` with `-p hive`; the JSONL events Hive formats;
  the session id from `thread.started`.
- The `hive` MCP server starts from the profile, receives the forwarded `HIVE_*` variables,
  and its tools arrive as `mcp__hive__<tool>` without approval prompts.
- The hooks run with `--dangerously-bypass-hook-trust`; PreToolUse denials stop `apply_patch`
  and shell commands, and SessionStart context reaches the model.
- The generated `.rules` pass `codex execpolicy check`; skills in `CODEX_HOME/skills/` are
  listed to the model.
- A Codex programmer is refused an unleased write, leases, writes, integrates with
  `lease_release` and completes its task.

## Verified with Antigravity CLI (agy) 1.4.4 on Windows

Run live with a logged-in personal account and a real model (not an automated test: agy's
model can't be replaced by a stand-in). A Hive server and daemon, a scripted orchestrator and an
agy programmer:
- the plugin copied into the slot's home folder loads its hooks, MCP server and skills, and
  `~/.gemini` stays untouched; the MCP server and hooks get the `HIVE_*` variables;
- the programmer is woken with the prompt on stdin (`--input-format stream-json`), leases,
  writes with `write_file`, integrates with `lease_release` and completes its task; its live
  output and the `· tokens:` line are formatted from agy's events;
- a command outside the allowed tier is refused by Hive's hook and the turn continues;
- the next wake resumes the same conversation with `--conversation <id>` and remembers the
  earlier work;
- separately: `auto_approve` and `deny` from a PreToolUse hook, `SessionStart` and
  `PreInvocation` context, `Stop` with `continue`, and the 3-minute limit on MCP calls.

Not yet tried: the interactive orchestrator on agy (`agy -i`, reading the conversation id back
afterwards), usage-limit messages, macOS and Linux.

## Not yet exercised with a live, logged-in session

These need a real, logged-in account, and would use up some of its allowance:
- Claude: that `--plugin-dir` loads the plugin's hooks and MCP server, and the tool name prefix
  `mcp__plugin_hive_hive__`.
- Gemini: `--resume <uuid>` (Gemini documents `latest` or an index; see
  [Custom headless command](CONFIGURATION.md#custom-headless-command)) and the exact
  stream-json field names.
- Gemini: Hive's tool schemas. Gemini's function calling is reported to reject both `anyOf`
  and type lists (`["string", "null"]`), the two usual ways to write an optional argument, so
  Gemini slots get plain types with optional arguments simply not required. Not yet
  confirmed with a live Gemini session.
- Whether Claude Code and Gemini CLI pass `"source": "resume"` to the SessionStart hook
  (Hive then skips re-sending the role prompt; without it, it sends it as before), and the
  token-usage fields the `· tokens:` line reads.
- Codex with a real model and account: the interactive orchestrator (`codex resume <id>`) and
  usage-limit messages.
- [GitHub mode](INTEGRATION.md#github-mode-optional).
- The simpler interface ([CLI_REDESIGN.md](CLI_REDESIGN.md)), tested with in-process servers
  and simulated computers only so far:
  - `hive agents login`, which opens each CLI against the agent's settings folder (the login
    screens themselves weren't driven);
  - Hive's files generated at launch (`~/.hive/bundles/<slot>/`), loaded by real sessions of
    each CLI;
  - Gemini: re-linking an extension that a slot had linked to the committed
    `.hive/agent/gemini` folder (Hive reads the link's source from Gemini's
    `.gemini-extension-install.json`, as Gemini CLI 0.41 writes it);
  - pairing two real computers over a network.

---

## Gemini CLI and Antigravity CLI

Google replaced Gemini CLI with the Antigravity CLI (`agy`) in 2026
([announcement](https://developers.googleblog.com/an-important-update-transitioning-gemini-cli-to-antigravity-cli/)):
since 18 June 2026, Gemini CLI no longer serves Google AI Pro and Ultra subscribers or free
Gemini Code Assist users. It still works with a paid Gemini API key and with Gemini Code
Assist Standard or Enterprise licences, so Hive keeps supporting it for those; for a personal
Google account, enroll agents with `--vendor agy`.

What differs with agy:
- **One account per computer.** agy keeps its login in the computer's keyring, so every agy
  slot on a computer shares one Google account and its usage limits.
- **Its home folder is the slot's.** agy has no setting for where it keeps its files, so Hive
  changes the home directory for agy sessions; commands the agent runs see that folder too
  ([agy home folder](RUNNING.md#agy-home-folder)).
- **MCP calls end after 3 minutes**, so an idle agy orchestrator calls `await_events` again
  every 150 seconds, a model request each time, where other CLIs wait up to 30 minutes per
  call. A command approved through `request_approval` must finish within about a minute in
  the same call; otherwise the agent gets its output from `run_approved`, and a long command
  run that way can hit the same limit.
- **A headless turn ends at the first tool call agy wants to ask about.** Hive approves its own
  tools, web reads and the allowed shell commands, and holds other commands for an approver,
  but tools Hive doesn't check (such as files outside the worktree) still end the turn.

---

## Current limitations

- Only Claude Code and agy let a command wait for an approver and then run in the agent's own
  session. In Codex and Gemini CLI the command is refused, and the agent asks with
  `request_approval`; the Hive daemon then runs the approved command for it.
- PowerShell script blocks such as `ForEach-Object { $_.Name }` contain expressions Hive can't
  classify, so a command with one waits for approval even when every program in it is
  allowed. Pipeline cmdlets with plain parameters (`Where-Object Name -like *.py`) don't.

- Typing to the orchestrator (the agent you chat with) while it's waiting for news needs
  **Esc** first ([Chatting in its terminal](ORCHESTRATOR.md#chatting-in-its-terminal)).
- The orchestrator isn't yet told to reply in the `#user` channel to messages you send there,
  so replies to messages from the dashboard or `hive say` may appear only in its terminal.
- Plans the orchestrator writes to `.hive/plans/` aren't copied between computers; the plan
  reaches you in the approval question, and reaches programmers in their task descriptions.
- Hive doesn't yet notice agents messaging each other in an endless loop, and can't send you
  push notifications (e.g. to your phone).
- The check whether two file locks ([leases](INTEGRATION.md#leases)) overlap is cautious, so
  it sometimes refuses two locks whose files don't really overlap.
- Hive's checks can't always tell in advance which files a shell command will change; the git
  `pre-commit` hook, which re-checks every agent commit, catches what they miss.

---

[← Back to the README](../README.md)
