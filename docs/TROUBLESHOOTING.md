# Troubleshooting

This page lists common problems with a Hive fleet (your team of AI agents) and their fixes.
Terms are explained in the [glossary](GLOSSARY.md).

**Start with `hive overview`**, run inside your repository
([Checking a project's setup](REPOSITORY.md#checking-a-projects-setup-hive-overview)). It
checks most of the causes below and prints the fix for each problem it finds.

Then, depending on the symptom, look at:
- **`hive status`**: the [fleet state](LIFECYCLE.md) (the phase the team is in), which agents
  are online or *out of quota* (their account has used up its allowance for now), tasks, and
  [leases](INTEGRATION.md#leases) (agents' locks on files);
- **`hive watch <agent name>`** or the [dashboard](MONITORING.md): what the agent actually did;
- **`~/.hive/logs/daemon.log`** on the computer that runs the agent: what Hive's background
  program there (the [daemon](GLOSSARY.md#daemon), started by `hive up`) did;
- **`~/.hive/logs/<agent name>.log`** on the same computer: everything the agent's CLI
  printed.

For server-side problems (deployment, database, the dashboard not loading), see
[DEPLOYMENT.md §14](DEPLOYMENT.md#14-troubleshooting). For CLI error messages, see
[CLI.md §14](CLI.md#14-error-messages-and-what-to-do).

---

## Starting and waking

| Symptom | Check |
|---|---|
| `hive start` fails with `invalid_fleet` | The message lists missing roles: exactly one orchestrator and at least one programmer must have been created with `hive admin enroll` ([Roles](ROLES.md), [Enrolling agents](MACHINES.md)). |
| `hive up` stops with "could not reach the Hive server" | The server is down or unreachable from this computer, or an agent's token was replaced on the server (*rotated*) so the old one no longer works. `hive overview` shows which. |
| An agent never starts working (is never *woken*) | Is `hive up` running on its computer? Run `hive status`: is it online, or out of quota? Is the fleet in a state where its role may act ([What each role may do](LIFECYCLE.md#what-each-role-may-do-in-each-state))? Check `~/.hive/logs/daemon.log`. |
| Orchestrator doesn't respond to typing | It's waiting inside its `await_events` tool: press Esc first ([Chatting in its terminal](ORCHESTRATOR.md#chatting-in-its-terminal)). |
| Fleet stuck in `frozen` | An agent is stuck and the orchestrator is waiting for your answer. Run `hive decisions` and answer the open one with `hive decide`. `hive reset` is the emergency exit. |

## Tools and hooks

| Symptom | Check |
|---|---|
| Hive's tools missing in the orchestrator's session | `hive` must be findable by name in any terminal ([on PATH](INSTALLATION.md#hive-must-be-on-path)); check `hive up`'s output for "could not build Hive's files"; start the orchestrator through `hive up`, not by running `claude` yourself. |
| "Hive daemon unreachable; file writes are blocked" | `hive up` isn't running on that computer, or its settings (`~/.hive/machine.toml`) changed. Restart `hive up`. |
| Agent says "No lease covers …" | Expected: it must lock the file with its `lease_acquire` tool first. If it repeats, see whether another agent holds the file (`hive status` → Leases). |
| Commands keep being refused | The command isn't in the allowed tier. Add it to `[permissions] allow` or `approval` (`hive config set permissions.allow …`), then commit and push; agents' next sessions use it ([Shell-command permissions](PERMISSIONS.md#customising)). |

## Integration

| Symptom | Check |
|---|---|
| `lease_release` (merging an agent's work into `main`) returns `conflict` | Someone else changed the same lines. The agent must merge the latest `main` into its work, then release again. The orchestrator is notified. |
| `lease_release` returns `checks_failed` | One of your test commands (`checks` in `hive.toml`'s `[integration]` section) failed; the end of its output is in the result. If your agents' computers run different operating systems, make sure the commands work on all of them, or set `shell` in `[integration]` ([Checks](INTEGRATION.md#what-lease_releasecommit_message-task_id-does)). |
| Push to `main` rejected | Another agent's work reached `main` first; Hive retries automatically, up to 3 times. |
| Lease stays after a computer died | It expires within its lifetime (≤ 10 min by default), or the orchestrator or you can take it away (dashboard → Leases → *Revoke*). |

## Vendor CLIs

| Symptom | Check |
|---|---|
| A Gemini agent fails to continue its earlier conversation | Give it a custom start command with `--resume latest` ([Custom headless command](CONFIGURATION.md#custom-headless-command)). |
| A Codex agent's run ends at once, or Hive's end-of-run check ([hook](AGENT_INTERFACE.md#hooks)) repeats `invalid choice: 'codex'` | The `hive` that Codex finds is older than the one running `hive up`. Codex runs hooks through your shell, so your shell's startup files decide which `hive` it finds; reinstall Hive ([Installation](INSTALLATION.md)) or make the bundles use a fixed path with `hive init --hive-cmd /full/path/to/hive`. |
| Codex says the profile `hive` doesn't exist | The agent has no private Codex settings folder (`CODEX_HOME`), where Hive puts its Codex *profile*; re-run `hive join` (it creates `~/.hive/accounts/<name>`). |
| An agy agent's turn ends with no answer, and its output shows `needed a permission nobody could give` | agy refused a tool call it wanted to ask about. Hive approves the allowed shell commands and its own tools itself, so this was another tool, such as reading a web page or a file outside the worktree. Hive can't allow those for agy yet ([Known limitations](LIMITATIONS.md#current-limitations)); the agent's next turn usually takes another route. |
| agy agents on one computer use the wrong account | agy keeps one login per computer, in the keyring; every agy slot there uses it ([Accounts](MACHINES.md#log-each-agents-account-in-once)). |
| An agent pauses for an hour | Its account hit a usage limit. The server gives it no tasks until then, and the orchestrator is told. |

---

If the problem is a known gap rather than a fault, it may be listed in
[Known limitations](LIMITATIONS.md).

---

[← Back to the README](../README.md)
