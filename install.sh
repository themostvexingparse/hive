#!/bin/sh
# ===========================================================================
#  Hive installer for macOS and Linux: installs or re-installs the `hive`
#  command from this project directory, using uv.
#
#    ./install.sh             install / re-install
#    ./install.sh --test      run the test suite first; install only if it passes
#    ./install.sh --no-path   don't touch your PATH (uv tool update-shell)
#    ./install.sh --yes       answer "yes" to every question (unattended; also -y)
#    ./install.sh --help      help (also -h)
#
#  Works with any POSIX sh (dash, bash, zsh's sh mode); `sh install.sh` works
#  too when the file isn't executable. On macOS, double-click install.command.
# ===========================================================================
set -u

# Colours: ANSI sequences, only on a terminal. NO_COLOR=1 (or TERM=dumb) turns them off.
G=""; Y=""; RD=""; CY=""; B=""; DIM=""; R=""
if [ -t 1 ] && [ -z "${NO_COLOR:-}" ] && [ "${TERM:-}" != "dumb" ]; then
    ESC=$(printf '\033')
    case "${HIVE_COLOR_DEPTH:-}:${COLORTERM:-}:${TERM_PROGRAM:-}" in
        16:* | 256:*) truecolor="" ;;
        truecolor:* | 24bit:* | *:truecolor:* | *:24bit:* | *:*:vscode | *:*:iTerm.app | *:*:WezTerm)
            truecolor=1 ;;
        *) truecolor="" ;;
    esac
    if [ -n "$truecolor" ]; then  # Hive's palette (src/hive/ui.py): emerald, honey, coral, sky
        G="${ESC}[38;2;40;220;110m"; Y="${ESC}[38;2;255;200;30m"; RD="${ESC}[38;2;255;85;105m"
        CY="${ESC}[38;2;40;200;255m"; DIM="${ESC}[38;2;130;138;165m"
    else
        G="${ESC}[92m"; Y="${ESC}[93m"; RD="${ESC}[91m"; CY="${ESC}[96m"; DIM="${ESC}[90m"
    fi
    B="${ESC}[1m"; R="${ESC}[0m"
fi

ok()   { printf '%s[ok]%s %s\n' "$G" "$R" "$*"; }
warn() { printf '%s[warn]%s %s\n' "$Y" "$R" "$*"; }
note() { printf '       %s\n' "$*"; }
step() { printf '\n%s==>%s %s\n' "$CY" "$R" "$*"; }
err()  { printf '%s[error]%s %s\n' "$RD" "$R" "$*" >&2; }

usage() {  # usage [exit-status]
    printf '%sUsage:%s ./install.sh [--test] [--no-path] [--yes]\n' "$B" "$R"
    printf '  --test      run the test suite first; install only if it passes\n'
    printf '  --no-path   don'"'"'t add uv'"'"'s tool folder to your PATH\n'
    printf '  --yes, -y   answer "yes" to every question\n'
    exit "${1:-0}"
}

RUN_TESTS=0
UPDATE_PATH=1
ASSUME_YES=0
for arg in "$@"; do
    case "$arg" in
        --test|/test)          RUN_TESTS=1 ;;
        --no-path|/nopath)     UPDATE_PATH=0 ;;
        --yes|-y|/yes)         ASSUME_YES=1 ;;
        -h|--help|/\?|/help)   usage ;;
        *) err "Unknown option: $arg"; usage 1 ;;
    esac
done

# confirm "Question" -> status 0 for yes, 1 for no. Reads the terminal even when stdin is
# redirected; without a terminal the answer is "no" (use --yes for unattended runs).
confirm() {
    [ "$ASSUME_YES" = 1 ] && return 0
    # A subshell: in dash, a failed redirection on `:` would end the whole script.
    if ! ( : </dev/tty ) 2>/dev/null; then
        note "(no terminal to ask on; answering no. Use --yes for unattended runs.)"
        return 1
    fi
    printf '%s [y/N] ' "$1"
    read -r answer </dev/tty || return 1
    case "$answer" in [yY]|[yY][eE][sS]) return 0 ;; *) return 1 ;; esac
}

