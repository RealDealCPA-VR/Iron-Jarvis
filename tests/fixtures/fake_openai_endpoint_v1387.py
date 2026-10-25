"""A stand-in OpenAI-compatible model server (v1.387.0, the reply budget).

Behaves the way the servers behind the user's local endpoint behave where it
matters for an OUTPUT budget, and nothing else:

* ``POST /v1/chat/completions`` — JSON or SSE (``stream: true``, with the
  ``include_usage`` chunk). A ``handler(body) -> Reply`` decides what the model
  says; :func:`write_call_reply` builds the case the bench found — a
  ``write_file`` call whose arguments are cut where the budget ends, with
  ``finish_reason: "length"`` — and :func:`swallowed_reply` the GLM-under-vLLM
  shape the bench recorded (the budget spent in thinking / a held-back tool
  call: no text and no call at all, ``finish_reason: "length"``).
* a CONTEXT LIMIT like vLLM's: ``prompt + max_tokens > context_len`` is a
  ``400`` in one of the real wordings (:data:`REFUSALS`), counting the prompt
  as ``len(json(messages) + json(tools)) / chars_per_token``.
* ``GET /model/info`` — LiteLLM's table, ``model_info`` per served name
  (``max_input_tokens`` / ``max_output_tokens``…) — and ``GET /v1/models``.

It RECORDS every request (path, method, the JSON body, whether an auth
header came) and never records or prints a header VALUE.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable

#: The real wordings of "the budget does not fit" (vLLM old/new, SGLang,
#: LiteLLM wrapping vLLM). ``{ctx}``, ``{prompt}``, ``{asked}``, ``{total}``.
REFUSALS = {
    "vllm_old": (
        "This model's maximum context length is {ctx} tokens. However, you requested "
        "{total} tokens ({prompt} in the messages, {asked} in the completion). Please "
        "reduce the length of the messages or completion."
    ),
    "vllm_new": (
        "'max_tokens' or 'max_completion_tokens' is too large: {asked}. This model's "
        "maximum context length is {ctx} tokens and your request has {prompt} input "
        "tokens ({asked} > {ctx} - {prompt})."
    ),
    "sglang": (
        "Requested token count exceeds the model's maximum context length of {ctx} "
        "tokens. You requested a total of {total} tokens: {prompt} tokens from the input "
        "messages and {asked} tokens for the completion. Please reduce the number of "
        "tokens in the input messages or the completion to fit within the limit."
    ),
    "litellm": (
        "litellm.ContextWindowExceededError: litellm.BadRequestError: OpenAIException - "
        "This model's maximum context length is {ctx} tokens. However, you requested "
        "{total} tokens ({prompt} in the messages, {asked} in the completion). Please "
        "reduce the length of the messages or completion."
    ),
}


@dataclass
class Reply:
    """What the model says to one request."""

    text: str = ""
    thinking: str = ""
    #: ``(name, arguments_json_string)`` — the string as the server streams it
    #: (a cut call's string simply stops).
    calls: list[tuple[str, str]] = field(default_factory=list)
    finish: str = "stop"
    completion_tokens: int = 10


def text_reply(text: str) -> Reply:
    return Reply(text=text, finish="stop")


def calls_reply(*calls: tuple[str, dict]) -> Reply:
    return Reply(calls=[(n, json.dumps(a)) for n, a in calls], finish="tool_calls")


def write_call_reply(body: dict, path: str, content: str, *, name: str = "write_file",
                     chars_per_token: float = 3.6, spent_tokens: int = 0) -> Reply:
    """A ``write_file`` call — WHOLE when the request's budget holds it, else
    its arguments CUT where the budget ends, ``finish_reason: "length"``.
    ``spent_tokens`` of thinking come out of the SAME budget first (vLLM counts
    a reasoning parser's text toward ``max_tokens``)."""
    args = json.dumps({"path": path, "content": content})
    budget = int(body.get("max_tokens") or body.get("max_completion_tokens") or 0)
    thinking = "Planning the module. " * max(0, int(spent_tokens * chars_per_token) // 21)
    room = int((budget - spent_tokens) * chars_per_token)
    if room >= len(args):
        return Reply(thinking=thinking, calls=[(name, args)], finish="tool_calls",
                     completion_tokens=spent_tokens + len(args) // 4)
    if room <= 0:
        return Reply(thinking=thinking, finish="length", completion_tokens=budget)
    return Reply(thinking=thinking, calls=[(name, args[:room])], finish="length",
                 completion_tokens=budget)


def swallowed_reply(body: dict, thinking: str = "Let me write the whole module now. ") -> Reply:
    """The bench's GLM shape: the whole budget gone, NO text, NO call."""
    budget = int(body.get("max_tokens") or 0)
    return Reply(thinking=thinking, finish="length", completion_tokens=budget)


def last_user_text(body: dict) -> str:
    for m in reversed(body.get("messages") or []):
        if m.get("role") == "user":
            c = m.get("content")
            return c if isinstance(c, str) else json.dumps(c)
    return ""


class FakeEndpoint:
    """The server. ``handler`` answers chat requests; ``context_len`` (None =
    no limit) is enforced like vLLM; ``model_info`` serves ``/model/info``."""

    def __init__(
        self,
        handler: Callable[[dict], Reply] | None = None,
        *,
        models: tuple[str, ...] = ("glm-5.3-flash",),
        context_len: int | None = None,
        chars_per_token: float = 3.6,
        refusal: str = "vllm_old",
        model_info: dict[str, dict[str, Any]] | None = None,
        output_limit: int | None = None,
        output_limit_words: str = "Invalid value for 'max_tokens': the value is too big for this model.",
    ) -> None:
        self.handler = handler or (lambda body: text_reply("ok"))
        self.models = models
        self.context_len = context_len
        self.chars_per_token = chars_per_token
        self.refusal = refusal
        self.model_info = model_info
        #: A server that refuses a budget over this in words with NO number
        #: in them (the case the adapter can only answer with the old 4,096).
        self.output_limit = output_limit
        self.output_limit_words = output_limit_words
        self.requests: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    # -- what a test reads ----------------------------------------------------
    def chats(self) -> list[dict]:
        with self._lock:
            return [r["body"] for r in self.requests if r["path"].endswith("/chat/completions")]

    def prompt_tokens(self, body: dict) -> int:
        blob = json.dumps(body.get("messages") or [])
        if body.get("tools"):
            blob += json.dumps(body["tools"])
        return int(len(blob) / self.chars_per_token)

    @property
    def url(self) -> str:
        assert self._server is not None
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    # -- lifecycle --------------------------------------------------------------
    def __enter__(self) -> "FakeEndpoint":
        outer = self

        class _H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a: Any) -> None:  # quiet
                return

            def _send(self, code: int, payload: Any, ctype: str = "application/json") -> None:
                data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _record(self, body: Any) -> None:
                with outer._lock:
                    outer.requests.append({
                        "method": self.command, "path": self.path, "body": body,
                        "auth": bool(self.headers.get("Authorization") or self.headers.get("x-api-key")),
                    })

            def do_GET(self) -> None:  # noqa: N802
                self._record(None)
                if self.path.rstrip("/").endswith("/model/info"):
                    if outer.model_info is None:
                        return self._send(404, {"detail": "Not Found"})
                    data = [
                        {"model_name": name, "litellm_params": {"model": f"hosted_vllm/{name}"},
                         "model_info": info}
                        for name, info in outer.model_info.items()
                    ]
                    return self._send(200, {"data": data})
                if self.path.rstrip("/").endswith("/models"):
                    return self._send(200, {"object": "list", "data": [{"id": m} for m in outer.models]})
                return self._send(404, {"detail": "Not Found"})

            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                try:
                    body = json.loads(raw or b"{}")
                except ValueError:
                    body = {}
                self._record(body)
                if not self.path.rstrip("/").endswith("/chat/completions"):
                    return self._send(404, {"detail": "Not Found"})
                asked = int(body.get("max_tokens") or body.get("max_completion_tokens") or 0)
                if outer.output_limit is not None and asked > outer.output_limit:
                    return self._send(400, {"error": {
                        "message": outer.output_limit_words, "type": "BadRequestError", "code": 400,
                    }})
                if outer.context_len is not None:
                    prompt = outer.prompt_tokens(body)
                    if prompt + asked > outer.context_len:
                        words = REFUSALS[outer.refusal].format(
                            ctx=outer.context_len, prompt=prompt, asked=asked, total=prompt + asked,
                        )
                        return self._send(400, {"error": {
                            "message": words, "type": "BadRequestError", "code": 400,
                        }})
                reply = outer.handler(body)
                if body.get("stream"):
                    return self._stream(reply, body)
                message: dict[str, Any] = {"role": "assistant", "content": reply.text or None}
                if reply.thinking:
                    message["reasoning_content"] = reply.thinking
                if reply.calls:
                    message["tool_calls"] = [
                        {"id": f"call_{i}", "type": "function",
                         "function": {"name": n, "arguments": a}}
                        for i, (n, a) in enumerate(reply.calls)
                    ]
                return self._send(200, {
                    "choices": [{"index": 0, "message": message, "finish_reason": reply.finish}],
                    "usage": {"prompt_tokens": outer.prompt_tokens(body),
                              "completion_tokens": reply.completion_tokens},
                })

            def _stream(self, reply: Reply, body: dict) -> None:
                chunks: list[dict] = []

                def delta(d: dict, finish: str | None = None) -> dict:
                    return {"choices": [{"index": 0, "delta": d, "finish_reason": finish}]}

                if reply.thinking:
                    for i in range(0, len(reply.thinking), 40):
                        chunks.append(delta({"reasoning_content": reply.thinking[i:i + 40]}))
                if reply.text:
                    for i in range(0, len(reply.text), 40):
                        chunks.append(delta({"content": reply.text[i:i + 40]}))
                for idx, (name, args) in enumerate(reply.calls):
                    chunks.append(delta({"tool_calls": [{
                        "index": idx, "id": f"call_{idx}", "type": "function",
                        "function": {"name": name, "arguments": ""},
                    }]}))
                    for i in range(0, len(args), 64):
                        chunks.append(delta({"tool_calls": [{
                            "index": idx, "function": {"arguments": args[i:i + 64]},
                        }]}))
                chunks.append(delta({}, reply.finish))
                chunks.append({"choices": [], "usage": {
                    "prompt_tokens": outer.prompt_tokens(body),
                    "completion_tokens": reply.completion_tokens,
                }})
                data = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
                self._send(200, data.encode(), "text/event-stream")

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _H)
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)
