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

_ATTRS = {"reset": 0, "bold": 1, "dim": 2, "italic": 3, "underline": 4}

# Hive's palette: vivid 24-bit colours where the terminal shows them, else the nearest of the
# 256 standard colours, else the 16 basic ones (the last number). The names say what each is
# used for across the CLI: bright_yellow headings, bright_cyan things to type, yellow
# placeholders and warnings, green success, red errors, blue paths, magenta notes.
_PALETTE: dict[str, tuple[tuple[int, int, int], int]] = {
    "red": ((255, 85, 105), 31),             # coral: errors, failed states
    "bright_red": ((255, 45, 85), 91),
    "green": ((40, 220, 110), 32),           # emerald: success, healthy states
    "bright_green": ((120, 255, 120), 92),   # lime: choices in help
    "yellow": ((255, 160, 0), 33),           # amber: placeholders, warnings, waiting states
    "bright_yellow": ((255, 200, 30), 93),   # honey: headings, the Hive name
    "blue": ((90, 160, 255), 34),            # azure: paths and URLs
    "bright_blue": ((120, 140, 255), 94),    # periwinkle: next steps
    "magenta": ((190, 120, 255), 35),        # violet: notes
    "bright_magenta": ((255, 90, 210), 95),  # pink
    "cyan": ((0, 225, 210), 36),             # turquoise: information, work in progress
    "bright_cyan": ((40, 200, 255), 96),     # sky: commands and options to type
    "gray": ((130, 138, 165), 90),           # slate: rules, timestamps, secondary text
    "orange": ((255, 125, 50), 33),
    "mint": ((110, 255, 200), 92),
    "rose": ((255, 130, 160), 91),
}
_ANSI = re.compile(r"\x1b\[[0-9;]*m")

# Agent names get a stable colour each, so one agent's lines are easy to follow.
_AGENT_COLORS = ("bright_cyan", "bright_magenta", "bright_yellow", "bright_green",
                 "magenta", "orange", "cyan", "rose", "blue", "mint")

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
    global _MODE, _DEPTH
    _MODE = mode
    _DEPTH = None
    _decided.clear()


_DEPTH: str | None = None
_TRUECOLOR_TERMINALS = ("vscode", "iTerm.app", "WezTerm", "Hyper", "ghostty", "Tabby")


def depth() -> str:
    """How many colours the terminal shows: `truecolor`, `256` or `16`. HIVE_COLOR_DEPTH
    overrides it; otherwise COLORTERM, the terminal program and TERM say. Windows consoles
    (Windows Terminal, and the classic console since Windows 10) show 24-bit colour."""
    global _DEPTH
    if _DEPTH is None:
        v = os.environ.get("HIVE_COLOR_DEPTH", "").strip().lower()
        term = os.environ.get("TERM", "")
        if v in ("truecolor", "24bit", "256", "16"):
            _DEPTH = "truecolor" if v == "24bit" else v
        elif os.environ.get("COLORTERM", "").lower() in ("truecolor", "24bit") \
                or os.environ.get("WT_SESSION") \
                or os.environ.get("TERM_PROGRAM") in _TRUECOLOR_TERMINALS \
                or "direct" in term or os.name == "nt":
            _DEPTH = "truecolor"
        elif "256" in term:
            _DEPTH = "256"
        else:
            _DEPTH = "16"
    return _DEPTH


def _xterm256(rgb: tuple[int, int, int]) -> int:
    """The nearest of the 256 standard colours: the 6x6x6 cube or the grey ramp."""
    steps = (0, 95, 135, 175, 215, 255)

    def level(c: int) -> int:
        return min(range(6), key=lambda i: abs(steps[i] - c))
    r, g, b = (level(c) for c in rgb)
    cube = (steps[r], steps[g], steps[b])
    grey = max(0, min(23, round((sum(rgb) / 3 - 8) / 10)))

    def dist(a) -> int:
        return sum((x - y) ** 2 for x, y in zip(a, rgb))
    return 232 + grey if dist((8 + grey * 10,) * 3) < dist(cube) else 16 + 36 * r + 6 * g + b


