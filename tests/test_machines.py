"""Paired machines: a computer joins a project with a one-time code, then runs the agents the
admin places on it (docs/CLI_REDESIGN.md, rung 3)."""

import re
import subprocess

import pytest

from hive import cli, machines
from hive.config import load_machine_config
from hive.daemon.client import HiveError, ServerClient
from hive.server.app import create_app
from hive.server.service import Actor, ApiError, create_hive
from test_e2e import ADMIN, serve

ADMIN_ACTOR = Actor("admin", "user", [])


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                          text=True).stdout.strip()


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("HIVE_HOME", str(tmp_path / "home-admin"))
    for var in ("HIVE_PROJECT", "HIVE_SERVER_URL", "HIVE_ADMIN_TOKEN"):
        monkeypatch.delenv(var, raising=False)


def test_pairing_codes_tokens_and_placements(tmp_path):
    hv = create_hive(f"sqlite:///{tmp_path / 'h.db'}")
    with pytest.raises(ApiError):
        hv.create_pairing_code(Actor("agent", "x", ["programmer"]))
    code = hv.create_pairing_code(ADMIN_ACTOR)["code"]
    assert re.fullmatch(r"[2-9A-HJ-NP-Z]{4}-[2-9A-HJ-NP-Z]{4}", code)
    m = hv.pair_machine(code.lower().replace("-", ""), "Desk Top!", "Linux", ["claude", "x"])
    assert m["name"] == "desk-top" and m["token"].startswith("hm_")
    with pytest.raises(ApiError) as e:  # single use
        hv.pair_machine(code, "again")
    assert e.value.code == "bad_code"
    m2 = hv.pair_machine(hv.create_pairing_code(ADMIN_ACTOR)["code"], "desk-top")
    assert m2["name"] == "desk-top-2"
    listed = hv.list_machines(ADMIN_ACTOR)
    assert [x["name"] for x in listed] == ["desk-top", "desk-top-2"]
    assert listed[0]["clis"] == ["claude"] and "token_hash" not in listed[0]

    desk = hv.authenticate_machine(m["token"])
    other = hv.authenticate_machine(m2["token"])
    first = hv.enroll(ADMIN_ACTOR, "dev", "claude", ["programmer"])["token"]
    with pytest.raises(ApiError) as e:
        hv.place_agent(ADMIN_ACTOR, "dev", "nowhere")
    assert e.value.code == "no_such_machine"
    hv.place_agent(ADMIN_ACTOR, "dev", "desk-top")
    assert hv.machine_placements(desk) == [{"agent": "dev", "vendor": "claude",
                                           "roles": ["programmer"], "claimed": False}]
    assert hv.machine_placements(other) == []
    with pytest.raises(ApiError) as e:
        hv.claim_agent(other, "dev")
    assert e.value.code == "not_placed_here"
    got = hv.claim_agent(desk, "dev")
    assert got["vendor"] == "claude" and got["token"] != first
    assert hv.authenticate(got["token"]).name == "dev"
    with pytest.raises(ApiError):
        hv.authenticate(first)  # the claim replaced the token
    assert hv.machine_placements(desk)[0]["claimed"] is True
    assert next(a for a in hv.roster() if a["name"] == "dev")["machine"] == "desk-top"
    with pytest.raises(ApiError):
        hv.machine_placements(Actor("agent", "dev", ["programmer"]))

    assert hv.remove_machine(ADMIN_ACTOR, "desk-top") == {"name": "desk-top",
                                                          "unplaced": ["dev"]}
    with pytest.raises(ApiError):
        hv.authenticate_machine(m["token"])
    with pytest.raises(ApiError):  # the unpaired computer's agents are cut off too
        hv.authenticate(got["token"])
    assert next(a for a in hv.roster() if a["name"] == "dev")["machine"] is None
    hv.place_agent(ADMIN_ACTOR, "dev", "desk-top-2")
    hv.remove_agent(ADMIN_ACTOR, "dev")
    assert hv.machine_placements(other) == []


def test_machine_tokens_only_reach_machine_endpoints(tmp_path):
    hv = create_hive(f"sqlite:///{tmp_path / 'h.db'}")
    srv = serve(create_app(hv, admin_token=ADMIN))
    url = f"http://127.0.0.1:{srv.server_port}"
    try:
        admin = ServerClient(url, ADMIN)
        code = admin.post("/machines/invite")["code"]
        m = ServerClient(url, "").post("/machines/pair", {"code": code, "name": "box"})
        box = ServerClient(url, m["token"])
        agent_token = admin.post("/agents", {"name": "dev", "vendor": "codex",
                                             "roles": ["programmer"]})["token"]
        for path in ("/agents/me", "/agents", "/fleet", "/machines"):
            with pytest.raises(HiveError) as e:
                box.get(path)
            assert e.value.status == 403, path
        with pytest.raises(HiveError) as e:  # an agent's token isn't a machine's
            ServerClient(url, agent_token).post("/machines/me/placements")
        assert e.value.status == 403
        admin.put("/agents/dev/placement", {"machine": "box"})
        assert [p["agent"] for p in box.post("/machines/me/placements")] == ["dev"]
        assert box.post("/machines/me/claim/dev")["token"].startswith("hv_")
    finally:
        srv.shutdown()


