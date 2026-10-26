"""Rung 4 of docs/CLI_REDESIGN.md: `hive api`, `hive-<name>` plugins, reusable join keys, and
upgrading databases made before the new tables gained columns."""

import json
import os
import sqlite3
import sys

import pytest

from hive import cli
from hive.server.service import Actor, ApiError, create_hive
from test_simple_cli import fleet, isolated  # noqa: F401  (fixtures)

ADMIN = "admin-secret"
ADMIN_ACTOR = Actor("admin", "user", [])


def test_hive_api(fleet, capsys, monkeypatch):
    clone, hv, url = fleet
    cli.main(["login", "--server", url, "--token", ADMIN])
    capsys.readouterr()
    cli.main(["api", "GET", "/fleet"])
    assert json.loads(capsys.readouterr().out)["state"] == "setup"
    cli.main(["api", "post", "/agents", "-d", '{"name": "dev", "vendor": "codex", '
                                              '"roles": ["programmer"]}'])
    assert json.loads(capsys.readouterr().out)["token"].startswith("hv_")
    cli.main(["api", "GET", "api/v1/agents", "-f", "unused=1"])
    assert [a["name"] for a in json.loads(capsys.readouterr().out)] == ["dev"]
    with pytest.raises(SystemExit):
        cli.main(["api", "GET", "/nope/route"])
    with pytest.raises(SystemExit):
        cli.main(["api", "POST", "/agents", "-d", "{not json"])
    monkeypatch.setenv("HIVE_SLOT", "dev")  # inside an agent's session
    with pytest.raises(SystemExit):
        cli.main(["api", "GET", "/fleet"])
    assert "agent's session" in capsys.readouterr().err


def test_plugins_on_path(tmp_path, monkeypatch, capsys):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    out = tmp_path / "plugin-out.txt"
    if os.name == "nt":
        (bin_dir / "hive-hello.bat").write_text(
            f'@echo off\r\necho %* %HIVE_PROJECT%> "{out}"\r\nexit /b 3\r\n')
    else:
        script = bin_dir / "hive-hello"
        script.write_text(f'#!/bin/sh\necho "$@ $HIVE_PROJECT" > "{out}"\nexit 3\n')
        script.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ["PATH"])
    with pytest.raises(SystemExit) as e:
        cli.main(["--project", "acme", "hello", "a", "b"])
    assert e.value.code == 3  # the plugin's exit status
    assert out.read_text().split() == ["a", "b", "acme"]
    # Hive's own commands win over a plugin of the same name, and unknown names still fail.
    assert cli.find_plugin(["status"], {"status": None}) is None
    with pytest.raises(SystemExit) as e:
        cli.main(["no-such-thing"])
    assert e.value.code == 2
    monkeypatch.setenv("HIVE_SLOT", "dev")
    with pytest.raises(SystemExit) as e:
        cli.main(["hello"])
    assert e.value.code == 1 and "agent's session" in capsys.readouterr().err


def test_reusable_join_keys(tmp_path):
    hv = create_hive(f"sqlite:///{tmp_path / 'h.db'}")
    with pytest.raises(ApiError):
        hv.create_pairing_code(ADMIN_ACTOR, reusable=True, days=0)
    key = hv.create_pairing_code(ADMIN_ACTOR, reusable=True, days=7, label="ci")
    assert key["code"].startswith("hk_") and key["expires_in"] == 7 * 86400
    names = [hv.pair_machine(key["code"], "runner")["name"] for _ in range(3)]
    assert names == ["runner", "runner-2", "runner-3"]
    with pytest.raises(ApiError):  # exact match: a key's case matters
        hv.pair_machine(key["code"].upper(), "x")
    listed = hv.list_join_keys(ADMIN_ACTOR)
    assert [(k["label"], k["uses"]) for k in listed] == [("ci", 3)]
    code = hv.create_pairing_code(ADMIN_ACTOR)["code"]  # short codes stay single-use
    assert len(hv.list_join_keys(ADMIN_ACTOR)) == 1
    hv.pair_machine(code, "once")
    with pytest.raises(ApiError):
        hv.pair_machine(code, "twice")
    hv.revoke_join_key(ADMIN_ACTOR, listed[0]["id"])
    with pytest.raises(ApiError) as e:
        hv.pair_machine(key["code"], "late")
    assert e.value.code == "bad_code"
    with pytest.raises(ApiError):
        hv.revoke_join_key(ADMIN_ACTOR, listed[0]["id"])


