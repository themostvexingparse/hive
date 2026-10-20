# Known limitations and verification status

Hive drives three vendors' CLIs through their documented features, and those CLIs change
often. This page records which integration points have been verified, against which
versions and how, which haven't been exercised yet, and the limitations you should know about
today.

---

## Verified against the installed CLIs (Claude Code 2.1.x, Gemini CLI 0.41)

- `--plugin-dir`, `--settings`, `--session-id`, `--resume` and the stream-json flags exist.
- The generated plugin passes `claude plugin validate`.
- The generated extension passes `gemini extensions validate`.
- Linking the extension into a private `GEMINI_CLI_HOME` loads its context, MCP server and
  skills there and leaves `~/.gemini` untouched.
- Gemini's hook decision format matches its bundled documentation.

## Verified with Codex CLI 0.163

Driven end to end by a scripted model server in place of the OpenAI API
(`tests/test_codex_e2e.py`, which runs when `codex` is installed):
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

These would use account quota:
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

- Typing into the orchestrator while it waits needs **Esc** first
  ([Chatting in its terminal](ORCHESTRATOR.md#chatting-in-its-terminal)).
- The orchestrator isn't yet told to answer `#user` messages in `#user`, so replies to
  dashboard or `hive say` messages may appear only in its terminal.
- Plans written to `.hive/plans/` aren't synchronized between machines; plans reach you
  through the approval decision and reach programmers through task specs.
- No detection of agents messaging each other in loops, and no push notifications yet.
- Lease overlap checks are conservative ([Leases](INTEGRATION.md#leases)).
- Shell commands can't be inspected reliably by hooks; the git `pre-commit` hook is the
  backstop.

---

[← Back to the README](../README.md)
