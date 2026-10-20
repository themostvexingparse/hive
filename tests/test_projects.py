"""Several projects on one server: routing, isolation and the two kinds of admin token."""

import pytest
from werkzeug.test import Client

from hive.server.router import Server
from hive.server.service import ADMIN

OWNER = "owner-secret"


@pytest.fixture
def srv(tmp_path, monkeypatch):
    monkeypatch.delenv("HIVE_PROJECT_DATABASE_URL", raising=False)
    monkeypatch.delenv("HIVE_DATA_DIR", raising=False)
    monkeypatch.delenv("HIVE_ARCHIVE_DIR", raising=False)
    s = Server(OWNER, f"sqlite:///{tmp_path / 'hive.db'}", tick_interval=0)
    return s


def call(client, method, path, token=None, json=None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    r = client.open(path, method=method, json=json, headers=headers)
    return r.status_code, (r.get_json(silent=True) if r.is_json else r.data)


def enroll(c, base, token, name):
    code, out = call(c, "POST", f"{base}/api/v1/agents", token,
                     {"name": name, "vendor": "claude", "roles": ["programmer"]})
    assert code == 201, out
    return out["token"]


def test_projects_are_isolated(srv, tmp_path):
    c = Client(srv)
    code, out = call(c, "POST", "/api/v1/admin/projects", OWNER, {"slug": "acme", "name": "Acme"})
    assert code == 201, out
    acme_admin = out["admin_token"]
    assert out["project"]["path"] == "/p/acme"
    assert (tmp_path / "projects" / "acme" / "hive.db").exists()
    _, out = call(c, "POST", "/api/v1/admin/projects", OWNER, {"slug": "beta"})
    beta_admin = out["admin_token"]

    # Same agent name in two projects: they are different agents with different tokens.
    a_tok = enroll(c, "/p/acme", acme_admin, "dev")
    b_tok = enroll(c, "/p/beta", OWNER, "dev")  # the owner token works in every project
    assert call(c, "GET", "/p/acme/api/v1/agents/me", a_tok)[1]["name"] == "dev"
    assert call(c, "GET", "/p/beta/api/v1/agents/me", a_tok)[0] == 401
    assert call(c, "GET", "/api/v1/agents/me", a_tok)[0] == 401  # nor in the default project

    # A project's admin token is admin there and nowhere else.
    assert call(c, "GET", "/p/acme/api/v1/admin/check", acme_admin)[1] == {
        "ok": True, "project": "acme", "owner": False}
    assert call(c, "GET", "/p/beta/api/v1/admin/check", acme_admin)[0] == 403 or \
        call(c, "GET", "/p/beta/api/v1/admin/check", acme_admin)[0] == 401
    assert call(c, "GET", "/api/v1/admin/check", acme_admin)[0] in (401, 403)
    assert call(c, "GET", "/p/beta/api/v1/admin/check", beta_admin)[0] == 200

    # Data stays in its project: a task made in acme is not in beta or default.
    code, t = call(c, "POST", "/p/acme/api/v1/tasks", acme_admin, {"title": "Acme only"})
    assert code in (200, 201), t
    tid = t["id"]
    assert [x["title"] for x in call(c, "GET", "/p/acme/api/v1/tasks", a_tok)[1]] == ["Acme only"]
    assert call(c, "GET", "/p/beta/api/v1/tasks", b_tok)[1] == []
    assert call(c, "GET", f"/p/beta/api/v1/tasks/{tid}", b_tok)[0] == 404
    assert call(c, "GET", "/api/v1/tasks", OWNER)[1] == []

    # Each project has its own fleet state.
    assert call(c, "GET", "/p/acme/api/v1/health")[1]["project"] == "acme"
    assert call(c, "GET", "/api/v1/health")[1]["project"] == "default"


def test_project_management_is_for_the_owner(srv):
    c = Client(srv)
    _, out = call(c, "POST", "/api/v1/admin/projects", OWNER, {"slug": "acme"})
    acme_admin = out["admin_token"]
    # A project admin can't list, create or change projects.
    assert call(c, "GET", "/api/v1/admin/projects", acme_admin)[0] == 403
    assert call(c, "POST", "/p/acme/api/v1/admin/projects", acme_admin, {"slug": "x"})[0] == 404
    slugs = [p["slug"] for p in call(c, "GET", "/api/v1/admin/projects", OWNER)[1]]
    assert slugs == ["default", "acme"]

    for bad in ("Acme", "a", "default", "api", "-x", "x" * 40):
        assert call(c, "POST", "/api/v1/admin/projects", OWNER, {"slug": bad})[0] == 400, bad
    assert call(c, "POST", "/api/v1/admin/projects", OWNER, {"slug": "acme"})[0] == 409

    # Rotating the project's admin token retires the old one at once.
    _, out = call(c, "POST", "/api/v1/admin/projects/acme/token", OWNER)
    assert call(c, "GET", "/p/acme/api/v1/admin/check", acme_admin)[0] == 403
    assert call(c, "GET", "/p/acme/api/v1/admin/check", out["admin_token"])[0] == 200

    # An archived project is no longer served; un-archiving brings it back with its data.
    code, p = call(c, "PATCH", "/api/v1/admin/projects/acme", OWNER, {"archived": True})
    assert code == 200 and p["archived"]
    assert call(c, "GET", "/p/acme/api/v1/health")[0] == 404
    call(c, "PATCH", "/api/v1/admin/projects/acme", OWNER, {"archived": False})
    assert call(c, "GET", "/p/acme/api/v1/health")[0] == 200
    assert call(c, "PATCH", "/api/v1/admin/projects/nope", OWNER, {"name": "x"})[0] == 404


def test_routing_details(srv, tmp_path, monkeypatch):
    c = Client(srv)
    call(c, "POST", "/api/v1/admin/projects", OWNER, {"slug": "acme"})
    assert call(c, "GET", "/p/nope/api/v1/health")[0] == 404
    assert call(c, "GET", "/p/BAD!/api/v1/health")[0] == 404
    r = c.get("/p/acme")
    assert r.status_code == 308 and r.headers["Location"].endswith("/p/acme/")
    r = c.get("/p/acme/")
    assert r.status_code == 200 and b"<html" in r.data.lower()

    # Archives are kept per project.
    tok = call(c, "POST", "/api/v1/admin/projects", OWNER, {"slug": "beta"})[1]["admin_token"]
    code, meta = call(c, "POST", "/p/beta/api/v1/admin/archives", tok, {"label": "beta"})
    assert code == 201, meta
    assert len(call(c, "GET", "/p/beta/api/v1/admin/archives", tok)[1]) == 1
    assert call(c, "GET", "/p/acme/api/v1/admin/archives", OWNER)[1] == []
    assert call(c, "GET", "/api/v1/admin/archives", OWNER)[1] == []
    assert any((tmp_path / "projects" / "beta" / "hive-archives").iterdir())

    # A project's GitHub token comes from the environment variable it names.
    monkeypatch.setenv("HIVE_GITHUB_TOKEN_ACME", "ghp_acme")
    call(c, "PATCH", "/api/v1/admin/projects/acme", OWNER,
         {"github_token_env": "HIVE_GITHUB_TOKEN_ACME"})
    assert srv.hive_for("acme").github_token == "ghp_acme"
    assert srv.hive_for("beta").github_token == ""
    assert call(c, "PATCH", "/api/v1/admin/projects/acme", OWNER,
                {"github_token_env": "lower case"})[0] == 400


def test_project_database_template(tmp_path, monkeypatch):
    monkeypatch.setenv("HIVE_PROJECT_DATABASE_URL",
                       f"sqlite:///{(tmp_path / 'dbs').as_posix()}/{{slug}}.db")
    s = Server(OWNER, f"sqlite:///{tmp_path / 'hive.db'}", tick_interval=0)
    c = Client(s)
    assert call(c, "POST", "/api/v1/admin/projects", OWNER, {"slug": "acme"})[0] == 201
    assert (tmp_path / "dbs" / "acme.db").exists()
    # A database that can't be opened leaves no project behind.
    code, out = call(c, "POST", "/api/v1/admin/projects", OWNER,
                     {"slug": "bad", "db_url": "nosuchdriver://x"})
    assert code == 400 and out["error"] == "db_unreachable"
    assert [p["slug"] for p in call(c, "GET", "/api/v1/admin/projects", OWNER)[1]] == [
        "default", "acme"]


def test_cli_manages_and_targets_projects(tmp_path, monkeypatch, capsys):
    from hive import cli
    from hive.config import load_admin_config, load_machine_config
    from test_e2e import serve
    for var in ("HIVE_PROJECT", "HIVE_SERVER_URL", "HIVE_ADMIN_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("HIVE_HOME", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
    s = Server(OWNER, f"sqlite:///{tmp_path / 'hive.db'}", tick_interval=0)
    srv = serve(s)
    url = f"http://127.0.0.1:{srv.server_port}"
    try:
        cli.main(["admin", "login", "--server", url, "--token", OWNER])
        assert "the server's admin" in capsys.readouterr().out
        cli.main(["admin", "project", "create", "acme", "--name", "Acme"])
        out = capsys.readouterr().out
        assert f"{url}/p/acme" in out
        acme_admin = next(w for w in out.split() if w.startswith("hp_"))
        cli.main(["admin", "project", "list"])
        assert "acme" in capsys.readouterr().out

        # --project picks the project; the owner token reaches it without another login.
        cli.main(["admin", "enroll", "dev", "--vendor", "claude", "--roles", "programmer",
                  "--project", "acme"])
        out = capsys.readouterr().out
        assert f"--server {url}/p/acme " in out
        acme_dev = next(w for w in out.split() if w.startswith("hv_"))
        cli.main(["--project", "acme", "status"])
        capsys.readouterr()

        # Inside a repository whose hive.toml names the project, it is the default target.
        repo = tmp_path / "acme-repo"
        repo.mkdir()
        (repo / "hive.toml").write_text(f'[server]\nurl = "{url}/p/acme"\n')
        monkeypatch.chdir(repo)
        assert load_admin_config().target() == (f"{url}/p/acme", OWNER)
        monkeypatch.chdir(tmp_path)
        assert load_admin_config().target() == (url, OWNER)

        # A project admin's login is kept beside the owner's and used for that project only.
        cli.main(["admin", "login", "--server", f"{url}/p/acme", "--token", acme_admin])
        assert "admin of project acme" in capsys.readouterr().out
        cfg = load_admin_config()
        assert cfg.server_url == url and cfg.target("acme") == (f"{url}/p/acme", acme_admin)
        assert cfg.target("beta") == (f"{url}/p/beta", OWNER)

        # The same agent name in the default project is a different agent: the machine
        # keeps one slot per name, so the second join is refused.
        def_dev = s.default_hive.enroll(ADMIN, "dev", "claude", ["programmer"])["token"]
        cli.main(["join", "dev", "--token", acme_dev, "--vendor", "claude",
                  "--server", f"{url}/p/acme", "--worktree", str(tmp_path / "wt-acme")])
        with pytest.raises(SystemExit):
            cli.main(["join", "dev", "--token", def_dev, "--vendor", "claude",
                      "--server", url, "--worktree", str(tmp_path / "wt-def")])
        m = load_machine_config()
        assert m.url_for(m.slot("dev")) == f"{url}/p/acme"
    finally:
        srv.shutdown()
