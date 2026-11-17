#!/usr/bin/env bash
# THE UPDATE REHEARSAL: an older Linux app must update ITSELF to the new one.
#
#   scripts/linux_update_rehearsal.sh <old.AppImage> <new files folder> <quit|restart> [new version]
#
# <new files folder> holds what a release would carry: Iron-Jarvis-x86_64.AppImage
# and latest-linux.yml. It is served by a web server on THIS machine, and the
# old app is started with IRONJARVIS_UPDATE_REHEARSAL_URL pointing at it (the
# only update source besides its own branches that the app accepts: plain http
# on 127.0.0.1, Linux only). Nothing here is public and nothing is published.
#
#   quit     the old app downloads the update, is asked to quit, and installs it
#            on the way out (what happens when a person just closes the app).
#   restart  the old app downloads it and presses "Restart to update" itself;
#            the new app must come up by itself.
#
# Passes only when the file the person started IS now the new file (same
# bytes), it starts, and its service answers (reporting <new version> when one
# is given). Runs on GitHub's Linux machine from .github/workflows/linux-release.yml.
set -uo pipefail

old="${1:?the older app file}"
new_dir="${2:?the folder with the new files}"
mode="${3:?quit or restart}"
want="${4:-}"
port=9123
ours='(/tmp/\.mount_|/tmp/appimage_extracted_)'
summary="${GITHUB_STEP_SUMMARY:-/dev/null}"

fail() {
  echo "FAIL ($mode): $1" | tee -a "$summary"
  # What the app itself wrote (the new one is started with no console).
  find "$HOME/.config/Iron Jarvis/logs" -name "*.log" 2>/dev/null | while read -r f; do
    echo "== $f"; grep -v "uvicorn.access" "$f" | tail -25 | cut -c1-300
  done
  cleanup; exit 1
}
cleanup() {
  pkill -9 -f "$ours" 2>/dev/null
  [ -n "${screen:-}" ] && kill "$screen" 2>/dev/null
  [ -n "${server:-}" ] && kill "$server" 2>/dev/null
  # The next run needs both ports free.
  for i in $(seq 1 30); do
    if ! curl -s -m 1 -o /dev/null http://127.0.0.1:8787/health && ! curl -s -m 1 -o /dev/null http://127.0.0.1:8788/; then break; fi
    sleep 1
  done
}
health() { curl -s -m 3 http://127.0.0.1:8787/health 2>/dev/null; }
answers() { curl -s -m 3 -o /dev/null -w "%{http_code}" http://127.0.0.1:8787/health 2>/dev/null | grep -qE "200|401"; }

[ -s "$new_dir/latest-linux.yml" ] || { echo "no latest-linux.yml in $new_dir"; exit 2; }
new_file="$new_dir/$(grep -m1 -oE 'url: .*\.AppImage' "$new_dir/latest-linux.yml" | sed 's/^url: //')"
[ -s "$new_file" ] || { echo "the manifest's AppImage is not in $new_dir"; exit 2; }

for try in 1 2 3; do
  if sudo apt-get update -qq && sudo apt-get install -y -qq xvfb > /dev/null; then break; fi
  echo "apt try $try failed"; sleep 20
done

# A person's own copy, in a folder they can write to, with a name of its own.
home="$(mktemp -d)"
app="$home/Iron-Jarvis-x86_64.AppImage"
cp "$old" "$app" && chmod +x "$app"
rm -rf "$HOME/.config/Iron Jarvis" "$HOME/.cache/iron-jarvis-desktop-updater"
before="$(sha256sum "$app" | cut -d' ' -f1)"
target="$(sha256sum "$new_file" | cut -d' ' -f1)"
[ "$before" != "$target" ] || { echo "the old and the new file are the same file"; exit 2; }

( cd "$new_dir" && exec python3 -m http.server "$port" --bind 127.0.0.1 > "$home/server.log" 2>&1 ) &
server=$!
sleep 1

# ONE virtual screen for the whole rehearsal. `xvfb-run` takes its screen away
# when the program it started ends, and here the old app ends on purpose while
# the new one is starting: on a person's PC the screen stays.
Xvfb :97 -screen 0 1280x800x24 -nolisten tcp > "$home/screen.log" 2>&1 &
screen=$!
export DISPLAY=:97
sleep 1

log="rehearsal-$mode.log"
export IRONJARVIS_UPDATE_REHEARSAL_URL="http://127.0.0.1:$port"
if [ "$mode" = "restart" ]; then export IRONJARVIS_UPDATE_REHEARSAL_INSTALL=restart; else unset IRONJARVIS_UPDATE_REHEARSAL_INSTALL; fi
( "$app" > "$log" 2>&1 & )

for i in $(seq 1 90); do answers && break; sleep 1; done
answers || { tail -40 "$log"; fail "the older app never started"; }
echo "the older app is up: $(health | head -c 200)"

# The download: the app says so in its own log.
got=""
for i in $(seq 1 300); do
  if grep -q "\[update\] downloaded + ready" "$log"; then got=1; break; fi
  sleep 1
done
[ -n "$got" ] || { grep -i "update" "$log" | tail -30; fail "the older app never downloaded the update"; }
grep "\[update\]" "$log" | tail -5

if [ "$mode" = "quit" ]; then
  main="$(pgrep -o -f "^$ours")"
  [ -n "$main" ] || fail "the older app is not running"
  kill -TERM "$main"
  for i in $(seq 1 60); do pgrep -f "^$ours" > /dev/null || break; sleep 1; done
  pgrep -f "^$ours" > /dev/null && fail "the older app did not finish quitting"
  now="$(sha256sum "$app" | cut -d' ' -f1)"
  [ "$now" = "$target" ] || { tail -30 "$log"; fail "after Quit the file is not the new one"; }
  ( "$app" > "rehearsal-$mode-after.log" 2>&1 & )
else
  # "Restart to update": the service goes away, the file is replaced, and the
  # new app comes up by itself.
  for i in $(seq 1 60); do answers || break; sleep 1; done
  answers && fail "the older app never stopped to install"
  for i in $(seq 1 60); do [ "$(sha256sum "$app" 2>/dev/null | cut -d' ' -f1)" = "$target" ] && break; sleep 1; done
  [ "$(sha256sum "$app" | cut -d' ' -f1)" = "$target" ] || { tail -30 "$log"; fail "after Restart to update the file is not the new one"; }
fi

for i in $(seq 1 120); do answers && break; sleep 1; done
answers || { grep -v "uvicorn.access" "$log" | tail -30; fail "the new app did not come up"; }
said="$(health)"
echo "the new app is up: $(printf '%s' "$said" | head -c 200)"
if [ -n "$want" ]; then
  printf '%s' "$said" | grep -q "\"version\": *\"$want\"" || fail "the new app reports another version than $want"
fi
echo "PASS ($mode): the older app updated itself${want:+ to $want}" | tee -a "$summary"
cleanup
exit 0