def _sgr(style: str) -> str:
    if style in _ATTRS:
        return str(_ATTRS[style])
    rgb, basic = _PALETTE[style]
    d = depth()
    if d == "truecolor":
        return "38;2;%d;%d;%d" % rgb
    if d == "256":
        return f"38;5;{_xterm256(rgb)}"
    return str(basic)


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
        "√×═─│┌┐└┘├┤┬┴┼╭╮╰╯→←·•»…".encode(getattr(stream, "encoding", None) or "ascii")
        return True
    except (UnicodeEncodeError, LookupError):
        return False


def sym(fancy: str, plain: str, stream=None) -> str:
    return fancy if _fancy(stream or sys.stdout) else plain


# ---------------------------------------------------------------------- painting

def paint(text, *styles: str, stream=None) -> str:
    """`text` wrapped in the given styles (_ATTRS and _PALETTE names) when the stream shows
    colour."""
    text = str(text)
    if not styles or not text or not enabled(stream):
        return text
    return f"\x1b[{';'.join(_sgr(s) for s in styles)}m{text}\x1b[0m"


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
    """The heavy framed box shown when a command starts."""
    stream = stream or sys.stdout
    w = rule_width(stream)
    if _fancy(stream):
        top = "╭" + "─" * (w - 2) + "╮"
        bot = "╰" + "─" * (w - 2) + "╯"
        inner_w = w - 4
        text = paint(title, "bold", "bright_yellow", stream=stream) + (
            paint("  " + subtitle, "dim", stream=stream) if subtitle else "")
        if width(text) > inner_w and subtitle:
            sub_max = max(0, inner_w - width(title) - 2)
            sub_txt = _truncate(subtitle, sub_max, stream) if sub_max > 1 else ""
            text = paint(title, "bold", "bright_yellow", stream=stream) + (
                paint("  " + sub_txt, "dim", stream=stream) if sub_txt else "")
        pad = " " * max(0, inner_w - width(text))
        side = paint("│", "gray", stream=stream)
        out(paint(top, "gray", stream=stream), stream=stream)
        out(f"{side} {text}{pad} {side}", stream=stream)
        out(paint(bot, "gray", stream=stream), stream=stream)
    else:
        bar = "=" * w
        out(paint(bar, "gray", stream=stream), stream=stream)
        out(" " + paint(title, "bold", "bright_yellow", stream=stream)
            + (paint("  " + subtitle, "dim", stream=stream) if subtitle else ""), stream=stream)
        out(paint(bar, "gray", stream=stream), stream=stream)


def kv(pairs: list[tuple[str, str]], stream=None, indent: str = "  ") -> None:
    """Aligned `key │ value` lines inside a structured box frame."""
    stream = stream or sys.stdout
    pairs = [(k, v) for k, v in pairs if v not in (None, "")]
    if not pairs:
        return
    w_k = max(width(k) for k, _ in pairs)
    term_cols = shutil.get_terminal_size((100, 24)).columns
    max_v = max(36, term_cols - len(indent) - w_k - 7)
    val_rows: list[tuple[str, list[str]]] = []
    w_v = 24
    for k, v in pairs:
        lines = _wrap_cell(str(v), max_v)
        for ln in lines:
            w_v = max(w_v, width(ln))
        val_rows.append((k, lines))
    tl, tm, tr = sym("╭", "+", stream), sym("┬", "+", stream), sym("╮", "+", stream)
    bl, bm, br = sym("╰", "+", stream), sym("┴", "+", stream), sym("╯", "+", stream)
    hbar, vbar = sym("─", "-", stream), sym("│", "|", stream)
    b_v = paint(vbar, "gray", stream=stream)
    top = indent + paint(f"{tl}{hbar * (w_k + 2)}{tm}{hbar * (w_v + 2)}{tr}", "gray",
                         stream=stream)
    bot = indent + paint(f"{bl}{hbar * (w_k + 2)}{bm}{hbar * (w_v + 2)}{br}", "gray",
                         stream=stream)
    out(top, stream=stream)
    for k, lines in val_rows:
        for idx, ln in enumerate(lines):
            k_txt = k if idx == 0 else ""
            k_pad = " " * max(0, w_k - width(k_txt))
            v_pad = " " * max(0, w_v - width(ln))
            k_cell = (paint(k_txt, "bright_yellow", "bold", stream=stream)
                      if k_txt else "") + k_pad
            out(f"{indent}{b_v} {k_cell} {b_v} {ln}{v_pad} {b_v}", stream=stream)
    out(bot, stream=stream)


