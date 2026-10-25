"""`hive help TOPIC`: the help pages in src/hive/help/ and how a topic is found."""

import re

import pytest

from hive import cli, helpdocs, ui


@pytest.fixture(autouse=True)
def plain(monkeypatch, tmp_path):
    monkeypatch.setenv("HIVE_HOME", str(tmp_path / "home"))
    ui.set_mode("never")


def commands():
    """Every `hive …` command path the parser knows: {("agents",), ("agents", "add"), …}."""
    import argparse
    seen = set()

    def walk(parser, path):
        for action in parser._actions:
            if isinstance(action, argparse._SubParsersAction):
                for name, sub in action.choices.items():
                    seen.add(path + (name,))
                    walk(sub, path + (name,))
    p = None

    class Stop(Exception):
        pass

    def grab(parser_self, *a, **kw):  # the fully built parser, without running anything
        nonlocal p
        p = parser_self
        raise Stop
    orig = cli.TopParser.parse_args
    cli.TopParser.parse_args = grab
    try:
        cli.main(["status"])
    except Stop:
        pass
    finally:
        cli.TopParser.parse_args = orig
    walk(p, ())
    return seen


def test_every_page_is_well_formed_and_links_resolve():
    topics = helpdocs.load()
    assert len(topics) >= 30
    cmds = commands()
    for t in topics.values():
        assert t.title and t.body, t.name
        assert t.body.startswith("# "), f"{t.name}.md should start with a # heading"
        for s in t.see:
            assert s in topics, f"{t.name}.md: see {s!r} is no page"
        for ref in re.findall(r"hive help ([a-z][a-z-]*)", t.body):
            assert ref in topics or (ref,) in cmds or ref == "topics",                 f"{t.name}.md: `hive help {ref}`"
        assert t.body.count("```") % 2 == 0, f"{t.name}.md: an unclosed ``` block"
        width = max(len(line) for line in t.body.split("\n"))
        assert width <= 96, f"{t.name}.md has a line of {width} characters"
        # Commands shown in code blocks exist (hive-<name> plugins aside).
        for line in re.findall(r"^hive ([a-z][a-z-]*)", t.body, re.M):
            assert (line,) in cmds or line in ("standup",), f"{t.name}.md: hive {line}"
    # A keyword finds one page; the same word on two pages would only list both.
    owners: dict[str, str] = {}
    for t in topics.values():
        for k in t.keywords:
            if k in owners and k not in ("network",):
                pytest.fail(f"keyword {k!r} is on {owners[k]}.md and {t.name}.md")
            owners[k] = t.name


def test_finding_a_topic():
    topics = helpdocs.load()

    def page(*words):
        return helpdocs.find(list(words), topics).topic.name
    assert page("hooks") == "hooks"                      # its name
    assert page("notification") == "hooks"               # a keyword
    assert page("hive", "pause") == "lifecycle"          # `hive` in front is ignored
    assert page("agents", "add") == "agents"             # a subcommand: its command's page
    assert page("HIVE_HOME") == "environment"            # case doesn't matter
    m = helpdocs.find(["network"], topics)
    assert m.topic is None and {t.name for t in m.choices} == {"machines", "server"}
    m = helpdocs.find(["hoks"], topics)
    assert m.topic is None and "hooks" in m.suggestions
    assert helpdocs.find(["zzzqqq"], topics).suggestions == []


def test_a_broken_header_is_reported():
    with pytest.raises(helpdocs.HelpError, match="header"):
        helpdocs.parse("x", "no header\n")
    with pytest.raises(helpdocs.HelpError, match="title"):
        helpdocs.parse("x", "---\nkeywords: a\n---\nbody\n")
    t = helpdocs.parse("x", "---\ntitle: X\nkeywords: A, b c\nsee: y\n---\n# X\n\nbody\n")
    assert t.keywords == ["a", "b c"] and t.see == ["y"] and t.body == "# X\n\nbody"


def test_hive_help_topic(capsys):
    cli.main(["help", "hooks"])
    out = capsys.readouterr().out
    assert out.startswith("Hooks on fleet events")      # the heading, without its #
    assert "    hive config set hooks.on_decision" in out   # code blocks indented
    assert "See also: hive help config" in out
    assert "Options of" not in out                      # hooks isn't a command

    cli.main(["help", "agents", "add"])                 # a command: its page and its options
    out = capsys.readouterr().out
    assert "Adding: hive agents add" in out
    assert "Options of hive agents add" in out and "--elsewhere" in out

    cli.main(["help", "mcp"])                           # a command without a page
    assert "usage: hive mcp" in capsys.readouterr().out

    cli.main(["help", "topics"])
    out = capsys.readouterr().out
    assert "hooks" in out and "Hooks: run your own commands" in out

    cli.main(["help", "network"])
    assert "Several help topics cover network" in capsys.readouterr().out

    with pytest.raises(SystemExit):
        cli.main(["help", "hoks"])
    assert "hive help hooks" in capsys.readouterr().err

    cli.main(["help"])                                  # no topic: the common commands
    out = capsys.readouterr().out
    assert "hive help TOPIC" in out and "hive help topics" in out


def test_color_may_come_after_the_command(capsys):
    cli.main(["help", "topics", "--color", "never"])
    cli.main(["help", "--color=never", "say"])
    assert "Messaging the orchestrator" in capsys.readouterr().out
