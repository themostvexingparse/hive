"""Flask/WSGI application exposing the REST API (SPEC §14) and the dashboard."""

from __future__ import annotations

import hmac
import os
import tempfile
import time
from functools import wraps
from importlib import resources

from flask import Flask, Response, g, jsonify, request
from sqlalchemy.exc import OperationalError

from .service import ADMIN, Actor, ApiError, Hive, create_hive


def create_app(hive: Hive | None = None, admin_token: str | None = None) -> Flask:
    admin_token = admin_token if admin_token is not None else os.environ.get("HIVE_ADMIN_TOKEN", "")
    if not admin_token:
        raise RuntimeError("HIVE_ADMIN_TOKEN must be set")
    if hive is None:
        hive = create_hive(os.environ.get("HIVE_DATABASE_URL", "sqlite:///hive.db"),
                           github_token=os.environ.get("HIVE_GITHUB_TOKEN", ""))
    app = Flask(__name__)
    app.config["hive"] = hive

    @app.errorhandler(ApiError)
    def _api_error(e: ApiError):
        return jsonify({"error": e.code, "message": e.message, **e.extra}), e.status

    @app.errorhandler(OperationalError)
    def _db_busy(e: OperationalError):
        # SQLite under contention (or a slow network filesystem): ask the client to retry.
        if "locked" in str(e.orig).lower() or "busy" in str(e.orig).lower():
            resp = jsonify({"error": "db_busy", "message": "database busy; retry shortly"})
            resp.headers["Retry-After"] = "1"
            return resp, 503
        raise e

    @app.before_request
    def _tick():
        hive.tick()

    def auth(admin: bool = False):
        def deco(fn):
            @wraps(fn)
            def wrapper(*a, **kw):
                header = request.headers.get("Authorization", "")
                token = header[7:] if header.startswith("Bearer ") else ""
                if not token:
                    raise ApiError(401, "no_token", "Authorization: Bearer <token> required")
                if hmac.compare_digest(token, admin_token):
                    g.actor = ADMIN
                elif admin:
                    raise ApiError(403, "forbidden", "admin token required")
                else:
                    g.actor = hive.authenticate(token)
                return fn(*a, **kw)
            return wrapper
        return deco

    def body() -> dict:
        data = request.get_json(silent=True)
        if data is None:
            data = {}
        if not isinstance(data, dict):
            raise ApiError(400, "bad_json", "JSON object body required")
        return data

    def arg_int(name: str, default: int = 0) -> int:
        try:
            return int(request.args.get(name, default))
        except ValueError:
            raise ApiError(400, "bad_arg", f"{name} must be an integer")

    def me() -> Actor:
        return g.actor

    api = "/api/v1"

    # ---------------------------------------------------------------- agents & config
    @app.put(f"{api}/config")
    @auth(admin=True)
    def put_config():
        return jsonify(hive.set_config(me(), body()))

    @app.post(f"{api}/agents")
    @auth(admin=True)
    def enroll():
        b = body()
        return jsonify(hive.enroll(me(), b.get("name", ""), b.get("vendor", ""),
                                   list(b.get("roles", [])))), 201

    @app.post(f"{api}/agents/<name>/token")
    @auth(admin=True)
    def rotate(name):
        return jsonify(hive.rotate_token(me(), name))

    @app.get(f"{api}/agents")
    @auth()
    def roster():
        return jsonify(hive.roster())

    @app.patch(f"{api}/agents/<name>")
    @auth()
    def patch_agent(name):
        return jsonify(hive.set_roles(me(), name, list(body().get("roles", []))))

    @app.get(f"{api}/agents/me")
    @auth()
    def whoami():
        return jsonify(hive.whoami(me()))

    @app.post(f"{api}/agents/me/heartbeat")
    @auth()
    def heartbeat():
        b = body()
        return jsonify(hive.heartbeat(me(), bool(b.get("busy")), bool(b.get("attached"))))

    @app.post(f"{api}/agents/me/quota")
    @auth()
    def quota():
        b = body()
        return jsonify(hive.report_quota(me(), float(b.get("until", 0)), b.get("detail", "")))

    # ---------------------------------------------------------------- events & messages
    @app.get(f"{api}/events")
    @auth()
    def list_events():
        return jsonify(hive.list_events(me(), arg_int("since"), arg_int("limit", 200)))

    @app.post(f"{api}/messages")
    @auth()
    def post_message():
        b = body()
        thread = b.get("thread_id")
        return jsonify(hive.post_message(
            me(), b.get("body", ""), b.get("channel", "#general"), b.get("recipient"),
            b.get("priority", "normal"), int(thread) if thread else None)), 201

    @app.get(f"{api}/messages")
    @auth()
    def list_messages():
        return jsonify(hive.list_messages(me(), request.args.get("channel"),
                                          arg_int("since_id"), arg_int("limit", 100),
                                          request.args.get("dm_with")))

    @app.post(f"{api}/messages/ack")
    @auth()
    def ack():
        return jsonify(hive.ack(me(), [int(i) for i in body().get("ids", [])]))

    # ---------------------------------------------------------------- tasks
    @app.post(f"{api}/tasks")
    @auth()
    def create_task():
        b = body()
        return jsonify(hive.create_task(
            me(), b.get("title", ""), b.get("spec", ""), b.get("kind", "code"),
            [int(d) for d in b.get("deps", [])], list(b.get("path_hints", [])),
            int(b.get("priority", 0)), b.get("assignee"))), 201

    @app.get(f"{api}/tasks")
    @auth()
    def list_tasks():
        return jsonify(hive.list_tasks(request.args.get("state"), request.args.get("assignee")))

    @app.get(f"{api}/tasks/<int:tid>")
    @auth()
    def get_task(tid):
        return jsonify(hive.get_task(tid))

    @app.patch(f"{api}/tasks/<int:tid>")
    @auth()
    def patch_task(tid):
        b = body()
        return jsonify(hive.update_task(me(), tid, b.get("state"), b.get("note", "")))

    @app.post(f"{api}/tasks/<int:tid>/claim")
    @auth()
    def claim(tid):
        return jsonify(hive.claim_task(me(), tid))

    @app.post(f"{api}/tasks/<int:tid>/assign")
    @auth()
    def assign(tid):
        return jsonify(hive.assign_task(me(), tid, body().get("to", "")))

    @app.post(f"{api}/tasks/<int:tid>/handoff")
    @auth()
    def handoff(tid):
        b = body()
        return jsonify(hive.handoff_task(me(), tid, b.get("to", ""), b.get("notes", "")))

    @app.post(f"{api}/tasks/<int:tid>/complete")
    @auth()
    def complete(tid):
        return jsonify(hive.complete_task(me(), tid, body().get("result", "")))

    @app.post(f"{api}/tasks/<int:tid>/cancel")
    @auth()
    def cancel(tid):
        return jsonify(hive.cancel_task(me(), tid, body().get("reason", "")))

    @app.post(f"{api}/tasks/<int:tid>/review")
    @auth()
    def review(tid):
        b = body()
        return jsonify(hive.review_verdict(me(), tid, bool(b.get("approve")), b.get("notes", "")))

    @app.post(f"{api}/stuck")
    @auth()
    def stuck():
        b = body()
        tid = b.get("task_id")
        return jsonify(hive.report_stuck(me(), int(tid) if tid else None, b.get("details", "")))

    # ---------------------------------------------------------------- leases & integration
    @app.post(f"{api}/leases")
    @auth()
    def acquire():
        b = body()
        tid = b.get("task_id")
        return jsonify(hive.acquire_lease(me(), list(b.get("globs", [])), int(b.get("ttl", 600)),
                                          b.get("reason", ""), int(tid) if tid else None)), 201

    @app.get(f"{api}/leases")
    @auth()
    def list_leases():
        return jsonify(hive.list_leases(request.args.get("holder"),
                                        request.args.get("all") is None))

    @app.post(f"{api}/leases/<int:lid>/extend")
    @auth()
    def extend(lid):
        return jsonify(hive.extend_lease(me(), lid, int(body().get("ttl", 600))))

    @app.post(f"{api}/leases/<int:lid>/release")
    @auth()
    def release(lid):
        return jsonify(hive.release_lease(me(), lid))

    @app.post(f"{api}/leases/<int:lid>/force-release")
    @auth()
    def force_release(lid):
        return jsonify(hive.force_release_lease(me(), lid, body().get("reason", "")))

    @app.post(f"{api}/integrate")
    @auth()
    def integrate():
        b = body()
        tid = b.get("task_id")
        return jsonify(hive.integrate(me(), b.get("sha", ""), b.get("base_sha", ""),
                                      dict(b.get("leases", {})), int(tid) if tid else None,
                                      list(b.get("paths", []))))

    @app.post(f"{api}/integrate/confirm")
    @auth()
    def integrate_confirm():
        b = body()
        tid = b.get("task_id")
        return jsonify(hive.confirm_integration(me(), b.get("sha", ""), dict(b.get("leases", {})),
                                                int(tid) if tid else None,
                                                list(b.get("paths", []))))

    @app.post(f"{api}/integrate/conflict")
    @auth()
    def integrate_conflict():
        b = body()
        tid = b.get("task_id")
        return jsonify(hive.report_conflict(me(), int(tid) if tid else None, b.get("detail", "")))

    # ---------------------------------------------------------------- fleet & decisions
    @app.get(f"{api}/fleet")
    @auth()
    def get_fleet():
        return jsonify(hive.get_fleet())

    @app.post(f"{api}/fleet/start")
    @auth(admin=True)
    def start():
        return jsonify(hive.start_fleet(me(), body().get("goal", "")))

    @app.post(f"{api}/fleet/<action>")
    @auth()
    def fleet_action(action):
        if action == "reset":
            return jsonify(hive.reset_fleet(me()))
        return jsonify(hive.fleet_control(me(), action))

    @app.post(f"{api}/decisions")
    @auth()
    def request_decision():
        b = body()
        tid = b.get("task_id")
        return jsonify(hive.request_decision(
            me(), b.get("kind", "question"), b.get("question", ""), list(b.get("options", [])),
            b.get("context", ""), int(tid) if tid else None, b.get("freeze"))), 201

    @app.get(f"{api}/decisions")
    @auth()
    def list_decisions():
        return jsonify(hive.list_decisions(request.args.get("state")))

    @app.post(f"{api}/decisions/<int:did>/answer")
    @auth(admin=True)
    def answer(did):
        b = body()
        return jsonify(hive.answer_decision(me(), did, b.get("answer", ""), b.get("note", "")))

    # ---------------------------------------------------------------- status feed
    @app.post(f"{api}/status")
    @auth()
    def publish_status():
        b = body()
        return jsonify(hive.publish_status(me(), b.get("summary", ""), b.get("body", ""),
                                           b.get("level", "fyi"))), 201

    @app.get(f"{api}/status")
    @auth()
    def list_status():
        return jsonify(hive.list_status(arg_int("since_id"), arg_int("limit", 50)))

    # ---------------------------------------------------------------- live streams (admin view)
    @app.post(f"{api}/agents/me/stream")
    @auth()
    def append_stream():
        b = body()
        return jsonify(hive.append_stream(me(), str(b.get("run", "")), list(b.get("lines", []))))

    @app.get(f"{api}/streams")
    @auth(admin=True)
    def read_stream():
        return jsonify(hive.read_stream(me(), request.args.get("agent"), arg_int("since_id"),
                                        arg_int("limit", 500)))

    # ---------------------------------------------------------------- reports
    @app.post(f"{api}/reports/requests")
    @auth()
    def request_report():
        b = body()
        tid = b.get("task_id")
        return jsonify(hive.request_report(me(), b.get("target", ""), b.get("about", ""),
                                           int(tid) if tid else None)), 201

    @app.post(f"{api}/reports")
    @auth()
    def submit_report():
        b = body()
        tid, rid = b.get("task_id"), b.get("request_id")
        return jsonify(hive.submit_report(me(), b.get("summary", ""), b.get("body", ""),
                                          int(tid) if tid else None,
                                          int(rid) if rid else None)), 201

    @app.get(f"{api}/reports")
    @auth()
    def list_reports():
        a = request.args
        return jsonify(hive.list_reports(a.get("kind"), a.get("author"), a.get("target"),
                                         a.get("state"), arg_int("limit", 50)))

    @app.get(f"{api}/reports/<int:rid>")
    @auth()
    def get_report(rid):
        return jsonify(hive.get_report(rid))

    @app.get(f"{api}/admin/backup")
    @auth(admin=True)
    def backup():
        """Download a consistent copy of a SQLite database (made inside this process)."""
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            hive.backup_sqlite(path)
        except BaseException:
            os.remove(path)
            raise

        def stream():
            try:
                with open(path, "rb") as f:
                    while chunk := f.read(1 << 20):
                        yield chunk
            finally:
                os.remove(path)

        name = time.strftime("hive-%Y%m%d-%H%M%S.db")
        return Response(stream(), mimetype="application/vnd.sqlite3",
                        headers={"Content-Disposition": f'attachment; filename="{name}"',
                                 "Content-Length": str(os.path.getsize(path))})

    @app.get(f"{api}/health")
    def health():
        out = {"ok": True}
        if hive.is_sqlite:
            out["sqlite_journal"] = getattr(hive.engine, "hive_sqlite_journal", None)
        return jsonify(out)

    # ---------------------------------------------------------------- dashboard
    @app.get("/")
    def dashboard():
        html = resources.files("hive.server").joinpath("dashboard.html").read_text("utf-8")
        return Response(html, mimetype="text/html")

    return app
