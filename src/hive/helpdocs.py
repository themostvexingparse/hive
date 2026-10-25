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


def render(topic: Topic, stream=None) -> str:
    """The page as the terminal shows it."""
    out: list[str] = []
    code = False
    for line in topic.body.split("\n"):
        if line.strip().startswith("```"):
            code = not code
            continue
        if code:
            text = "    " + line
            if line.lstrip().startswith("#"):
                text = ui.dim(text, stream)
            else:
                body, hash_, comment = line.partition("  # ")
                text = "    " + ui.cmd(body, stream) + (ui.dim(hash_ + comment, stream)
                                                       if hash_ else "")
            out.append(text)
        elif line.startswith(("# ", "## ")):
            title = line.lstrip("#").strip().replace("`", "")
            out.append(ui.paint(title, "bold", "bright_yellow", stream=stream))
        elif m := re.match(r"^(\s*)- ", line):  # a list item: a coloured bullet
            bullet = ui.paint(ui.sym("•", "-", stream), "bright_yellow", stream=stream)
            out.append(m.group(1) + bullet + " " + _inline(line[m.end():], stream))
        else:
            out.append(_inline(line, stream))
    return "\n".join(out).rstrip() + "\n"


def index(topics: dict[str, Topic] | None = None, stream=None) -> str:
    """`hive help topics`: every page, one line each."""
    topics = topics if topics is not None else load()
    w = max(len(n) for n in topics) + 2
    lines = [ui.paint("Help topics", "bold", "bright_yellow", stream=stream)
             + ui.dim("  (hive help TOPIC)", stream)]
    lines += [f"  {ui.cmd(n.ljust(w), stream)}{t.title}" for n, t in topics.items()]
    return "\n".join(lines) + "\n"
