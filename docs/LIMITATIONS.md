# Known limitations and verification status

Hive's agents are three vendors' command-line programs (CLIs): Claude Code, Gemini CLI and
Codex. Hive drives them through their documented features, and those CLIs change often. This
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

## Not yet exercised with a live, logged-in session

These need a real, logged-in account, and would use up some of its allowance:
- Claude: that `--plugin-dir` loads the plugin's hooks and MCP server, and the tool name prefix
  `mcp__plugin_hive_hive__`.
- Gemini: `--resume <uuid>` (Gemini documents `latest` or an index; see
  [Custom headless command](CONFIGURATION.md#custom-headless-command)) and the exact
  stream-json field names.
- Codex with a real model and account: the interactive orchestrator (`codex resume <id>`) and
  usage-limit messages.
- [GitHub mode](INTEGRATION.md#github-mode-optional).

---

## Current limitations

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
