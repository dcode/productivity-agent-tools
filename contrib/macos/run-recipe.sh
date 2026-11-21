#!/usr/bin/env bash
# Open a terminal window running one of the Goose recipes interactively.
#
# For a button in Obsidian (any plugin that runs a shell command) or a keyboard
# shortcut. Uses iTerm2 when it is installed, else Terminal. The command is typed
# into your normal shell, so goose is found on your usual PATH.
#
#   contrib/macos/run-recipe.sh plan-day
#   contrib/macos/run-recipe.sh plan-day day=tomorrow
#   contrib/macos/run-recipe.sh meeting-prep meeting=standup
#   contrib/macos/run-recipe.sh wrap-day
set -euo pipefail

checkout="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
name="${1:?name a recipe: plan-day, meeting-prep or wrap-day}"
shift
recipe="$checkout/contrib/goose/$name.yaml"
if [ ! -f "$recipe" ]; then
  echo "No recipe $recipe" >&2
  exit 1
fi

command=(goose run --recipe "$recipe" -s)
for parameter in "$@"; do
  command+=(--params "$parameter")
done
line="$(printf '%q ' "${command[@]}")"

if [ -d "/Applications/iTerm.app" ]; then
  osascript - "$line" <<'EOF'
on run argv
  tell application "iTerm2"
    activate
    set newWindow to (create window with default profile)
    tell current session of newWindow to write text (item 1 of argv)
  end tell
end run
EOF
else
  osascript - "$line" <<'EOF'
on run argv
  tell application "Terminal"
    activate
    do script (item 1 of argv)
  end tell
end run
EOF
fi