def _truncate(text: str, limit: int, stream=None) -> str:
    """Cut a possibly painted string to `limit` visible characters."""
    if width(text) <= limit:
        return text
    plain = strip(text)
    cut = plain[:max(1, limit - 1)] + sym("…", "~", stream)
    # Keep the cell's leading style (if any) on the shortened text.
    m = re.match(r"(\x1b\[[0-9;]*m)", text)
    return f"{m.group(1)}{cut}\x1b[0m" if m else cut


def _wrap_cell(text: str, limit: int) -> list[str]:
    """Wrap a possibly ANSI-styled string into lines of at most `limit` visible characters."""
    limit = max(10, limit)
    out_lines: list[str] = []
    for para in str(text).split("\n"):
        if width(para) <= limit:
            out_lines.append(para)
            continue
        words = para.split(" ")
        cur = ""
        cur_w = 0
        active_sgr = ""
        for w in words:
            w_vis = width(w)
            if not cur:
                cur = active_sgr + w
                cur_w = w_vis
            elif cur_w + 1 + w_vis <= limit:
                cur += " " + w
                cur_w += 1 + w_vis
            else:
                if active_sgr and not cur.endswith("\x1b[0m"):
                    cur += "\x1b[0m"
                out_lines.append(cur)
                cur = active_sgr + w
                cur_w = w_vis
            for code in _ANSI.findall(w):
                active_sgr = "" if code == "\x1b[0m" else code
        if cur:
            if active_sgr and not cur.endswith("\x1b[0m"):
                cur += "\x1b[0m"
            out_lines.append(cur)
    return out_lines or [""]


