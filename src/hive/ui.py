"""Console styling for everything `hive` prints to a person. SPEC §17.

Colours are ANSI escape sequences, which every current terminal understands; on Windows 10+
the console's virtual-terminal mode is switched on for the process (cmd.exe, PowerShell and
Windows Terminal alike). They are used only when the stream is a terminal, so pipes, files,
git hooks and agent CLIs reading Hive's output get plain text. Override with `hive --color
always|never`, HIVE_COLOR=always|never, NO_COLOR=1 or FORCE_COLOR=1 (in that order).

Machine-facing output (MCP stdio, hook JSON, messages agents read) never goes through here.
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import shutil
import sys
import time
import zlib

_MODE = "auto"
_decided: dict[int, tuple[object, bool]] = {}

_CODES = {
    "reset": 0, "bold": 1, "dim": 2, "italic": 3, "underline": 4,
    "red": 31, "green": 32, "yellow": 33, "blue": 34, "magenta": 35, "cyan": 36,
    "gray": 90, "bright_red": 91, "bright_green": 92, "bright_yellow": 93, "bright_blue": 94,
    "bright_magenta": 95, "bright_cyan": 96,
}
_ANSI = re.compile(r"\x1b\[[0-9;]*m")

# Agent names get a stable colour each, so one agent's lines are easy to follow.
_AGENT_COLORS = ("bright_cyan", "bright_magenta", "bright_yellow", "bright_green",
                 "bright_blue", "cyan", "magenta", "yellow", "green", "blue")

# What a state means decides its colour, wherever it is shown.
_STATE_COLORS = {
    "green": {"running", "done", "online", "idle", "approved", "answered", "integrated", "ok",
              "passed", "granted", "succeeded", "completed"},
    "yellow": {"planning", "awaiting_approval", "paused", "review", "claimed", "queued", "open",
               "busy", "setup", "draft", "pending", "decision-needed", "waiting", "expiring"},
    "cyan": {"in_progress", "working", "fyi"},
    "red": {"frozen", "failed", "offline", "blocked", "rejected", "error", "denied", "expired",
            "alert", "out of quota", "exhausted"},
    "gray": {"cancelled", "superseded", "never", "-"},
}


# ---------------------------------------------------------------------- deciding on colour

def set_mode(mode: str) -> None:
    """`auto` (default), `always` or `never`; set from `hive --color`."""
    global _MODE
    _MODE = mode
    _decided.clear()


def _env_mode() -> str:
    v = os.environ.get("HIVE_COLOR", "").strip().lower()
    if v in ("always", "never"):
        return v
    if os.environ.get("NO_COLOR"):
        return "never"
    if os.environ.get("FORCE_COLOR", "") not in ("", "0"):
        return "always"
    return "auto"


def _enable_vt(stream) -> bool:
    """True when the stream renders ANSI sequences (turning that on for Windows consoles)."""
    if os.name != "nt":
        return True
    try:
        import ctypes
        import msvcrt
        handle = msvcrt.get_osfhandle(stream.fileno())
        kernel32 = ctypes.windll.kernel32
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False  # not a console (e.g. a mintty pipe): only `always` colours it
        return bool(mode.value & 0x0004) or bool(
            kernel32.SetConsoleMode(handle, mode.value | 0x0004))  # VIRTUAL_TERMINAL_PROCESSING
    except (AttributeError, OSError, ValueError, ImportError):
        return False


def _decide(stream) -> bool:
    mode = _MODE if _MODE != "auto" else _env_mode()
    if mode == "never":
        return False
    if mode == "auto":
        try:
            tty = stream.isatty()
        except (AttributeError, ValueError):
            tty = False
        if not tty or os.environ.get("TERM") == "dumb":
            return False
    return _enable_vt(stream) or mode == "always"


def enabled(stream=None) -> bool:
    stream = stream or sys.stdout
    hit = _decided.get(id(stream))
    if hit is None or hit[0] is not stream:
        hit = _decided[id(stream)] = (stream, _decide(stream))
    return hit[1]


def _fancy(stream) -> bool:
    """Whether the stream can show the box-drawing characters and symbols used below."""
    try:
        "√×═─│→←·•»…".encode(getattr(stream, "encoding", None) or "ascii")
        return True
    except (UnicodeEncodeError, LookupError):
        return False


def sym(fancy: str, plain: str, stream=None) -> str:
    return fancy if _fancy(stream or sys.stdout) else plain


# ---------------------------------------------------------------------- painting

def paint(text, *styles: str, stream=None) -> str:
    """`text` wrapped in the given styles (names from _CODES) when the stream shows colour."""
    text = str(text)
    if not styles or not text or not enabled(stream):
        return text
    return f"\x1b[{';'.join(str(_CODES[s]) for s in styles)}m{text}\x1b[0m"


def strip(text: str) -> str:
    return _ANSI.sub("", text)


def width(text: str) -> int:
    return len(strip(text))


def bold(t, stream=None): return paint(t, "bold", stream=stream)
def dim(t, stream=None): return paint(t, "dim", stream=stream)
def cmd(t, stream=None): return paint(t, "bright_cyan", stream=stream)   # something to type
def path(t, stream=None): return paint(t, "blue", stream=stream)
def num(t, stream=None): return paint(t, "bold", stream=stream)


def agent(name: str | None, stream=None) -> str:
    if not name:
        return dim("-", stream)
    color = _AGENT_COLORS[zlib.crc32(name.encode()) % len(_AGENT_COLORS)]
    return paint(name, color, "bold", stream=stream)


def state(value: str | None, stream=None, label: str | None = None) -> str:
    value = value or "-"
    for color, names in _STATE_COLORS.items():
        if value in names:
            return paint(label or value, color, stream=stream)
    return label or value


# ---------------------------------------------------------------------- printing

def out(*parts, stream=None, end: str = "\n") -> None:
    stream = stream or sys.stdout
    stream.write(" ".join(str(p) for p in parts) + end)
    stream.flush()


def _line(icon: str, color: str, msg: str, stream=None, indent: str = "") -> None:
    stream = stream or sys.stdout
    first, *rest = str(msg).split("\n")
    pad = indent + " " * (width(icon) + 1)
    text = "\n".join([f"{indent}{paint(icon, color, 'bold', stream=stream)} {first}"]
                     + [pad + r if r else "" for r in rest])
    out(text, stream=stream)


def ok(msg: str, stream=None) -> None:
    _line(sym("√", "+", stream), "green", msg, stream)


def warn(msg: str, stream=None) -> None:
    stream = stream or sys.stderr
    _line("!", "yellow", paint(msg, "yellow", stream=stream), stream)


def error(msg: str, stream=None) -> None:
    stream = stream or sys.stderr
    _line(sym("×", "x", stream), "red", paint("hive: ", "red", "bold", stream=stream) + msg,
          stream)


def info(msg: str, stream=None) -> None:
    _line(sym("•", "*", stream), "cyan", msg, stream)


def step(msg: str, stream=None) -> None:
    _line(sym("»", ">", stream), "bright_blue", msg, stream)


def note(msg: str, stream=None) -> None:
    _line(sym("→", "->", stream), "magenta", msg, stream)


def rule_width(stream=None) -> int:
    return max(20, min(shutil.get_terminal_size((80, 24)).columns, 100))


def section(title: str, extra: str = "", stream=None) -> None:
    """A heading line: `── Title ─────────────`."""
    stream = stream or sys.stdout
    bar = sym("─", "-", stream)
    head = f"{bar * 2} {title}" + (f" {extra}" if extra else "") + " "
    tail = bar * max(3, rule_width() - width(head))
    out("\n" + paint(f"{bar * 2} ", "gray", stream=stream)
        + paint(title, "bold", "bright_cyan", stream=stream)
        + (f" {extra}" if extra else "") + " " + paint(tail, "gray", stream=stream),
        stream=stream)


def banner(title: str, subtitle: str = "", stream=None) -> None:
    """The heavy rule shown when a long-running command starts."""
    stream = stream or sys.stdout
    bar = sym("═", "=", stream)
    w = rule_width(stream)
    out(paint(bar * w, "gray", stream=stream), stream=stream)
    out(" " + paint(title, "bold", "bright_yellow", stream=stream)
        + (paint("  " + subtitle, "dim", stream=stream) if subtitle else ""), stream=stream)
    out(paint(bar * w, "gray", stream=stream), stream=stream)


def kv(pairs: list[tuple[str, str]], stream=None, indent: str = "  ") -> None:
    """Aligned `key  value` lines."""
    pairs = [(k, v) for k, v in pairs if v not in (None, "")]
    if not pairs:
        return
    w = max(width(k) for k, _ in pairs)
    for k, v in pairs:
        first, *rest = str(v).split("\n")
        out(f"{indent}{paint(k.ljust(w), 'gray', stream=stream)}  {first}", stream=stream)
        for r in rest:
            out(f"{indent}{' ' * w}  {r}", stream=stream)


def _truncate(text: str, limit: int, stream=None) -> str:
    """Cut a possibly painted string to `limit` visible characters."""
    if width(text) <= limit:
        return text
    plain = strip(text)
    cut = plain[:max(1, limit - 1)] + sym("…", "~", stream)
    # Keep the cell's leading style (if any) on the shortened text.
    m = re.match(r"(\x1b\[[0-9;]*m)", text)
    return f"{m.group(1)}{cut}\x1b[0m" if m else cut


def table(headers: list[str], rows: list[list], stream=None, indent: str = "  ") -> None:
    """Columns aligned on visible width; the last column is cut to fit the terminal."""
    stream = stream or sys.stdout
    cells = [[str(c) if c is not None else "" for c in r] for r in rows]
    widths = [max([width(h)] + [width(r[i]) for r in cells]) for i, h in enumerate(headers)]
    room = shutil.get_terminal_size((100, 24)).columns - len(indent) \
        - sum(w + 2 for w in widths[:-1])
    widths[-1] = max(10, min(widths[-1], room))

    def fmt(r):
        parts = [_truncate(c, widths[i], stream) for i, c in enumerate(r)]
        return indent + "  ".join(p + " " * (widths[i] - width(p)) if i < len(r) - 1 else p
                                  for i, p in enumerate(parts))

    out(paint(fmt(headers).rstrip(), "gray", "underline", stream=stream) if enabled(stream)
        else fmt(headers).rstrip(), stream=stream)
    if not enabled(stream):
        out(indent + "  ".join(sym("─", "-", stream) * w for w in widths), stream=stream)
    for r in cells:
        out(fmt(r).rstrip(), stream=stream)


def ask(question: str, default: str = "") -> str:
    """input() with a styled question; EOF counts as the default."""
    try:
        return input(paint("? ", "bright_yellow", "bold") + paint(question, "bold") + " ").strip()
    except EOFError:
        return default


def ago(ts: float | None) -> str:
    if not ts:
        return "never"
    s = int(time.time() - ts)
    return f"{s}s" if s < 60 else f"{s // 60}m" if s < 3600 else f"{s // 3600}h"


# ---------------------------------------------------------------------- agent output

def stream_line(agent_name: str | None, line: str, stream=None, name_width: int = 0) -> str:
    """One line of an agent's formatted output (see wake.format_stream_event): tool calls,
    results, errors and session notes each get their own colour."""
    stream = stream or sys.stdout
    if line.startswith("→"):
        body = paint("→", "bright_blue", "bold", stream=stream) + paint(line[1:], "blue",
                                                                         stream=stream)
    elif line.startswith("←"):
        body = paint(line, "gray", stream=stream)
    elif line.startswith(("×", "✗")):  # ✗: lines stored by older daemons
        body = paint(line, "bright_red", stream=stream)
    elif line.startswith("·"):
        body = paint(line, "magenta", "italic", stream=stream)
    else:
        body = line
    if not _fancy(stream):
        body = body.translate(str.maketrans({"→": ">", "←": "<", "×": "x", "✗": "x", "·": "*"}))
    if agent_name is None:
        return body
    pad = " " * max(0, name_width - len(agent_name))
    bar = paint(sym("│", "|", stream), "gray", stream=stream)
    return f"{agent(agent_name, stream)}{pad} {bar} {body}"


class LogFormatter(logging.Formatter):
    """Console log lines: dim time, coloured level, `[slot]` prefixes in the agent's colour."""

    LEVELS = {logging.DEBUG: ("DEBUG", "gray"), logging.INFO: ("INFO ", "green"),
              logging.WARNING: ("WARN ", "yellow"), logging.ERROR: ("ERROR", "red"),
              logging.CRITICAL: ("FATAL", "bright_red")}
    SLOT = re.compile(r"^\[([^\]]+)\] ")

    def __init__(self, stream=None):
        super().__init__()
        self.stream = stream or sys.stderr

    def format(self, record: logging.LogRecord) -> str:
        s = self.stream
        label, color = self.LEVELS.get(record.levelno, (record.levelname[:5], "reset"))
        msg = record.getMessage()
        m = self.SLOT.match(msg)
        if m:
            msg = f"{agent(m.group(1), s)} {paint(sym('│', '|', s), 'gray', stream=s)} " \
                  + msg[m.end():]
        if record.levelno >= logging.WARNING:
            msg = paint(strip(msg), color, stream=s) if not m else msg
        text = (paint(time.strftime("%H:%M:%S", time.localtime(record.created)), "gray",
                      stream=s)
                + " " + paint(label, color, "bold", stream=s) + " " + msg)
        if record.exc_info:
            text += "\n" + paint(self.formatException(record.exc_info), "red", stream=s)
        return text


