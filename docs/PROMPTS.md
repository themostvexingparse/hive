# Customising what agents are told

Every agent gets Hive's built-in instructions: its skill and its role prompt. You can add
your own project's instructions after them, without editing anything Hive generates. Hive's
built-in instructions stay as they are, and yours are appended.

Use this for **preferences and conventions**:
- how the orchestrator plans, how finely it splits tasks, and how and when it reports to you;
- testing expectations, commit style, coding conventions;
- how a particular role should work.

Rules that must hold no matter what an agent decides belong in `hive.toml` instead
(`[permissions]`, roles). Hive enforces those in code; prompts only ask.

| You want… | Use |
|---|---|
| The orchestrator to report or plan differently | `.hive/prompts/orchestrator.md` |
| Every worker to follow a convention | `.hive/prompts/worker.md` |
| One role to work differently | `.hive/prompts/roles/<role>.md` |
| To replace a role's built-in prompt entirely | `prompt` in `[roles.<role>]` ([Roles](ROLES.md#custom-roles-and-overrides)) |
| To forbid or allow a command | `[permissions]` ([Shell-command permissions](PERMISSIONS.md#customising)) |

---

## The files

They live in `.hive/prompts/` in the repository and are committed:

| File | Added to | When it takes effect |
|---|---|---|
| `orchestrator.md` | The end of the orchestrator's skill (`hive-orchestrator`), under **Project rules** | After `hive init`; commit the refreshed `.hive/agent/` |
| `worker.md` | The end of every other agent's skill (`hive-worker`), under **Project rules** | After `hive init`; commit the refreshed `.hive/agent/` |
| `roles/<role>.md` | The start of every session of an agent holding that role, after the role's `prompt` from `hive.toml`. Works for built-in roles (`orchestrator`, `programmer`, `reviewer`, …) and your own. | Once the file is on `main`; each agent reads it from its own checkout when a session starts |

The difference in timing comes from where the text goes. `orchestrator.md` and `worker.md`
are built into the skills inside `.hive/agent/`, so they need `hive init` to rebuild the
bundles. Role files are read at the start of each session, so they only need to reach `main`.

### Starter files

`hive init` creates each of these files that doesn't exist yet: both skill files, and a role
file for every role, built-in or defined in `hive.toml`. Files that already exist are never
touched, even if you emptied them. A file you delete comes back as a starter on the next
`hive init`; leave it in place, commented out, instead. Each starter is one `<!-- … -->`
comment explaining the file, with examples for that role, its placeholders and the preview
command.

### Comments and empty files

`<!-- … -->` comments are removed before anything is sent, and runs of blank lines left
behind are collapsed. A file with nothing left after that (only comments and whitespace) adds
**nothing**:
- no "Project rules" heading in the skill, which stays byte-for-byte the built-in one;
- no text at the start of sessions.

Unused starters therefore cost the agents no context. To use one, write below the comment, or
replace it. Example `orchestrator.md`:

```markdown
<!-- (the starter comment can stay; it isn't sent) -->
- Report to me in at most five bullets, and say first whether anything needs my decision.
- Keep tasks to half a day or less; split anything bigger.
- Ask before adding a new dependency.
```

An unclosed `<!--` hides everything after it, as in HTML; `hive init` and `hive prompts`
warn about it. Comments are removed anywhere in the file, including inside code blocks.

### The "Project rules" section

It starts with a note telling the agent the rules come from the project, and that Hive's
protocol wins where the two conflict: leases, `lease_release`, decisions, approvals, never
pushing to `main`. Hive enforces those regardless of what any prompt says.

---

## Placeholders

Write `{{name}}` and Hive fills it in:

| Placeholder | Value | Where |
|---|---|---|
| `{{main}}` | Integration branch (`[repo] main`) | all files |
| `{{review}}` | Review policy (`[integration] review`) | all files |
| `{{checks}}` | The integration checks, joined with `; `, or `(none)` | all files |
| `{{project}}` | The repository folder's name | all files |
| `{{slot}}`, `{{roles}}`, `{{vendor}}` | The agent's name, roles and CLI | `roles/<role>.md` only |

For example, `worker.md` could say "Run `{{checks}}` before every `lease_release`", and stay
correct when you change the checks.

Unknown names are left exactly as written, so code samples with braces (`${{ github.ref }}`,
`{key}`) are safe. `hive init` and `hive prompts` warn about placeholders that won't be
filled (placeholders inside comments are ignored), and about role files with real content
whose name isn't a role. A comment-only role file left over from a removed role is ignored
silently.

---

## Checking the result

`hive prompts` shows what an agent with the given roles receives: its skill's project rules,
the role prompts from `hive.toml` and your role files, with comments removed and placeholders
filled. Files with only comments are reported as adding nothing. It also warns when the
committed bundles are older than your files.

```sh
hive prompts --role orchestrator                   # --full also prints the built-in skill
hive prompts --role programmer,tester --slot claude-2 --vendor gemini
```

---

## Agents can't change these files

A file that tells agents how to behave mustn't be editable by them:
- Write and edit tools are refused under `.hive/prompts/`, even with a lease covering it and
  even for the orchestrator, which may otherwise write anywhere in `.hive/`.
- `lease_release` never commits files there, and an agent's commit touching them can't be
  integrated (`uncovered_commits`).
- The pre-commit hook refuses them in agents' own commits.

You change them in your own clone and push to `main` like any other change.

---

## Limits

The text left after removing comments may be at most 32 KB per file, since every byte of it
is sent to the agent; comments don't count, up to a 1 MB file. `hive init` refuses a larger
file and leaves the existing bundles untouched. Windows line endings and a UTF-8 byte-order
mark are handled.

---

## What stays fixed

The built-in skill text, the MCP tool descriptions, the messages Hive's hooks send
(refusals, approvals, end-of-turn reminders) and the formats of messages and event digests.
They describe exactly what the code does, so changing them would break the fleet rather than
change its behaviour. `hive prompts --full` shows the built-in text;
[What the agents see](AGENT_INTERFACE.md) and [Shell-command permissions](PERMISSIONS.md)
describe it.

---

**Next:** [Security model](SECURITY.md) ·
**See also:** [Roles](ROLES.md), [Preparing the repository](REPOSITORY.md) ·
[← Back to the README](../README.md)
