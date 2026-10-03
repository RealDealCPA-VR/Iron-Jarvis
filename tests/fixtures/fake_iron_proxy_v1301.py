"""A fake Iron-Proxy speaking the control API Iron Jarvis uses (v1.301.0).

Two ways to use it:

* IN-PROCESS: ``FakeIronProxy(data_dir).start()`` serves on a free port in a
  daemon thread and writes ``<data_dir>/proxy.json`` exactly like
  ``iron-proxy serve`` does (``{url, token, pid}``). ``.stop()`` shuts it down.
* AS THE SPAWNED "BUNDLE": ``python fake_iron_proxy_v1301.py serve --port 0
  --data-dir D`` — point ``IRONJARVIS_IRON_PROXY_NODE`` at ``sys.executable``
  and ``IRONJARVIS_IRON_PROXY_BUNDLE`` at this file, and the service under test
  starts / locates / reuses / stops a REAL child process. ``--version`` prints a
  version. A ``<data_dir>/fake-seed.json`` (``{"profiles": [...], "states":
  {...}, "discover": [...]}``) seeds the spawned child. Environment knobs for
  failure tests: ``FAKE_IRON_PROXY_START_DELAY`` (seconds before listening) and
  ``FAKE_IRON_PROXY_EXIT`` (exit immediately with that code) and
``FAKE_IRON_PROXY_OLD`` (serve as a pre-executor Iron-Proxy: no ``features``
on health, ``/iron/pick`` 404). In-process: ``FakeIronProxy(..., features=None)``.

Errors use the Iron-Proxy ``json`` dialect written by
``packages/proxy/src/server.ts::errorBody``: ``{"error": {"message", "code"},
"iron": {"code", "retryable", "details", "hint"?}}`` with the status from
``statusForCode``. Stdlib only (it runs as a bare child interpreter).
"""

from __future__ import annotations

import json
import os
import secrets
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

