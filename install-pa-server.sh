#!/bin/bash
# ===========================================================================
#  Hive server installer for PythonAnywhere: removes the previous Hive install
#  (its virtualenv, and stray `pip install --user` copies) and installs this
#  project directory fresh, then reloads the web app.
#
#  Run it in a PythonAnywhere Bash console, from this project's folder:
#
#    ./install-pa-server.sh                 re-install (asks before changing anything)
#    ./install-pa-server.sh --yes           unattended (also -y)
#    ./install-pa-server.sh --help          all options (also -h)
#
#  Never modified: the WSGI file (admin token, database password), ~/.hive-env
#  or similar secret files, the database (SQLite file or MySQL), and the
#  dashboard archives. The WSGI file is only copied to a private backup folder
#  and touched to reload the web app.
#
#  Safe swap: the old virtualenv is moved aside, a new one is built and tested,
#  and only then is the web app reloaded. If anything fails, or the reloaded
#  site doesn't answer its health check, the old virtualenv is put back.
# ===========================================================================
set -u

# Colours: ANSI sequences, only on a terminal. NO_COLOR=1 (or TERM=dumb) turns them off.
G=""; Y=""; RD=""; CY=""; B=""; DIM=""; R=""
if [ -t 1 ] && [ -z "${NO_COLOR:-}" ] && [ "${TERM:-}" != "dumb" ]; then
    G=$'\033[92m'; Y=$'\033[93m'; RD=$'\033[91m'; CY=$'\033[96m'
    B=$'\033[1m'; DIM=$'\033[90m'; R=$'\033[0m'
fi
ok()   { printf '%s[ok]%s %s\n' "$G" "$R" "$*"; }
warn() { printf '%s[warn]%s %s\n' "$Y" "$R" "$*"; }
note() { printf '       %s\n' "$*"; }
step() { printf '\n%s==>%s %s\n' "$CY" "$R" "$*"; }
err()  { printf '%s[error]%s %s\n' "$RD" "$R" "$*" >&2; }
kv()   { printf '  %s%-14s%s %s\n' "$DIM" "$1" "$R" "$2"; }

usage() {  # usage [exit-status]
    cat <<EOF
${B}Usage:${R} ./install-pa-server.sh [options]

  --yes, -y          answer "yes" to every question
  --venv PATH        virtualenv the web app uses (default: ~/.virtualenvs/hive)
  --python VERSION   Python for the new virtualenv, e.g. 3.12, or a command/path
                     (default: the old virtualenv's version, else 3.12). It must
                     match the Python version set on the Web tab.
  --wsgi FILE        the web app's WSGI file (default: the /var/www/*_wsgi.py that
                     imports hive.server.wsgi)
  --url URL          site to health-check after reloading (default: from the WSGI
                     file name, e.g. https://USER.pythonanywhere.com)
  --extra "PKGS"     more pip packages to install into the virtualenv
  --no-reload        install only; reload on the Web tab yourself
  --keep-old         keep the previous virtualenv as <venv>.previous-<time>
  --skip-pa-check    run even if this doesn't look like PythonAnywhere
EOF
    exit "${1:-0}"
}

ASSUME_YES=0; VENV="$HOME/.virtualenvs/hive"; PYARG=""; WSGI=""; URL=""; EXTRA=""
RELOAD=1; KEEP_OLD=0; PA_CHECK=1
need_val() { [ $# -ge 2 ] && [ -n "$2" ] || { err "$1 needs a value."; usage 1; }; }
while [ $# -gt 0 ]; do
    case "$1" in
        -y|--yes)        ASSUME_YES=1 ;;
        --venv)          need_val "$@"; VENV="$2"; shift ;;
        --python)        need_val "$@"; PYARG="$2"; shift ;;
        --wsgi)          need_val "$@"; WSGI="$2"; shift ;;
        --url)           need_val "$@"; URL="${2%/}"; shift ;;
        --extra)         need_val "$@"; EXTRA="$2"; shift ;;
        --no-reload)     RELOAD=0; KEEP_OLD=1 ;;  # unchecked live: keep a way back
        --keep-old)      KEEP_OLD=1 ;;
        --skip-pa-check) PA_CHECK=0 ;;
        -h|--help)       usage ;;
        *) err "Unknown option: $1"; usage 1 ;;
    esac
    shift
