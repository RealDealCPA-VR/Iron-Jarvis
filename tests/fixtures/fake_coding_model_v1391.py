"""A stand-in model that writes files through tool calls (v1.391.0, the update
rehearsal's coding reply).

An OpenAI-compatible server (``/v1/models``, ``/v1/chat/completions`` — JSON
or SSE) for the REAL daemon in a subprocess, served from the test process. It
plays a coding reply by the conversation it is sent, never by a counter of
its own, so a resumed reply that is shown its own earlier rounds answers on
from there — exactly what a real model given its conversation does:

* the reply is chosen by a MARKER in the turn's question ("[A]", "[B]", …);
* the step is the number of ``role: "tool"`` results in the conversation:
  step ``n`` asks for ``write_file`` of the ``n``-th file (call id
  ``<marker><n>``), and after the last file it answers in words;
* a HOLD stops it at a step before it answers — one-shot, and only when the
  conversation has (or has not) been resumed — so the test can stop the app
  while the reply is working at a known place.

It records every chat request (the messages it was sent) and never records a
header value.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

#: The words of the daemon's resume note (``harness.reply_record.RESUME_NOTE``)
#: the model recognises a resumed conversation by.
RESUMED_WORDS = "Iron Jarvis stopped while you were working on this reply"


@dataclass
class Hold:
    marker: str
    step: int
    resumed: bool
    reached: threading.Event = field(default_factory=threading.Event)
    release: threading.Event = field(default_factory=threading.Event)
    used: bool = False


class ScriptedCoder:
    """``files[marker]`` — the ``(name, content)`` the reply writes, in order."""

    def __init__(self, files: dict[str, list[tuple[str, str]]]) -> None:
        self.files = files
        self.requests: list[dict[str, Any]] = []
        self._holds: list[Hold] = []
        self._lock = threading.Lock()
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    # -- what a test sets and reads --------------------------------------------
    def hold(self, marker: str, step: int, *, resumed: bool) -> Hold:
        h = Hold(marker, step, resumed)
        with self._lock:
            self._holds.append(h)
        return h

    def release_all(self) -> None:
        with self._lock:
            for h in self._holds:
                h.release.set()

    def chats(self, marker: str) -> list[list[dict[str, Any]]]:
        with self._lock:
            return [r["messages"] for r in self.requests if r["marker"] == marker]

    @property
    def url(self) -> str:
        assert self._server is not None
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    # -- the script ------------------------------------------------------------
    def _marker(self, messages: list[dict[str, Any]]) -> str:
        first = next((m for m in messages if m.get("role") == "user"), {})
        text = first.get("content")
        text = text if isinstance(text, str) else json.dumps(text)
        return next((k for k in self.files if f"[{k}]" in text), "")

    def answer(self, body: dict[str, Any]) -> dict[str, Any]:
        messages = body.get("messages") or []
        marker = self._marker(messages)
        step = sum(1 for m in messages if m.get("role") == "tool")
        resumed = any(
            m.get("role") == "user" and RESUMED_WORDS in str(m.get("content") or "") for m in messages
        )
        with self._lock:
            self.requests.append({"marker": marker, "step": step, "resumed": resumed, "messages": messages})
            hold = next((h for h in self._holds if not h.used and h.marker == marker and h.step == step
                         and h.resumed == resumed), None)
            if hold is not None:
                hold.used = True
        if hold is not None:
            hold.reached.set()
            hold.release.wait(timeout=120)
        files = self.files.get(marker) or []
        if step < len(files):
            name, content = files[step]
            return {"calls": [(f"{marker}{step + 1}", "write_file", {"path": name, "content": content})]}
        return {"text": f"Made {len(files)} file(s)."}

    # -- the server ------------------------------------------------------------
    def __enter__(self) -> "ScriptedCoder":
        outer = self

        class _H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a: Any) -> None:  # quiet
                return

            def _send(self, code: int, payload: Any, ctype: str = "application/json") -> None:
                data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
                try:
                    self.send_response(code)
                    self.send_header("Content-Type", ctype)
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                except OSError:
                    pass  # the daemon went away while the model was held

            def do_GET(self) -> None:  # noqa: N802
                if self.path.rstrip("/").endswith("/models"):
                    return self._send(200, {"object": "list", "data": [{"id": "scripted"}]})
                return self._send(404, {"detail": "Not Found"})

            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                try:
                    body = json.loads(raw or b"{}")
                except ValueError:
                    body = {}
                if not self.path.rstrip("/").endswith("/chat/completions"):
                    return self._send(404, {"detail": "Not Found"})
                reply = outer.answer(body)
                usage = {"prompt_tokens": 50, "completion_tokens": 5}
                calls = reply.get("calls") or []
                text = reply.get("text") or ""
                finish = "tool_calls" if calls else "stop"
                if not body.get("stream"):
                    message: dict[str, Any] = {"role": "assistant", "content": text or None}
                    if calls:
                        message["tool_calls"] = [
                            {"id": cid, "type": "function", "function": {"name": n, "arguments": json.dumps(a)}}
                            for cid, n, a in calls
                        ]
                    return self._send(200, {"choices": [{"index": 0, "message": message,
                                                         "finish_reason": finish}], "usage": usage})
                chunks: list[dict[str, Any]] = []

                def delta(d: dict[str, Any], fin: "str | None" = None) -> dict[str, Any]:
                    return {"choices": [{"index": 0, "delta": d, "finish_reason": fin}]}

                if text:
                    chunks.append(delta({"content": text}))
                for i, (cid, n, a) in enumerate(calls):
                    chunks.append(delta({"tool_calls": [{"index": i, "id": cid, "type": "function",
                                                         "function": {"name": n, "arguments": json.dumps(a)}}]}))
                chunks.append(delta({}, finish))
                chunks.append({"choices": [], "usage": usage})
                data = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
                self._send(200, data.encode(), "text/event-stream")

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _H)
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.release_all()
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)
