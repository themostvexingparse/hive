"""The server's WSGI entry: `/p/<slug>/...` goes to that project's own app (its own `Hive` and
database); everything else to the default project, whose app also manages the projects.

Project apps are built on first use and kept. An idle project's database connections are
closed after IDLE_SECONDS; the next request reopens them. Nothing runs in the background, so
this works on hosts with no worker processes (PythonAnywhere): each project's lease expiry,
scheduling and failover run on its own requests, as before.
"""

from __future__ import annotations

import json
import os
import threading
import time

from .app import create_app
from .projects import PREFIX, SLUG_RE, ProjectError, Registry, make_project_engine
from .service import Hive, create_hive

IDLE_SECONDS = 600


class Server:
    def __init__(self, admin_token: str, database_url: str, *, github_token: str = "",
                 sqlite_journal: str | None = None, **hive_kw):
        if not admin_token:
            raise RuntimeError("HIVE_ADMIN_TOKEN must be set")
        self.admin_token = admin_token
        self.sqlite_journal = sqlite_journal
        self.hive_kw = hive_kw
        self.default_hive = create_hive(database_url, sqlite_journal, github_token=github_token,
                                        **hive_kw)
        self.registry = Registry(self.default_hive)
        self.root = create_app(self.default_hive, admin_token, server=self)
        self._apps: dict[str, tuple] = {}  # slug -> (app, hive)
        self._used: dict[str, float] = {}
        self._lock = threading.Lock()
        self._last_sweep = time.time()

    # ------------------------------------------------------------------ projects

    def hive_for(self, slug: str) -> Hive | None:
        entry = self._entry(slug)
        return entry[1] if entry else None

    def _entry(self, slug: str):
        with self._lock:
            entry = self._apps.get(slug)
            if entry:
                self._used[slug] = time.time()
                return entry
        p = self.registry.get(slug)
        if p is None or p.archived:
            return None
        hive = Hive(make_project_engine(p.db_url, self.sqlite_journal),
                    github_token=os.environ.get(p.github_token_env, "")
                    if p.github_token_env else "",
                    archive_dir=str(self.registry.archive_dir(slug)), **self.hive_kw)
        app = create_app(hive, self.admin_token, project=slug,
                         check_project_admin=lambda t: self.registry.check_admin(slug, t))
        with self._lock:
            entry = self._apps.setdefault(slug, (app, hive))  # another thread may have won
            self._used[slug] = time.time()
        if entry[1] is not hive:
            hive.engine.dispose()
        return entry

    def create_project(self, slug: str, name: str = "", db_url: str = "",
                       github_token_env: str = "") -> tuple[dict, str]:
        p, token = self.registry.create(slug, name, db_url, github_token_env)
        try:
            self._entry(slug)  # creates its tables now, so a bad database URL shows at once
        except Exception as e:
            self.registry.delete(slug)
            raise ProjectError(400, "db_unreachable",
                               f"could not open the project's database: {e}") from e
        return p.public(), token

    def forget(self, slug: str) -> None:
        """Drop a cached project so its next request reloads it (settings changed, archived)."""
        with self._lock:
            entry = self._apps.pop(slug, None)
            self._used.pop(slug, None)
        if entry:
            entry[1].engine.dispose()

    def _sweep(self) -> None:
        t = time.time()
        if t - self._last_sweep < 60:
            return
        self._last_sweep = t
        with self._lock:
            idle = [(s, self._apps[s][1]) for s, used in self._used.items()
                    if t - used > IDLE_SECONDS]
        for _, hive in idle:
            hive.engine.dispose()  # the pool reopens on the next request

    # ------------------------------------------------------------------ WSGI

    def __call__(self, environ, start_response):
        self._sweep()
        path = environ.get("PATH_INFO", "") or "/"
        if not path.startswith(PREFIX):
            return self.root(environ, start_response)
        slug, _, rest = path[len(PREFIX):].partition("/")
        if not rest and not path.endswith("/"):
            # The dashboard loads its API relative to its own address: /p/<slug>/
            start_response("308 Permanent Redirect",
                           [("Location", environ.get("SCRIPT_NAME", "") + path + "/"),
                            ("Content-Length", "0")])
            return [b""]
        entry = self._entry(slug) if SLUG_RE.match(slug) else None
        if entry is None:
            body = json.dumps({"error": "no_such_project",
                               "message": f"no project named {slug!r} on this server"}).encode()
            start_response("404 Not Found", [("Content-Type", "application/json"),
                                             ("Content-Length", str(len(body)))])
            return [body]
        environ = dict(environ)
        environ["SCRIPT_NAME"] = environ.get("SCRIPT_NAME", "") + PREFIX + slug
        environ["PATH_INFO"] = "/" + rest
        return entry[0](environ, start_response)


def create_server(admin_token: str | None = None, database_url: str | None = None,
                  **kw) -> Server:
    return Server(admin_token if admin_token is not None
                  else os.environ.get("HIVE_ADMIN_TOKEN", ""),
                  database_url or os.environ.get("HIVE_DATABASE_URL", "sqlite:///hive.db"),
                  github_token=kw.pop("github_token", os.environ.get("HIVE_GITHUB_TOKEN", "")),
                  **kw)
