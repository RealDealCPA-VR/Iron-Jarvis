#!/usr/bin/env bash
# Prove the browser add-on in a REAL browser on this machine, against an ISOLATED daemon.
#
#   bash scripts/verify_browser_addon.sh            # auto-picks msedge, then chrome
#   BROWSER="C:/path/to/msedge.exe" bash scripts/verify_browser_addon.sh
#   PORT=8797 bash scripts/verify_browser_addon.sh
#
# WHY THIS EXISTS (v1.259.0). Chrome and Edge load the same add-on with the same
# pinned id, and "it should work in Edge" had never been proven — on a PC that has
# no Chrome at all. And the obvious check is UNSAFE: the production bundle dials
# ws://127.0.0.1:8787, the user's live daemon, and a newer browser connection
# REPLACES the user's real pairing. So this script:
#   * builds a DEV bundle whose daemon address is another loopback port (the build
#     script refuses anything that is not loopback), into a scratch folder;
#   * starts a fresh `ironjarvis serve --root <scratch>` on that port;
#   * launches the browser HEADLESS with a throwaway profile on a fictional data:
#     URL — no login, no real tab, no touch of the user's own profile;
#   * approves the pairing through the isolated daemon's own API (the human
#     press, on a daemon nobody uses), then does the read-only round trip;
#   * prints the paired browser's NAME as the daemon recorded it, and tears down
#     only what it started. It never sends a byte to :8787 or :8788.
set -u
REPO="$(cd "$(dirname "$0")/.." && pwd)"
PORT="${PORT:-8797}"; DASH_PORT=$((PORT + 1))
WORK="${WORK:-$(mktemp -d 2>/dev/null || echo "/tmp/ij-addon-verify-$$")}"
ROOT="$WORK/root"; PROFILE="$WORK/profile"; ADDON="$WORK/addon"; LOG="$WORK/daemon.log"
BASE="http://127.0.0.1:$PORT"
mkdir -p "$ROOT" "$PROFILE" "$ADDON"

# --- which browser -------------------------------------------------------------
if [ -z "${BROWSER:-}" ]; then
  for c in "/c/Program Files (x86)/Microsoft/Edge/Application/msedge.exe" \
           "/c/Program Files/Microsoft/Edge/Application/msedge.exe" \
           "/c/Program Files/Google/Chrome/Application/chrome.exe" \
           "/c/Program Files (x86)/Google/Chrome/Application/chrome.exe"; do
    [ -f "$c" ] && { BROWSER="$c"; break; }
  done
fi
[ -n "${BROWSER:-}" ] && [ -f "$BROWSER" ] || { echo "no Chrome/Edge found; set BROWSER="; exit 1; }
echo "browser: $BROWSER"

# --- the dev bundle (loopback-only override, refused otherwise) ------------------
( cd "$REPO/extensions/chrome" && \
  IJ_ADDON_OUT="$( (command -v cygpath >/dev/null && cygpath -w "$ADDON/dist") || echo "$ADDON/dist")" \
  IJ_ADDON_DAEMON_WS="ws://127.0.0.1:$PORT/browser/ws" \
  IJ_ADDON_JARVIS_URL="http://127.0.0.1:$DASH_PORT/computeruse" \
  node esbuild.config.mjs > "$WORK/build.log" 2>&1 ) || { echo "dev build failed"; cat "$WORK/build.log"; exit 1; }
cp "$REPO/extensions/chrome/manifest.json" "$ADDON/manifest.json"
grep -q "ws://127.0.0.1:$PORT/browser/ws" "$ADDON/dist/background.js" || { echo "dev bundle does not dial $PORT"; exit 1; }
! grep -q "ws://127.0.0.1:8787/browser/ws" "$ADDON/dist/background.js" || { echo "dev bundle still dials the LIVE port; refusing to load it"; exit 1; }

