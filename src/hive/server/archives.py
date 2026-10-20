"""Archive files: gzip-compressed JSON snapshots of a fleet's history, kept on the server's disk.

Each archive is `<id>.json.gz` plus a small `<id>.meta.json` so listing never decompresses the
(possibly large) archives themselves. Ids are timestamps (`20261020-101500`, with a `-2` suffix
on collisions) and are validated before they touch the filesystem.
"""

from __future__ import annotations

import gzip
import json
import os
import re
import time
from pathlib import Path

FORMAT, VERSION = "hive-archive", 1
ID_RE = re.compile(r"^\d{8}-\d{6}(?:-\d{1,3})?$")


class ArchiveStore:
    def __init__(self, directory: str | os.PathLike):
        self.dir = Path(directory).expanduser()

    def _paths(self, aid: str) -> tuple[Path, Path]:
        if not ID_RE.match(aid or ""):
            raise KeyError(aid)
        return self.dir / f"{aid}.json.gz", self.dir / f"{aid}.meta.json"

    def new_id(self, ts: float) -> str:
        base = time.strftime("%Y%m%d-%H%M%S", time.localtime(ts))
        aid, n = base, 1
        while self._paths(aid)[0].exists() or self._paths(aid)[1].exists():
            n += 1
            aid = f"{base}-{n}"
        return aid

    def write(self, aid: str, data: dict, meta: dict) -> dict:
        """Write the archive atomically (temp file + rename), data first, then its metadata."""
        self.dir.mkdir(parents=True, exist_ok=True)
        gz, mp = self._paths(aid)
        raw = json.dumps(data, separators=(",", ":"), default=str).encode("utf-8")
        tmp = gz.with_name(gz.name + ".tmp")
        with open(tmp, "wb") as f:
            # mtime=0 keeps the bytes reproducible; level 6 is a good size/speed trade-off.
            with gzip.GzipFile(fileobj=f, mode="wb", compresslevel=6, mtime=0) as z:
                z.write(raw)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, gz)
        meta = {**meta, "id": aid, "size": gz.stat().st_size, "raw_size": len(raw)}
        tmp = mp.with_name(mp.name + ".tmp")
        tmp.write_text(json.dumps(meta), encoding="utf-8")
        os.replace(tmp, mp)
        return meta

    def list(self) -> list[dict]:
        if not self.dir.is_dir():
            return []
        out = []
        for mp in self.dir.glob("*.meta.json"):
            aid = mp.name[:-len(".meta.json")]
            if not ID_RE.match(aid) or not (self.dir / f"{aid}.json.gz").exists():
                continue
            try:
                out.append(json.loads(mp.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
        return sorted(out, key=lambda m: m.get("created", 0), reverse=True)

    def meta(self, aid: str) -> dict:
        _, mp = self._paths(aid)
        try:
            return json.loads(mp.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise KeyError(aid)

    def path(self, aid: str) -> Path:
        gz, _ = self._paths(aid)
        if not gz.exists():
            raise KeyError(aid)
        return gz

    def delete(self, aid: str) -> None:
        gz, mp = self._paths(aid)
        if not gz.exists() and not mp.exists():
            raise KeyError(aid)
        for p in (gz, mp):
            p.unlink(missing_ok=True)


def default_dir(url) -> Path:
    """$HIVE_ARCHIVE_DIR, else next to a SQLite database file, else ~/hive-archives (e.g. MySQL
    on PythonAnywhere). `url` is the engine's SQLAlchemy URL."""
    explicit = os.environ.get("HIVE_ARCHIVE_DIR")
    if explicit:
        return Path(explicit).expanduser()
    if url.get_backend_name() == "sqlite" and url.database not in (None, "", ":memory:"):
        return Path(url.database).expanduser().resolve().parent / "hive-archives"
    return Path.home() / "hive-archives"
