"""Thin HTTP client for talking to a running daemon (used by the CLI).

AUTH IS NOT OPTIONAL HERE. The packaged desktop app generates a
per-install bearer token, writes it to ``<userData>/token.txt`` and starts the
daemon with ``IRONJARVIS_TOKEN`` set, so every path except ``/health`` answers
401 without it. This client used to send no credential at all — and because
``httpx`` does not raise on 4xx and ``.json()`` happily parses the error body,
``ironjarvis cancel``/``rerun``/``delete-session`` took their SUCCESS path:
they printed ``{'detail': 'missing or invalid token'}`` and exited 0 while the
runaway session they were told to stop kept running.

Three rules follow, and all three are load-bearing:

1. Find the token the way the desktop app stores it (env first, then the
   packaged ``token.txt``), and send it as ``Authorization: Bearer``.
2. DISCOVERY IS SCOPED TO LOOPBACK. Reading ``token.txt`` off this install and
   attaching it to whatever host ``--url`` names would exfiltrate the key that
   unlocks an RCE-by-design daemon (agents run shell, ``/terminals`` spawns a
   PTY) to a machine the user merely typed. ``auth.py`` supports remote/tailnet
   deployments, so a non-loopback ``--url`` is real usage — it just has to
   supply its OWN credential via ``--token`` or an explicitly-set
   ``IRONJARVIS_TOKEN``. Anything else sends no Authorization header at all.
3. A non-2xx response RAISES. The CLI wraps every call in ``try/except`` and
   exits 1, so a refusal now fails loudly instead of being printed as a result.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from .auth import _LOOPBACK_HOSTS, _host_label

_TOKEN_ENV = "IRONJARVIS_TOKEN"


class DaemonError(RuntimeError):
    """A daemon answered, and it said no. Carries the status for callers."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _token_files() -> list[Path]:
    """Where the desktop app's ``getOrCreateToken()`` persists this install's
    token — Electron's ``app.getPath('userData')`` per platform."""
    out: list[Path] = []
    appdata = os.environ.get("APPDATA")
    if appdata:
        out.append(Path(appdata) / "Iron Jarvis" / "token.txt")
    home = Path.home()
    out.append(home / "Library" / "Application Support" / "Iron Jarvis" / "token.txt")
    out.append(home / ".config" / "Iron Jarvis" / "token.txt")
    return out


def daemon_token() -> str:
    """The bearer token for the local daemon, or ``""`` when auth is off.

    ``IRONJARVIS_TOKEN`` wins (a shell that launched the daemon has it, and it
    is how a user overrides); otherwise read the packaged app's ``token.txt``.
    """
    env = (os.environ.get(_TOKEN_ENV) or "").strip()
    if env:
        return env
    for path in _token_files():
        try:
            value = path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if value:
            return value
    return ""


def _is_loopback_url(base_url: str) -> bool:
    """Does ``base_url`` name THIS machine?

    Reuses the daemon's own host vocabulary (``auth._LOOPBACK_HOSTS``) so client
    and server can never disagree about what "local" means.
    ``urlparse().hostname`` already lowercases, drops the port and unwraps an
    IPv6 literal's brackets, so the hostname goes straight to the set; a URL with
    no scheme parses as a bare path and falls back to ``auth._host_label``.
    Unknown or unparsable => False. This gate decides whether a stored
    credential leaves the box, so it fails CLOSED.
    """
    try:
        host = (urlparse(base_url).hostname or "").strip().lower()
    except ValueError:  # malformed IPv6 literal, e.g. "http://[::1"
        return False
    if not host:
        host = _host_label(base_url).strip().lower()
    # `""` is a member of _LOOPBACK_HOSTS (a missing Host header, server-side);
    # for a URL an empty host proves nothing, so require a real label.
    return bool(host) and host in _LOOPBACK_HOSTS


class DaemonClient:
    def __init__(
        self, base_url: str = "http://127.0.0.1:8787", token: str | None = None
    ) -> None:
        self.base_url = base_url.rstrip("/")
        if token is not None:
            # An explicit --token is the caller's own decision, for any host;
            # ``token=""`` means "send nothing".
            self.token = token.strip()
        elif _is_loopback_url(self.base_url):
            self.token = daemon_token()
        else:
            # A remote/tailnet daemon must bring its own credential. The env var
            # is an explicit act; this install's token.txt is NOT (see rule 2).
            self.token = (os.environ.get(_TOKEN_ENV) or "").strip()

    # --- plumbing ---------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    def _unwrap(self, resp: httpx.Response) -> dict[str, Any]:
        """Parsed JSON for a 2xx; a raised :class:`DaemonError` otherwise."""
        if resp.status_code >= 400:
            detail = ""
            try:
                body = resp.json()
                detail = str(body.get("detail") or body) if isinstance(body, dict) else str(body)
            except Exception:  # noqa: BLE001 — non-JSON error body
                detail = (resp.text or "").strip()[:500]
            msg = f"HTTP {resp.status_code} from {resp.request.url.path}: {detail}"
            if resp.status_code == 401 and not self.token:
                msg += (
                    " — this daemon requires a token and none was found. Pass"
                    f" --token, or set {_TOKEN_ENV} (the desktop app stores it in"
                    " token.txt next to its state home)."
                )
            raise DaemonError(resp.status_code, msg)
        return resp.json()

    def _call(
        self, method: str, path: str, *, timeout: float, json: Any | None = None
    ) -> dict[str, Any]:
        resp = httpx.request(
            method,
            f"{self.base_url}{path}",
            headers=self._headers(),
            json=json,
            timeout=timeout,
        )
        return self._unwrap(resp)

    # --- endpoints --------------------------------------------------------

    def health(self) -> dict[str, Any]:
        return self._call("GET", "/health", timeout=5)

    def create_session(
        self, task: str, agent_type: str = "builder", provider: str | None = None
    ) -> dict[str, Any]:
        return self._call(
            "POST",
            "/sessions",
            json={"task": task, "agent_type": agent_type, "provider": provider},
            timeout=120,
        )

    def sessions(self) -> dict[str, Any]:
        return self._call("GET", "/sessions", timeout=10)

    def cancel(self, session_id: str) -> dict[str, Any]:
        return self._call("POST", f"/sessions/{session_id}/cancel", timeout=10)

    def rerun(self, session_id: str) -> dict[str, Any]:
        return self._call("POST", f"/sessions/{session_id}/rerun", timeout=120)

    def delete(self, session_id: str) -> dict[str, Any]:
        return self._call("DELETE", f"/sessions/{session_id}", timeout=10)

    def update_check(self) -> dict[str, Any]:
        return self._call("GET", "/update/check", timeout=30)

    def update_apply(self, build_dashboard: bool = True) -> dict[str, Any]:
        # A pull + uv sync + pnpm build can take a while — generous timeout.
        return self._call(
            "POST",
            "/update/apply",
            json={"build_dashboard": build_dashboard},
            timeout=900,
        )
