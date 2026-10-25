"""The fleet as a file: hive plan, hive apply, hive agents export (docs/CLI_REDESIGN.md)."""

import pytest

from hive import cli, fleetfile
from hive.config import Slot, load_machine_config
from hive.server.service import Actor
from test_simple_cli import fleet, isolated  # noqa: F401  (fixtures)

ADMIN_ACTOR = Actor("admin", "user", [])


def write(tmp_path, text):
    p = tmp_path / "hive.fleet.toml"
    p.write_text(text)
    return p


def test_load_names_and_checks(tmp_path):
    wants = fleetfile.load(write(tmp_path, """
[[agents]]
vendor = "claude"
role = "orchestrator"

[[agents]]
vendor = "claude"
roles = "programmer, tester"
count = 2
on = "desk"
model = "m"

[[agents]]
name = "rev"
vendor = "gemini"
roles = ["reviewer"]

[[agents]]
name = "cx"
vendor = "codex"
roles = ["programmer"]
count = 2
"""))
    assert [(w.name, w.roles, w.on) for w in wants] == [
        ("claude-1", ["orchestrator"], ""), ("claude-2", ["programmer", "tester"], "desk"),
        ("claude-3", ["programmer", "tester"], "desk"), ("rev", ["reviewer"], ""),
        ("cx-1", ["programmer"], ""), ("cx-2", ["programmer"], "")]
    assert wants[1].model == "m"
    for bad, msg in (('[[agents]]\nvendor = "gpt"\nroles = ["x"]', "vendor"),
                     ('[[agents]]\nvendor = "claude"', "say what it does"),
                     ('[[agents]]\nvendor = "claude"\nrole = "x"\ncolor = 1', "unknown key"),
                     ('[[agents]]\nname = "a"\nvendor = "claude"\nrole = "x"\n'
                      '[[agents]]\nname = "a"\nvendor = "claude"\nrole = "x"', "twice")):
        with pytest.raises(fleetfile.FleetFileError, match=msg):
            fleetfile.load(write(tmp_path, bad))
    with pytest.raises(fleetfile.FleetFileError, match="export"):
        fleetfile.load(tmp_path / "missing.toml")


def test_plan_lists_each_kind_of_change():
    roster = [
        {"name": "a", "vendor": "claude", "roles": ["orchestrator"], "machine": None},
        {"name": "b", "vendor": "claude", "roles": ["programmer"], "machine": "desk",
         "placement": {"model": "", "permission_mode": ""}},
        {"name": "c", "vendor": "codex", "roles": ["programmer"], "machine": None},
        {"name": "old", "vendor": "codex", "roles": ["tester"], "machine": None},
    ]
    local = {"a": Slot("a", "claude", "t", "/w/a"), "c": Slot("c", "codex", "t", "/w/c")}
    W = fleetfile.Want
    wants = [W("a", "claude", ["orchestrator"]),                       # unchanged
             W("b", "claude", ["programmer", "tester"], "desk", "m2"),  # roles + settings
             W("c", "codex", ["programmer"], "desk"),                  # here → desk
             W("new", "gemini", ["reviewer"], "desk")]                 # create
    p = fleetfile.plan(wants, roster, local, {"desk"})
    assert [(ch.kind, ch.name) for ch in p.changes] == [
        ("roles", "b"), ("settings", "b"), ("move", "c"), ("create", "new")]
    assert p.ok == ["a"] and p.unlisted == ["old"]
    assert "this computer → desk" in p.changes[2].detail
    p = fleetfile.plan(wants, roster, local, {"desk"}, prune=True)
    assert ("remove", "old") in [(ch.kind, ch.name) for ch in p.changes]
    with pytest.raises(fleetfile.FleetFileError, match="no paired machine"):
        fleetfile.plan([W("x", "claude", ["programmer"], "lab")], roster, local, {"desk"})
    with pytest.raises(fleetfile.FleetFileError, match="can't change"):
        fleetfile.plan([W("a", "codex", ["orchestrator"])], roster, local, set())
    # "elsewhere" leaves where an agent runs alone.
    p = fleetfile.plan([W("c", "codex", ["programmer"], "elsewhere")], roster, local, set())
    assert p.ok == ["c"] and not p.changes