# --- isolated daemon -----------------------------------------------------------
cd "$REPO"
IRONJARVIS_TOKEN= uv run ironjarvis serve --host 127.0.0.1 --port "$PORT" --root "$ROOT" > "$LOG" 2>&1 &
DPID=$!
for i in $(seq 1 60); do curl -s -m 2 "$BASE/health" > /dev/null 2>&1 && break; sleep 1; done
curl -s -m 3 "$BASE/health" > /dev/null || { echo "daemon never answered"; kill $DPID 2>/dev/null; exit 1; }
curl -s -m 5 -X PUT "$BASE/settings" -H "Content-Type: application/json" -d '{"values":{"browser_access":"read_only"}}' > /dev/null

# --- the browser ---------------------------------------------------------------
W() { (command -v cygpath >/dev/null && cygpath -w "$1") || echo "$1"; }
"$BROWSER" --headless=new --no-first-run --no-default-browser-check --disable-gpu \
  --user-data-dir="$(W "$PROFILE")" --load-extension="$(W "$ADDON")" \
  "data:text/html,<title>Fictional Client Intake</title><h1>Intake for a fictional client</h1>" > "$WORK/browser.log" 2>&1 &
BPID=$!

# --- pair, then prove ----------------------------------------------------------
REQ=""
for i in $(seq 1 45); do
  REQ=$(curl -s -m 3 "$BASE/browser/status" | python -c "import sys,json; s=json.load(sys.stdin); print((s.get('pending_pairing') or {}).get('request_id') or '')" 2>/dev/null)
  [ -n "$REQ" ] && break; sleep 1
done
RC=0
if [ -z "$REQ" ]; then
  echo "FAIL: the add-on never knocked (no pending pairing in 45 s)"; RC=2
else
  curl -s -m 5 -X POST "$BASE/browser/pair" -H "Content-Type: application/json" -d "{\"request_id\":\"$REQ\"}" > /dev/null
  for i in $(seq 1 30); do
    curl -s -m 3 "$BASE/browser/status" | python -c "import sys,json; s=json.load(sys.stdin); sys.exit(0 if s.get('connected') and s.get('browser_name') else 1)" 2>/dev/null && break; sleep 1
  done
  curl -s -m 3 "$BASE/browser/status" | python -c "
import sys,json; s=json.load(sys.stdin)
ok = s.get('connected') and s.get('paired') and s.get('extension_id')==s.get('expected_extension_id')
print(('PASS' if ok else 'FAIL')+': paired', s.get('browser_name'), s.get('browser_version'), '| add-on', s.get('extension_version'), '| id matches pinned:', s.get('extension_id')==s.get('expected_extension_id'))
sys.exit(0 if ok else 3)" || RC=3
  # The round trip must hand back a REAL tab row. Its title is null here ON
  # PURPOSE: site access is an optional permission the user grants by hand, a
  # throwaway profile has never granted it, and the add-on answers `title: null`
  # + `needs_host_permission: true` rather than invent a title (tabs.ts). So
  # the pin is the tab id and that flag — a title is printed when a grant exists.
  curl -s -m 20 -X POST "$BASE/browser/test" | python -c "
import sys,json; r=json.load(sys.stdin); t=r.get('active_tab') or {}
ok = bool(r.get('ok')) and isinstance(t.get('id'), int) and t.get('id') >= 0 and isinstance(t.get('needs_host_permission'), bool)
title = t.get('title') if t.get('title') else '(withheld: no site grant in this fresh profile, needs_host_permission=%s)' % t.get('needs_host_permission')
print(('PASS' if ok else 'FAIL')+': read-only round trip |', r.get('detail'), '| tab id', t.get('id'), '| title', title, '|', r.get('round_trip_ms'), 'ms')
sys.exit(0 if ok else 4)" || RC=4
fi

# --- teardown, mine only -------------------------------------------------------
kill $BPID 2>/dev/null; sleep 1; taskkill //F //T //PID $BPID > /dev/null 2>&1
kill $DPID 2>/dev/null
echo "work dir: $WORK  (daemon.log, browser.log)"
exit $RC
