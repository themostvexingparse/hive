"""`hive config`: read, explain and change settings by name (`integration.review`) across the
layers in config.config_layers, editing the files in place so their comments survive."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import tomli_w

from .config import DEFAULT_REPO_CONFIG, LOCAL_TOML, config_layers


class SettingError(ValueError):
    pass


def split_key(key: str) -> tuple[str, str]:
    """"integration.review" -> ("integration", "review"). Role settings have three parts
    ("roles.docs-writer.can_code"); their section is "roles.docs-writer"."""
    parts = key.strip().split(".")
    if len(parts) < 2 or not all(parts):
        raise SettingError(f"{key!r} is not a setting name; names look like integration.review "
                           "(see `hive config list`)")
    section, name = ".".join(parts[:-1]), parts[-1]
    top = parts[0]
    if top not in DEFAULT_REPO_CONFIG:
        raise SettingError(f"no settings section {top!r}; sections: "
                           + ", ".join(sorted(DEFAULT_REPO_CONFIG)))
    if top == "roles":
        if len(parts) != 3:
            raise SettingError("role settings are roles.<role>.<setting>, e.g. "
                               "roles.docs-writer.can_code")
    elif len(parts) != 2:
        raise SettingError(f"{key!r}: settings are <section>.<name>, e.g. integration.review")
    elif name not in DEFAULT_REPO_CONFIG[top]:
        raise SettingError(f"no setting {key!r}; {top} has: "
                           + ", ".join(sorted(DEFAULT_REPO_CONFIG[top])))
    return section, name


def _lookup(data: dict, section: str, name: str):
    cur = data
    for part in section.split("."):
        cur = cur.get(part) if isinstance(cur, dict) else None
        if cur is None:
            return _MISSING
    return cur.get(name, _MISSING) if isinstance(cur, dict) else _MISSING


_MISSING = object()


def explain(root: Path | None, key: str) -> dict:
    """The value in force and where it comes from: {"key", "value", "source", "layers":
    [(layer, file, value or None)]}."""
    section, name = split_key(key)
    layers, value, source = [], None, None
    for layer, path, data in config_layers(root):
        v = _lookup(data, section, name)
        layers.append((layer, path, None if v is _MISSING else v))
        if v is not _MISSING:
            value, source = v, layer
    return {"key": key, "value": value, "source": source, "layers": layers}


def effective(root: Path | None) -> list[tuple[str, object, str]]:
    """Every setting in force: (key, value, layer it comes from)."""
    keys = [f"{s}.{n}" for s, sect in DEFAULT_REPO_CONFIG.items() if s != "roles"
            for n in sect]
    for _, _, data in config_layers(root)[1:]:
        for role, settings in (data.get("roles") or {}).items():
            keys += [f"roles.{role}.{n}" for n in settings if f"roles.{role}.{n}" not in keys]
    out = []
    for k in keys:
        e = explain(root, k)
        out.append((k, e["value"], e["source"]))
    return out


def parse_value(text: str, key: str):
    """A TOML value (`true`, `90`, `["npm test"]`, `"x"`), else the text as a string. Checked
    against the built-in default's type, so `review = 5` is refused here, not by the server."""
    try:
        value = tomllib.loads(f"v = {text}")["v"]
    except tomllib.TOMLDecodeError:
        value = text
    section, name = split_key(key)
    default = _lookup(DEFAULT_REPO_CONFIG, section, name)
    if default is not _MISSING and default is not None:
        want = type(default)
        if want is float and isinstance(value, int) and not isinstance(value, bool):
            value = float(value)
        if want is int and isinstance(value, float) and value.is_integer():
            value = int(value)
        if isinstance(default, bool) != isinstance(value, bool) or not isinstance(value, want):
            if want is list and isinstance(value, str):
                value = [value]  # `checks "npm test"` means one check
            else:
                raise SettingError(f"{key} takes {_type_name(default)}, not {text!r}")
    return value


