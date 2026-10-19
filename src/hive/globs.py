"""Path glob matching and conservative overlap detection for leases (SPEC §12).

Globs are repo-relative POSIX paths. `**` matches any number of segments, `*` and `?` match within a
single segment. A glob without wildcards covers that path and everything below it.
"""

from __future__ import annotations

import re
from functools import lru_cache

# Supported wildcards: `**`, `*` and `?`. `[` is an ordinary character (no character classes).
_WILDCARD = re.compile(r"[*?]")


def normalize(path: str) -> str:
    p = path.replace("\\", "/").strip()
    while p.startswith("./"):
        p = p[2:]
    p = re.sub(r"/+", "/", p).strip("/")
    if not p or p == ".":
        return "**"
    if ".." in p.split("/"):
        raise ValueError(f"path escapes the repository: {path!r}")
    return p


def is_literal(glob: str) -> bool:
    return not _WILDCARD.search(glob)


@lru_cache(maxsize=1024)
def _regex(glob: str) -> re.Pattern[str]:
    out, i = [], 0
    while i < len(glob):
        c = glob[i]
        if glob.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif glob.startswith("**", i):
            out.append(".*")
            i += 2
        elif c == "*":
            out.append("[^/]*")
            i += 1
        elif c == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(c))
            i += 1
    return re.compile("".join(out) + r"\Z")


def matches(path: str, glob: str) -> bool:
    path, glob = normalize(path), normalize(glob)
    if is_literal(glob):
        return path == glob or path.startswith(glob + "/")
    return bool(_regex(glob).match(path))


def covered(path: str, globs: list[str]) -> bool:
    return any(matches(path, g) for g in globs)


def literal_prefix(glob: str) -> list[str]:
    segs = []
    for seg in normalize(glob).split("/"):
        if _WILDCARD.search(seg):
            break
        segs.append(seg)
    return segs


def overlaps(a: str, b: str) -> bool:
    """Conservative: may report overlap for disjoint globs, never misses a real overlap.
    Case-insensitive, because on Windows and macOS `src/App.py` and `src/app.py` are the same
    file (on Linux this only makes leases slightly more conservative)."""
    a, b = normalize(a).casefold(), normalize(b).casefold()
    if is_literal(a) and is_literal(b):
        pa, pb = a.split("/"), b.split("/")
        n = min(len(pa), len(pb))
        return pa[:n] == pb[:n]
    if is_literal(a):
        return matches(a, b) or _prefix_related(a, b)
    if is_literal(b):
        return matches(b, a) or _prefix_related(b, a)
    return _prefix_related(a, b)


def _prefix_related(a: str, b: str) -> bool:
    pa, pb = literal_prefix(a), literal_prefix(b)
    n = min(len(pa), len(pb))
    return pa[:n] == pb[:n]


def any_overlap(globs_a: list[str], globs_b: list[str]) -> bool:
    return any(overlaps(x, y) for x in globs_a for y in globs_b)
