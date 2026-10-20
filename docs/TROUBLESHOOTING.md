# Troubleshooting

**Start with `hive overview`** in the project ([Checking a project's setup](REPOSITORY.md#checking-a-projects-setup-hive-overview)).
It checks most of the causes below and prints the fix for each problem it finds.

Then, depending on the symptom, look at:
- **`hive status`**: the fleet state, which agents are online or out of quota, tasks and leases;
- **`hive watch <agent>`** or the dashboard: what the agent actually did;
- **`~/.hive/logs/daemon.log`** on the agent's machine: what the daemon did;
- **`~/.hive/logs/<slot>.log`**: the agent CLI's raw output.

For server-side problems (deployment, database, the dashboard not loading), see
[DEPLOYMENT.md §14](DEPLOYMENT.md#14-troubleshooting). For CLI error messages, see
[CLI.md §14](CLI.md#14-error-messages-and-what-to-do).

---

## Starting and waking

| Symptom | Check |
|---|---|
| `hive start` fails with `invalid_fleet` | The message lists missing roles: exactly one orchestrator and at least one programmer must be enrolled ([Roles](ROLES.md)). |
| `hive up` stops with "could not reach the Hive server" | The server is down or unreachable from this machine, or a slot's token was rotated. `hive overview` shows which. |
| An agent never wakes | Is `hive up` running on its machine? Run `hive status`: is it online, or out of quota? Is the fleet in a state where its role may act ([What each role may do](LIFECYCLE.md#what-each-role-may-do-in-each-state))? Check `~/.hive/logs/daemon.log`. |
| Orchestrator doesn't respond to typing | It's waiting in `await_events`: press Esc first ([Chatting in its terminal](ORCHESTRATOR.md#chatting-in-its-terminal)). |
| Fleet stuck in `frozen` | Run `hive decisions` and answer the open one. `hive reset` is the emergency exit. |

## Tools and hooks

| Symptom | Check |
|---|---|
| Tools missing in the orchestrator session | `hive` must be on PATH ([Installation](INSTALLATION.md#hive-must-be-on-path)); re-run `hive init` and commit; launch through `hive up`, not plain `claude`. |
| "Hive daemon unreachable; file writes are blocked" | `hive up` isn't running on that machine, or `machine.toml` changed. Restart `hive up`. |
| Agent says "No lease covers …" | Expected: it must call `lease_acquire` first. If it repeats, see whether another agent holds the path (`hive status` → Leases). |
| Commands keep being refused | The command isn't in the allowed tier. Add it to `[permissions] allow` or `approval`, re-run `hive init` and commit ([Shell-command permissions](PERMISSIONS.md#customising)). |

## Integration

| Symptom | Check |
|---|---|
| `lease_release` returns `conflict` | The agent must merge `origin/main` into its work, then release again. The orchestrator is notified. |
| `lease_release` returns `checks_failed` | A command in `[integration] checks` failed; the output tail is in the result. On mixed Windows/Unix fleets, check the checks are portable or set `[integration] shell`. |
| Push to `main` rejected | Another machine integrated first; Hive retries automatically, up to 3 times. |
| Lease stays after a machine died | It expires within its TTL (≤ 10 min by default), or the orchestrator or you can force-release it (dashboard → Leases → *Revoke*). |

## Vendor CLIs

| Symptom | Check |
|---|---|
| A Gemini slot fails to resume | Set a custom `command` with `--resume latest` ([Custom headless command](CONFIGURATION.md#custom-headless-command)). |
| A Codex turn ends at once, or its Stop hook repeats `invalid choice: 'codex'` | The `hive` that Codex finds on PATH is older than the one running `hive up`. Codex runs hooks through your shell, so its startup files decide; reinstall Hive ([Installation](INSTALLATION.md)) or pin it with `hive init --hive-cmd /full/path/to/hive`. |
| Codex says the profile `hive` doesn't exist | The slot has no private `CODEX_HOME`; re-run `hive join` (it creates `~/.hive/accounts/<name>`). |
| An agent pauses for an hour | Its account hit a usage limit. The scheduler skips it until then, and the orchestrator is told. |

---

If the problem is a known gap rather than a fault, it may be listed in
[Known limitations](LIMITATIONS.md).

---

[← Back to the README](../README.md)