PROVIDERS = ("anthropic", "openai", "google", "xai", "openai-compatible")
HOME_VAR = {
    "anthropic": "CLAUDE_CONFIG_DIR",
    "openai": "CODEX_HOME",
    "xai": "GROK_HOME",
    "google": "GEMINI_CLI_HOME",
}
BINARY = {"anthropic": "claude", "openai": "codex", "xai": "grok", "google": "gemini"}
LOGIN_ARGS = {
    "anthropic": ["auth", "login"],
    "openai": ["login"],
    "xai": ["login", "--oauth"],
    "google": [],
}
COMMON_STRIP = [
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "XAI_API_KEY",
    "GROK_API_KEY",
]
STATUS_BY_CODE = {
    "NO_PROFILE": 404,
    "PROFILE_NOT_FOUND": 404,
    "AUTH_REQUIRED": 401,
    "ALL_PROFILES_EXHAUSTED": 429,
    "QUOTA_EXCEEDED": 429,
    "INVALID_REQUEST": 400,
    "UNSUPPORTED": 400,
}
FAKE_VERSION = "0.0.0-fake"
#: v1.302.0: an Iron-Proxy that picks a CHOSEN account (``/iron/pick?profileId=``).
#: Without "pick-profile" the fake behaves like the older one: the parameter is
#: IGNORED and the first free account is answered.
FEATURES_PICK_PROFILE = ("executor-v1", "pick-profile")
#: Iron-Proxy's own DEFAULT_HINTS (packages/core/src/errors.ts), verbatim.
HINT_PROFILE_NOT_FOUND = (
    "Run iron-proxy profiles list (or open the switcher) and use one of the ids shown there."
)
HINT_AUTH_REQUIRED = (
    "Log the account in again: iron-proxy login <id>, or 'Log in' on it in the switcher."
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class FakeError(Exception):
    def __init__(self, code: str, message: str, *, hint: str | None = None,
                 details: dict | None = None, status: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint
        self.details = details or {}
        self.status = status or STATUS_BY_CODE.get(code, 502)

    def body(self) -> dict[str, Any]:
        iron: dict[str, Any] = {"code": self.code, "retryable": False, "details": self.details}
        if self.hint:
            iron["hint"] = self.hint
        return {"error": {"message": self.message, "code": self.code}, "iron": iron}


class FakeIronProxy:
    """The fake's state + HTTP server. Thread-safe enough for tests (one lock)."""

    def __init__(self, data_dir: str | Path, *, token: str | None = None,
                 health_version: str | None = None,
                 features: list[str] | None = ("executor-v1",)) -> None:  # type: ignore[assignment]
        self.data_dir = Path(data_dir)
        self.token = token or secrets.token_urlsafe(24)
        self.health_version = health_version
        #: ``None`` = an OLDER Iron-Proxy (no ``features`` field on health,
        #: no ``/iron/pick``) — the tray app before the executor API.
        self.features = list(features) if features is not None else None
        self.lock = threading.Lock()
        self.profiles: dict[str, dict[str, Any]] = {}
        self.states: dict[str, dict[str, Any]] = {}
        self.discovered: list[dict[str, Any]] = []
        self.calls: list[tuple[str, str, Any]] = []  # (method, path, body)
        #: v1.302.0: (path, query) for every request — ``calls`` drops the query.
        self.queries: list[tuple[str, dict[str, list[str]]]] = []
        #: v1.302.0: True = a pinned pick lends a parked / signed-out account
        #: anyway (what ``pickProfile`` did before it checked availability).
        self.pinned_lenient = False
        self.auth_headers: list[str | None] = []
        self._seq = 0
        self.server: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None
        self.url = ""
        seed = self.data_dir / "fake-seed.json"
        if seed.exists():
            self.seed(json.loads(seed.read_text(encoding="utf-8")))

    # ------------------------------------------------------------- state
    def seed(self, doc: dict[str, Any]) -> None:
        for p in doc.get("profiles", []):
            self.add_profile(**p)
        for pid, st in (doc.get("states") or {}).items():
            self.states.setdefault(pid, self._fresh_state(pid)).update(st)
        self.discovered = list(doc.get("discover") or [])

    def _fresh_state(self, pid: str) -> dict[str, Any]:
        return {"profileId": pid, "status": "ready", "served": 0}

    def add_profile(self, provider: str, title: str, lane: str = "cli", *,
                    id: str | None = None, enabled: bool = True, home: str | None = None,
                    adopted: bool = False, order: int | None = None) -> dict[str, Any]:
        if provider not in PROVIDERS:
            raise FakeError("INVALID_REQUEST", f'Unknown provider "{provider}".',
                            hint=f"Use one of: {', '.join(PROVIDERS)}.")
        if not (title or "").strip():
            raise FakeError("INVALID_REQUEST", "A profile needs a title.",
                            hint='Give the account a title you will recognise, e.g. "Work Claude".')
        self._seq += 1
        pid = id or f"prof_{self._seq:04d}"
        siblings = [p for p in self.profiles.values() if p["provider"] == provider]
        if order is None:
            order = (max(p["order"] for p in siblings) + 1) if siblings else 0
        now = _now()
        prof: dict[str, Any] = {
            "id": pid, "title": title.strip(), "provider": provider, "lane": lane,
            "order": order, "enabled": enabled, "createdAt": now, "updatedAt": now,
        }
        if lane == "cli":
            prof["cli"] = {"home": home or str(self.data_dir / "cli-homes" / provider / pid)}
            if adopted:
                prof["cli"]["adopted"] = True
        self.profiles[pid] = prof
        self.states[pid] = self._fresh_state(pid)
        return prof

    def _get(self, pid: str) -> dict[str, Any]:
        prof = self.profiles.get(pid)
        if prof is None:
            raise FakeError("PROFILE_NOT_FOUND", f'No profile "{pid}".',
                            hint=HINT_PROFILE_NOT_FOUND)
        return prof

    def _ordered(self, provider: str | None = None, lane: str | None = None) -> list[dict]:
        out = [p for p in self.profiles.values()
               if (provider is None or p["provider"] == provider)
               and (lane is None or p["lane"] == lane)]
        return sorted(out, key=lambda p: (p["provider"], p["order"]))

    def pick(self, provider: str, lane: str = "cli") -> dict[str, Any]:
        cands = [p for p in self._ordered(provider, lane) if p["enabled"]]
        if not cands:
            raise FakeError("NO_PROFILE", f"No enabled {provider} {lane} account.")
        now = datetime.now(timezone.utc)
        parked, unauth = [], []
        for p in cands:
            st = self.states[p["id"]]
            if st["status"] == "unauthenticated":
                unauth.append(p)
                continue
            until = st.get("parkedUntil")
            if st["status"] == "parked" and until and datetime.fromisoformat(
                until.replace("Z", "+00:00")
            ) > now:
                parked.append(until)
                continue
            home_var = HOME_VAR.get(provider, "HOME")
            return {
                "profile": p,
                "env": {"set": {home_var: p["cli"]["home"]}, "unset": list(COMMON_STRIP)},
            }
        if unauth:
            p = unauth[0]
            raise FakeError("AUTH_REQUIRED", f'"{p["title"]}" needs to sign in.',
                            details={"profileId": p["id"], "title": p["title"]},
                            hint=HINT_AUTH_REQUIRED)
        earliest = min(parked)
        raise FakeError("ALL_PROFILES_EXHAUSTED", f"All {provider} accounts are parked.",
                        details={"resetAt": earliest, "earliestResetAt": earliest})

    def pick_profile(self, provider: str, lane: str, pid: str) -> dict[str, Any]:
        """``GET /iron/pick?profileId=`` (v1.302.0): THAT account, or an error
        naming it — Iron-Proxy 2d4f645's ``pickProfile(provider, {profileId,
        lane})``: unknown 404 PROFILE_NOT_FOUND ``{profileId}``; wrong
        provider/lane 400 INVALID_REQUEST; disabled 400 INVALID_REQUEST
        ("…is disabled."); parked 429 QUOTA_EXCEEDED ``{profileId, title,
        provider, resetAt?, kind?}``; signed out 401 AUTH_REQUIRED
        ``{profileId, title}``; an expired park answers 200."""
        if pid not in self.profiles:
            raise FakeError("PROFILE_NOT_FOUND", f'No profile "{pid}".',
                            hint=HINT_PROFILE_NOT_FOUND, details={"profileId": pid})
        p = self._get(pid)
        if p["provider"] != provider or p["lane"] != lane:
            raise FakeError(
                "INVALID_REQUEST",
                f'Profile "{p["title"]}" is a {p["provider"]} {p["lane"]} account, '
                f"not a {provider} {lane} one.",
                details={"profileId": pid, "provider": p["provider"], "lane": p["lane"]},
            )
        if not p["enabled"]:
            raise FakeError("INVALID_REQUEST", f'Profile "{p["title"]}" is disabled.',
                            details={"profileId": pid})
        st = self.states[pid]
        if not self.pinned_lenient:
            if st["status"] == "unauthenticated":
                raise FakeError("AUTH_REQUIRED", f'"{p["title"]}" needs to sign in.',
                                details={"profileId": pid, "title": p["title"]},
                                hint=HINT_AUTH_REQUIRED)
            until = st.get("parkedUntil")
            if st["status"] == "parked" and until and datetime.fromisoformat(
                until.replace("Z", "+00:00")
            ) > datetime.now(timezone.utc):
                details = {"profileId": pid, "title": p["title"], "provider": p["provider"],
                           "resetAt": until}
                reason = st.get("parkedReason")
                if isinstance(reason, dict) and reason.get("kind"):
                    details["kind"] = reason["kind"]
                raise FakeError("QUOTA_EXCEEDED", f'"{p["title"]}" is parked.', details=details)
        home_var = HOME_VAR.get(provider, "HOME")
        return {
            "profile": p,
            "env": {"set": {home_var: p["cli"]["home"]}, "unset": list(COMMON_STRIP)},
        }

    def signal(self, pid: str, body: dict[str, Any]) -> dict[str, Any]:
        self._get(pid)
        status = body.get("status")
        text = str(body.get("text") or "")
        kind = None
        if status in (429, 529):
            kind = "rate-limit"
        elif status == 401:
            kind = "auth-expired"
        elif status is None and "limit" in text.lower():
            kind = "quota-exhausted"
        if kind is None:
            return {"parked": False}
        st = self.states[pid]
        sig: dict[str, Any] = {"kind": kind}
        if kind == "auth-expired":
            st["status"] = "unauthenticated"
        else:
            reset = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
            sig["resetAt"] = reset
            st.update({"status": "parked", "parkedUntil": reset,
                       "parkedReason": {"kind": kind, "source": "status",
                                        "message": text[:80] or None}})
        return {"parked": True, "signal": sig, "state": st}

    def finished(self, pid: str, body: dict[str, Any]) -> dict[str, Any]:
        prof = self._get(pid)
        for other in self._ordered(prof["provider"]):
            if self.states[other["id"]]["status"] == "active":
                self.states[other["id"]]["status"] = "ready"
        st = self.states[pid]
        st["status"] = "active"
        st["served"] = int(st.get("served", 0)) + 1
        st["lastUsedAt"] = _now()
        return {"ok": True, "state": st}

    def usage(self, pid: str | None) -> list[dict[str, Any]]:
        targets = [self._get(pid)] if pid else self._ordered()
        out = []
        for p in targets:
            served = int(self.states[p["id"]].get("served", 0))
            w = {"requests": served, "inputTokens": 0, "outputTokens": 0}
            out.append({"profileId": p["id"],
                        "windows": {"1h": w, "5h": w, "24h": w, "7d": w},
                        "parks7d": 0, "estimate": {"minutesLeft": 42,
                                                   "basis": "utilisation-trend",
                                                   "confidence": "low"}})
        return out

    # ------------------------------------------------------------ routing
    def handle(self, method: str, raw_path: str, headers: dict[str, str],
               body: Any) -> tuple[int, Any]:
        parsed = urlparse(raw_path)
        path = parsed.path.rstrip("/") or "/"
        query = parse_qs(parsed.query)
        with self.lock:
            self.calls.append((method, path, body))
            self.queries.append((path, query))
            if path == "/iron/health":
                out: dict[str, Any] = {"ok": True, "name": "iron-proxy",
                                       "profiles": len(self.profiles)}
                if self.health_version:
                    out["version"] = self.health_version
                if self.features is not None:
                    out["features"] = list(self.features)
                return 200, out
            auth = headers.get("authorization")
            self.auth_headers.append(auth)
            if auth != f"Bearer {self.token}":
                raise FakeError("AUTH_REQUIRED", "Invalid token.", status=401)
            parts = [unquote(p) for p in path.split("/") if p]
            sub = parts[1] if len(parts) > 1 else ""
            pid = parts[2] if len(parts) > 2 else None
            action = parts[3] if len(parts) > 3 else None
            if sub == "states" and method == "GET":
                return 200, dict(self.states)
            if sub == "discover" and method == "GET":
                return 200, list(self.discovered)
            if sub == "usage" and method == "GET":
                return 200, self.usage((query.get("profileId") or [None])[0])
            if sub == "pick" and method == "GET" and self.features is None:
                raise FakeError("INVALID_REQUEST", f"No control route {method} {path}.",
                                status=404)
            if sub == "pick" and method == "GET":
                provider = (query.get("provider") or [""])[0]
                lane = (query.get("lane") or ["cli"])[0]
                chosen = (query.get("profileId") or [""])[0]
                if chosen and "pick-profile" in (self.features or []):
                    return 200, self.pick_profile(provider, lane, chosen)
                return 200, self.pick(provider, lane)
            if sub == "adopt" and method == "POST":
                if not isinstance(body.get("provider"), str) or not isinstance(body.get("home"), str):
                    raise FakeError("INVALID_REQUEST", "`provider` and `home` are required.",
                                    status=400)
                title = body.get("title") or f"{body['provider']} (existing login)"
                return 201, self.add_profile(body["provider"], title, "cli",
                                             home=body["home"], adopted=True)
            if sub == "profiles":
                if pid is None and method == "GET":
                    return 200, self._ordered()
                if pid is None and method == "POST":
                    return 201, self.add_profile(body.get("provider", ""), body.get("title", ""),
                                                 body.get("lane", "cli"))
                if pid == "reorder" and method == "POST":
                    for i, rid in enumerate(body.get("ids") or []):
                        self._get(rid)["order"] = i
                    return 200, self._ordered(body.get("provider"))
                if pid is None:
                    raise FakeError("INVALID_REQUEST", "Method not allowed.", status=405)
                if action is None:
                    if method == "GET":
                        return 200, self._get(pid)
                    if method == "PATCH":
                        prof = self._get(pid)
                        for k in ("title", "enabled", "order"):
                            if k in body:
                                prof[k] = body[k]
                        prof["updatedAt"] = _now()
                        return 200, prof
                    if method == "DELETE":
                        self._get(pid)
                        del self.profiles[pid]
                        self.states.pop(pid, None)
                        return 200, {"ok": True}
                if method == "GET" and action == "login-command":
                    prof = self._get(pid)
                    if prof["lane"] != "cli":
                        raise FakeError("UNSUPPORTED", "Only CLI profiles have a login command.")
                    env = {"PATH": os.environ.get("PATH", ""),
                           "TEMP": os.environ.get("TEMP", ""),
                           HOME_VAR.get(prof["provider"], "HOME"): prof["cli"]["home"]}
                    return 200, {"binary": BINARY.get(prof["provider"], "cli"),
                                 "args": LOGIN_ARGS.get(prof["provider"], []),
                                 "env": env,
                                 "requiresTerminal": prof["provider"] == "google"}
                if method == "POST" and action == "logout":
                    self._get(pid)
                    self.states[pid]["status"] = "unauthenticated"
                    return 200, {"ok": True}
                if method == "POST" and action == "unpark":
                    self._get(pid)
                    st = self.states[pid]
                    st["status"] = "ready"
                    st.pop("parkedUntil", None)
                    st.pop("parkedReason", None)
                    return 200, {"ok": True}
                if method == "POST" and action == "signal":
                    return 200, self.signal(pid, body)
                if method == "POST" and action == "finished":
                    return 200, self.finished(pid, body)
            raise FakeError("INVALID_REQUEST", f"No control route {method} {path}.", status=404)

    # ------------------------------------------------------------- server
    def _handler(self):
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):  # noqa: D401 — quiet
                pass

            def _do(self) -> None:
                length = int(self.headers.get("content-length") or 0)
                raw = self.rfile.read(length) if length else b""
                try:
                    body = json.loads(raw) if raw.strip() else {}
                except ValueError:
                    body = {}
                hdrs = {k.lower(): v for k, v in self.headers.items()}
                try:
                    status, out = fake.handle(self.command, self.path, hdrs, body)
                except FakeError as exc:
                    status, out = exc.status, exc.body()
                data = json.dumps(out).encode()
                self.send_response(status)
                self.send_header("content-type", "application/json; charset=utf-8")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            do_GET = do_POST = do_PATCH = do_DELETE = _do

        return H

    def start(self, port: int = 0, *, write_descriptor: bool = True,
              pid: int | None = None) -> "FakeIronProxy":
        self.server = ThreadingHTTPServer(("127.0.0.1", port), self._handler())
        self.server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        if write_descriptor:
            self.write_descriptor(pid if pid is not None else os.getpid())
        return self

    def write_descriptor(self, pid: int) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "proxy.json").write_text(
            json.dumps({"url": self.url, "token": self.token, "pid": pid}), encoding="utf-8"
        )

    def stop(self) -> None:
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
            self.server = None


