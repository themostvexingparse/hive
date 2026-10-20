"""`hive join`: each slot keeps the server it was joined to."""

import pytest

from hive import cli
from hive.config import load_machine_config
from hive.daemon.client import ServerClient
from hive.daemon.store import Store
from hive.server.app import create_app
from hive.server.service import create_hive
from test_e2e import ADMIN, serve


def server(tmp_path, name):
    srv = serve(create_app(create_hive(f"sqlite:///{tmp_path / f'{name}.db'}"), admin_token=ADMIN))
    return srv, f"http://127.0.0.1:{srv.server_port}"


def enroll(url, name):
    return ServerClient(url, ADMIN).post("/agents", {"name": name, "vendor": "claude",
                                                     "roles": ["programmer"]})["token"]


def join(tmp_path, name, token, url=None, *extra):
    argv = ["join", name, "--token", token, "--vendor", "claude",
            "--worktree", str(tmp_path / f"wt-{name}"), *extra]
    cli.main(argv + (["--server", url] if url else []))


def test_join_server_belongs_to_the_slot(tmp_path, monkeypatch):
    monkeypatch.setenv("HIVE_HOME", str(tmp_path / "home"))
    srv_a, url_a = server(tmp_path, "a")
    srv_b, url_b = server(tmp_path, "b")
    try:
        join(tmp_path, "dev-a", enroll(url_a, "dev-a"), url_a)
        join(tmp_path, "dev-b", enroll(url_b, "dev-b"), url_b)
        cfg = load_machine_config()
        # Joining dev-b must not move dev-a to server b (it used to rewrite the machine's URL).
        assert cfg.url_for(cfg.slot("dev-a")) == url_a
        assert cfg.url_for(cfg.slot("dev-b")) == url_b
        assert cfg.server_url == url_a  # the first join set the default
        assert cfg.server_urls() == [url_a, url_b]

        # Re-joining without --server keeps the slot where it was.
        token_b = ServerClient(url_b, ADMIN).post("/agents/dev-b/token")["token"]
        join(tmp_path, "dev-b", token_b)
        assert load_machine_config().url_for(load_machine_config().slot("dev-b")) == url_b

        # Moving a slot to another server resets its local state.
        store = Store(tmp_path / "home" / "state.db")
        store.set_cursor("dev-a", 42)
        store.put("dev-a", "session", "s1")
        store.set_cursor("dev-b", 7)
        token = enroll(url_b, "dev-a")
        # Another server's agent of the same name doesn't silently take the slot over.
        with pytest.raises(SystemExit):
            join(tmp_path, "dev-a", token, url_b)
        assert store.cursor("dev-a") == 42
        assert load_machine_config().url_for(load_machine_config().slot("dev-a")) == url_a
        join(tmp_path, "dev-a", token, url_b, "--replace")
        assert load_machine_config().url_for(load_machine_config().slot("dev-a")) == url_b
        assert store.cursor("dev-a") == 0 and store.get("dev-a", "session") is None
        assert store.cursor("dev-b") == 7  # other slots untouched
    finally:
        srv_a.shutdown()
        srv_b.shutdown()
