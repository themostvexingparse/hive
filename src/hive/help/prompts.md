---
title: Adding your project's rules to what agents are told
keywords: prompt, instructions, .hive/prompts, project rules, conventions, skill, skills, orchestrator.md, worker.md, placeholders, preview
see: roles, init, permissions
---
# Customising what agents are told

Every agent gets Hive's built-in instructions: a **skill** (the operating procedure:
`hive-orchestrator` for the orchestrator, `hive-worker` for everyone else) and a **role
prompt** for each role it holds. You add your project's own rules after them, in
`.hive/prompts/`, without editing anything Hive generates:

- `.hive/prompts/orchestrator.md`: how it plans and reports to you;
- `.hive/prompts/worker.md`: conventions every worker follows (tests, commit style, …);
- `.hive/prompts/roles/<role>.md`: how one role works, e.g. `roles/reviewer.md`.

Commit them: a change takes effect once it is on `main`, at each agent's next launch or
session. `hive init` writes a commented-out starter for each; `<!-- … -->` comments are never
sent, and a file with nothing else adds nothing.

```markdown
- Report to me in at most five bullets, and say first whether anything needs my decision.
- Keep tasks to half a day or less; split anything bigger.
```

Placeholders are filled in: in every file `{{main}}` (the main branch), `{{review}}` (who
reviews), `{{checks}}` (the checks that must pass) and `{{project}}` (the repository's folder
name); in role files also `{{slot}}`, `{{roles}}` and `{{vendor}}` (the agent's name, roles
and CLI). Unknown names are left as written. For example, `worker.md` could say "Run
`{{checks}}` before every `lease_release`".

## Previewing

```sh
hive prompts                                   # what a programmer is told
hive prompts --role orchestrator
hive prompts --role programmer,tester --slot dev --vendor gemini
hive prompts --role reviewer --full            # include the built-in skill text
```

Prompts only ask. Rules that must hold whatever an agent decides belong in `hive.toml`:
which commands are allowed (`hive help permissions`) and what each role may do
(`hive help roles`). To replace a role's built-in prompt entirely, set `prompt` in its
`[roles.<role>]` section.