def main(argv: list[str]) -> int:
    if "--version" in argv:
        print(FAKE_VERSION)
        return 0
    code = os.environ.get("FAKE_IRON_PROXY_EXIT")
    if code:
        print("fake iron-proxy: exiting on request", flush=True)
        return int(code)
    if not argv or argv[0] != "serve":
        print("usage: fake_iron_proxy_v1301.py serve --port P --data-dir D", file=sys.stderr)
        return 2
    port, data_dir = 0, str(Path.home() / ".iron-proxy")
    rest = argv[1:]
    for i, a in enumerate(rest):
        if a == "--port":
            port = int(rest[i + 1])
        elif a == "--data-dir":
            data_dir = rest[i + 1]
    delay = float(os.environ.get("FAKE_IRON_PROXY_START_DELAY") or 0)
    if delay:
        time.sleep(delay)
    fake = FakeIronProxy(
        data_dir,
        health_version=os.environ.get("FAKE_IRON_PROXY_HEALTH_VERSION"),
        features=None if os.environ.get("FAKE_IRON_PROXY_OLD") else ["executor-v1"],
    )
    fake.start(port)
    # Prove which env the child got (ELECTRON_RUN_AS_NODE, data dir) — tests read it.
    (Path(data_dir) / "fake-child-env.json").write_text(
        json.dumps({"ELECTRON_RUN_AS_NODE": os.environ.get("ELECTRON_RUN_AS_NODE"),
                    "IRONJARVIS_TOKEN": os.environ.get("IRONJARVIS_TOKEN"),
                    "argv": argv, "pid": os.getpid()}),
        encoding="utf-8",
    )
    print(f"Iron-Proxy listening on {fake.url}", flush=True)
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        fake.stop()
        try:
            (Path(data_dir) / "proxy.json").unlink()
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
