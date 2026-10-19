"""Localhost-only HTTP API used by `hive mcp` and `hive hook` to reach the daemon."""

from __future__ import annotations

import hmac
import logging

from flask import Flask, abort, jsonify, request
from werkzeug.serving import make_server

from .. import globs as G
from .client import HiveError
from .core import Daemon

log = logging.getLogger("hive.daemon")


def create_local_app(daemon: Daemon) -> Flask:
    app = Flask("hive-local")
    secret = daemon.mcfg.local_secret

    @app.before_request
    def _auth():
        if not hmac.compare_digest(request.headers.get("X-Hive-Local", ""), secret):
            abort(401)

    def slot_or_404(slot):
        if slot not in daemon.slots:
            abort(404)
        return daemon.slots[slot]

    @app.get("/slots")
    def slots():
        return jsonify([{"name": s.name, "worktree": s.cfg.worktree, "vendor": s.cfg.vendor,
                         "running": s.running, "attached": s.attached, "roles": s.roles,
                         "fleet": s.fleet.get("state")} for s in daemon.slots.values()])

    @app.post("/slot/<slot>/hook/<event>")
    def hook(slot, event):
        slot_or_404(slot)
        return jsonify(daemon.handle_hook(slot, event, request.get_json(silent=True) or {}))

    @app.post("/slot/<slot>/tool/<name>")
    def tool(slot, name):
        slot_or_404(slot)
        try:
            return jsonify({"result": daemon.call_tool(slot, name,
                                                       request.get_json(silent=True) or {})})
        except HiveError as e:
            return jsonify({"error": e.code, "message": e.message, **{
                k: v for k, v in e.data.items() if k not in ("error", "message")}}), 200
        except KeyError as e:
            return jsonify({"error": "bad_args", "message": f"missing argument {e}"}), 200
        except Exception as e:  # surface git errors etc. to the agent instead of a 500
            log.exception("tool %s failed", name)
            return jsonify({"error": "internal", "message": str(e)[:2000]}), 200

    @app.post("/slot/<slot>/attach")
    def attach(slot):
        rt = slot_or_404(slot)
        rt.attached = bool((request.get_json(silent=True) or {}).get("attached"))
        return jsonify({"attached": rt.attached, "running": rt.running})

    @app.post("/slot/<slot>/check-paths")
    def check_paths(slot):
        rt = slot_or_404(slot)
        paths = (request.get_json(silent=True) or {}).get("paths", [])
        allowed = list(rt.me.get("write_globs", []))
        if rt.me.get("can_code"):
            allowed += [g for l in rt.leases for g in l["globs"]]
        return jsonify({"uncovered": [p for p in paths if not G.covered(p, allowed)]})

    return app


def serve(daemon: Daemon):
    server = make_server("127.0.0.1", daemon.mcfg.local_port, create_local_app(daemon),
                         threaded=True)
    log.info("local API on 127.0.0.1:%d", daemon.mcfg.local_port)
    return server