OS=$(uname -s 2>/dev/null || echo unknown)

# Work from the folder this script is in, wherever it was started from.
cd "$(dirname "$0")" 2>/dev/null || { err "Could not change to the script's folder."; exit 1; }
printf '\n%s%s=== Hive installer ===%s\n' "$B" "$CY" "$R"
printf '%sProject: %s (%s)%s\n\n' "$DIM" "$(pwd)" "$OS" "$R"

# --- 1. Is this the Hive project? ------------------------------------------
if [ ! -f pyproject.toml ] || ! grep -Eq '^name *= *"hive"' pyproject.toml \
        || [ ! -f src/hive/__init__.py ]; then
    err 'This doesn'"'"'t look like the Hive project (pyproject.toml with name = "hive",'
    note 'src/hive/). Put install.sh in the project root and run it from there.'
    exit 1
fi
ok "Hive project found."

# --- 2. uv -------------------------------------------------------------------
install_uv() {
    printf '%s[!!]%s uv was not found. Hive is installed with uv (https://docs.astral.sh/uv/).\n' "$Y" "$R"
    if command -v curl >/dev/null 2>&1; then
        fetch="curl -LsSf https://astral.sh/uv/install.sh | sh"
    elif command -v wget >/dev/null 2>&1; then
        fetch="wget -qO- https://astral.sh/uv/install.sh | sh"
    else
        err "Neither curl nor wget is available to download uv."
        note "Install uv yourself (e.g. your package manager, or brew install uv on macOS),"
        note "then run this script again."
        return 1
    fi
    if ! confirm "Install uv now with its official installer?"; then
        printf 'Install uv yourself, then run this script again:\n  %s%s%s\n' "$CY" "$fetch" "$R"
        return 1
    fi
    sh -c "$fetch" || return 1
    # The installer puts uv in ~/.local/bin (or $XDG_BIN_HOME, $UV_INSTALL_DIR); use it in
    # this shell right away.
    PATH="${UV_INSTALL_DIR:-${XDG_BIN_HOME:-$HOME/.local/bin}}:$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
    export PATH
    if ! command -v uv >/dev/null 2>&1; then
        err "uv was installed but can't be found; open a new terminal and run this script again."
        return 1
    fi
}
if ! command -v uv >/dev/null 2>&1; then
    install_uv || { err "Installation stopped."; exit 1; }
fi
ok "$(uv --version 2>/dev/null)"

# --- 3. git (agents need it; the install itself doesn't) -----------------------
if command -v git >/dev/null 2>&1 && git --version >/dev/null 2>&1; then
    ok "$(git --version)"
else
    warn "git was not found. Hive agents need git; install it:"
    if [ "$OS" = Darwin ]; then
        note "xcode-select --install      (or: brew install git)"
    else
        note "sudo apt install git  /  sudo dnf install git  /  sudo pacman -S git"
    fi
fi

# --- 4. Running Hive processes keep the old version ----------------------------
# Unlike Windows, macOS and Linux can replace a running program, but processes that are
# already running keep the old code until they are restarted.
hive_pids() {
    # A process is Hive if its program or its script argument is a file named `hive`
    # (uv runs it as `<venv>/bin/python <bin>/hive up`).
    ps -eo pid=,args= 2>/dev/null | awk -v me="$$" '
        $1 != me && ($2 ~ /(^|\/)hive$/ || $3 ~ /(^|\/)hive$/) { print $1 }'
}
PIDS=$(hive_pids)
RESTART_NOTE=""
if [ -n "$PIDS" ]; then
    printf '%s[!!]%s Hive is running (pid %s).\n' "$Y" "$R" "$(echo $PIDS | tr ' ' ',')"
    note "Running processes keep the old version until restarted. This script can stop them;"
    note "any agent turn in progress is interrupted."
    if confirm "Stop all running Hive processes now?"; then
        # shellcheck disable=SC2086
        kill $PIDS 2>/dev/null
        n=0
        while [ -n "$(hive_pids)" ] && [ "$n" -lt 10 ]; do sleep 1; n=$((n + 1)); done
        LEFT=$(hive_pids)
        if [ -n "$LEFT" ]; then
            # shellcheck disable=SC2086
            kill -9 $LEFT 2>/dev/null
            sleep 1
        fi
        if [ -n "$(hive_pids)" ]; then
            warn "Could not stop every Hive process; restart them yourself after the install."
            RESTART_NOTE=1
        else
            ok "Stopped running Hive processes."
        fi
    else
        note "Leaving them running; restart them after the install to use the new version."
        RESTART_NOTE=1
    fi
