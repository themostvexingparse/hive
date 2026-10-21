"""Project prompt templates: the user's own additions to what Hive tells its agents.

Files under `.hive/prompts/` in the repository (committed, so every machine sees the same):

- `orchestrator.md`  appended to the hive-orchestrator skill by `hive init`
- `worker.md`        appended to the hive-worker skill by `hive init`
- `roles/<role>.md`  added at the start of every session of an agent holding that role,
                     next to the role's `prompt` from hive.toml

The built-in text is never replaced: additions come after it, under a heading that keeps
Hive's protocol in charge where the two conflict. Agents may not edit these files (the daemon
refuses the writes and the integration of commits that touch them).

`{{name}}` placeholders are filled in; unknown names are left as they are, so ordinary braces
in code examples are safe. `<!-- ... -->` comments are removed first, and a file that is empty
after that adds nothing at all (no heading, no session text), so the commented-out starter
files `hive init` writes cost the agents no context.
"""

from __future__ import annotations

import re
from pathlib import Path

from . import globs as G

PROMPTS_DIR = Path(".hive") / "prompts"
SKILL_FILES = {"hive-orchestrator": "orchestrator.md", "hive-worker": "worker.md"}
# The user's prompt files, and the generated CLI bundles: an agent editing its own hooks,
# rules or MCP command there would change what Hive enforces on its next session.
PROTECTED_GLOBS = [".hive/prompts/**", ".hive/agent/**"]
MAX_BYTES = 32 * 1024          # what is sent to the agent, after comments are removed
MAX_FILE_BYTES = 1024 * 1024   # the file itself, comments included
_COMMENT = re.compile(r"<!--.*?-->", re.S)
_BLANKS = re.compile(r"\n{3,}")
INIT_VARS = ("main", "review", "checks", "project")
SESSION_VARS = INIT_VARS + ("slot", "roles", "vendor")
_VAR = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")

SKILL_HEADING = """\
## Project rules

These rules come from this project's maintainers (`.hive/prompts/{file}`). Follow them as \
well as the procedure above. Where they conflict with Hive's protocol above (leases, \
lease_release, decisions, approvals, never pushing to main), the protocol wins: Hive enforces \
it regardless.

"""

README = """\
# Project prompts

Your additions to what Hive tells its agents. Hive's built-in instructions stay in place;
these files are added after them. Commit them like any other file.

| File | Added to | Takes effect |
|---|---|---|
| `orchestrator.md` | the orchestrator's skill, under "Project rules" | after `hive init` (commit the refreshed `.hive/agent/`) |
| `worker.md` | every other agent's skill, under "Project rules" | after `hive init` (commit the refreshed `.hive/agent/`) |
| `roles/<role>.md` | the start of every session of an agent with that role | once the change is on main; new sessions pick it up |

`hive init` creates each file with only a commented-out example (`<!-- ... -->`). Comments
are removed before anything is sent, and a file with nothing left adds nothing, so unused
files cost the agents no context. Write your rules outside the comment, or delete it.

Placeholders: `{{main}}`, `{{review}}`, `{{checks}}`, `{{project}}` everywhere;
`{{slot}}`, `{{roles}}`, `{{vendor}}` in `roles/<role>.md` only.
Preview the result with `hive prompts --role <role>`.

Agents can't edit this folder; only you can. Use it for preferences and conventions (how
to plan, report, test, write commits). Rules that must always hold belong in `hive.toml`
(`[permissions]`, roles), which Hive enforces in code.
"""


class PromptError(ValueError):
    pass


def prompts_dir(root: Path) -> Path:
    return Path(root) / PROMPTS_DIR


def is_protected(path: str) -> bool:
    """A repository-relative path agents may not change."""
    return G.covered(path, PROTECTED_GLOBS)


def variables(repo_cfg: dict, root: Path | None, **session) -> dict[str, str]:
    repo = repo_cfg.get("repo", {}) or {}
    integ = repo_cfg.get("integration", {}) or {}
    out = {"main": str(repo.get("main", "main")),
           "review": str(integ.get("review", "off")),
           "checks": "; ".join(integ.get("checks", []) or []) or "(none)",
           "project": Path(root).name if root else ""}
    for k, v in session.items():
        out[k] = ", ".join(v) if isinstance(v, (list, tuple)) else str(v)
    return out


def fill(text: str, values: dict[str, str]) -> str:
    return _VAR.sub(lambda m: values.get(m.group(1), m.group(0)), text)