def test_export_then_plan_changes_nothing():
    roster = [{"name": "a", "vendor": "claude", "roles": ["orchestrator"], "machine": None},
              {"name": "b", "vendor": "codex", "roles": ["programmer"], "machine": "desk",
               "placement": {"model": "gpt-x", "permission_mode": "read-only"}},
              {"name": "c", "vendor": "gemini", "roles": ["reviewer"], "machine": None}]
    local = {"a": Slot("a", "claude", "t", "/w/a", model="opus")}
    text = fleetfile.export(roster, local)
    import tomllib
    data = tomllib.loads(text)["agents"]
    assert data[0] == {"name": "a", "vendor": "claude", "roles": ["orchestrator"],
                       "model": "opus"}
    assert data[1]["on"] == "desk" and data[1]["permission_mode"] == "read-only"
    assert data[2]["on"] == "elsewhere"
    import pathlib
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        path = pathlib.Path(d) / "f.toml"
        path.write_text(text)
        p = fleetfile.plan(fleetfile.load(path), roster, local, {"desk"})
    assert not p.changes and p.ok == ["a", "b", "c"]


def test_apply_builds_and_changes_the_fleet(fleet, tmp_path, capsys):
    clone, hv, url = fleet
    cli.main(["login", "--server", url, "--token", ADMIN])
    code = hv.create_pairing_code(ADMIN_ACTOR)["code"]
    hv.pair_machine(code, "desk")
    (clone / "hive.fleet.toml").write_text("""
[[agents]]
name = "orch"
vendor = "claude"
role = "orchestrator"

[[agents]]
vendor = "codex"
role = "programmer"
count = 2
on = "desk"
permission_mode = "read-only"
""")
    capsys.readouterr()
    cli.main(["plan"])
    out = capsys.readouterr().out
    assert out.count("create") == 3 and "hive apply" in out
    with pytest.raises(SystemExit):
        cli.main(["apply"])  # no terminal to ask: needs --yes
    cli.main(["apply", "--yes"])
    roster = {a["name"]: a for a in hv.roster()}
    assert roster["orch"]["roles"] == ["orchestrator"] and roster["orch"]["machine"] is None
    assert [s.name for s in load_machine_config().slots] == ["orch"]
    assert roster["codex-1"]["machine"] == "desk"
    assert roster["codex-2"]["placement"]["permission_mode"] == "read-only"
    capsys.readouterr()
    cli.main(["plan"])
    assert "already matches" in capsys.readouterr().out

    # Change the file: a role, a move back here, one agent dropped (kept without --prune).
    (clone / "hive.fleet.toml").write_text("""
[[agents]]
name = "orch"
vendor = "claude"
role = "orchestrator"

[[agents]]
name = "codex-1"
vendor = "codex"
roles = ["programmer", "tester"]
""")
    cli.main(["apply", "--yes"])
    roster = {a["name"]: a for a in hv.roster()}
    assert roster["codex-1"]["roles"] == ["programmer", "tester"]
    assert roster["codex-1"]["machine"] is None
    assert sorted(s.name for s in load_machine_config().slots) == ["codex-1", "orch"]
    assert "codex-2" in roster
    cli.main(["apply", "--yes", "--prune"])
    assert "codex-2" not in {a["name"] for a in hv.roster()}

    capsys.readouterr()
    cli.main(["agents", "export"])
    exported = capsys.readouterr().out
    (clone / "hive.fleet.toml").write_text(exported)
    cli.main(["plan"])
    assert "already matches" in capsys.readouterr().out


ADMIN = "admin-secret"
