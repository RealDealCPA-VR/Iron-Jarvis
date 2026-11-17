#!/usr/bin/env bash
# TRIAL ONLY (branch trial/linux-parity). Starts the Linux app each way a
# person might start it on this Ubuntu and reports what happened. Never fails.
set +e
for try in 1 2 3; do
  sudo apt-get update -qq && sudo apt-get install -y -qq xvfb > /dev/null && break
  echo "apt try $try failed"; sleep 20
done
lsb_release -ds
sysctl kernel.apparmor_restrict_unprivileged_userns kernel.unprivileged_userns_clone 2>&1
dpkg -l | grep -E "libfuse2|fuse3" | awk '{print $1, $2, $3}'
app="$(ls desktop/release/*.AppImage | head -1)"
chmod +x "$app"
ours='(\.mount_|appimage_extracted_|/opt/Iron Jarvis/|packaging/dist/ironjarvis|standalone/server\.js|iron-proxy)'
summary="${GITHUB_STEP_SUMMARY:-/dev/null}"

try_start() {
  name="$1"; shift
  echo "::group::start: $name"
  ( xvfb-run -a "$@" > "start-$name.log" 2>&1 & )
  ok="NO"
  for i in $(seq 1 75); do
    if curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8787/health | grep -qE "200|401"; then ok="yes, after ${i}s"; break; fi
    sleep 1
  done
  page="$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8788/ 2>/dev/null)"
  echo "RESULT $name: service answered: $ok; pages: $page" | tee -a "$summary"
  grep -v "uvicorn.access" "start-$name.log" | head -30 | cut -c1-300
  echo "-- its programs:"
  ps -eo pid,ppid,args | grep -E "$ours" | grep -v grep | cut -c1-200 | head -20
  echo "-- what the updater needs (APPIMAGE):"
  for p in $(pgrep -f "$ours"); do
    tr '\0' '\n' < "/proc/$p/environ" 2>/dev/null | grep -E "^(APPIMAGE|APPDIR)="
  done | sort -u
  main="$(pgrep -o -f '^(/tmp/\.mount_|/tmp/appimage_extracted_|/opt/Iron Jarvis/)')"
  if [ -n "$main" ]; then
    kill -TERM "$main"; sleep 12
    left="$(ps -eo pid,args | grep -E "$ours" | grep -v grep | cut -c1-160)"
    echo "RESULT $name: programs still running 12 s after a polite stop: $(printf '%s' "$left" | grep -c .)" | tee -a "$summary"
    printf '%s\n' "$left" | head -12
  fi
  pkill -9 -f "$ours"; pkill -9 -f Xvfb; sleep 3
  echo "::endgroup::"
}

try_start "appimage-new-runtime-as-downloaded" "$app"

deb="$(ls desktop/release/*.deb 2>/dev/null | head -1)"
if [ -n "$deb" ]; then
  echo "::group::the .deb"
  dpkg-deb -I "$deb" | head -30
  mkdir ctl && dpkg-deb -e "$deb" ctl
  for f in ctl/postinst ctl/postrm; do echo "== $f"; cat "$f"; done
  dpkg-deb -c "$deb" | grep -vE "/resources/(daemon|dashboard|vosk-model)/" | head -40
  sudo apt-get install -y -qq "./$deb" > deb-install.log 2>&1; echo "deb install exit: $?"; tail -5 deb-install.log
  ls -la "/opt/Iron Jarvis" | head -20
  sudo aa-status 2>/dev/null | grep -i -E "iron|jarvis" || echo "no AppArmor profile names the app"
  echo "::endgroup::"
  bin="$(ls -d "/opt/Iron Jarvis/"* | grep -E "/iron-jarvis[^/]*$" | head -1)"
  try_start "deb-installed" "${bin:-/opt/Iron Jarvis/iron-jarvis-desktop}"
else
  echo "RESULT deb: not built" | tee -a "$summary"
fi
exit 0
