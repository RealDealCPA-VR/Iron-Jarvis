"""Install/upgrade/packaging lens fixes — the testable (Python) slice.

The Electron/electron-updater/CI fixes (quitAndInstall teardown, failed-update
recovery, releaseType, version-sync) are verified by review + node --check; here
we cover the daemon preflight identity probe and the version single-source guard.
"""

from __future__ import annotations

import http.server
import json
import pathlib
import threading
import tomllib

from iron_jarvis.daemon.cli import _is_ironjarvis_daemon

_ROOT = pathlib.Path(__file__).resolve().parents[1]


def _serve(body: bytes):
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):  # silence
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def test_preflight_recognizes_a_real_daemon():
    srv = _serve(json.dumps({"status": "ok", "version": "1.0.0"}).encode())
    try:
        assert _is_ironjarvis_daemon("127.0.0.1", srv.server_address[1]) is True
    finally:
        srv.shutdown()


def test_preflight_rejects_a_foreign_server_on_the_port():
    # IJ-PKG-02: an unrelated program on the baked port must NOT be mistaken for us.
    srv = _serve(b"<html>totally different app</html>")
    try:
        assert _is_ironjarvis_daemon("127.0.0.1", srv.server_address[1]) is False
    finally:
        srv.shutdown()

    srv2 = _serve(json.dumps({"status": "other"}).encode())  # JSON but wrong shape
    try:
        assert _is_ironjarvis_daemon("127.0.0.1", srv2.server_address[1]) is False
    finally:
        srv2.shutdown()


def test_preflight_false_when_nothing_listens():
    assert _is_ironjarvis_daemon("127.0.0.1", 59637) is False


def test_desktop_version_matches_pyproject():
    # PKG-1/F2/AU-2/VER-1/CI-1: the single most-reported drift. electron-updater
    # compares desktop/package.json — it must equal the pyproject/daemon version.
    py = tomllib.loads((_ROOT / "pyproject.toml").read_text())["project"]["version"]
    desk = json.loads((_ROOT / "desktop" / "package.json").read_text())["version"]
    assert desk == py, f"version drift: desktop {desk} != pyproject {py}"
