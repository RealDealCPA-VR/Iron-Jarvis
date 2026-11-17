#!/usr/bin/env bash
# Starts a built Linux app THE WAY A PERSON WOULD and proves it works.
#
#   scripts/linux_app_check.sh appimage <file.AppImage>
#   scripts/linux_app_check.sh deb      <file.deb>
#
# No helper flags: the earlier check passed --appimage-extract-and-run and
# --no-sandbox, and so never saw that the file did not start as downloaded on a
# current Ubuntu (it needed libfuse2, which Ubuntu no longer installs).
#
# It passes only when the service answers on 8787, the pages answer 200 on
# 8788, and nothing of the app is left running after a polite stop. Used by
# .github/workflows/linux-release.yml on GitHub's machine; it installs xvfb
# (a virtual screen) and, for a .deb, the package itself.
set -uo pipefail

kind="${1:?appimage or deb}"
file="${2:?the file to check}"
ours='(/tmp/\.mount_|/tmp/appimage_extracted_|/opt/Iron Jarvis/)'

for try in 1 2 3; do
  if sudo apt-get update -qq && sudo apt-get install -y -qq xvfb > /dev/null; then break; fi
  echo "apt try $try failed"; sleep 20
done

case "$kind" in
  appimage)
    chmod +x "$file"
    start=("$file")
    ;;
  deb)
    sudo apt-get install -y -qq "./$file" > deb-install.log 2>&1 || { tail -20 deb-install.log; echo "FAIL: the package did not install"; exit 1; }
    bin="$(ls -d "/opt/Iron Jarvis/"* | grep -E "/iron-jarvis[^/]*$" | head -1)"
    [ -x "$bin" ] || { echo "FAIL: no program under /opt/Iron Jarvis"; exit 1; }
    [ "$(cat "/opt/Iron Jarvis/resources/package-type" 2>/dev/null)" = "deb" ] || { echo "FAIL: the package does not say it is a package (no update would install)"; exit 1; }
    start=("$bin")
    ;;
  *) echo "appimage or deb"; exit 2 ;;
esac

log="start-$kind.log"
( xvfb-run -a "${start[@]}" > "$log" 2>&1 & )
ok=""
for i in $(seq 1 90); do
  if curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8787/health | grep -qE "200|401"; then ok="$i"; break; fi
  sleep 1
done
page=""
for i in $(seq 1 30); do
  page="$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8788/ 2>/dev/null)"
  [ "$page" = "200" ] && break
  sleep 1
done
grep -v "uvicorn.access" "$log" | head -40 | cut -c1-300
main="$(pgrep -o -f "^$ours" || true)"
flags="$(ps -o args= -p "${main:-0}" 2>/dev/null | cut -c1-200)"
echo "the app's own program: $flags"
left=0
if [ -n "$main" ]; then
  kill -TERM "$main"
  for i in $(seq 1 20); do
    left="$(ps -eo args | grep -E "$ours|standalone/server\.js" | grep -v grep | grep -c . || true)"
    [ "$left" = "0" ] && break
    sleep 1
  done
  ps -eo pid,args | grep -E "$ours|standalone/server\.js" | grep -v grep | cut -c1-160 | head -12
fi
pkill -9 -f "$ours" 2>/dev/null; pkill -9 -f Xvfb 2>/dev/null; sleep 2

summary="${GITHUB_STEP_SUMMARY:-/dev/null}"
echo "linux $kind: service answered after ${ok:-NEVER}s; pages $page; left running after a polite stop: $left" | tee -a "$summary"
[ -n "$ok" ] || { echo "FAIL: the service never answered"; exit 1; }
[ "$page" = "200" ] || { echo "FAIL: the pages did not answer"; exit 1; }
[ "$left" = "0" ] || { echo "FAIL: programs were left running after the app was asked to stop"; exit 1; }
if [ "$kind" = "deb" ]; then
  # The package runs with the browser engine's inner safety box ON.
  case "$flags" in *--no-sandbox*) echo "FAIL: the installed package runs with the safety box off"; exit 1 ;; esac
fi
exit 0