def test_join_key_from_the_cli(fleet, capsys):
    clone, hv, url = fleet
    cli.main(["login", "--server", url, "--token", ADMIN])
    capsys.readouterr()
    cli.main(["--color", "never", "machines", "invite", "--reusable", "--days", "2",
              "--label", "lab"])
    out = capsys.readouterr().out
    key = next(w for w in out.split() if w.startswith("hk_"))
    assert "--as <its name>" in out
    cli.main(["--color", "never", "machines", "keys"])
    out = capsys.readouterr().out
    assert "lab" in out
    key_id = next(ln.split()[0] for ln in out.splitlines()
                  if len(ln.split()) > 1 and ln.split()[1] == "lab")
    assert hv.pair_machine(key, "box")["name"] == "box"
    cli.main(["machines", "revoke", key_id])
    with pytest.raises(ApiError):
        hv.pair_machine(key, "box2")


def test_older_databases_get_the_new_columns(tmp_path):
    db = tmp_path / "old.db"
    con = sqlite3.connect(db)
    # As the tables were first created on this branch, before they gained columns.
    con.execute("CREATE TABLE pairing_codes (code_hash VARCHAR(64) PRIMARY KEY, "
                "expires FLOAT NOT NULL)")
    con.execute("CREATE TABLE placements (agent VARCHAR(32) PRIMARY KEY, machine VARCHAR(32) "
                "NOT NULL, claimed FLOAT NOT NULL)")
    con.execute("INSERT INTO placements VALUES ('dev', 'box', 0)")
    con.commit()
    con.close()
    hv = create_hive(f"sqlite:///{db}")
    key = hv.create_pairing_code(ADMIN_ACTOR, reusable=True, days=1)
    hv.pair_machine(key["code"], "box")
    hv.enroll(ADMIN_ACTOR, "dev", "claude", ["programmer"])
    assert next(a for a in hv.roster() if a["name"] == "dev")["placement"] == {
        "model": "", "permission_mode": ""}


def test_tasks_reopened_by_an_older_version_are_freed_once(tmp_path):
    from hive.server.db import tasks as tasks_table
    db = tmp_path / "old.db"
    hv = create_hive(f"sqlite:///{db}")
    hv.engine.dispose()
    con = sqlite3.connect(db)
    con.execute("ALTER TABLE tasks DROP COLUMN pending_patch")   # as an older version made it
    for i, (state, assignee) in enumerate([("open", "dev"), ("claimed", "dev"), ("open", None)]):
        con.execute("INSERT INTO tasks (id, title, spec, kind, state, priority, deps, assignee, "
                    "created_by, path_hints, branch, approved, notes, result, created, updated)"
                    " VALUES (?, 't', '', 'code', ?, 0, '[]', ?, 'orch', '[]', ?, 0, '', '', 0, 0)",
                    (i + 1, state, assignee, f"hive/{assignee}" if assignee else None))
    con.commit()
    con.close()
    hv = create_hive(f"sqlite:///{db}")
    tasks = {t["id"]: t for t in hv.list_tasks()}
    assert tasks[1]["assignee"] is None and tasks[1]["branch"] is None   # reopened: free again
    assert tasks[2]["assignee"] == "dev"                                 # claimed: untouched
    assert tasks[1]["pending_patch"] == ""
    # Only once: an open task reserved by this version keeps its agent on the next start.
    with hv._write() as c:
        c.execute(tasks_table.update().where(tasks_table.c.id == 1).values(assignee="dev"))
    hv.engine.dispose()
    assert {t["id"]: t for t in create_hive(f"sqlite:///{db}").list_tasks()}[1]["assignee"] == "dev"
