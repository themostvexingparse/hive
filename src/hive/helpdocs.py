"""`hive help TOPIC`: the detailed help pages, read from the Markdown files in hive/help/
(installed with the package), so they can be edited without touching the code.

Each page starts with a header between two `---` lines:

    ---
    title: Adding and managing agents
    keywords: agent, slot, worktree
    see: machines, roles
    ---

`title` is shown in `hive help topics`; `keywords` are other words that find the page; `see`
names related pages. The rest is Markdown, of which `#`/`##` headings, ``` code blocks,
`- ` lists, `inline code` and **bold** are shown styled; everything else as written.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from importlib import resources

from . import ui


@dataclass
class Topic:
    name: str  # the file name without .md: what `hive help NAME` finds first
    title: str
    body: str
    keywords: list[str] = field(default_factory=list)
    see: list[str] = field(default_factory=list)


class HelpError(ValueError):
    """A help page that can't be read (a broken header)."""


def _split(value: str) -> list[str]:
    return [w.strip().lower() for w in value.split(",") if w.strip()]


def parse(name: str, text: str) -> Topic:
    lines = text.replace("\r\n", "\n").split("\n")
    if not lines or lines[0].strip() != "---":
        raise HelpError(f"help page {name}.md doesn't start with a --- header")
    try:
        end = lines.index("---", 1)
    except ValueError:
        raise HelpError(f"help page {name}.md: its header has no closing ---") from None
    meta: dict[str, str] = {}
    for line in lines[1:end]:
        if not line.strip():
            continue
        key, sep, value = line.partition(":")
        if not sep:
            raise HelpError(f"help page {name}.md: header line {line!r} isn't `key: value`")
        meta[key.strip().lower()] = value.strip()
    if not meta.get("title"):
        raise HelpError(f"help page {name}.md has no title")
    return Topic(name=name, title=meta["title"], body="\n".join(lines[end + 1:]).strip("\n"),
                 keywords=_split(meta.get("keywords", "")), see=_split(meta.get("see", "")))


def load() -> dict[str, Topic]:
    """Every help page, by name."""
    folder = resources.files("hive").joinpath("help")
    out: dict[str, Topic] = {}
    for entry in sorted(folder.iterdir(), key=lambda e: e.name):
        if entry.name.endswith(".md") and entry.is_file():
            name = entry.name[:-3]
            out[name] = parse(name, entry.read_text("utf-8"))
    return out


def _norm(words: list[str]) -> str:
    q = " ".join(words).strip().lower()
    q = re.sub(r"^hive\s+", "", q)
    return re.sub(r"\s+", " ", q)


@dataclass
class Match:
    topic: Topic | None = None  # the page to show
    choices: list[Topic] = field(default_factory=list)  # several fit: let the user pick
    suggestions: list[str] = field(default_factory=list)  # nothing fits: near misses


def find(words: list[str], topics: dict[str, Topic] | None = None) -> Match:
    """The page for `hive help WORDS`: a page of that name (`agents add` → agents-add, then
    agents); else the one page with it as a keyword; else the pages that mention it."""
    topics = topics if topics is not None else load()
    q = _norm(words)
    if not q:
        return Match()
    slug = q.replace(" ", "-")
    if slug in topics:
        return Match(topics[slug])
    by_kw = [t for t in topics.values() if q in t.keywords]
    if len(by_kw) == 1:
        return Match(by_kw[0])
    if by_kw:
        return Match(choices=by_kw)
    first = q.split(" ")[0]
    if " " in q and first in topics:  # `hive help agents frobnicate`: the agents page
        return Match(topics[first])
    mentions = [t for t in topics.values()
                if q in t.body.lower() or q in t.title.lower()]
    if len(mentions) == 1:
        return Match(mentions[0])
    if mentions:
        return Match(choices=mentions)
    names = list(topics) + [k for t in topics.values() for k in t.keywords]
    near = difflib.get_close_matches(q, names, n=3, cutoff=0.75)
    # A keyword near miss suggests its page.
    pages = []
    for n in near:
        page = n if n in topics else next(t.name for t in topics.values() if n in t.keywords)
        if page not in pages:
            pages.append(page)
    return Match(suggestions=pages)


# ---------------------------------------------------------------------- rendering

_INLINE = re.compile(r"`([^`]+)`|\*\*([^*]+)\*\*")


def _inline(text: str, stream) -> str:
    if not ui.enabled(stream):
        return text.replace("**", "")
    return _INLINE.sub(lambda m: ui.cmd(m.group(1), stream) if m.group(1) is not None
                       else ui.bold(m.group(2), stream), text)


def _split_list_item(text: str) -> tuple[str, str] | None:
    """Split a `- key: description` or `- **term** description` item into (key, desc)."""
