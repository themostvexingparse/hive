import io
import logging

import pytest

from hive import cli, ui


class Tty(io.StringIO):
    """A terminal-like stream (on Windows, VT mode can't be enabled on it, so only
    `always` colours it; on POSIX, `auto` does too)."""

    encoding = "utf-8"

    def isatty(self):
        return True


@pytest.fixture(autouse=True)
def clean_mode(monkeypatch):
    for var in ("HIVE_COLOR", "NO_COLOR", "FORCE_COLOR", "TERM"):
        monkeypatch.delenv(var, raising=False)
    ui.set_mode("auto")
    yield
    ui.set_mode("auto")


def test_plain_when_not_a_terminal():
    s = io.StringIO()
    assert not ui.enabled(s)
    assert ui.paint("x", "red", stream=s) == "x"


def test_precedence_flag_then_hive_color_then_no_color_then_force(monkeypatch):
    s = io.StringIO()
    monkeypatch.setenv("FORCE_COLOR", "1")
    assert ui.enabled(s)
    ui.set_mode("auto")
    monkeypatch.setenv("NO_COLOR", "1")
    assert not ui.enabled(s)                       # NO_COLOR beats FORCE_COLOR
    monkeypatch.setenv("HIVE_COLOR", "always")
    ui.set_mode("auto")
    assert ui.enabled(s)                           # HIVE_COLOR beats NO_COLOR
    ui.set_mode("never")
    assert not ui.enabled(s)                       # --color beats everything
    ui.set_mode("always")
    assert ui.paint("x", "red", "bold", stream=s) == "\x1b[31;1mx\x1b[0m"


def test_dumb_terminal_is_plain(monkeypatch):
    monkeypatch.setenv("TERM", "dumb")
    assert not ui.enabled(Tty())


def test_symbols_fall_back_on_narrow_encodings():
    class Cp1252(io.StringIO):
        encoding = "cp1252"
    assert ui.sym("√", "+", Cp1252()) == "+"
    assert ui.sym("√", "+", Tty()) == "√"


def test_table_aligns_on_visible_width():
    ui.set_mode("always")
    s = Tty()
    ui.table(["name", "state"], [[ui.agent("a", s), ui.state("online", s)],
                                 [ui.agent("longer-name", s), ui.state("offline", s)]], stream=s)
    lines = [ui.strip(l) for l in s.getvalue().splitlines()]
    assert lines[1].index("online") == lines[2].index("offline") == lines[0].index("state")


def test_table_truncates_last_column(monkeypatch):
    monkeypatch.setattr(ui.shutil, "get_terminal_size", lambda *a: __import__("os")
                        .terminal_size((40, 24)))
    s = io.StringIO()
    ui.table(["id", "title"], [["#1", "x" * 200]], stream=s)
    assert all(len(l) <= 40 for l in s.getvalue().splitlines())


def test_state_colours_by_meaning():
    ui.set_mode("always")
    s = Tty()
    assert ui.state("failed", s).startswith("\x1b[31m")
    assert ui.state("done", s).startswith("\x1b[32m")
    assert ui.state("whatever", s) == "whatever"
    assert ui.agent("x", s) == ui.agent("x", s)  # stable per name


def test_stream_line_markers_and_plain_fallback():
    ui.set_mode("always")
    s = Tty()
    assert "\x1b[91m" in ui.stream_line("w", "× npm: not found", s)
    assert "\x1b[91m" in ui.stream_line("w", "✗ old daemon", s)
    assert ui.strip(ui.stream_line("w", "→ Read x", s, name_width=3)).startswith("w   │ → Read")
    ui.set_mode("never")

    class Ascii(io.StringIO):
        encoding = "ascii"
    assert ui.stream_line("w", "→ Read x", Ascii()) == "w | > Read x"


def test_log_formatter_colours_slot_prefix():
    ui.set_mode("always")
    s = Tty()
    rec = logging.LogRecord("hive", logging.WARNING, __file__, 1, "[%s] went %s", ("w1", "bad"),
                            None)
    text = ui.LogFormatter(s).format(rec)
    assert "WARN" in text and "w1" in text and "[w1]" not in ui.strip(text)
    assert ui.strip(text).endswith("w1 │ went bad")


def test_help_is_coloured_without_breaking_layout(capsys):
    with pytest.raises(SystemExit):
        cli.main(["--color", "always", "--help"])
    coloured = capsys.readouterr().out
    with pytest.raises(SystemExit):
        cli.main(["--color", "never", "--help"])
    plain = capsys.readouterr().out
    assert "\x1b[" in coloured and "\x1b[" not in plain
    assert ui.strip(coloured) == plain


def test_usage_error_is_styled_and_exits_2(capsys):
    with pytest.raises(SystemExit) as e:
        cli.main(["--color", "never", "no-such-command"])
    assert e.value.code == 2
    err = capsys.readouterr().err
    assert "hive: argument cmd: invalid choice" in err and "hive: hive:" not in err


def test_die_goes_to_stderr_plain_when_piped(capsys):
    with pytest.raises(SystemExit):
        cli.die("boom")
    err = capsys.readouterr().err
    assert "hive: boom" in err and "\x1b[" not in err