done

confirm() {  # confirm "Question" -> 0 yes, 1 no. Without a terminal: no (use --yes).
    [ "$ASSUME_YES" = 1 ] && return 0
    if ! ( : </dev/tty ) 2>/dev/null; then
        note "(no terminal to ask on; answering no. Use --yes for unattended runs.)"
        return 1
    fi
    local answer
    read -r -p "$1 [y/N] " answer </dev/tty || return 1
    [[ "$answer" =~ ^[yY]([eE][sS])?$ ]]
}

# Virtualenvs keep their programs in bin/ (Linux, macOS) or Scripts/ (Windows, for testing).
vbin() { if [ -d "$1/Scripts" ] && [ ! -d "$1/bin" ]; then echo "$1/Scripts"; else echo "$1/bin"; fi; }
# inside PATH DIR: is PATH (which need not exist yet) DIR itself or somewhere below it?
canon() { realpath -m -- "$1" 2>/dev/null || printf '%s\n' "$1"; }
inside() { case "$(canon "$1")/" in "$(canon "$2")"/*) return 0 ;; esac; return 1; }

cd "$(dirname "$0")" 2>/dev/null || { err "Could not change to the script's folder."; exit 1; }
PROJECT=$(pwd -P)
printf '\n%s%s=== Hive server installer (PythonAnywhere) ===%s\n' "$B" "$CY" "$R"
printf '%sProject: %s%s\n' "$DIM" "$PROJECT" "$R"

# --- 1. Where are we? -------------------------------------------------------------
step "Checking the project and the host..."
if [ ! -f pyproject.toml ] || ! grep -Eq '^name *= *"hive"' pyproject.toml \
        || [ ! -f src/hive/__init__.py ] || [ ! -f src/hive/server/wsgi.py ]; then
    err 'This doesn'"'"'t look like the Hive project (pyproject.toml with name = "hive",'
    note "src/hive/server/). Run this script from the project's folder."
    exit 1
fi
ok "Hive project found."
if [ -z "${PYTHONANYWHERE_DOMAIN:-}${PYTHONANYWHERE_SITE:-}" ]; then
    if [ "$PA_CHECK" = 1 ]; then
        err "This doesn't look like a PythonAnywhere console (PYTHONANYWHERE_DOMAIN isn't set)."
        note "Run it in a Bash console on PythonAnywhere, or pass --skip-pa-check."
        exit 1
    fi
    warn "Not PythonAnywhere; continuing because of --skip-pa-check."
else
    ok "PythonAnywhere console (${PYTHONANYWHERE_SITE:-$PYTHONANYWHERE_DOMAIN})."
fi
VENV="${VENV%/}"
case "$VENV" in /*|?:/*) ;; *) VENV="$PWD/$VENV" ;; esac
if inside "$PROJECT" "$VENV" || inside "$HOME" "$VENV" || [ "$VENV" = "$PROJECT" ] \
        || [ "$VENV" = "$HOME" ]; then
    err "Refusing to use $VENV as the virtualenv: it would remove the project or your home."
    exit 1
fi

# --- 2. The web app's WSGI file ----------------------------------------------------
if [ -z "$WSGI" ]; then
    mapfile -t CANDIDATES < <(grep -l "hive.server.wsgi" /var/www/*_wsgi.py 2>/dev/null)
    if [ "${#CANDIDATES[@]}" -eq 0 ]; then
        err "No web app WSGI file in /var/www imports hive.server.wsgi."
        note "Create the web app first (docs/DEPLOYMENT.md §4.5), or pass --wsgi FILE."
        exit 1
    elif [ "${#CANDIDATES[@]}" -gt 1 ]; then
        err "Several web apps import Hive; choose one with --wsgi:"
        for c in "${CANDIDATES[@]}"; do note "$c"; done
        exit 1
    fi
    WSGI="${CANDIDATES[0]}"
fi
[ -f "$WSGI" ] || { err "WSGI file not found: $WSGI"; exit 1; }
ok "Web app WSGI file: $WSGI"
DOMAIN=$(basename "$WSGI"); DOMAIN="${DOMAIN%_wsgi.py}"; DOMAIN="${DOMAIN//_/.}"
[ -n "$URL" ] || URL="https://$DOMAIN"

# --- 3. Read the configuration the web app uses (without touching it) ----------------
# The WSGI file is executed with a stand-in for `hive.server.wsgi`, so the environment it
# sets up (including values loaded from a .env file) is seen exactly as the web app sees it,
# without importing Hive or opening the database. If that fails, plain assignments are parsed.
# Only non-secret facts are printed: the token's presence and the database URL without its
# password.
OLD_BIN=$(vbin "$VENV")
PROBE_PY=python3; [ -x "$OLD_BIN/python" ] && PROBE_PY="$OLD_BIN/python"
command -v "$PROBE_PY" >/dev/null 2>&1 || PROBE_PY=python
PROBE=$("$PROBE_PY" - "$WSGI" "$HOME" <<'PY' 2>&1
import os, re, sys, types
from urllib.parse import urlsplit
path, home = sys.argv[1], sys.argv[2]
for name in ("hive", "hive.server", "hive.server.wsgi"):
    m = types.ModuleType(name); m.__path__ = []; m.application = None
    sys.modules[name] = m
src = open(path, encoding="utf-8").read()
how = "executed"
try:
    exec(compile(src, path, "exec"), {"__name__": "__hive_probe__", "__file__": path})
except BaseException as e:
    how = f"parsed (running it failed: {type(e).__name__}: {e})"
    pat = r'os\.environ\[\s*["\'](HIVE_[A-Z_]+)["\']\s*\]\s*=\s*\(?\s*((?:["\'][^"\'\n]*["\']\s*)+)\)?'
    for m in re.finditer(pat, src):
        os.environ[m.group(1)] = "".join(re.findall(r'["\']([^"\'\n]*)["\']', m.group(2)))
url = os.environ.get("HIVE_DATABASE_URL", "") or "sqlite:///hive.db"
kind = url.split(":", 1)[0].split("+", 1)[0]
shown, dbfile = url, ""
if kind == "sqlite":
    dbfile = url.split(":///", 1)[1].split("?", 1)[0] if ":///" in url else ""
    if dbfile in ("", ":memory:"):
        dbfile = ""
else:
    p = urlsplit(url)
    if p.password:
        shown = url.replace(":" + p.password + "@", ":****@", 1)
arch = os.environ.get("HIVE_ARCHIVE_DIR", "")
if not arch:
    arch = os.path.join(os.path.dirname(os.path.abspath(dbfile)), "hive-archives") \
        if dbfile and os.path.isabs(dbfile) else os.path.join(home, "hive-archives")
print("HOW=" + how.replace("\n", " "))
print("KIND=" + kind)
print("SHOWN=" + shown)
print("DBFILE=" + dbfile)
print("ARCHIVES=" + os.path.expanduser(arch))
print("JOURNAL=" + os.environ.get("HIVE_SQLITE_JOURNAL", ""))
print("TOKEN=" + ("1" if os.environ.get("HIVE_ADMIN_TOKEN") else "0"))
print("DBURL_SET=" + ("1" if os.environ.get("HIVE_DATABASE_URL") else "0"))
print("DOTENV=" + ("1" if "dotenv" in src else "0"))
PY
) || { err "Could not read the WSGI file:"; note "$PROBE"; exit 1; }
cfg() { printf '%s\n' "$PROBE" | sed -n "s/^$1=//p" | tail -n 1; }
HOW=$(cfg HOW); KIND=$(cfg KIND); SHOWN=$(cfg SHOWN); DBFILE=$(cfg DBFILE)
ARCHIVES=$(cfg ARCHIVES); JOURNAL=$(cfg JOURNAL); TOKEN=$(cfg TOKEN)
DBURL_SET=$(cfg DBURL_SET); DOTENV=$(cfg DOTENV)

# --- 4. Python for the new virtualenv ----------------------------------------------
OLD_VER=""
[ -x "$OLD_BIN/python" ] && OLD_VER=$("$OLD_BIN/python" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null)
[ -n "$PYARG" ] || PYARG="${OLD_VER:-3.12}"
if [[ "$PYARG" =~ ^[0-9]+\.[0-9]+$ ]]; then PYTHON="python$PYARG"; else PYTHON="$PYARG"; fi
command -v "$PYTHON" >/dev/null 2>&1 || { err "$PYTHON was not found. Pass --python with an installed version (e.g. 3.12)."; exit 1; }
# The base interpreter, by absolute path: with a virtualenv active (`workon hive`), `python3.12`
# would otherwise be the copy inside the virtualenv that is about to be moved.
PYTHON=$("$PYTHON" -c 'import sys; print(getattr(sys, "_base_executable", None) or sys.executable)') \
    || { err "Could not run $PYTHON."; exit 1; }
NEW_VER=$("$PYTHON" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null)
"$PYTHON" -c 'import sys; sys.exit(sys.version_info < (3, 12))' 2>/dev/null \
    || { err "Hive needs Python 3.12 or newer; $PYTHON is $NEW_VER."; exit 1; }

PKGS=(".")
case "$KIND" in
    mysql)      PKGS=(".[mysql]") ;;
    postgresql) PKGS+=("psycopg[binary]") ;;
esac
[ "$DOTENV" = 1 ] && PKGS+=("python-dotenv")
# shellcheck disable=SC2206
[ -n "$EXTRA" ] && PKGS+=($EXTRA)

# --- 5. Summary and safety checks -------------------------------------------------------
step "What will happen"
kv "web app" "$URL"
kv "WSGI file" "$WSGI  ${DIM}(config ${HOW%% (*})${R}"
kv "virtualenv" "$VENV  ${DIM}(rebuilt with Python $NEW_VER${OLD_VER:+; was $OLD_VER})${R}"
kv "install" "${PKGS[*]/#./$PROJECT}"
kv "database" "$SHOWN  ${DIM}(kept)${R}"
kv "archives" "$ARCHIVES  ${DIM}(kept)${R}"
kv "admin token" "$([ "$TOKEN" = 1 ] && echo "set  ${DIM}(kept)${R}" || echo "${RD}NOT SET${R}")"
[ "${HOW#parsed}" != "$HOW" ] && warn "The WSGI file could not be run in isolation; its settings were ${HOW}."

PROBLEM=0
for p in "$DBFILE" "$ARCHIVES"; do
    [ -n "$p" ] || continue
    if inside "$p" "$VENV"; then
        err "$p is inside the virtualenv that is about to be replaced. Move it out first."
        PROBLEM=1
    fi
done
[ "$PROBLEM" = 1 ] && exit 1
[ "$TOKEN" = 1 ] || warn "HIVE_ADMIN_TOKEN isn't set by the WSGI file; the web app won't start until it is (§4.5)."
if [ "$KIND" = sqlite ]; then
    if [ "$DBURL_SET" = 0 ]; then
        warn "HIVE_DATABASE_URL isn't set: the server uses a relative hive.db, wherever the web"
        note "app happens to run. Set an absolute path (docs/DEPLOYMENT.md §4.8)."
    elif [ -n "$DBFILE" ] && [ "${DBFILE#/}" = "$DBFILE" ] && [ "${DBFILE#?:/}" = "$DBFILE" ]; then
        warn "The SQLite path is relative ($DBFILE); an absolute path is safer (§4.8)."
    fi
    if [ -n "$DBFILE" ] && inside "$DBFILE" "$PROJECT"; then
        warn "The database is inside the project folder. This script leaves it alone, but"
        note "deleting or re-cloning the project would lose it. Keep it outside (§4.8)."
    fi
    [ -z "$JOURNAL" ] || [ "$JOURNAL" = delete ] || [ "$JOURNAL" = truncate ] \
        || warn "HIVE_SQLITE_JOURNAL=$JOURNAL; PythonAnywhere needs delete (§4.8)."
fi
if [ -n "$OLD_VER" ] && [ "$OLD_VER" != "$NEW_VER" ]; then
    warn "Python changes from $OLD_VER to $NEW_VER: set the same version on the Web tab"
    note "(Code section, Python version) before reloading, or the web app won't start."
fi
echo
note "Tip: back up the database first, from your computer: hive admin backup"
echo
confirm "Re-install Hive now?" || { echo "Nothing was changed."; exit 0; }

# --- 6. Private backup of the WSGI file and the old package list -----------------------
STAMP=$(date +%Y%m%d-%H%M%S)
BACKUP="$HOME/hive-install-backups/$STAMP"
step "Backing up the WSGI file to $BACKUP ..."
( umask 077 && mkdir -p "$BACKUP" && cp -p "$WSGI" "$BACKUP/" ) \
    || { err "Could not write the backup; nothing was changed."; exit 1; }
chmod 700 "$HOME/hive-install-backups" "$BACKUP" 2>/dev/null
[ -x "$OLD_BIN/python" ] && "$OLD_BIN/python" -m pip freeze > "$BACKUP/old-packages.txt" 2>/dev/null
ok "Backed up (readable only by you)."

# --- 7. Stray copies installed outside the virtualenv -------------------------------
# Earlier installs with `pip install` outside the virtualenv land in ~/.local and can be
# picked up instead of the virtualenv's copy.
step "Looking for stray Hive installs outside the virtualenv..."
STRAY=0
for py in python3.11 python3.12 python3.13 python3.14 python3; do
    command -v "$py" >/dev/null 2>&1 || continue
    loc=$(PIP_DISABLE_PIP_VERSION_CHECK=1 "$py" -m pip show hive 2>/dev/null | sed -n 's/^Location: //p')
    case "$loc" in
        "$HOME"/.local/*)
            STRAY=1
            warn "$py has Hive installed in $loc"
            if confirm "Uninstall that copy?"; then
                PIP_DISABLE_PIP_VERSION_CHECK=1 "$py" -m pip uninstall -y hive >/dev/null 2>&1 \
                    && ok "Removed it." || warn "Could not remove it; run: $py -m pip uninstall hive"
            fi ;;
    esac
done
[ "$STRAY" = 0 ] && ok "None found."

# --- 8. Swap in a fresh virtualenv ----------------------------------------------------
OLD=""          # where the previous virtualenv was moved; empty if there was none
RELOADED=0
rollback() {
    trap - INT TERM
    printf '\n%s==>%s Restoring the previous installation...\n' "$Y" "$R"
    rm -rf "$VENV"
    if [ -n "$OLD" ] && [ -d "$OLD" ]; then
        mv "$OLD" "$VENV" && ok "Previous virtualenv restored at $VENV."
        if [ "$RELOADED" = 1 ]; then touch "$WSGI" && ok "Web app reloaded with it."; fi
    else
        note "There was no previous virtualenv to restore."
    fi
    note "Database, archives and WSGI file were not touched."
}
abort() { err "$1"; rollback; exit 1; }
trap 'printf "\n"; abort "Interrupted."' INT TERM

step "Building a new virtualenv (Python $NEW_VER)..."
if [ -e "$VENV" ]; then
    OLD="$VENV.previous-$STAMP"
    mv "$VENV" "$OLD" || { err "Could not move the old virtualenv aside; nothing was changed."; exit 1; }
    ok "Old virtualenv moved aside to $OLD"
fi
mkdir -p "$(dirname "$VENV")"
"$PYTHON" -m venv "$VENV" || abort "Could not create the virtualenv."
NEW_BIN=$(vbin "$VENV")
export PIP_DISABLE_PIP_VERSION_CHECK=1
"$NEW_BIN/python" -m pip install --quiet --upgrade pip || abort "Could not upgrade pip."

step "Installing Hive from $PROJECT ..."
# --no-cache-dir: always rebuild from this folder, never reuse an older build of the same version.
"$NEW_BIN/python" -m pip install --no-cache-dir "${PKGS[@]}" \
    || abort "pip install failed (see above). Free accounts can only reach PyPI and GitHub."

# --- 9. Verify before the web app sees it ----------------------------------------------
step "Testing the new installation..."
VERSION=$("$NEW_BIN/python" -c 'import hive; print(hive.__version__)' 2>&1) \
    || abort "Hive doesn't import: $VERSION"
case "$KIND" in
    mysql)      "$NEW_BIN/python" -c 'import pymysql' 2>/dev/null || abort "The MySQL driver (pymysql) is missing." ;;
    postgresql) "$NEW_BIN/python" -c 'import psycopg' 2>/dev/null || abort "The Postgres driver (psycopg) is missing." ;;
esac
# A throwaway server on a temporary SQLite file: health, dashboard and an admin call. The
# real database is never opened here (another process must not open a live SQLite file).
SMOKE_DIR=$(mktemp -d)
SMOKE=$(cd "$SMOKE_DIR" && HIVE_ARCHIVE_DIR="$SMOKE_DIR/archives" "$NEW_BIN/python" - <<'PY' 2>&1
import os
from hive.server.app import create_app
from hive.server.service import create_hive
app = create_app(create_hive("sqlite:///" + os.path.abspath("smoke.db"), tick_interval=0),
                 admin_token="smoke")
c = app.test_client()
h = {"Authorization": "Bearer smoke"}
assert c.get("/api/v1/health").get_json()["ok"] is True, "health"
assert c.get("/").status_code == 200, "dashboard"
assert c.get("/api/v1/fleet", headers=h).get_json()["state"] == "setup", "fleet"
assert c.get("/api/v1/admin/archives", headers=h).status_code == 200, "archives"
print("ok")
PY
)
rm -rf "$SMOKE_DIR"
[ "$(printf '%s' "$SMOKE" | tail -n 1)" = ok ] || abort "The new installation failed its self-test: $SMOKE"
ok "hive $VERSION installed; self-test passed (health, dashboard, admin API)."

# --- 10. Reload and check the live site ---------------------------------------------
HEALTH=""
health() {  # prints the health JSON, or nothing if the site can't be reached
    command -v curl >/dev/null 2>&1 || return 0
    curl -fsS --max-time 10 "$URL/api/v1/health" 2>/dev/null
}
if [ "$RELOAD" = 1 ]; then
    step "Reloading the web app..."
    # PythonAnywhere reloads a web app when its WSGI file's modification time changes.
    touch "$WSGI" || abort "Could not touch $WSGI to reload the web app."
    RELOADED=1
    for _ in $(seq 1 12); do
        sleep 5
        HEALTH=$(health)
        [ -n "$HEALTH" ] && break
    done
    if printf '%s' "$HEALTH" | grep -q '"ok": *true'; then
        ok "$URL is up: $HEALTH"
    elif [ -n "$HEALTH" ]; then
        abort "$URL answered, but not healthy: $HEALTH"
    else
        CODE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$URL/api/v1/health" 2>/dev/null)
        if [ -n "$CODE" ] && [ "$CODE" != 000 ]; then
            err "$URL answers HTTP $CODE instead of the health check."
            note "Error log: /var/log/$DOMAIN.error.log"
            abort "The reloaded web app is failing."
        fi
        warn "Couldn't reach $URL from this console to check it. Open it in a browser;"
        note "if it fails, read /var/log/$DOMAIN.error.log."
        KEEP_OLD=1
        note "The old virtualenv is kept until you've checked: $OLD"
    fi
fi
trap - INT TERM

# --- 11. Clean up ---------------------------------------------------------------
if [ -n "$OLD" ]; then
    if [ "$KEEP_OLD" = 1 ]; then
        note "Previous virtualenv kept at $OLD (delete it with: rm -rf \"$OLD\")"
    else
        rm -rf "$OLD" && ok "Removed the previous virtualenv."
    fi
fi

printf '\n%s%s=== Hive %s installed on the server ===%s\n' "$B" "$G" "$VERSION" "$R"
[ "$RELOAD" = 1 ] || printf '%sNOTE:%s reload the web app on the Web tab to start using it.\n' "$Y" "$R"
printf '\n%sNext steps:%s\n' "$B" "$R"
printf '  1. Check that the Web tab'"'"'s virtualenv is %s.\n' "$VENV"
printf '  2. Upgrade every agent machine to the same version (install.sh / install.bat),\n'
printf '     then restart `hive up` and run `hive init` in each project.\n'
printf '  3. The WSGI file backup is in %s; delete it once all is well.\n\n' "$BACKUP"
exit 0