def _split_list_item(text: str) -> tuple[str, str] | None:
    """Extract `(item_label, description)` from a Markdown bullet or numbered list item."""
    m = re.match(
        r"^((?:`[^`]+`|\*\*[^*]+\*\*)(?:\s*,\s*(?:`[^`]+`|\*\*[^*]+\*\*))*(?:\s*\([^)]*\))?)"
        r":\s+(.+)$",
        text,
        re.S,
    )
    if not m:
        m = re.match(
            r"^((?:`[^`]+`|\*\*[^*]+\*\*)(?:\s*,\s*(?:`[^`]+`|\*\*[^*]+\*\*))*)"
            r"(?:[:.,;]?\s+)(.+)$",
            text,
            re.S,
        )
    if m:
        key, rest = m.groups()
        clean_key = re.sub(r"[:.,;]\*\*$", "**", key).rstrip(":.,;")
        return clean_key, rest.strip()
    m2 = re.match(r"^([A-Za-z0-9_. /<>-]{1,28}):\s+(.+)$", text, re.S)
    if m2:
        return f"**{m2.group(1)}**", m2.group(2).strip()
    return None


def render(topic: Topic, stream=None) -> str:
    """The page as the terminal shows it, with structured headings, code blocks and tables."""
    stream = stream or ui.sys.stdout
    out: list[str] = []
    code = False
    code_lines: list[tuple[str, str, str, str]] = []
    bullet_group: list[tuple[str, str, str | None, str | None]] = []
    num_group: list[tuple[str, str, str | None, str | None]] = []

    def flush_code() -> None:
        nonlocal code_lines
        if not code_lines:
            return
        has_inline_comments = (
            any(h for _, _, h, _ in code_lines)
            and any(raw.lstrip().startswith("hive") for raw, _, _, _ in code_lines)
            and not any(raw.lstrip().startswith(("#", "[")) for raw, _, _, _ in code_lines)
        )
        if has_inline_comments and len(code_lines) >= 2:
            rows = []
            for raw, body, hash_, comment in code_lines:
                rows.append([
                    ui.cmd(body.strip(), stream),
                    _inline(comment, stream) if hash_ else ui.dim("—", stream),
                ])
            out.append(ui.format_table(["Command", "Description"], rows, stream=stream,
                                       boxed=True, wrap=True))
        else:
            tl, tr = ui.sym("╭", "+", stream), ui.sym("╮", "+", stream)
            bl, br = ui.sym("╰", "+", stream), ui.sym("╯", "+", stream)
            hbar, vbar = ui.sym("─", "-", stream), ui.sym("│", "|", stream)
            b_v = ui.paint(vbar, "gray", stream=stream)
            w = max(max(len(raw) for raw, _, _, _ in code_lines), 24)
            top = "  " + ui.paint(f"{tl}{hbar * (w + 5)}{tr}", "gray", stream=stream)
            bot = "  " + ui.paint(f"{bl}{hbar * (w + 5)}{br}", "gray", stream=stream)
            box = [top]
            for raw, body, hash_, comment in code_lines:
                pad = " " * max(0, w - len(raw))
                if raw.lstrip().startswith("#"):
                    styled = ui.dim(raw, stream)
                elif hash_:
                    gap = raw[len(body):len(raw) - len(comment) - 1].rstrip("#")
                    styled = ui.cmd(body, stream) + gap + ui.dim("# " + comment, stream)
                else:
                    styled = ui.cmd(raw, stream)
                box.append(f"  {b_v}    {styled}{pad} {b_v}")
            box.append(bot)
            out.append("\n".join(box))
        code_lines = []

    def flush_bullets() -> None:
        nonlocal bullet_group
        if not bullet_group:
            return
        structured = [b for b in bullet_group if b[2] is not None]
        if structured and len(structured) >= len(bullet_group) // 2 + 1:
            rows = []
            for _, raw_rest, k, desc in bullet_group:
                if k is not None and desc is not None:
                    rows.append([_inline(k, stream), _inline(desc, stream)])
                else:
                    rows.append([ui.paint(ui.sym("•", "-", stream), "bright_yellow",
                                          stream=stream), _inline(raw_rest, stream)])
            out.append(ui.format_table(["Item", "Details"], rows, stream=stream,
                                       boxed=True, wrap=True))
        else:
            bullet = ui.paint(ui.sym("•", "-", stream), "bright_yellow", stream=stream)
            rows = [[bullet, _inline(raw_rest.replace("\n", " "), stream)]
                    for _, raw_rest, _, _ in bullet_group]
            out.append(ui.format_table(["•", "Details"], rows, stream=stream,
                                       boxed=True, wrap=True))
        bullet_group = []

    def flush_numbers() -> None:
        nonlocal num_group
        if not num_group:
            return
        structured = [n for n in num_group if n[2] is not None]
        if len(structured) == len(num_group):
            rows = [
                [ui.paint(num_s, "bright_yellow", "bold", stream=stream),
                 _inline(k, stream),
                 _inline(desc, stream)]
                for num_s, _, k, desc in num_group
            ]
            out.append(ui.format_table(["#", "Item", "Details"], rows, stream=stream,
                                       boxed=True, wrap=True))
        else:
            rows = [
                [ui.paint(num_s, "bright_yellow", "bold", stream=stream),
                 _inline(raw_rest, stream)]
                for num_s, raw_rest, _, _ in num_group
            ]
            out.append(ui.format_table(["Step", "Action"], rows, stream=stream,
                                       boxed=True, wrap=True))
        num_group = []

    for line in topic.body.split("\n"):
        if line.strip().startswith("```"):
            flush_bullets()
            flush_numbers()
            if code:
                flush_code()
            code = not code
            continue
        if code:
            m_c = re.match(r"^(\s*\S.*?)\s+#\s+(.+)$", line)
            if m_c and not line.lstrip().startswith("#"):
                body, hash_, comment = m_c.group(1), "#", m_c.group(2)
            else:
                body, hash_, comment = line, "", ""
            code_lines.append((line, body, hash_, comment))
            continue
        if line.startswith("# "):
            flush_bullets()
            flush_numbers()
            title = line[2:].strip().replace("`", "")
            bar = ui.sym("═", "=", stream)
            rule = ui.paint(bar * ui.rule_width(stream), "gray", stream=stream)
            out.append(ui.paint(title, "bold", "bright_yellow", stream=stream)
                       + ui.dim(f"  ·  hive help {topic.name}", stream))
            out.append(rule)
        elif line.startswith("## "):
            flush_bullets()
            flush_numbers()
            title = line[3:].strip().replace("`", "")
            bar = ui.sym("─", "-", stream)
            head = f"{bar * 2} {title} "
            tail = bar * max(3, ui.rule_width(stream) - ui.width(head))
            out.append(ui.paint(f"{bar * 2} ", "gray", stream=stream)
                       + ui.paint(title, "bold", "bright_yellow", stream=stream)
                       + " " + ui.paint(tail, "gray", stream=stream))
        elif m := re.match(r"^(\s*)- ", line):
            flush_numbers()
            indent = m.group(1)
            rest = line[m.end():]
            split = _split_list_item(rest)
            if split:
                bullet_group.append((indent, rest, split[0], split[1]))
            else:
                bullet_group.append((indent, rest, None, None))
        elif bullet_group and line.startswith("  ") and line.strip():
            indent, raw_rest, k, d = bullet_group[-1]
            new_raw = raw_rest + " " + line.strip()
            split = _split_list_item(new_raw)
            if split:
                bullet_group[-1] = (indent, new_raw, split[0], split[1])
            elif d is not None:
                bullet_group[-1] = (indent, new_raw, k, d + " " + line.strip())
            else:
                bullet_group[-1] = (indent, new_raw, None, None)
        elif m_num := re.match(r"^(\s*)(\d+\.)\s+(.*)$", line):
            flush_bullets()
            _, num_str, rest = m_num.groups()
            split = _split_list_item(rest)
            if split:
                num_group.append((num_str, rest, split[0], split[1]))
            else:
                num_group.append((num_str, rest, None, None))
        elif num_group and line.startswith("   ") and line.strip():
            num_s, raw_rest, k, d = num_group[-1]
            new_raw = raw_rest + " " + line.strip()
            split = _split_list_item(new_raw)
            if split:
                num_group[-1] = (num_s, new_raw, split[0], split[1])
            elif d is not None:
                num_group[-1] = (num_s, new_raw, k, d + " " + line.strip())
            else:
                num_group[-1] = (num_s, new_raw, None, None)
        else:
            flush_bullets()
            flush_numbers()
            out.append(("  " + _inline(line, stream)) if line.strip() else "")
    flush_bullets()
    flush_numbers()
    flush_code()
    return "\n".join(out).rstrip() + "\n"


def index(topics: dict[str, Topic] | None = None, stream=None) -> str:
    """`hive help topics`: every page, in a structured table."""
    stream = stream or ui.sys.stdout
    topics = topics if topics is not None else load()
    bar = ui.sym("═", "=", stream)
    lines = [
        ui.paint("Help topics", "bold", "bright_yellow", stream=stream)
        + ui.dim("  ·  hive help TOPIC", stream),
        ui.paint(bar * ui.rule_width(stream), "gray", stream=stream),
        "",
        ui.format_table(
            ["Topic", "Description", "See Also"],
            [[ui.cmd(n, stream), t.title,
              ui.dim(", ".join(t.see[:3]), stream) if t.see else ui.dim("—", stream)]
             for n, t in topics.items()],
            stream=stream,
            boxed=True,
            wrap=True,
            max_width=max(105, ui.rule_width(stream)),
        ),
    ]
    return "\n".join(lines) + "\n"
