#!/usr/bin/env bash
# SessionStart hook: inject Iron Jarvis version + repo state into Claude's context.
cd "$(dirname "$0")/.." || exit 0

py=$(sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml | head -1)
init=$(sed -n 's/^__version__ = "\(.*\)"/\1/p' src/iron_jarvis/__init__.py | head -1)
desk=$(sed -n 's/.*"version": "\(.*\)",/\1/p' desktop/package.json | head -1)

if [ "$py" = "$init" ] && [ "$py" = "$desk" ]; then
  ver="v$py (all 3 version files in sync)"
else
  ver="MISMATCH — pyproject=$py __init__=$init desktop=$desk (fix before shipping!)"
fi

branch=$(git branch --show-current 2>/dev/null)
dirty=$(git status --porcelain 2>/dev/null | wc -l | tr -d ' ')
[ "$dirty" = "0" ] && tree="clean" || tree="$dirty uncommitted change(s)"

timeout 8 git fetch -q origin master 2>/dev/null
lr=$(git rev-list --left-right --count HEAD...origin/master 2>/dev/null)
ahead=$(echo "$lr" | awk '{print $1}')
behind=$(echo "$lr" | awk '{print $2}')
if [ "$ahead" = "0" ] && [ "$behind" = "0" ]; then
  sync="in sync with origin/master"
else
  sync="ahead $ahead / behind $behind vs origin/master"
fi

echo "[Iron Jarvis session state] version: $ver | branch: $branch | tree: $tree | $sync"
echo "RULE: State the current (or new target) Iron Jarvis app version in EVERY response so the user knows what to expect after pulling an update. Version bumps must edit all 3 files with ANCHORED edits: pyproject.toml, src/iron_jarvis/__init__.py, desktop/package.json. Push to master => CI publishes the release (~10 min)."