def unknown_vars(text: str, allowed) -> list[str]:
    return sorted({m.group(1) for m in _VAR.finditer(text)} - set(allowed))


def strip_comments(text: str) -> str:
    """Remove `<!-- ... -->` comments. An unclosed `<!--` hides the rest of the file, as in
    HTML (check() warns about it). Blank lines left behind are collapsed."""
    text = _COMMENT.sub("", text)
    if "<!--" in text:
        text = text[:text.index("<!--")]
    return _BLANKS.sub("\n\n", text).strip()


def _raw(path: Path, strict: bool) -> str:
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return ""
    except OSError as e:
        if strict:
            raise PromptError(f"{path}: {e}")
        return ""
    if len(data) > MAX_FILE_BYTES:
        if strict:
            raise PromptError(f"{path} is {len(data) // 1024} KB; prompt files must stay under "
                              f"{MAX_FILE_BYTES // 1024} KB")
        data = data[:MAX_FILE_BYTES]
    # Files saved on Windows have CRLF; the skills are written in text mode, which would
    # otherwise double the CR.
    return data.decode("utf-8-sig", errors="replace").replace("\r\n", "\n")


def _read(path: Path, strict: bool) -> str:
    """The text a file contributes: comments removed; "" when nothing is left."""
    text = strip_comments(_raw(path, strict))
    size = len(text.encode("utf-8"))
    if size > MAX_BYTES:
        if strict:
            raise PromptError(f"{path} has {size // 1024} KB of text outside comments; keep it "
                              f"under {MAX_BYTES // 1024} KB (every byte is sent to the agent)")
        text = text.encode("utf-8")[:MAX_BYTES].decode("utf-8", errors="ignore")
    return text


def has_content(path: Path) -> bool:
    """Whether a file adds anything once comments and whitespace are removed."""
    return bool(_read(path, strict=False))


def skill_addition(root: Path, skill: str, repo_cfg: dict, strict: bool = True) -> str:
    """The section `hive init` appends to a skill, or "" when the project adds nothing."""
    name = SKILL_FILES[skill]
    text = _read(prompts_dir(root) / name, strict)
    if not text:
        return ""
    return "\n" + SKILL_HEADING.format(file=name) + fill(text, variables(repo_cfg, root)) + "\n"


def role_files(root: Path | None, roles) -> list[tuple[str, Path]]:
    if not root:
        return []
    base = prompts_dir(root) / "roles"
    return [(r, base / f"{r}.md") for r in roles if (base / f"{r}.md").is_file()]


def session_addition(root: Path | None, roles, repo_cfg: dict, slot: str = "",
                     vendor: str = "") -> str:
    """Text for the start of a session: the project's files for each of the agent's roles.
    Read at run time from the agent's own checkout, so it is never fatal."""
    parts = []
    values = variables(repo_cfg, root, slot=slot, roles=list(roles), vendor=vendor)
    for role, path in role_files(root, roles):
        text = _read(path, strict=False)
        if text:
            parts.append(fill(text, values))
    return "\n\n".join(parts)


def _rel(path: Path) -> str:
    sub = "roles/" if path.parent.name == "roles" else ""
    return f"{PROMPTS_DIR.as_posix()}/{sub}{path.name}"


def _unclosed(path: Path) -> str:
    if "<!--" in _COMMENT.sub("", _raw(path, strict=False)):
        return (f"{_rel(path)}: a `<!--` comment is never closed with `-->`, so everything "
                "after it is ignored")
    return ""


def check(root: Path, repo_cfg: dict, roles) -> list[str]:
    """Problems worth telling the user about: oversized files, unclosed comments,
    placeholders that won't fill, role files for roles that don't exist."""
    problems = []
    for skill, name in SKILL_FILES.items():
        path = prompts_dir(root) / name
        try:
            text = _read(path, strict=True)
        except PromptError as e:
            problems.append(str(e))
            continue
        if _unclosed(path):
            problems.append(_unclosed(path))
        bad = unknown_vars(text, INIT_VARS)
        if bad:
            problems.append(f"{PROMPTS_DIR.as_posix()}/{name}: unknown placeholder(s) "
                            f"{', '.join('{{' + b + '}}' for b in bad)} stay as written "
                            f"(available here: {', '.join(INIT_VARS)})")
    base = prompts_dir(root) / "roles"
    if base.is_dir():
        for path in sorted(base.glob("*.md")):
            rel = _rel(path)
            try:
                text = _read(path, strict=True)
            except PromptError as e:
                problems.append(str(e))
                continue
            if _unclosed(path):
                problems.append(_unclosed(path))
            # A comment-only file (e.g. the starter of a role since removed) sends nothing.
            if text and path.stem not in roles:
                problems.append(f"{rel}: there is no role named {path.stem!r} "
                                f"(roles: {', '.join(sorted(roles))})")
            bad = unknown_vars(text, SESSION_VARS)
            if bad:
                problems.append(f"{rel}: unknown placeholder(s) "
                                f"{', '.join('{{' + b + '}}' for b in bad)} stay as written")
    return problems


