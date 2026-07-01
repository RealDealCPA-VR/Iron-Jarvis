"""One-shot loopback HTTP listener for OAuth redirects (RFC 8252 native-app flow).

Some embedded public OAuth clients are registered against a FIXED localhost
redirect that is not the daemon's own port — OpenAI's Codex client only accepts
``http://localhost:1455/auth/callback``. This module binds that exact port for
the duration of one flow, catches the single redirect, hands ``code``/``state``
to a callback (which runs the PKCE-validated token exchange), renders a small
result page, and shuts itself down.

Security: the listener binds 127.0.0.1 only and the callback can't complete an
OAuth flow without the CSRF ``state`` minted by ``start_oauth`` (a drive-by
request with a bogus state fails the exchange). Every interpolated value in the
result page is HTML-escaped; the postMessage payload is JSON built server-side
(mirrors the daemon's /oauth/{provider}/callback hardening). The listener also
expires on a TTL so an abandoned flow never leaves a port bound.
"""

from __future__ import annotations

import html as _html
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

from ..core.logging import get_logger

log = get_logger("oauth-loopback")

#: Abandon an unanswered flow after this long (matches the registry's pending TTL).
DEFAULT_TTL_SECONDS = 600


class OAuthLoopbackServer:
    """Bind a loopback port, catch ONE OAuth redirect, complete, shut down."""

    def __init__(
        self,
        *,
        port: int,
        path: str,
        provider: str,
        on_code,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
    ) -> None:
        self.port = port
        self.path = path
        self.provider = provider
        self.on_code = on_code  # (code, state) -> None; raise = failure page
        self.ttl_seconds = ttl_seconds
        self._httpd: HTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._timer: threading.Timer | None = None

    @property
    def bound_port(self) -> int:
        """The actually-bound port (differs from ``port`` when 0 = ephemeral)."""
        return self._httpd.server_address[1] if self._httpd else self.port

    def start(self) -> None:
        """Bind and serve in a daemon thread. Raises ``OSError`` if the port is busy."""
        self._httpd = HTTPServer(("127.0.0.1", self.port), self._make_handler())
        self._thread = threading.Thread(
            target=self._httpd.serve_forever,
            name=f"oauth-loopback-{self.provider}",
            daemon=True,
        )
        self._thread.start()
        self._timer = threading.Timer(self.ttl_seconds, self.stop)
        self._timer.daemon = True
        self._timer.start()
        log.info("oauth loopback listening on 127.0.0.1:%s%s", self.bound_port, self.path)

    def stop(self) -> None:
        """Idempotent teardown (called after the redirect, on TTL, or on restart)."""
        timer, self._timer = self._timer, None
        if timer:
            timer.cancel()
        httpd, self._httpd = self._httpd, None
        if httpd:
            try:
                httpd.shutdown()  # stops serve_forever (safe from any OTHER thread)
                httpd.server_close()
            except Exception:  # noqa: BLE001 — teardown must never raise
                pass

    def _make_handler(self):
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args) -> None:  # silence per-request stderr noise
                pass

            def do_GET(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler API
                parsed = urlparse(self.path)
                if parsed.path != outer.path:
                    self.send_error(404)
                    return
                qs = parse_qs(parsed.query)
                code = (qs.get("code") or [""])[0]
                state = (qs.get("state") or [""])[0]
                try:
                    outer.on_code(code, state)
                    ok, msg = True, f"Connected to {outer.provider}. You can close this window."
                except Exception as exc:  # noqa: BLE001 — surface, don't crash the thread
                    ok, msg = False, f"Connection failed: {exc}"
                body = _result_page(outer.provider, ok, msg)
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header(
                    "Content-Security-Policy",
                    "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'",
                )
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                # One-shot: tear down after the redirect. shutdown() would
                # deadlock from THIS (serving) thread — stop from another one.
                threading.Thread(target=outer.stop, daemon=True).start()

        return Handler


def _result_page(provider: str, ok: bool, msg: str) -> bytes:
    """Dark result page mirroring the daemon's /oauth callback (XSS-escaped)."""
    color = "#22d3ee" if ok else "#fb7185"
    safe_msg = _html.escape(msg)
    payload = json.dumps(
        {"type": "ironjarvis-oauth", "provider": provider, "ok": ok}
    ).replace("<", "\\u003c")
    html = (
        "<!doctype html><meta charset=utf-8><title>Iron Jarvis</title>"
        "<body style='background:#0a0a0f;color:#e5e7eb;font-family:system-ui;"
        "display:grid;place-items:center;height:100vh;margin:0'>"
        f"<div style='text-align:center'><div style='font-size:42px;color:{color}'>"
        f"{'&#10003;' if ok else '&#10005;'}</div><p>{safe_msg}</p></div>"
        "<script>try{window.opener&&window.opener.postMessage("
        f"JSON.parse({json.dumps(payload)}),'*');"
        "setTimeout(()=>window.close(),1200)}catch(e){}</script></body>"
    )
    return html.encode("utf-8")