# ---------------------------------------------------------------------- argparse

class HelpFormatter(argparse.RawDescriptionHelpFormatter):
    """Help text with coloured headings, option names and placeholders (after layout, so
    the column alignment argparse computes is unaffected)."""

    def format_help(self) -> str:
        return colorize_help(super().format_help(), sys.stdout)


def colorize_help(text: str, stream) -> str:
    if not enabled(stream):
        return text
    out_lines = []
    in_usage = False
    for line in text.split("\n"):
        if line.startswith("usage:"):
            in_usage = True
            line = paint("usage:", "bold", "bright_yellow", stream=stream) \
                + _color_usage(line[6:], stream)
        elif in_usage and line.strip():
            line = _color_usage(line, stream)
        elif not line.strip():
            in_usage = False
        elif re.match(r"^\S.*:$", line):
            line = paint(line, "bold", "bright_yellow", stream=stream)
        elif m := re.match(r"^(  +)(\S.*?)(\s{2,}.*|)$", line):
            lead, inv, rest = m.groups()
            if len(lead) <= 4 and not line.startswith(" " * 6):
                line = lead + _color_invocation(inv, stream) + rest
        out_lines.append(line)
    return "\n".join(out_lines)


def _color_invocation(inv: str, stream) -> str:
    def repl(m):
        tok = m.group(0)
        if tok.startswith("-"):
            return paint(tok, "bright_cyan", stream=stream)
        if tok.startswith("{"):
            return paint(tok, "bright_green", stream=stream)
        if tok.isupper():
            return paint(tok, "yellow", stream=stream)
        return paint(tok, "bright_green", stream=stream)
    return re.sub(r"\{[^}]*\}|[^\s,\[\]]+", repl, inv)


def _color_usage(text: str, stream) -> str:
    def repl(m):
        tok = m.group(0)
        if tok.startswith("-"):
            return paint(tok, "bright_cyan", stream=stream)
        if tok.isupper() and len(tok) > 1:
            return paint(tok, "yellow", stream=stream)
        return tok
    return re.sub(r"-{1,2}[\w-]+|\b[A-Z][A-Z_]+\b", repl, text)


class ArgumentParser(argparse.ArgumentParser):
    def __init__(self, *a, **kw):
        kw.setdefault("formatter_class", HelpFormatter)
        super().__init__(*a, **kw)

    def format_usage(self) -> str:
        return colorize_help(super().format_usage(), sys.stderr)

    def error(self, message: str):
        self.print_usage(sys.stderr)
        sub = self.prog.split(" ", 1)[1] + ": " if " " in self.prog else ""
        error(f"{sub}{message}\n" + dim(f"See: {self.prog} --help", sys.stderr))
        raise SystemExit(2)
