#!/bin/sh
# ===========================================================================
#  Hive installer for macOS, for double-clicking in Finder: runs install.sh
#  in a Terminal window and keeps the window open so you can read the result.
#  Options are passed through (./install.command --test). See install.sh.
#
#  If macOS refuses to open it ("unidentified developer"), right-click it,
#  choose Open, then Open again; or run `sh install.sh` in Terminal instead.
# ===========================================================================
cd "$(dirname "$0")" || exit 1
/bin/sh ./install.sh "$@"
status=$?
if ( : </dev/tty ) 2>/dev/null; then
    printf '\nPress Return to close this window. '
    read -r _ </dev/tty
fi
exit "$status"