fi

# --- 5. Optional: tests -----------------------------------------------------------
if [ "$RUN_TESTS" = 1 ]; then
    step "Running the test suite (this takes about a minute)..."
    if ! uv run --quiet pytest -q -p no:logging; then
        err "Tests failed; nothing was installed. Fix the failures, or run without --test."
        exit 1
    fi
    ok "Tests passed."
fi

# --- 6. Install / re-install ------------------------------------------------------
step "Installing Hive (clean rebuild)..."
fail_install() {
    err "Installation failed. The messages above say why; common causes:"
    note "- no network access to download dependencies or Python"
    note "- no write access to uv's tool folder ($(uv tool dir 2>/dev/null || echo '?'))"
    exit 1
}
# --reinstall rebuilds even when the version number is unchanged, so no stale build
# survives; --force replaces an existing `hive` executable.
uv tool install --force --reinstall . || fail_install

BIN=$(uv tool dir --bin 2>/dev/null) || fail_install
[ -n "$BIN" ] && [ -x "$BIN/hive" ] || fail_install
HIVE_VERSION=$("$BIN/hive" --version 2>/dev/null) || fail_install
[ -n "$HIVE_VERSION" ] || fail_install
ok "Installed hive $HIVE_VERSION at $BIN/hive"

# --- 7. PATH -------------------------------------------------------------------
# Agent CLIs start `hive mcp` / `hive hook` by name, so `hive` must be on PATH.
PATH_NOTE=""
if [ "$UPDATE_PATH" = 1 ]; then
    uv tool update-shell >/dev/null 2>&1
fi
hash -r 2>/dev/null
FIRST_HIVE=$(command -v hive 2>/dev/null || true)
if [ -z "$FIRST_HIVE" ]; then
    if [ "$UPDATE_PATH" = 1 ]; then
        PATH_NOTE="Open a NEW terminal: $BIN was added to your shell's PATH."
    else
        PATH_NOTE="Add $BIN to your PATH yourself, or re-run without --no-path."
    fi
elif [ "$FIRST_HIVE" != "$BIN/hive" ]; then
    warn "Another \`hive\` comes first on PATH: $FIRST_HIVE"
    note "Remove it (e.g. an old \`pip install --user hive\`: pip uninstall hive)"
    note "so that $BIN/hive is used."
else
    ok "\`hive\` on PATH points to the new install."
fi

# --- Done ----------------------------------------------------------------------
printf '\n%s%s=== Hive %s installed ===%s\n' "$B" "$G" "$HIVE_VERSION" "$R"
[ -n "$PATH_NOTE" ] && printf '%sNOTE:%s %s\n' "$Y" "$R" "$PATH_NOTE"
printf '\n%sNext steps after an upgrade:%s\n' "$B" "$R"
if [ -n "$RESTART_NOTE" ]; then
    printf '  1. Restart `hive up` on this machine (it is still running the old version).\n'
else
    printf '  1. Restart `hive up` on this machine.\n'
fi
printf '  2. In each project using Hive: `hive init`, then commit and push the refreshed files.\n'
printf '  3. Upgrade the Hive server too (docs/DEPLOYMENT.md, "Upgrading"): server and agent\n'
printf '     machines must run the same version.\n\n'
exit 0
