"""Projects: one server, several fleets. Each project is its own `Hive` with its own database,
served under `/p/<slug>`; the server's own database is the `default` project and also holds
this registry.

A project's admin token is stored hashed, like agent tokens; it acts as the admin inside that
project only. The server's admin token (the owner's) works everywhere and alone manages
projects.
"""

from __future__ import annotations

import hashlib
import os
import re
import secrets
import time
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import Boolean, Column, Float, MetaData, String, Table, insert, select, update

from . import db
from .archives import default_dir

SLUG_RE = re.compile(r"^[a-z][a-z0-9-]{1,31}$")
DEFAULT = "default"
RESERVED = {DEFAULT, "api", "admin", "static"}
PREFIX = "/p/"

registry_md = MetaData()  # separate from db.metadata: project databases never get this table

projects = Table(
    "projects", registry_md,
    Column("slug", String(32), primary_key=True),
    Column("name", String(200), nullable=False),
    Column("db_url", String(500), nullable=False),
    Column("admin_token_hash", String(64), nullable=False, unique=True),
    # Name of the environment variable holding this project's GitHub token (never the token).
    Column("github_token_env", String(100), nullable=False, default=""),
    Column("archived", Boolean, nullable=False, default=False),
    Column("created", Float, nullable=False),
)


class ProjectError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_token() -> str:
    return "hp_" + secrets.token_urlsafe(32)


@dataclass
class Project:
    slug: str
    name: str
    db_url: str
    admin_token_hash: str
    github_token_env: str
    archived: bool
    created: float

    def public(self) -> dict:
        return {"slug": self.slug, "name": self.name, "path": PREFIX + self.slug,
                "github_token_env": self.github_token_env, "archived": self.archived,
                "created": self.created,
                "database": "sqlite" if self.db_url.startswith("sqlite") else
                self.db_url.split(":", 1)[0]}


class Registry:
    """The projects table, in the default project's database."""

    def __init__(self, hive, data_dir: Path | None = None):
        # The default project's Hive: its transactions take the right locks (SQLite).
        self.hive = hive
        registry_md.create_all(hive.engine)
        self.data_dir = data_dir or _data_dir(hive.engine.url)

    def get(self, slug: str) -> Project | None:
        with self.hive._read() as c:
            r = c.execute(select(projects).where(projects.c.slug == slug)).first()
        return Project(**r._mapping) if r else None

    def list(self) -> list[Project]:
        with self.hive._read() as c:
            return [Project(**r._mapping)
                    for r in c.execute(select(projects).order_by(projects.c.slug))]

    def check_admin(self, slug: str, token: str) -> bool:
        p = self.get(slug)
        return bool(p and not p.archived and
                    secrets.compare_digest(p.admin_token_hash, hash_token(token)))

    def create(self, slug: str, name: str = "", db_url: str = "",
               github_token_env: str = "") -> tuple[Project, str]:
        if not SLUG_RE.match(slug or "") or slug in RESERVED:
            raise ProjectError(400, "bad_slug", "project names must match "
                               "^[a-z][a-z0-9-]{1,31}$ and not be one of "
                               + ", ".join(sorted(RESERVED)))
        _check_env_name(github_token_env)
        db_url = db_url or self.default_db_url(slug)
        token = new_token()
        p = Project(slug, (name or slug).strip()[:200], db_url, hash_token(token),
                    github_token_env, False, time.time())
        with self.hive._write() as c:
            if c.execute(select(projects.c.slug).where(projects.c.slug == slug)).first():
                raise ProjectError(409, "exists", f"project {slug!r} already exists")
            c.execute(insert(projects).values(**p.__dict__))
        return p, token

    def update(self, slug: str, **values) -> Project:
        allowed = {k: v for k, v in values.items()
                   if k in ("name", "github_token_env", "archived") and v is not None}
        if "github_token_env" in allowed:
            _check_env_name(allowed["github_token_env"])
        if "name" in allowed:
            allowed["name"] = str(allowed["name"]).strip()[:200] or slug
        self._require(slug)
        if allowed:
            with self.hive._write() as c:
                c.execute(update(projects).where(projects.c.slug == slug).values(**allowed))
        return self._require(slug)

    def rotate(self, slug: str) -> str:
        self._require(slug)
        token = new_token()
        with self.hive._write() as c:
            c.execute(update(projects).where(projects.c.slug == slug)
                      .values(admin_token_hash=hash_token(token)))
        return token

    def delete(self, slug: str) -> None:
        """Only for a project whose database could not be set up; others are archived."""
        with self.hive._write() as c:
            c.execute(projects.delete().where(projects.c.slug == slug))

    def _require(self, slug: str) -> Project:
        p = self.get(slug)
        if p is None:
            raise ProjectError(404, "no_such_project", f"no project named {slug!r}")
        return p

    def default_db_url(self, slug: str) -> str:
        """$HIVE_PROJECT_DATABASE_URL with {slug} filled in; else a SQLite file per project
        under the data folder. A MySQL/Postgres server with no template needs an explicit URL,
        since only you can create its databases (on PythonAnywhere: the Databases tab)."""
        template = os.environ.get("HIVE_PROJECT_DATABASE_URL", "")
        if template:
            if "{slug}" not in template:
                raise ProjectError(500, "bad_template",
                                   "HIVE_PROJECT_DATABASE_URL must contain {slug}")
            return template.replace("{slug}", slug)
        if not self.hive.is_sqlite:
            raise ProjectError(400, "db_url_required",
                               "this server's database is not SQLite: create the project's "
                               "database, then pass its URL (db_url), or set "
                               "HIVE_PROJECT_DATABASE_URL with a {slug} placeholder")
        path = (self.data_dir / "projects" / slug / "hive.db").resolve()
        return "sqlite:///" + path.as_posix()

    def archive_dir(self, slug: str) -> Path:
        return self.data_dir / "projects" / slug / "hive-archives"


def _check_env_name(name: str) -> None:
    if name and not re.fullmatch(r"[A-Z_][A-Z0-9_]{0,99}", name):
        raise ProjectError(400, "bad_env", "github_token_env must be an environment variable "
                           "name such as HIVE_GITHUB_TOKEN_ACME")


def _data_dir(url) -> Path:
    """Where project databases and archives live: $HIVE_DATA_DIR, else next to a SQLite
    database file, else the default archive folder's parent."""
    explicit = os.environ.get("HIVE_DATA_DIR")
    if explicit:
        return Path(explicit).expanduser()
    if url.get_backend_name() == "sqlite" and url.database not in (None, "", ":memory:"):
        return Path(url.database).expanduser().resolve().parent
    return default_dir(url).parent / "hive-data"


def make_project_engine(url: str, sqlite_journal: str | None = None):
    """Project databases are many and mostly idle: keep their pools small."""
    return db.make_engine(url, sqlite_journal, small_pool=True)
