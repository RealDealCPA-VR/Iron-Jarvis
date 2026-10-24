"""A stand-in for the user's keyed LiteLLM gateway (the gateway-key fix).

Answers the way the real proxy at ``100.66.161.52:4000`` answered on
2026-10-24 (read with NO key, the only reads allowed): every route wants a
key — without ``Authorization: Bearer <key>`` (or ``x-api-key: <key>``) it
says ``401 {"error": {"message": "Authentication Error, No api key passed
in.", ...}}`` — and every Ollama-style ``/api/*`` address is ``404
{"detail": "Not Found"}``. With the key it serves the real shapes:
``/model/info`` (LiteLLM's table, ``model_info`` carrying
``supports_vision`` / ``supports_function_calling`` / ``max_input_tokens``),
``/v1/models`` and ``/models``, ``/health``, a ``/metrics`` 404 (the user's
proxy has none), ``POST /v1/chat/completions`` and
``POST /v1/audio/transcriptions``.

It RECORDS every request: method, path, whether ANY auth header was sent and
whether the right key was. It never records or prints a header VALUE, so a
test failure can never show the key.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

#: The gateway's served names (the user's real list, 2026-10-24).
SERVED = ("glm-5.3-flash", "glm-5.3-flash-think", "glm-5.3-flash-fast", "vision")

#: What each name's ``model_info`` states. ``vision`` can see; the GLM names
#: say they cannot. ``max_input_tokens`` is stated for every name.
STATED: dict[str, dict[str, Any]] = {
    "glm-5.3-flash": {"supports_vision": False, "supports_function_calling": True, "max_input_tokens": 1_000_000},
    "glm-5.3-flash-think": {"supports_vision": False, "supports_function_calling": True, "max_input_tokens": 1_000_000},
    "glm-5.3-flash-fast": {"supports_vision": False, "supports_function_calling": True, "max_input_tokens": 1_000_000},
    "vision": {"supports_vision": True, "supports_function_calling": True, "max_input_tokens": 262_144},
}

NO_KEY_BODY = {
    "error": {
        "message": "Authentication Error, No api key passed in.",
        "type": "auth_error",
        "param": "None",
        "code": "401",
    }
}


def model_info_body(backend: str = "") -> dict[str, Any]:
    """LiteLLM's ``/model/info`` for :data:`SERVED`. ``backend`` is the
    upstream every name routes to (``api_base`` — another server, like the
    user's vLLM box): give it a :class:`FakeBackend` so the proxy's children
    the sampler absorbs are asked there, never here. Empty = no ``api_base``."""
    params: dict[str, Any] = {"use_in_pass_through": False}
    if backend:
        params["api_base"] = backend.rstrip("/") + "/v1"
    return {
        "data": [
            {
                "model_name": name,
                "litellm_params": {"model": f"hosted_vllm/{name}", **params},
                "model_info": {"id": f"id-{name}", "db_model": False, **STATED[name]},
            }
            for name in SERVED
        ]
    }


class FakeBackend:
    """The vLLM box behind the proxy: a KEYLESS OpenAI-compatible server on
    another port. Only here so the proxy's topology children (absorbed by the
    sampler) are asked somewhere other than the gateway — the real children
    are other machines. Counts requests; nothing is asserted on them."""

    def __init__(self) -> None:
        self.hits = 0
        self._server: ThreadingHTTPServer | None = None
        self.base = ""

    def start(self) -> str:
        backend = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_a: Any) -> None:
                return

            def do_GET(self) -> None:  # noqa: N802
                backend.hits += 1
                raw = json.dumps({"object": "list", "data": [{"id": n} for n in SERVED]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        threading.Thread(target=self._server.serve_forever, name="fake-backend", daemon=True).start()
        self.base = f"http://127.0.0.1:{self._server.server_address[1]}"
        return self.base

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None


class FakeGateway:
    """The stand-in. ``start()`` -> its base address; ``stop()`` when done."""

    def __init__(self, key: str, backend: str = "") -> None:
        self._key = key
        self.backend = backend
        self._lock = threading.Lock()
        self.requests: list[dict[str, Any]] = []
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.base = ""

    # -- the record ----------------------------------------------------------
    def _note(self, method: str, path: str, *, any_auth: bool, keyed: bool, extra: dict | None = None) -> None:
        row = {"method": method, "path": path, "any_auth": any_auth, "keyed": keyed}
        if extra:
            row.update(extra)
        with self._lock:
            self.requests.append(row)

    def log(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self.requests]

    def unkeyed(self) -> list[str]:
        """``"METHOD /path"`` of every request that did not carry the key."""
        return [f"{r['method']} {r['path']}" for r in self.log() if not r["keyed"]]

    def ollama_style(self) -> list[str]:
        return [f"{r['method']} {r['path']}" for r in self.log() if r["path"].startswith("/api/")]

    def paths(self) -> list[str]:
        return [f"{r['method']} {r['path']}" for r in self.log()]

    # -- lifecycle -----------------------------------------------------------
    def start(self) -> str:
        gateway = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_a: Any) -> None:  # quiet
                return

            def _auth(self) -> tuple[bool, bool]:
                bearer = self.headers.get("Authorization") or ""
                xkey = self.headers.get("x-api-key") or ""
                any_auth = bool(bearer or xkey)
                keyed = bearer == f"Bearer {gateway._key}" or xkey == gateway._key
                return any_auth, keyed

            def _send(self, code: int, body: Any) -> None:
                raw = json.dumps(body).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def _body(self) -> bytes:
                n = int(self.headers.get("Content-Length") or 0)
                return self.rfile.read(n) if n > 0 else b""

            def _handle(self, method: str) -> None:
                path = self.path.split("?", 1)[0]
                raw = self._body() if method == "POST" else b""
                any_auth, keyed = self._auth()
                extra: dict[str, Any] = {}
                if method == "POST" and path.endswith("/chat/completions"):
                    try:
                        payload = json.loads(raw.decode("utf-8") or "{}")
                    except ValueError:
                        payload = {}
                    extra["model"] = str(payload.get("model") or "")
                    extra["has_image"] = "image_url" in raw.decode("utf-8", "replace")
                gateway._note(method, path, any_auth=any_auth, keyed=keyed, extra=extra)
                if path.startswith("/api/"):
                    return self._send(404, {"detail": "Not Found"})
                if not keyed:
                    return self._send(401, NO_KEY_BODY)
                if method == "GET" and path == "/model/info":
                    return self._send(200, model_info_body(gateway.backend))
                if method == "GET" and path in ("/v1/models", "/models"):
                    return self._send(
                        200, {"object": "list", "data": [{"id": n, "object": "model"} for n in SERVED]}
                    )
                if method == "GET" and path == "/health":
                    return self._send(
                        200,
                        {
                            "healthy_endpoints": [{"model": f"hosted_vllm/{n}"} for n in SERVED],
                            "unhealthy_endpoints": [],
                        },
                    )
                if method == "POST" and path.endswith("/chat/completions"):
                    return self._send(
                        200,
                        {
                            "id": "chatcmpl-stand-in",
                            "object": "chat.completion",
                            "model": extra.get("model") or "glm-5.3-flash",
                            "choices": [
                                {
                                    "index": 0,
                                    "message": {"role": "assistant", "content": "red"},
                                    "finish_reason": "stop",
                                }
                            ],
                            "usage": {"prompt_tokens": 5, "completion_tokens": 1, "total_tokens": 6},
                        },
                    )
                if method == "POST" and path.endswith("/audio/transcriptions"):
                    return self._send(200, {"text": "hello from the stand-in"})
                return self._send(404, {"detail": "Not Found"})

            def do_GET(self) -> None:  # noqa: N802
                self._handle("GET")

            def do_POST(self) -> None:  # noqa: N802
                self._handle("POST")

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, name="fake-gateway", daemon=True)
        self._thread.start()
        self.base = f"http://127.0.0.1:{port}"
        return self.base

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