def _type_name(v) -> str:
    return {bool: "true or false", int: "a whole number", float: "a number",
            str: "text", list: 'a list, e.g. ["a", "b"]'}.get(type(v), type(v).__name__)


def show(value) -> str:
    """A value as it is written in TOML."""
    return tomli_w.dumps({"v": value})[4:].strip()


# ---------------------------------------------------------------------- editing in place

_HEADER = re.compile(r"^\s*\[\s*([^\[\]]+?)\s*\]\s*(#.*)?$")


def _section_name(raw: str) -> str:
    return ".".join(p.strip().strip('"').strip("'") for p in raw.split("."))


def _value_end(lines: list[str], i: int) -> int:
    """Index of the last line of the value that starts on line i (arrays may span lines): the
    first line where the setting parses on its own."""
    for j in range(i, len(lines)):
        try:
            tomllib.loads("\n".join(lines[i:j + 1]))
            return j
        except tomllib.TOMLDecodeError:
            continue
    return i


def write(path: Path, key: str, value) -> None:
    """Set `key` in the TOML file at `path`, creating file and section as needed. A line
    already setting it is replaced in place, keeping its trailing comment."""
    section, name = split_key(key)
    lines = path.read_text("utf-8").splitlines() if path.exists() else []
    new = f"{name} = {show(value)}"
    start, end = _find_section(lines, section)
    if start is None:
        if lines and lines[-1].strip():
            lines.append("")
        lines += [f"[{section}]", new]
    else:
        at = _find_key(lines, start, end, name)
        if at is None:
            # After the section's last setting, before the blank lines leading to the next one.
            last = end
            while last > start + 1 and not lines[last - 1].strip():
                last -= 1
            lines.insert(last, new)
        else:
            stop = _value_end(lines, at)
            comment = _trailing_comment(lines[at]) if stop == at else ""
            lines[at:stop + 1] = [new + comment]
    _save(path, lines)


def _trailing_comment(line: str) -> str:
    """The `  # …` ending a one-line setting (a `#` inside a string doesn't count)."""
    for m in re.finditer(r"\s*#", line):
        try:
            tomllib.loads(line[:m.start()])
        except tomllib.TOMLDecodeError:
            continue
        return line[m.start():]
    return ""


def unset(path: Path, key: str) -> bool:
    """Remove `key` from the file; False if it wasn't set there."""
    section, name = split_key(key)
    if not path.exists():
        return False
    lines = path.read_text("utf-8").splitlines()
    start, end = _find_section(lines, section)
    at = None if start is None else _find_key(lines, start, end, name)
    if at is None:
        return False
    del lines[at:_value_end(lines, at) + 1]
    _save(path, lines)
    return True


def _find_section(lines: list[str], section: str) -> tuple[int | None, int]:
    start = None
    for i, line in enumerate(lines):
        m = _HEADER.match(line)
        if not m:
            continue
        if start is not None:
            return start, i
        if _section_name(m.group(1)) == section:
            start = i
    return start, len(lines)


def _find_key(lines: list[str], start: int, end: int, name: str) -> int | None:
    pat = re.compile(rf"^\s*[\"']?{re.escape(name)}[\"']?\s*=")
    return next((i for i in range(start + 1, end) if pat.match(lines[i])), None)


def _save(path: Path, lines: list[str]) -> None:
    text = "\n".join(lines) + "\n"
    try:
        tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:  # never leave a file Hive can't read
        raise SettingError(f"editing {path.name} would make it invalid TOML ({e}); "
                           "edit it by hand") from e
    path.write_text(text, "utf-8")


def ensure_ignored(root: Path) -> bool:
    """Add hive.local.toml to the clone's .gitignore; True if it was added."""
    gi = root / ".gitignore"
    lines = gi.read_text("utf-8").splitlines() if gi.exists() else []
    if LOCAL_TOML in lines or f"/{LOCAL_TOML}" in lines:
        return False
    gi.write_text("\n".join(lines + [LOCAL_TOML]) + "\n", "utf-8")
    return True
