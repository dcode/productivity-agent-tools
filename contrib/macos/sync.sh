#!/usr/bin/env bash
# Run obsidian_sync once and say how it went in a macOS notification.
#
# For a button in Obsidian (any plugin that runs a shell command), a keyboard
# shortcut, or the launchd agent in this folder. It needs no agent and no model.
#
#   contrib/macos/sync.sh             # sync
#   contrib/macos/sync.sh --dry-run   # show what would change, write nothing
#
# The full result is appended to ~/Library/Logs/productivity-agent-tools/sync.log.
set -euo pipefail

checkout="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
tool="$checkout/.venv/bin/productivity-agent-tools"
python="$checkout/.venv/bin/python"
logs="$HOME/Library/Logs/productivity-agent-tools"
mkdir -p "$logs"

if [ ! -x "$tool" ]; then
  echo "No $tool: run 'uv sync' in $checkout first." >&2
  exit 1
fi

notify() {
  # $1: title, $2: message. osascript is quiet when there is no GUI session.
  osascript - "$1" "$2" >/dev/null 2>&1 <<'EOF' || true
on run argv
  display notification (item 2 of argv) with title (item 1 of argv)
end run
EOF
}

started="$(date '+%Y-%m-%d %H:%M:%S')"
if result="$("$tool" call obsidian_sync "$@" 2>>"$logs/sync.err")"; then
  printf '%s\n%s\n' "== $started" "$result" >>"$logs/sync.log"
  message="$(printf '%s' "$result" | "$python" -I -c \
    'import json, sys; print(json.load(sys.stdin).get("message", "Done."))' 2>/dev/null ||
    echo "Done.")"
  notify "Productivity sync" "$message"
else
  printf '%s\n%s\n' "== $started (failed)" "$result" >>"$logs/sync.log"
  notify "Productivity sync failed" "See $logs/sync.err"
  exit 1
fi