@pytest.fixture
def two_computers(tmp_path, monkeypatch, capsys):
    """An admin's computer and a second one, each with its own clone and ~/.hive."""
    origin = tmp_path / "origin.git"
    git(tmp_path, "init", "--bare", "-b", "main", str(origin))
    admin_clone = tmp_path / "a" / "app"
    git(tmp_path, "clone", str(origin), str(admin_clone))
    for k, v in (("user.email", "t@example.com"), ("user.name", "t")):
        git(admin_clone, "config", k, v)
    (admin_clone / "README.md").write_text("app\n")
    hv = create_hive(f"sqlite:///{tmp_path / 'hive.db'}")
    srv = serve(create_app(hv, admin_token=ADMIN))
    url = f"http://127.0.0.1:{srv.server_port}"
    monkeypatch.chdir(admin_clone)
    cli.main(["login", "--server", url, "--token", ADMIN])
    cli.main(["init"])
    git(admin_clone, "add", "-A")
    git(admin_clone, "commit", "-m", "init")
    git(admin_clone, "push", "origin", "main")
    worker_clone = tmp_path / "b" / "app"
    git(tmp_path, "clone", str(origin), str(worker_clone))
    capsys.readouterr()

    def as_admin():
        monkeypatch.setenv("HIVE_HOME", str(tmp_path / "home-admin"))
        monkeypatch.chdir(admin_clone)

    def as_worker():
        monkeypatch.setenv("HIVE_HOME", str(tmp_path / "home-worker"))
        monkeypatch.chdir(worker_clone)

    try:
        yield hv, url, as_admin, as_worker, worker_clone
    finally:
        srv.shutdown()


def test_pair_a_computer_and_place_agents_on_it(two_computers, tmp_path, capsys):
    hv, url, as_admin, as_worker, worker_clone = two_computers
    as_admin()
    cli.main(["--color", "never", "machines", "invite"])
    out, err = capsys.readouterr()
    line = next(ln.strip() for ln in out.splitlines() if ln.strip().startswith("hive join "))
    code = line.split()[2]
    assert "only works on this computer" in err  # a 127.0.0.1 server: say how to share it
    assert "127.0.0.1" not in line  # ...and print the address others reach it at

    as_worker()
    cli.main(["join", code, "--server", url, "--as", "desk"])
    out = capsys.readouterr().out
    assert "This computer is now desk" in out and "--on desk" in out
    pairing = load_machine_config().machines[url]
    assert pairing["name"] == "desk" and pairing["repo"] == str(worker_clone)
    with pytest.raises(SystemExit):
        cli.main(["join", code, "--server", url])  # the code worked once

    as_admin()
    with pytest.raises(SystemExit):
        cli.main(["agents", "add", "claude", "--on", "nope"])
    cli.main(["agents", "add", "2", "claude", "programmers", "--on", "desk"])
    assert load_machine_config().slots == []  # nothing set up on the admin's computer
    assert {a["name"]: a["machine"] for a in hv.roster()} == {"claude-1": "desk",
                                                             "claude-2": "desk"}

    as_worker()
    res = machines.sync()
    assert [s.name for s in res.added] == ["claude-1", "claude-2"] and not res.errors
    for s in load_machine_config().slots:
        assert s.placed and s.vendor == "claude" and s.server_url == url
        assert s.worktree == str(tmp_path / "b" / f"app-{s.name}")
        assert ServerClient(url, s.token).get("/agents/me")["name"] == s.name
    assert not machines.sync().added  # already set up: nothing changes, no new tokens
    token = load_machine_config().slot("claude-1").token
    assert ServerClient(url, token).get("/agents/me")["name"] == "claude-1"

    as_admin()
    cli.main(["agents"])
    assert "desk" in capsys.readouterr().out
    cli.main(["agents", "edit", "claude-2", "--on", "none"])
    as_worker()
    res = machines.sync()
    assert res.removed == ["claude-2"]
    assert [s.name for s in load_machine_config().slots] == ["claude-1"]

    # A running daemon picks placements up without a restart.
    from hive.daemon import core
    from hive.daemon.store import Store
    d = core.Daemon(load_machine_config(), Store(tmp_path / "w-state.db"), echo=False)
    assert list(d.slots) == ["claude-1"]
    as_admin()
    cli.main(["agents", "add", "codex", "--on", "desk"])
    as_worker()
    d.sync_placements()
    assert sorted(d.slots) == ["claude-1", "codex-1"] and d.slots["codex-1"].me["name"] == \
        "codex-1"
    as_admin()
    cli.main(["agents", "rm", "codex-1", "--yes"])
    as_worker()
    d.slots["claude-1"].running = True  # mid-turn: left alone
    d.sync_placements()
    assert sorted(d.slots) == ["claude-1"]

    # The paired computer's home screen knows what it is.
    capsys.readouterr()
    cli.main(["--color", "never"])
    out = capsys.readouterr().out
    assert "paired with" in out and "desk" in out

    as_admin()
    cli.main(["machines", "rm", "desk", "--yes"])
    assert hv.list_machines(Actor("admin", "user", [])) == []
    as_worker()
    assert machines.sync().errors  # its token no longer works
