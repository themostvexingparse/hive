"""Flask/WSGI application exposing the REST API (SPEC §14) and the dashboard."""

from __future__ import annotations

import gzip
import hmac
import os
import tempfile
import time
from functools import wraps
from importlib import resources

from flask import Flask, Response, g, jsonify, request
from sqlalchemy.exc import OperationalError

from .projects import DEFAULT, ProjectError
from .service import ADMIN, Actor, ApiError, Hive, create_hive


def create_app(hive: Hive | None = None, admin_token: str | None = None, *,
               project: str = DEFAULT, check_project_admin=None, server=None) -> Flask:
    """One project's API and dashboard. `admin_token` is the server owner's and works in every
    project; `check_project_admin(token)` also accepts that project's own admin token. The
    default project's app gets `server` (the router) and manages the other projects."""
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

    @app.errorhandler(ProjectError)
    def _project_error(e: ProjectError):
        return jsonify({"error": e.code, "message": e.message}), e.status

    @app.errorhandler(OperationalError)
    def _db_busy(e: OperationalError):
        # SQLite under contention (or a slow network filesystem): ask the client to retry.
        msg = str(e.orig).lower()
        if any(k in msg for k in ("locked", "busy", "deadlock", "lock wait timeout",
                                  "could not serialize")):
            resp = jsonify({"error": "db_busy", "message": "database busy; retry shortly"})
            resp.headers["Retry-After"] = "1"
            return resp, 503
        raise e

    @app.before_request
    def _tick():
        hive.tick()

    def auth(admin: bool = False, machine: bool = False):
        """admin: only the admin. machine: only a paired machine's token (hm_…), which is
        refused everywhere else, so a machine can never act as an agent or as the user."""
        def deco(fn):
            @wraps(fn)
            def wrapper(*a, **kw):
                header = request.headers.get("Authorization", "")
                token = header[7:] if header.startswith("Bearer ") else ""
                if not token:
                    raise ApiError(401, "no_token", "Authorization: Bearer <token> required")
                g.owner = hmac.compare_digest(token.encode("utf-8"), admin_token.encode("utf-8"))
                if machine:
                    if not token.startswith("hm_"):
                        raise ApiError(403, "forbidden", "machine token required")
                    g.actor = hive.authenticate_machine(token)
                elif token.startswith("hm_") and not g.owner:
                    raise ApiError(403, "forbidden", "a machine token only works for /machines/me")
                elif g.owner or (check_project_admin and check_project_admin(token)):
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

    def text(b: dict, name: str, default: str = "") -> str:
        """A string field; null means the default. Anything else answers 400."""
        v = b.get(name)
        if v is None:
            return default
        if not isinstance(v, str):
            raise ApiError(400, "bad_arg", f"{name} must be a string")
        return v

    def opt_text(b: dict, name: str) -> str | None:
        return text(b, name) if b.get(name) is not None else None

    def strs(b: dict, name: str) -> list[str]:
        """A list of strings; null means empty. Anything else answers 400."""
        v = b.get(name)
        if v is None:
            return []
        if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
            raise ApiError(400, "bad_arg", f"{name} must be a list of strings")
        return v

    def opt_flag(b: dict, name: str) -> bool | None:
        v = b.get(name)
        if v is not None and not isinstance(v, bool):
            raise ApiError(400, "bad_arg", f"{name} must be true or false")
        return v

    def arg_int(name: str, default: int = 0) -> int:
        try:
            return int(request.args.get(name, default))
        except ValueError:
            raise ApiError(400, "bad_arg", f"{name} must be an integer")

    def num(value, name: str, kind=int, optional: bool = False):
        """Convert a JSON value to int/float, or answer 400 instead of crashing."""
        if optional and value in (None, ""):
            return None
        try:
            return kind(value)
        except (TypeError, ValueError):
            raise ApiError(400, "bad_arg", f"{name} must be a number")

    def nums(values, name: str) -> list[int]:
        if not isinstance(values, list):
            raise ApiError(400, "bad_arg", f"{name} must be a list of numbers")
        return [num(v, name) for v in values]

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
        return jsonify(hive.enroll(me(), text(b, "name"), text(b, "vendor"),
                                   strs(b, "roles"))), 201

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
        return jsonify(hive.set_roles(me(), name, strs(body(), "roles")))

    @app.delete(f"{api}/agents/<name>")
    @auth(admin=True)
    def remove_agent(name):
        return jsonify(hive.remove_agent(me(), name))

    @app.put(f"{api}/agents/<name>/placement")
    @auth(admin=True)
    def place_agent(name):
        b = body()
        return jsonify(hive.place_agent(me(), name, opt_text(b, "machine") or None,
                                        str(b.get("model") or ""),
                                        str(b.get("permission_mode") or "")))

    # ---------------------------------------------------------------- machines
    @app.post(f"{api}/machines/invite")
    @auth(admin=True)
    def invite_machine():
        b = body()
        return jsonify(hive.create_pairing_code(me(), bool(b.get("reusable")),
                                                num(b.get("days", 0), "days", float),
                                                str(b.get("label") or "")))

    @app.get(f"{api}/machines/keys")
    @auth(admin=True)
    def join_keys():
        return jsonify(hive.list_join_keys(me()))

    @app.delete(f"{api}/machines/keys/<key_id>")
    @auth(admin=True)
    def revoke_join_key(key_id):
        return jsonify(hive.revoke_join_key(me(), key_id))

    @app.post(f"{api}/machines/pair")
    def pair_machine():  # the one-time code is the credential
        b = body()
        return jsonify(hive.pair_machine(text(b, "code"), text(b, "name"),
                                         text(b, "os"), strs(b, "clis"))), 201

    @app.get(f"{api}/machines")
    @auth(admin=True)
    def list_machines():
        return jsonify(hive.list_machines(me()))

    @app.delete(f"{api}/machines/<name>")
    @auth(admin=True)
    def remove_machine(name):
        return jsonify(hive.remove_machine(me(), name))

    @app.post(f"{api}/machines/me/placements")
    @auth(machine=True)
    def my_placements():
        b = body()
        return jsonify(hive.machine_placements(me(), opt_text(b, "os"),
                                               strs(b, "clis") if "clis" in b else None))

    @app.post(f"{api}/machines/me/claim/<agent>")
    @auth(machine=True)
    def claim_agent(agent):
        return jsonify(hive.claim_agent(me(), agent))

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
        return jsonify(hive.report_quota(me(), num(b.get("until", 0), "until", float),
                                         text(b, "detail")))

    # ---------------------------------------------------------------- events & messages
    @app.get(f"{api}/events")
    @auth()
    def list_events():
        return jsonify(hive.list_events(me(), arg_int("since"), arg_int("limit", 200)))

    @app.post(f"{api}/messages")
    @auth()
    def post_message():
        b = body()
        return jsonify(hive.post_message(
            me(), text(b, "body"), text(b, "channel", "#general"), opt_text(b, "recipient"),
            text(b, "priority", "normal"),
            num(b.get("thread_id"), "thread_id", optional=True))), 201

    @app.get(f"{api}/messages")
    @auth()
    def list_messages():
        return jsonify(hive.list_messages(me(), request.args.get("channel"),
                                          arg_int("since_id"), arg_int("limit", 100),
                                          request.args.get("dm_with")))

    @app.post(f"{api}/messages/ack")
    @auth()
    def ack():
        return jsonify(hive.ack(me(), nums(body().get("ids", []), "ids")))

    # ---------------------------------------------------------------- tasks
    @app.post(f"{api}/tasks")
    @auth()
    def create_task():
        b = body()
        return jsonify(hive.create_task(
            me(), text(b, "title"), text(b, "spec"), text(b, "kind", "code"),
            nums(b.get("deps", []), "deps"), strs(b, "path_hints"),
            num(b.get("priority", 0), "priority"), opt_text(b, "assignee"))), 201

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
        return jsonify(hive.update_task(me(), tid, opt_text(b, "state"), text(b, "note")))

    @app.post(f"{api}/tasks/<int:tid>/claim")
    @auth()
    def claim(tid):
        return jsonify(hive.claim_task(me(), tid))

    @app.post(f"{api}/tasks/<int:tid>/assign")
    @auth()
    def assign(tid):
        return jsonify(hive.assign_task(me(), tid, text(body(), "to")))

    @app.post(f"{api}/tasks/<int:tid>/handoff")
    @auth()
    def handoff(tid):
        b = body()
        return jsonify(hive.handoff_task(me(), tid, text(b, "to"), text(b, "notes")))

    @app.post(f"{api}/tasks/<int:tid>/complete")
    @auth()
    def complete(tid):
        return jsonify(hive.complete_task(me(), tid, text(body(), "result")))

    @app.post(f"{api}/tasks/<int:tid>/cancel")
    @auth()
    def cancel(tid):
        return jsonify(hive.cancel_task(me(), tid, text(body(), "reason")))

    @app.post(f"{api}/tasks/<int:tid>/review")
    @auth()
    def review(tid):
        b = body()
        return jsonify(hive.review_verdict(me(), tid, bool(b.get("approve")), text(b, "notes")))

    @app.post(f"{api}/stuck")
    @auth()
    def stuck():
        b = body()
        return jsonify(hive.report_stuck(me(), num(b.get("task_id"), "task_id", optional=True),
                                         text(b, "details")))

    # ---------------------------------------------------------------- leases & integration
    @app.post(f"{api}/leases")
    @auth()
    def acquire():
        b = body()
        return jsonify(hive.acquire_lease(me(), strs(b, "globs"),
                                          num(b.get("ttl", 600), "ttl"), text(b, "reason"),
                                          num(b.get("task_id"), "task_id", optional=True))), 201

    @app.get(f"{api}/leases")
    @auth()
    def list_leases():
        return jsonify(hive.list_leases(request.args.get("holder"),
                                        request.args.get("all") is None))

    @app.post(f"{api}/leases/<int:lid>/extend")
    @auth()
    def extend(lid):
        return jsonify(hive.extend_lease(me(), lid, num(body().get("ttl", 600), "ttl")))

    @app.post(f"{api}/leases/<int:lid>/release")
    @auth()
    def release(lid):
        return jsonify(hive.release_lease(me(), lid))

    @app.post(f"{api}/leases/<int:lid>/force-release")
    @auth()
    def force_release(lid):
        return jsonify(hive.force_release_lease(me(), lid, text(body(), "reason")))

    @app.post(f"{api}/integrate")
    @auth()
    def integrate():
        b = body()
        if not isinstance(b.get("leases", {}), dict):
            raise ApiError(400, "bad_leases", "leases must be an object")
        return jsonify(hive.integrate(me(), text(b, "sha"), text(b, "base_sha"),
                                      dict(b.get("leases", {})),
                                      num(b.get("task_id"), "task_id", optional=True),
                                      strs(b, "paths")))

    @app.post(f"{api}/integrate/confirm")
    @auth()
    def integrate_confirm():
        b = body()
        return jsonify(hive.confirm_integration(me(), str(b.get("grant") or ""),
                                                str(b.get("sha") or ""),
                                                strs(b, "paths")))

    @app.post(f"{api}/integrate/conflict")
    @auth()
    def integrate_conflict():
        b = body()
        return jsonify(hive.report_conflict(me(), num(b.get("task_id"), "task_id", optional=True),
                                            text(b, "detail")))

    # ---------------------------------------------------------------- fleet & decisions
    @app.get(f"{api}/fleet")
    @auth()
    def get_fleet():
        return jsonify(hive.get_fleet())

    @app.post(f"{api}/fleet/start")
    @auth(admin=True)
    def start():
        return jsonify(hive.start_fleet(me(), text(body(), "goal")))

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
        return jsonify(hive.request_decision(
            me(), text(b, "kind", "question"), text(b, "question"), strs(b, "options"),
            text(b, "context"), num(b.get("task_id"), "task_id", optional=True),
            opt_flag(b, "freeze"))), 201

    @app.get(f"{api}/decisions")
    @auth()
    def list_decisions():
        return jsonify(hive.list_decisions(request.args.get("state")))

    @app.post(f"{api}/decisions/<int:did>/answer")
    @auth(admin=True)
    def answer(did):
        b = body()
        return jsonify(hive.answer_decision(me(), did, text(b, "answer"), text(b, "note")))

    # ---------------------------------------------------------------- status feed
    @app.post(f"{api}/status")
    @auth()
    def publish_status():
        b = body()
        return jsonify(hive.publish_status(me(), text(b, "summary"), text(b, "body"),
                                           text(b, "level", "fyi"))), 201

    @app.get(f"{api}/status")
    @auth()
    def list_status():
        return jsonify(hive.list_status(arg_int("since_id"), arg_int("limit", 50)))

    # ---------------------------------------------------------------- live streams (admin view)
    @app.post(f"{api}/agents/me/stream")
    @auth()
    def append_stream():
        b = body()
        return jsonify(hive.append_stream(me(), text(b, "run"), strs(b, "lines")))

    @app.get(f"{api}/streams")
    @auth(admin=True)
    def read_stream():
        return jsonify(hive.read_stream(me(), request.args.get("agent"), arg_int("since_id"),
                                        arg_int("limit", 500)))

    # ---------------------------------------------------------------- command approvals
    @app.post(f"{api}/approvals")
    @auth()
    def request_approval():
        b = body()
        return jsonify(hive.request_approval(me(), text(b, "command"), text(b, "reason"),
                                             text(b, "shell", "bash"),
                                             num(b.get("task_id"), "task_id", optional=True))), 201

    @app.get(f"{api}/approvals")
    @auth()
    def list_approvals():
        return jsonify(hive.list_approvals(me(), request.args.get("state"),
                                           request.args.get("agent"), arg_int("limit", 50)))

    @app.get(f"{api}/approvals/<int:aid>")
    @auth()
    def get_approval(aid):
        return jsonify(hive.get_approval(me(), aid))

    @app.post(f"{api}/approvals/<int:aid>/decide")
    @auth()
    def decide_approval(aid):
        b = body()
        return jsonify(hive.decide_approval(me(), aid, bool(b.get("approve")),
                                            text(b, "note")))

    @app.post(f"{api}/approvals/<int:aid>/claim")
    @auth()
    def claim_approval(aid):
        return jsonify(hive.claim_approval(me(), aid))

    @app.post(f"{api}/approvals/<int:aid>/escalate")
    @auth()
    def escalate_approval(aid):
        return jsonify(hive.escalate_approval(me(), aid))

    @app.post(f"{api}/approvals/<int:aid>/result")
    @auth()
    def approval_result(aid):
        b = body()
        return jsonify(hive.approval_result(me(), aid, num(b.get("exit_code", -1), "exit_code"),
                                            text(b, "output")))

    # ---------------------------------------------------------------- reports
    @app.post(f"{api}/reports/requests")
    @auth()
    def request_report():
        b = body()
        return jsonify(hive.request_report(me(), text(b, "target"), text(b, "about"),
                                           num(b.get("task_id"), "task_id", optional=True))), 201

    @app.post(f"{api}/reports")
    @auth()
    def submit_report():
        b = body()
        return jsonify(hive.submit_report(me(), text(b, "summary"), text(b, "body"),
                                          num(b.get("task_id"), "task_id", optional=True),
                                          num(b.get("request_id"), "request_id",
                                              optional=True))), 201

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

    @app.get(f"{api}/admin/check")
    @auth(admin=True)
    def admin_check():
        """Lets the dashboard tell the admin token from an agent's at sign-in."""
        return jsonify({"ok": True, "project": project, "owner": g.owner})

    @app.get(f"{api}/admin/archives")
    @auth(admin=True)
    def list_archives():
        return jsonify(hive.list_archives(me()))

    @app.post(f"{api}/admin/archives")
    @auth(admin=True)
    def create_archive():
        b = body()
        return jsonify(hive.create_archive(me(), text(b, "label"), bool(b.get("flush")))), 201

    @app.get(f"{api}/admin/archives/<aid>")
    @auth(admin=True)
    def get_archive(aid):
        """The archive's JSON. Stored gzipped, so it is sent as-is to clients that accept gzip
        (browsers), unpacked for the rest, and as the .json.gz file with ?download=1."""
        path = hive.archive_file(me(), aid)
        if request.args.get("download"):
            return Response(path.read_bytes(), mimetype="application/gzip", headers={
                "Content-Disposition": f'attachment; filename="hive-archive-{aid}.json.gz"'})
        if "gzip" in request.headers.get("Accept-Encoding", ""):
            return Response(path.read_bytes(), mimetype="application/json",
                            headers={"Content-Encoding": "gzip", "Vary": "Accept-Encoding"})
        return Response(gzip.decompress(path.read_bytes()), mimetype="application/json")

    @app.delete(f"{api}/admin/archives/<aid>")
    @auth(admin=True)
    def delete_archive(aid):
        return jsonify(hive.delete_archive(me(), aid))

    @app.get(f"{api}/health")
    def health():
        out = {"ok": True, "project": project}
        if hive.is_sqlite:
            out["sqlite_journal"] = getattr(hive.engine, "hive_sqlite_journal", None)
        return jsonify(out)

    # ---------------------------------------------------------------- projects (owner only)
    if server is not None:
        def owner():
            if not g.owner:
                raise ApiError(403, "forbidden", "only the server's admin token manages projects")

        @app.get(f"{api}/admin/projects")
        @auth(admin=True)
        def list_projects():
            owner()
            return jsonify([{"slug": DEFAULT, "name": DEFAULT, "path": "", "archived": False,
                             "default": True}]
                           + [p.public() for p in server.registry.list()])

        @app.post(f"{api}/admin/projects")
        @auth(admin=True)
        def create_project():
            owner()
            b = body()
            p, token = server.create_project(text(b, "slug"), str(b.get("name") or ""),
                                             str(b.get("db_url") or ""),
                                             str(b.get("github_token_env") or ""))
            return jsonify({"project": p, "admin_token": token}), 201

        @app.patch(f"{api}/admin/projects/<slug>")
        @auth(admin=True)
        def update_project(slug):
            owner()
            b = body()
            p = server.registry.update(slug, name=b.get("name"),
                                       github_token_env=b.get("github_token_env"),
                                       archived=None if b.get("archived") is None
                                       else bool(b["archived"]))
            server.forget(slug)  # reload with the new settings (or stop serving it)
            return jsonify(p.public())

        @app.post(f"{api}/admin/projects/<slug>/token")
        @auth(admin=True)
        def rotate_project_token(slug):
            owner()
            return jsonify({"slug": slug, "admin_token": server.registry.rotate(slug)})

    # ---------------------------------------------------------------- dashboard
    @app.get("/")
    def dashboard():
        html = resources.files("hive.server").joinpath("dashboard.html").read_text("utf-8")
        return Response(html, mimetype="text/html")

    return app