def format_table(headers: list[str], rows: list[list], stream=None, indent: str = "  ",
                 max_width: int | None = None, boxed: bool = False,
                 wrap: bool = True, row_sep: bool = False) -> str:
    """Render a structured table with aligned columns; returns a string."""
    stream = stream or sys.stdout
    cells = [[str(c) if c is not None else "" for c in r] for r in rows]
    term_cols = max_width or shutil.get_terminal_size((100, 24)).columns
    hbar = sym("─", "-", stream)

    if not boxed:
        widths = [max([width(h)] + [width(r[i]) for r in cells]) for i, h in enumerate(headers)]
        room = term_cols - len(indent) - sum(w + 2 for w in widths[:-1])
        widths[-1] = max(10, min(widths[-1], room))

        def fmt_plain(r, is_header: bool = False) -> str:
            parts = []
            for i, c in enumerate(r):
                t = _truncate(c, widths[i], stream)
                pad = " " * max(0, widths[i] - width(t))
                if is_header and enabled(stream):
                    t = paint(t, "bright_yellow", "bold", "underline", stream=stream)
                parts.append(t + pad if i < len(r) - 1 else t)
            return indent + "  ".join(parts)

        lines = [fmt_plain(headers, is_header=True).rstrip()]
        if not enabled(stream):
            lines.append(indent + "  ".join(hbar * w for w in widths))
        for r in cells:
            lines.append(fmt_plain(r).rstrip())
        return "\n".join(lines)

    # Full rounded box-drawing grid table
    nat_widths = [
        max([width(h)] + [max((width(ln) for ln in r[i].split("\n")), default=0) for r in cells])
        for i, h in enumerate(headers)
    ]
    overhead = len(indent) + 3 * len(headers) + 1
    avail = max(24, term_cols - overhead)
    widths = list(nat_widths)
    if sum(widths) > avail and len(widths) > 1:
        if len(widths) == 2:
            widths[0] = min(widths[0], max(28, int(avail * 0.62)))
            widths[1] = max(16, avail - widths[0])
        else:
            for i in range(len(widths) - 1):
                widths[i] = min(widths[i], max(16, avail // len(widths) + 8))
            widths[-1] = max(16, avail - sum(widths[:-1]))
    elif sum(widths) > avail and len(widths) == 1:
        widths[0] = avail

    wrapped_rows: list[list[list[str]]] = []
    for r in cells:
        w_row: list[list[str]] = []
        for i, c in enumerate(r):
            if wrap:
                w_row.append(_wrap_cell(c, widths[i]))
            else:
                w_row.append([_truncate(ln, widths[i], stream) for ln in c.split("\n")])
        wrapped_rows.append(w_row)

    for i, h in enumerate(headers):
        widths[i] = max([width(h)] + [width(ln) for r in wrapped_rows for ln in r[i]])

    tl, tm, tr = sym("╭", "+", stream), sym("┬", "+", stream), sym("╮", "+", stream)
    ml, mm, mr = sym("├", "+", stream), sym("┼", "+", stream), sym("┤", "+", stream)
    bl, bm, br = sym("╰", "+", stream), sym("┴", "+", stream), sym("╯", "+", stream)
    vbar = sym("│", "|", stream)
    b_v = paint(vbar, "gray", stream=stream)

    def border(left: str, mid: str, right: str) -> str:
        line = left + mid.join(hbar * (w + 2) for w in widths) + right
        return indent + (paint(line, "gray", stream=stream) if enabled(stream) else line)

    lines = [border(tl, tm, tr)]
    hdr_parts = []
    for i, h in enumerate(headers):
        pad = " " * max(0, widths[i] - width(h))
        txt = paint(h, "bright_yellow", "bold", stream=stream) if enabled(stream) else h
        hdr_parts.append(f" {txt}{pad} ")
    lines.append(indent + b_v + b_v.join(hdr_parts) + b_v)
    lines.append(border(ml, mm, mr))

    multi_line_any = row_sep or any(max(len(col) for col in r) > 1 for r in wrapped_rows)
    for r_idx, r in enumerate(wrapped_rows):
        height = max((len(col) for col in r), default=1)
        for ln_idx in range(height):
            row_parts = []
            for i, col in enumerate(r):
                cell_ln = col[ln_idx] if ln_idx < len(col) else ""
                pad = " " * max(0, widths[i] - width(cell_ln))
                row_parts.append(f" {cell_ln}{pad} ")
            lines.append(indent + b_v + b_v.join(row_parts) + b_v)
        if multi_line_any and r_idx < len(wrapped_rows) - 1:
            lines.append(border(ml, mm, mr))

    lines.append(border(bl, bm, br))
    return "\n".join(lines)


def table(headers: list[str], rows: list[list], stream=None, indent: str = "  ",
          boxed: bool = False, wrap: bool = True, row_sep: bool = False) -> None:
    """Columns aligned on visible width; when `boxed=True`, renders a full bordered table."""
    stream = stream or sys.stdout
    out(format_table(headers, rows, stream=stream, indent=indent, boxed=boxed,
                     wrap=wrap, row_sep=row_sep), stream=stream)


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
    """Help text with structured boxed tables and coloured headings, option names and
    placeholders."""

    def format_help(self) -> str:
        return colorize_help(structure_help(super().format_help(), sys.stdout), sys.stdout)


def structure_help(text: str, stream=None) -> str:
    """Convert raw help sections (usage, examples, positional arguments, options, command
    groups) into aligned rounded-box tables before colouring."""
    stream = stream or sys.stdout
    if _ANSI.search(text):
        return text
    lines = text.split("\n")
    out_blocks: list[str] = []
    sections: list[tuple[str, list[tuple[str, str]]]] = []
    indent = "  "
    tl, tm, tr = sym("╭", "+", stream), sym("┬", "+", stream), sym("╮", "+", stream)
    ml, mm, mr = sym("├", "+", stream), sym("┼", "+", stream), sym("┤", "+", stream)
    bl, bm, br = sym("╰", "+", stream), sym("┴", "+", stream), sym("╯", "+", stream)
    hbar, vbar = sym("─", "-", stream), sym("│", "|", stream)

    def flush_sections() -> None:
        nonlocal sections
        valid = [(t, it) for t, it in sections if it]
        sections = []
        if not valid:
            return
        term_cols = shutil.get_terminal_size((100, 24)).columns
        w1 = max([width(t) for t, _ in valid]
                 + [width(inv) for _, it in valid for inv, _ in it])
        avail_w2 = max(24, term_cols - len(indent) - w1 - 7)
        wrapped_sec: list[tuple[str, list[tuple[str, list[str]]]]] = []
        w2 = width("Description")
        for title, items in valid:
            w_items: list[tuple[str, list[str]]] = []
            for inv, desc in items:
                d_lines = _wrap_cell(desc or "-", avail_w2)
                for ln in d_lines:
                    w2 = max(w2, width(ln))
                w_items.append((inv, d_lines))
            wrapped_sec.append((title, w_items))

        box: list[str] = [f"{indent}{tl}{hbar * (w1 + 2)}{tm}{hbar * (w2 + 2)}{tr}"]
        for s_idx, (title, w_items) in enumerate(wrapped_sec):
            if s_idx > 0:
                box.append(f"{indent}{ml}{hbar * (w1 + 2)}{mm}{hbar * (w2 + 2)}{mr}")
            box.append(f"{indent}{vbar} {title.ljust(w1)} {vbar} {'Description'.ljust(w2)} {vbar}")
            box.append(f"{indent}{ml}{hbar * (w1 + 2)}{mm}{hbar * (w2 + 2)}{mr}")
            for inv, d_lines in w_items:
                for l_idx, d_ln in enumerate(d_lines):
                    c1 = inv.ljust(w1) if l_idx == 0 else " " * w1
                    box.append(f"{indent}{vbar} {c1} {vbar} {d_ln.ljust(w2)} {vbar}")
        box.append(f"{indent}{bl}{hbar * (w1 + 2)}{bm}{hbar * (w2 + 2)}{br}")
        out_blocks.append("\n".join(box))

    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        if line.startswith("usage:"):
            flush_sections()
            u_lines = [line]
            i += 1
            while i < n and lines[i].strip():
                u_lines.append(lines[i])
                i += 1
            out_blocks.append("\n".join(u_lines))
            continue
        if not line.strip():
            i += 1
            continue
        if line.strip().lower() == "examples:":
            flush_sections()
            i += 1
            ex_lines: list[str] = []
            while i < n and lines[i].strip():
                ex_lines.append(lines[i].strip())
                i += 1
            if ex_lines:
                w_ex = max(max(width(x) for x in ex_lines), 24)
                top_bar = f"{indent}{tl}{hbar} Examples {hbar * max(1, w_ex - 9)}{tr}"
                bot_bar = f"{indent}{bl}{hbar * (w_ex + 2)}{br}"
                ex_box = [top_bar] + [f"{indent}{vbar} {x.ljust(w_ex)} {vbar}" for x in ex_lines] + [bot_bar]
                out_blocks.append("\n".join(ex_box))
            continue
        if m_sec := re.match(r"^(\S.*):$", line):
            sec_title = m_sec.group(1)
            items: list[tuple[str, str]] = []
            i += 1
            while i < n and lines[i].strip():
                ln = lines[i]
                if re.match(r"^  \{[^}]+\}\s*$", ln):
                    i += 1
                    continue
                if m_row := re.match(r"^  {1,2}(\S.*?\S|\S)\s{2,}(\S.*)$", ln):
                    items.append((m_row.group(1), m_row.group(2).strip()))
                elif m_cont := re.match(r"^      +(\S.*)$", ln):
                    if items:
                        prev_inv, prev_desc = items[-1]
                        items[-1] = (prev_inv, (prev_desc + " " + m_cont.group(1).strip()).strip())
                elif m_solo := re.match(r"^  {1,2}(\S.*)$", ln):
                    items.append((m_solo.group(1).strip(), ""))
                i += 1
            sections.append((sec_title, items))
            continue
        flush_sections()
        p_lines = [line]
        i += 1
        while i < n and lines[i].strip() and not re.match(r"^\S.*:$", lines[i]):
            p_lines.append(lines[i])
            i += 1
        out_blocks.append("\n".join(p_lines))

    flush_sections()
    return "\n\n".join(out_blocks) + ("\n" if text.endswith("\n") else "")


def colorize_help(text: str, stream) -> str:
    """argparse's help text, coloured. Text that already has colour is left as it is:
    colouring it again would split its escape sequences."""
    if not enabled(stream) or _ANSI.search(text):
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
        elif m_ex_top := re.match(r"^(\s*)([╭+][─-]+ )(Examples)( [─-]+[╮+])$", line):
            lead, left_b, title, right_b = m_ex_top.groups()
            line = (lead + paint(left_b, "gray", stream=stream)
                    + paint(title, "bold", "bright_yellow", stream=stream)
                    + paint(right_b, "gray", stream=stream))
        elif m_border := re.match(r"^(\s*)([╭├╰+][─┬┼┴╮┤╯+-]+)$", line):
            line = m_border.group(1) + paint(m_border.group(2), "gray", stream=stream)
        elif m_row2 := re.match(r"^(\s*)([│|]) (.*?) ([│|]) (.*?) ([│|])$", line):
            lead, b1, c1, b2, c2, b3 = m_row2.groups()
            bg = paint(b1, "gray", stream=stream)
            if c2.strip() == "Description":
                c1_p = paint(c1, "bold", "bright_yellow", stream=stream)
                c2_p = paint(c2, "bold", "bright_yellow", stream=stream)
                line = f"{lead}{bg} {c1_p} {bg} {c2_p} {bg}"
            else:
                c1_s = c1.rstrip(" ")
                c1_pad = c1[len(c1_s):]
                c1_p = (_color_invocation(c1_s, stream) if c1_s else "") + c1_pad
                line = f"{lead}{bg} {c1_p} {bg} {c2} {bg}"
        elif m_row1 := re.match(r"^(\s*)([│|]) (.*?) ([│|])$", line):
            lead, b1, c1, b2 = m_row1.groups()
            bg = paint(b1, "gray", stream=stream)
            c1_s = c1.rstrip(" ")
            c1_pad = c1[len(c1_s):]
            c1_p = (_color_invocation(c1_s, stream) if c1_s else "") + c1_pad
            line = f"{lead}{bg} {c1_p} {bg}"
        elif re.match(r"^\S.*:$", line):
            line = paint(line, "bold", "bright_yellow", stream=stream)
        elif m := re.match(r"^(  +)(\S.*?)(\s{2,})(.*)$", line):
            lead, inv, gap, rest = m.groups()
            if len(lead) <= 4 and not line.startswith(" " * 6):
                line = lead + _color_invocation(inv, stream) + gap + paint(rest, "reset", stream=stream)
        elif m := re.match(r"^(  +)(\S.*)$", line):
            lead, inv = m.groups()
            if len(lead) <= 4 and not line.startswith(" " * 6):
                line = lead + _color_invocation(inv, stream)
        out_lines.append(line)
    return "\n".join(out_lines)


# In help text: commands sky (as everywhere a command is shown), options pink, placeholders
# amber, choices lime.
def _color_invocation(inv: str, stream) -> str:
    def repl(m):
        tok = m.group(0)
        if tok.startswith("-"):
            return paint(tok, "bright_magenta", stream=stream)
        if tok.startswith("{"):
            return paint(tok, "bright_green", stream=stream)
        if tok.isupper():
            return paint(tok, "yellow", stream=stream)
        return paint(tok, "bright_cyan", stream=stream)
    return re.sub(r"\{[^}]*\}|[^\s,\[\]]+", repl, inv)


def _color_usage(text: str, stream) -> str:
    def repl(m):
        tok = m.group(0)
        if tok.startswith("-"):
            return paint(tok, "bright_magenta", stream=stream)
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