# ---------------------------------------------------------------------- starter files

_STARTER_SKILL = """\
<!--
Project rules for {who}. `hive init` adds what you write here to the end of the
{skill} skill, under "Project rules"; run it again after editing, then commit
.hive/agent/ and this file.

Everything inside a comment like this one is ignored. While this file holds only
comments, Hive adds nothing. Write your rules below the comment (or delete it).
For example:

{examples}

Placeholders: {{{{main}}}}, {{{{review}}}}, {{{{checks}}}}, {{{{project}}}}.
Preview: hive prompts --role {preview_role}
-->
"""

_STARTER_ROLE = """\
<!--
Instructions for every agent with the `{role}` role. Hive adds them at the start of
each of their sessions, after the role's prompt from hive.toml. Takes effect once
this file is on main.

Everything inside a comment like this one is ignored. While this file holds only
comments, Hive adds nothing. Write your instructions below the comment (or delete
it). For example:

{examples}

Placeholders: {{{{main}}}}, {{{{review}}}}, {{{{checks}}}}, {{{{project}}}}, {{{{slot}}}} (the
agent's name), {{{{roles}}}}, {{{{vendor}}}} (claude, gemini, codex or agy).
Preview: hive prompts --role {role}
-->
"""

_EXAMPLES = {
    "orchestrator.md": [
        "Report to me in at most five bullets; say first whether anything needs my decision.",
        "Keep tasks to half a day of work or less; split anything bigger.",
        "Ask me before adding a new dependency or changing the public API.",
    ],
    "worker.md": [
        "Use Conventional Commits (feat:, fix:, docs:, ...) for commit messages.",
        "Add or update tests for every behaviour you change.",
        "Run the linter before lease_release.",
    ],
    "orchestrator": [
        "Plan in small, independently testable tasks.",
        "Prefer assigning tests and docs to agents with matching roles.",
    ],
    "programmer": [
        "Keep functions small; follow the style of the surrounding code.",
        "Never commit generated files or secrets.",
    ],
    "reviewer": [
        "Reject changes without tests for new behaviour.",
        "Check error handling and edge cases before style.",
    ],
    "tester": [
        "Cover the failure paths, not only the happy path.",
        "Report flaky tests as their own tasks.",
    ],
    "planner": [
        "Write each plan to .hive/plans/<topic>.md with goals, steps and risks.",
        "Name the files each step will touch.",
    ],
}
_GENERIC = ["Describe how this role should work in this project.",
            "List conventions it must follow."]


def _examples(key: str) -> str:
    return "\n".join(f"- {e}" for e in _EXAMPLES.get(key, _GENERIC))


def starter(name: str) -> str:
    """Commented-out starter text for `orchestrator.md`, `worker.md` or `roles/<role>.md`."""
    if name in SKILL_FILES.values():
        orch = name == "orchestrator.md"
        return _STARTER_SKILL.format(
            who="the orchestrator" if orch else "every agent except the orchestrator",
            skill="hive-orchestrator" if orch else "hive-worker",
            examples=_examples(name), preview_role="orchestrator" if orch else "programmer")
    role = name.removeprefix("roles/").removesuffix(".md")
    return _STARTER_ROLE.format(role=role, examples=_examples(role))


def write_starters(root: Path, roles) -> list[str]:
    """Create the commented-out starter files that don't exist yet; never touch existing
    ones. Returns the paths written, relative to the repository."""
    written = []
    names = list(SKILL_FILES.values()) + [f"roles/{r}.md" for r in roles]
    for name in names:
        path = prompts_dir(root) / name
        if path.exists():
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(starter(name), "utf-8")
        written.append(f"{PROMPTS_DIR.as_posix()}/{name}")
    return written
