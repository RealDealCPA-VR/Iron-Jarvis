"""v1.324.0 (wave C, "apps that talk back"): the MCP client side.

A pack may, in the middle of a ``tools/call``, ASK THE USER
(``elicitation/create``), ASK A MODEL (``sampling/createMessage``) or REPORT
PROGRESS (``notifications/progress``). ``mcp/interact.py`` answers through the
:class:`InteractionScope` the chat lane sets around ONE tool call; with none,
the pack hears "no" at once. Pinned here:

1. The scope set on the loop around ``await client.call_tool(...)`` is seen
   on the TRANSPORT WORKER THREAD (``asyncio.to_thread`` copies the context)
   — through the real ``MCPClient`` with a sync transport double, through
   ``registry.invoke``, over HTTP, and end to end with a REAL
   ``StdioTransport`` + a fixture server.
2. A wait on the user is in 0.25 s slices and gives up when the call's cancel
   token is set (the future is cancelled, the pack hears decline / -1).
3. No scope -> ``{"action": "decline"}`` / ``-1`` with one sentence; a url
   mode elicitation -> decline.
4. Progress: only the scope's own token, at most 4 frames per second per call.
5. ``elicitation_fields`` / ``check_elicitation_answer`` are pure and
   table-tested.
6. ``initialize`` declares ``{"elicitation": {}, "sampling": {}}`` on BOTH
   transports; prompts/resources lists are paged and -32601 = ``[]``;
   ``live_client`` registers every recorded load (zero tools included).
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import sys
import threading
import time
from pathlib import Path
from typing import Any

import httpx
import pytest

from iron_jarvis.core.db import init_db, make_engine
from iron_jarvis.core.events import EventBus
from iron_jarvis.mcp import interact
from iron_jarvis.mcp import tools as mcp_tools_mod
from iron_jarvis.mcp.client import (
    FakeTransport,
    HttpTransport,
    MCPClient,
    MCPError,
    StdioTransport,
)
from iron_jarvis.mcp.interact import (
    InteractionScope,
    check_elicitation_answer,
    current_scope,
    elicitation_fields,
    handle_notification,
    interaction_scope,
    serve_server_request,
)
from iron_jarvis.mcp.tools import MCPRemoteTool, _load_one_server, live_client, live_clients
from iron_jarvis.tools.base import ToolContext
from iron_jarvis.tools.permissions import PermissionEngine
from iron_jarvis.tools.registry import ToolRegistry

FIXTURE = Path(__file__).parent / "fixtures" / "mcp_two_way_server_v1324.py"

#: A hang guard, never a performance bar.
_GUARD_S = 8.0

_FORM = {
    "message": "Which client?",
    "requestedSchema": {
        "type": "object",
        "properties": {"client": {"type": "string"}},
        "required": ["client"],
    },
}


@pytest.fixture(autouse=True)
def _fresh_throttle():
    interact._reset_progress_throttle()
    yield
    interact._reset_progress_throttle()


@pytest.fixture(autouse=True)
def _fresh_live_clients():
    saved = dict(mcp_tools_mod._LIVE_CLIENTS)
    mcp_tools_mod._LIVE_CLIENTS.clear()
    yield
    mcp_tools_mod._LIVE_CLIENTS.clear()
    mcp_tools_mod._LIVE_CLIENTS.update(saved)


class _Answers:
    """The callables a scope carries, recording what they were asked."""

    def __init__(self, elicit_result=None, sample_result=None, *, block=False, delay=0.0):
        self.elicited: list[tuple[str, dict]] = []
        self.sampled: list[tuple[str, dict]] = []
        self.frames: list[dict] = []
        self.elicit_result = elicit_result if elicit_result is not None else {
            "action": "accept", "content": {"client": "Ada"}}
        self.sample_result = sample_result if sample_result is not None else {
            "role": "assistant", "content": {"type": "text", "text": "hi"},
            "model": "local-7b", "stopReason": "endTurn"}
        self.block = block
        self.delay = delay
        self.started = asyncio.Event()
        self.cancelled = False
        self.threads: list[str] = []

    async def _wait(self):
        self.started.set()
        try:
            if self.block:
                await asyncio.Event().wait()
            if self.delay:
                await asyncio.sleep(self.delay)
        except asyncio.CancelledError:
            self.cancelled = True
            raise

    async def elicit(self, pack, params):
        self.elicited.append((pack, params))
        self.threads.append(threading.current_thread().name)
        await self._wait()
        return self.elicit_result

    async def sample(self, pack, params):
        self.sampled.append((pack, params))
        await self._wait()
        return self.sample_result

    def progress(self, frame):
        self.frames.append(frame)

    def scope(self, call_id="call-7") -> InteractionScope:
        return InteractionScope(
            loop=asyncio.get_running_loop(), call_id=call_id,
            elicit=self.elicit, sample=self.sample, progress=self.progress,
        )


async def _serve_off_loop(pack, msg, cancel=None, limit=_GUARD_S):
    """Run serve_server_request on a daemon thread in THIS context (as the
    transport does) and wait for it without blocking the loop."""
    ctx = contextvars.copy_context()
    box: dict[str, Any] = {}

    def run():
        box["reply"] = ctx.run(serve_server_request, pack, msg, cancel)

    th = threading.Thread(target=run, daemon=True)
    th.start()
    t0 = time.monotonic()
    while th.is_alive() and time.monotonic() - t0 < limit:
        await asyncio.sleep(0.01)
    assert not th.is_alive(), "serve_server_request never returned"
    box["elapsed"] = time.monotonic() - t0
    return box


# --------------------------------------------------------------------------- #
# 3. No scope: the pack hears "no" at once.
# --------------------------------------------------------------------------- #
def test_no_scope_elicitation_is_declined_at_once():
    assert current_scope() is None
    reply = serve_server_request("pack", {"jsonrpc": "2.0", "id": "e1",
                                          "method": "elicitation/create", "params": _FORM})
    assert reply == {"jsonrpc": "2.0", "id": "e1", "result": {"action": "decline"}}


def test_no_scope_sampling_is_refused_with_minus_one_and_one_sentence():
    reply = serve_server_request("pack", {"jsonrpc": "2.0", "id": 9,
                                          "method": "sampling/createMessage", "params": {}})
    assert reply["id"] == 9 and "result" not in reply
    assert reply["error"]["code"] == -1
    msg = reply["error"]["message"]
    assert msg.endswith(".") and msg.count(".") == 1 and len(msg) < 200, msg


def test_ping_and_unknown_methods_keep_the_v1322_answers():
    assert serve_server_request("p", {"jsonrpc": "2.0", "id": "x", "method": "ping"}) == {
        "jsonrpc": "2.0", "id": "x", "result": {}}
    reply = serve_server_request("p", {"jsonrpc": "2.0", "id": 4, "method": "roots/list"})
    assert reply["id"] == 4 and reply["error"]["code"] == -32601
    assert "roots/list" in reply["error"]["message"] and "result" not in reply


def test_serve_never_raises_on_garbage():
    assert serve_server_request("p", "not a dict")["error"]["code"] == -32600  # type: ignore[arg-type]
    reply = serve_server_request("p", {"id": 1, "method": "elicitation/create", "params": [1]})
    assert reply["result"] == {"action": "decline"}


# --------------------------------------------------------------------------- #
# Scoped answers (through a thread, as the transports call it).
# --------------------------------------------------------------------------- #
async def test_scoped_elicitation_is_answered_by_the_scope_on_the_loop():
    a = _Answers()
    with interaction_scope(a.scope()):
        box = await _serve_off_loop("billing", {"jsonrpc": "2.0", "id": "e1",
                                                "method": "elicitation/create", "params": _FORM})
    assert box["reply"] == {"jsonrpc": "2.0", "id": "e1",
                            "result": {"action": "accept", "content": {"client": "Ada"}}}
    assert a.elicited == [("billing", _FORM)]
    # The answerer ran ON the loop thread, not the worker.
    assert a.threads == [threading.current_thread().name]


@pytest.mark.parametrize("result, expected", [
    ({"action": "decline"}, {"action": "decline"}),
    ({"action": "cancel"}, {"action": "cancel"}),
    ({"action": "accept"}, {"action": "accept", "content": {}}),
    ({"action": "nonsense"}, {"action": "decline"}),
    ("not a dict", {"action": "decline"}),
])
async def test_elicit_results_are_cleaned_to_the_spec_shape(result, expected):
    a = _Answers(elicit_result=result)
    with interaction_scope(a.scope()):
        box = await _serve_off_loop("p", {"id": 1, "method": "elicitation/create", "params": _FORM})
    assert box["reply"]["result"] == expected


async def test_an_answerer_that_raises_is_a_decline_never_an_exception():
    async def boom(pack, params):
        raise RuntimeError("the lane broke")

    a = _Answers()
    scope = InteractionScope(asyncio.get_running_loop(), "c", boom, boom, a.progress)
    with interaction_scope(scope):
        e = await _serve_off_loop("p", {"id": 1, "method": "elicitation/create", "params": _FORM})
        s = await _serve_off_loop("p", {"id": 2, "method": "sampling/createMessage", "params": {}})
    assert e["reply"]["result"] == {"action": "decline"}
    assert s["reply"]["error"]["code"] == -1


async def test_scoped_sampling_result_and_refusal():
    a = _Answers()
    with interaction_scope(a.scope()):
        box = await _serve_off_loop("p", {"id": 3, "method": "sampling/createMessage",
                                          "params": {"maxTokens": 5}})
    assert box["reply"] == {"jsonrpc": "2.0", "id": 3, "result": a.sample_result}
    assert a.sampled == [("p", {"maxTokens": 5})]

    denied = _Answers(sample_result={"error": {"code": -1, "message": "You said no."}})
    with interaction_scope(denied.scope()):
        box = await _serve_off_loop("p", {"id": 4, "method": "sampling/createMessage",
                                          "params": {}})
    assert box["reply"] == {"jsonrpc": "2.0", "id": 4,
                            "error": {"code": -1, "message": "You said no."}}


async def test_a_url_mode_elicitation_is_declined_without_asking():
    a = _Answers()
    params = {"mode": "url", "message": "Sign in", "url": "https://example.com/x",
              "elicitationId": "e"}
    with interaction_scope(a.scope()):
        box = await _serve_off_loop("p", {"id": 1, "method": "elicitation/create",
                                          "params": params})
    assert box["reply"]["result"] == {"action": "decline"}
    assert a.elicited == []


async def test_an_unshowable_form_is_declined_without_asking():
    a = _Answers()
    params = {"message": "x", "requestedSchema": {"type": "object", "properties": {
        "addr": {"type": "object", "properties": {"street": {"type": "string"}}}}}}
    with interaction_scope(a.scope()):
        box = await _serve_off_loop("p", {"id": 1, "method": "elicitation/create",
                                          "params": params})
    assert box["reply"]["result"] == {"action": "decline"}
    assert a.elicited == []


# --------------------------------------------------------------------------- #
# 2. Giving up: the cancel token, in 0.25 s slices.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("method", ["elicitation/create", "sampling/createMessage"])
async def test_a_wait_on_the_user_gives_up_when_the_call_is_cancelled(method):
    a = _Answers(block=True)
    cancel = threading.Event()
    with interaction_scope(a.scope()):
        task = asyncio.ensure_future(
            _serve_off_loop("p", {"id": 1, "method": method, "params": _FORM}, cancel))
    await asyncio.wait_for(a.started.wait(), _GUARD_S)
    await asyncio.sleep(0.3)
    assert not task.done(), "it must WAIT for the user while the call is live"
    t0 = time.monotonic()
    cancel.set()
    box = await task
    gave_up_in = time.monotonic() - t0
    assert gave_up_in < 2.0, gave_up_in  # one 0.25 s slice, generously bounded
    if method == "elicitation/create":
        assert box["reply"]["result"] == {"action": "decline"}
    else:
        assert box["reply"]["error"]["code"] == -1
        assert box["reply"]["error"]["message"] == interact.GAVE_UP_SAMPLING
    # The pending answer on the loop was cancelled, not left running.
    for _ in range(100):
        if a.cancelled:
            break
        await asyncio.sleep(0.01)
    assert a.cancelled is True


async def test_a_wait_gives_up_when_the_scope_loop_is_gone():
    a = _Answers()
    dead = asyncio.new_event_loop()
    dead.close()
    scope = InteractionScope(dead, "c", a.elicit, a.sample, a.progress)
    with interaction_scope(scope):
        box = await _serve_off_loop("p", {"id": 1, "method": "elicitation/create",
                                          "params": _FORM})
    assert box["reply"]["result"] == {"action": "decline"}


@pytest.mark.filterwarnings("ignore:coroutine .* was never awaited:RuntimeWarning")
async def test_a_wait_gives_up_when_the_scope_loop_is_not_running():
    """A loop that exists but no longer runs (the turn's loop stopped): the
    ask would never be served, so the wait must not last for ever."""
    a = _Answers()
    idle = asyncio.new_event_loop()
    try:
        scope = InteractionScope(idle, "c", a.elicit, a.sample, a.progress)
        with interaction_scope(scope):
            box = await _serve_off_loop("p", {"id": 1, "method": "elicitation/create",
                                              "params": _FORM})
        assert box["reply"]["result"] == {"action": "decline"}
        assert box["elapsed"] < 2.0, box["elapsed"]
    finally:
        idle.close()


def test_called_on_the_loop_itself_it_declines_instead_of_deadlocking():
    """An async transport would call it ON the scope's loop: waiting there
    would deadlock the loop that must answer. Driven on its own thread so a
    regression fails by the guard, not by hanging the suite."""
    box: dict[str, Any] = {}

    def run():
        async def main():
            a = _Answers()
            with interaction_scope(a.scope()):
                return serve_server_request("p", {"id": 1, "method": "elicitation/create",
                                                  "params": _FORM})

        box["reply"] = asyncio.run(main())

    th = threading.Thread(target=run, daemon=True)
    th.start()
    th.join(_GUARD_S)
    assert not th.is_alive(), "serve_server_request deadlocked its own loop"
    assert box["reply"]["result"] == {"action": "decline"}


# --------------------------------------------------------------------------- #
# 4. Progress.
# --------------------------------------------------------------------------- #
def _note(token, progress, total=None, message=None):
    params: dict[str, Any] = {"progressToken": token, "progress": progress}
    if total is not None:
        params["total"] = total
    if message is not None:
        params["message"] = message
    return {"jsonrpc": "2.0", "method": "notifications/progress", "params": params}


async def test_progress_for_the_scopes_own_token_becomes_a_frame_on_the_loop():
    a = _Answers()
    with interaction_scope(a.scope("call-7")):
        handle_notification("billing", _note("call-7", 2, 5, "Reading invoices"))
        handle_notification("billing", _note("other-call", 3, 5))
        handle_notification("billing", {"jsonrpc": "2.0", "method": "notifications/message",
                                         "params": {"level": "info", "data": "x"}})
        handle_notification("billing", _note("call-7", "lots"))  # not a number
    await asyncio.sleep(0.02)
    assert a.frames == [{"call_id": "call-7", "pack": "billing", "progress": 2,
                         "total": 5, "message": "Reading invoices"}]


async def test_progress_without_a_scope_goes_nowhere():
    a = _Answers()
    handle_notification("p", _note("call-7", 1))
    await asyncio.sleep(0.02)
    assert a.frames == []


async def test_progress_is_throttled_to_four_per_second_per_call(monkeypatch):
    clock = {"t": 100.0}
    monkeypatch.setattr(interact, "_now", lambda: clock["t"])
    a = _Answers()
    with interaction_scope(a.scope("A")):
        for i in range(10):
            handle_notification("p", _note("A", i))
    with interaction_scope(a.scope("B")):
        handle_notification("p", _note("B", 0))  # another call has its own budget
    await asyncio.sleep(0.02)
    assert [(f["call_id"], f["progress"]) for f in a.frames] == [
        ("A", 0), ("A", 1), ("A", 2), ("A", 3), ("B", 0)]
    clock["t"] += 1.0
    with interaction_scope(a.scope("A")):
        handle_notification("p", _note("A", 99, None))
    await asyncio.sleep(0.02)
    assert a.frames[-1] == {"call_id": "A", "pack": "p", "progress": 99, "total": None,
                            "message": ""}


# --------------------------------------------------------------------------- #
# 5. elicitation_fields — table.
# --------------------------------------------------------------------------- #
def _schema(props, required=()):
    return {"message": "m", "requestedSchema": {"type": "object", "properties": props,
                                                 "required": list(required)}}


def test_fields_normalise_every_supported_shape():
    fields, reason = elicitation_fields(_schema({
        "name": {"type": "string", "title": "Your name", "description": "As on the return",
                 "minLength": 2, "maxLength": 40, "default": "Ada"},
        "email": {"type": "string", "format": "email"},
        "site": {"type": "string", "format": "uri"},
        "born": {"type": "string", "format": "date"},
        "when": {"type": "string", "format": "date-time"},
        "odd": {"type": "string", "format": "hostname"},
        "kids": {"type": "integer", "minimum": 0, "maximum": 12, "default": 2},
        "rate": {"type": "number", "minimum": 0.5},
        "ok": {"type": "boolean", "default": True},
        "state": {"type": "string", "enum": ["WA", "OR"], "enumNames": ["Washington", "Oregon"]},
        "plan": {"oneOf": [{"const": "a", "title": "Plan A"}, {"const": "b", "title": "Plan B"}]},
    }, required=["name", "kids"]))
    assert reason is None
    by = {f["name"]: f for f in fields}
    assert [f["name"] for f in fields][:3] == ["name", "email", "site"]  # order kept
    assert by["name"] == {"name": "name", "type": "string", "title": "Your name",
                          "description": "As on the return", "required": True,
                          "min_length": 2, "max_length": 40, "default": "Ada"}
    assert by["email"]["format"] == "email" and by["email"]["title"] == "email"
    assert by["email"]["required"] is False and by["email"]["description"] == ""
    assert by["site"]["format"] == "uri"
    assert by["born"]["format"] == "date" and by["when"]["format"] == "date-time"
    assert "format" not in by["odd"]
    assert by["kids"] == {"name": "kids", "type": "integer", "title": "kids", "description": "",
                          "required": True, "minimum": 0, "maximum": 12, "default": 2}
    assert by["rate"]["minimum"] == 0.5 and "maximum" not in by["rate"]
    assert by["ok"]["type"] == "boolean" and by["ok"]["default"] is True
    assert by["state"]["enum"] == ["WA", "OR"]
    assert by["state"]["enum_names"] == ["Washington", "Oregon"]
    assert by["plan"]["type"] == "string" and by["plan"]["enum"] == ["a", "b"]
    assert by["plan"]["enum_names"] == ["Plan A", "Plan B"]
    for f in fields:  # never a camelCase key on the card
        assert not {"minLength", "maxLength", "enumNames"} & set(f), f


@pytest.mark.parametrize("params, word", [
    ({"mode": "url", "message": "m", "url": "https://x"}, "web page"),
    (_schema({"addr": {"type": "object", "properties": {}}}), "inside"),
    (_schema({"tags": {"type": "array", "items": {"type": "string"}}}), "list"),
    (_schema({f"q{i}": {"type": "string"} for i in range(21)}), "more than 20"),
    (_schema({"bad name!": {"type": "string"}}), "name"),
    (_schema({"x" * 65: {"type": "string"}}), "name"),
    (_schema({"f": {"type": "file"}}), "type"),
    (_schema({"f": "string"}), "described"),
    (_schema({"n": {"type": "integer", "enum": ["one"]}}), "choices"),
    ({"message": "m", "requestedSchema": "nope"}, "not readable"),
    ("not a dict", "not readable"),
])
def test_fields_refuse_what_the_card_cannot_show(params, word):
    fields, reason = elicitation_fields(params)  # type: ignore[arg-type]
    assert fields == []
    assert reason is not None and word in reason, reason


def test_twenty_questions_are_fine_and_no_schema_is_a_plain_question():
    fields, reason = elicitation_fields(_schema({f"q{i}": {"type": "string"} for i in range(20)}))
    assert reason is None and len(fields) == 20
    assert elicitation_fields({"message": "Proceed?"}) == ([], None)
    fields, reason = elicitation_fields({"message": "m", "mode": "form", "requestedSchema": {
        "type": "object", "properties": {"a.b-c_9": {"type": "string"}}}})
    assert reason is None and fields[0]["name"] == "a.b-c_9"


# --------------------------------------------------------------------------- #
# 5. check_elicitation_answer — table.
# --------------------------------------------------------------------------- #
_CHECK_FIELDS, _ = elicitation_fields(_schema({
    "name": {"type": "string", "minLength": 2, "maxLength": 5},
    "kids": {"type": "integer", "minimum": 0, "maximum": 12},
    "rate": {"type": "number", "minimum": 0.5, "maximum": 2},
    "ok": {"type": "boolean"},
    "state": {"type": "string", "enum": ["WA", "OR"]},
    "email": {"type": "string", "format": "email"},
    "site": {"type": "string", "format": "uri"},
    "born": {"type": "string", "format": "date"},
    "when": {"type": "string", "format": "date-time"},
}, required=["name", "kids"]))

_GOOD = {"name": "Ada", "kids": 2, "rate": 1.5, "ok": False, "state": "OR",
         "email": "ada@example.com", "site": "https://example.com/a", "born": "1990-02-28",
         "when": "2026-10-08T09:30:00Z"}


def test_a_good_answer_has_no_problems():
    assert check_elicitation_answer(_CHECK_FIELDS, _GOOD) == {}
    assert check_elicitation_answer(_CHECK_FIELDS, {"name": "Bo", "kids": 0}) == {}
    assert check_elicitation_answer(_CHECK_FIELDS, {**_GOOD, "kids": 3.0, "rate": 2}) == {}
    assert check_elicitation_answer(_CHECK_FIELDS, {**_GOOD, "when": "2026-10-08 09:30"}) == {}


@pytest.mark.parametrize("change, field, words", [
    ({"name": None}, "name", "required"),
    ({"name": ""}, "name", "required"),
    ({"kids": None}, "kids", "required"),
    ({"name": 7}, "name", "text"),
    ({"kids": "2"}, "kids", "whole number"),
    ({"kids": 2.5}, "kids", "whole number"),
    ({"kids": True}, "kids", "whole number"),
    ({"rate": "1"}, "rate", "number"),
    ({"ok": "yes"}, "ok", "yes or no"),
    ({"state": "CA"}, "state", "choices"),
    ({"kids": -1}, "kids", "at least 0"),
    ({"kids": 13}, "kids", "at most 12"),
    ({"rate": 0.4}, "rate", "at least 0.5"),
    ({"rate": 2.5}, "rate", "at most 2"),
    ({"name": "A"}, "name", "at least 2 characters"),
    ({"name": "Adalovelace"}, "name", "at most 5 characters"),
    ({"email": "ada.example.com"}, "email", "email"),
    ({"site": "not a url"}, "site", "web address"),
    ({"born": "28/02/1990"}, "born", "date"),
    ({"born": "1990-02-30"}, "born", "date"),
    ({"when": "2026-10-08"}, "when", "date and time"),
    ({"when": "2026-13-08T09:30Z"}, "when", "date and time"),
    ({"surprise": "x"}, "surprise", "did not ask"),
])
def test_each_problem_is_named_under_its_field(change, field, words):
    answer = {**_GOOD, **change}
    if change.get(field, "") is None:
        answer.pop(field)
    problems = check_elicitation_answer(_CHECK_FIELDS, answer)
    assert set(problems) == {field}, problems
    sentence = problems[field]
    assert words in sentence and sentence.endswith("."), sentence


def test_a_missing_optional_answer_is_fine_and_garbage_never_raises():
    assert check_elicitation_answer(_CHECK_FIELDS, {"name": "Ada", "kids": 1}) == {}
    assert check_elicitation_answer(_CHECK_FIELDS, "nope") == {  # type: ignore[arg-type]
        "name": "This answer is required.", "kids": "This answer is required."}
    assert check_elicitation_answer("nope", {}) == {}  # type: ignore[arg-type]
    assert check_elicitation_answer([{"type": "string"}, 3], {}) == {}  # type: ignore[list-item]


# --------------------------------------------------------------------------- #
# 1. The scope crosses to the transport worker thread — real MCPClient.
# --------------------------------------------------------------------------- #
_ASK = {"jsonrpc": "2.0", "id": "srv-1", "method": "elicitation/create", "params": _FORM}


class _StdioLike:
    """A SYNCHRONOUS transport with ``abort`` (the stdio shape): mid-call it
    'receives' an elicitation and answers it through the real path."""

    def __init__(self):
        self.pack_name = None
        self.calls: list[tuple[str, dict]] = []
        self.thread = None

    def request(self, method, params, *, cancel=None):
        self.calls.append((method, dict(params)))
        self.thread = threading.current_thread()
        handle_notification(self.pack_name, _note((params.get("_meta") or {}).get(
            "progressToken"), 1, 2))
        reply = serve_server_request(self.pack_name, _ASK, cancel)
        return {"content": [{"type": "text", "text": json.dumps(reply)}]}

    def abort(self, cancel):
        pass


class _HttpLike(_StdioLike):
    """No ``abort``: MCPClient's other worker-thread path."""

    abort = None  # type: ignore[assignment]

    def request(self, method, params):  # type: ignore[override]
        return super().request(method, params, cancel=None)


@pytest.mark.parametrize("transport_cls", [_StdioLike, _HttpLike])
async def test_the_scope_set_on_the_loop_is_seen_on_the_worker_thread(transport_cls):
    transport = transport_cls()
    client = MCPClient(transport, name="billing")
    assert transport.pack_name == "billing"
    a = _Answers()
    with interaction_scope(a.scope("call-7")):
        result = await client.call_tool("lookup", {"q": 1})
    assert transport.thread is not threading.current_thread()  # really off the loop
    reply = json.loads(result["content"][0]["text"])
    assert reply == {"jsonrpc": "2.0", "id": "srv-1",
                     "result": {"action": "accept", "content": {"client": "Ada"}}}
    assert a.elicited == [("billing", _FORM)]
    await asyncio.sleep(0.02)
    assert [f["call_id"] for f in a.frames] == ["call-7"]
    assert transport.calls[0] == ("tools/call", {"name": "lookup", "arguments": {"q": 1},
                                                 "_meta": {"progressToken": "call-7"}})


async def test_without_a_scope_the_same_call_carries_no_token_and_is_declined():
    transport = _StdioLike()
    result = await MCPClient(transport, name="billing").call_tool("lookup", {})
    assert transport.calls[0] == ("tools/call", {"name": "lookup", "arguments": {}})
    assert json.loads(result["content"][0]["text"])["result"] == {"action": "decline"}


@pytest.fixture
def ctx(tmp_path: Path) -> ToolContext:
    engine = make_engine(str(tmp_path / "ws.db"))
    init_db(engine)
    return ToolContext(workspace=tmp_path, session_id="s1", agent_run_id="r1",
                       config=None, event_bus=EventBus(), engine=engine)


async def test_the_scope_survives_registry_invoke_with_a_deadline(ctx):
    transport = _StdioLike()
    registry = ToolRegistry()
    registry.register(MCPRemoteTool(MCPClient(transport, name="billing"), "billing", "lookup"),
                      mcp=True)
    a = _Answers()
    with interaction_scope(a.scope("tc-1")):
        result = await registry.invoke("mcp__billing__lookup", {}, ctx, PermissionEngine({}),
                                       session_allow=["mcp_call"], deadline_s=60)
    assert result.ok is True, result.error
    assert json.loads(result.output)["result"]["action"] == "accept"
    assert a.elicited and a.elicited[0][0] == "billing"
    assert transport.calls[0][1]["_meta"] == {"progressToken": "tc-1"}


# --------------------------------------------------------------------------- #
# 1. End to end: a REAL StdioTransport and a fixture server.
# --------------------------------------------------------------------------- #
def _stdio(request_timeout=None) -> StdioTransport:
    return StdioTransport(sys.executable, [str(FIXTURE)], request_timeout=request_timeout)


async def test_stdio_end_to_end_elicitation_sampling_and_progress():
    transport = _stdio(request_timeout=_GUARD_S)
    client = MCPClient(transport, name="twoway")
    a = _Answers()
    try:
        caps = await client.call_tool("caps", {})
        assert json.loads(caps["content"][0]["text"]) == {"elicitation": {}, "sampling": {}}
        with interaction_scope(a.scope("call-42")):
            result = await client.call_tool("ask", {})
    finally:
        transport.close()
    data = json.loads(result["content"][0]["text"])
    assert data["token"] == "call-42"
    assert data["elicitation"] == {"jsonrpc": "2.0", "id": "srv-e1",
                                   "result": {"action": "accept", "content": {"client": "Ada"}}}
    # The sampling request's id collided with OUR request id: still answered.
    assert data["sampling"]["result"] == a.sample_result
    assert a.elicited[0][0] == "twoway"
    assert a.elicited[0][1]["requestedSchema"]["required"] == ["client"]
    assert a.sampled[0][1]["maxTokens"] == 50
    await asyncio.sleep(0.05)
    assert [(f["progress"], f["total"], f["message"]) for f in a.frames] == [
        (1, 3, "starting"), (3, 3, "done")]
    assert {f["pack"] for f in a.frames} == {"twoway"}


async def test_stdio_end_to_end_without_a_scope_declines_and_refuses():
    transport = _stdio(request_timeout=_GUARD_S)
    try:
        result = await MCPClient(transport, name="twoway").call_tool("ask", {})
    finally:
        transport.close()
    data = json.loads(result["content"][0]["text"])
    assert data["token"] is None
    assert data["elicitation"]["result"] == {"action": "decline"}
    assert data["sampling"]["error"]["code"] == -1


async def test_stdio_url_mode_is_declined_end_to_end():
    transport = _stdio(request_timeout=_GUARD_S)
    a = _Answers()
    try:
        with interaction_scope(a.scope("c")):
            result = await MCPClient(transport, name="twoway").call_tool("url_ask", {})
    finally:
        transport.close()
    assert json.loads(result["content"][0]["text"])["result"] == {"action": "decline"}
    assert a.elicited == []


async def test_stdio_progress_spam_is_throttled_end_to_end():
    transport = _stdio(request_timeout=_GUARD_S)
    a = _Answers()
    try:
        with interaction_scope(a.scope("c")):
            await MCPClient(transport, name="twoway").call_tool("spam", {})
    finally:
        transport.close()
    await asyncio.sleep(0.05)
    assert 1 <= len(a.frames) <= 4, a.frames


async def test_stdio_a_stopped_call_gives_up_the_wait_and_cancels_the_ask():
    """Stop while the pack waits on the user: the token is set, the worker's
    wait gives up within a slice, the pending ask on the loop is cancelled,
    and the call ends (its server killed by abort, as any cancelled call)."""
    transport = _stdio(request_timeout=None)  # a registry pack: no transport floor
    client = MCPClient(transport, name="twoway")
    a = _Answers(block=True)
    try:
        with interaction_scope(a.scope("c")):
            task = asyncio.ensure_future(client.call_tool("ask", {}))
        await asyncio.wait_for(a.started.wait(), _GUARD_S)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, _GUARD_S)
        for _ in range(int(_GUARD_S / 0.02)):
            if a.cancelled:
                break
            await asyncio.sleep(0.02)
        assert a.cancelled is True
    finally:
        transport.close()


async def test_stdio_time_waiting_on_the_user_does_not_trip_the_transport_floor():
    transport = _stdio(request_timeout=1.0)
    a = _Answers(delay=1.5)
    try:
        with interaction_scope(a.scope("c")):
            result = await MCPClient(transport, name="twoway").call_tool("ask", {})
    finally:
        transport.close()
    assert json.loads(result["content"][0]["text"])["elicitation"]["result"]["action"] == "accept"


# --------------------------------------------------------------------------- #
# HTTP: capabilities, a scoped elicitation inside the stream, progress.
# --------------------------------------------------------------------------- #
def _sse(*messages: dict) -> bytes:
    return "".join(f"event: message\ndata: {json.dumps(m)}\n\n" for m in messages).encode()


class _HttpServer:
    def __init__(self):
        self.init_params: dict | None = None
        self.call_params: dict | None = None
        self.client_replies: list[dict] = []
        self.replied = threading.Event()

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        method = body.get("method")
        if method is None:
            self.client_replies.append(body)
            self.replied.set()
            return httpx.Response(202)
        if method == "initialize":
            self.init_params = body["params"]
            return httpx.Response(200, headers={"content-type": "application/json",
                                                "mcp-session-id": "s1"},
                                  json={"jsonrpc": "2.0", "id": body["id"], "result": {}})
        if "id" not in body:
            return httpx.Response(202)
        rid = body["id"]
        self.call_params = body["params"]
        token = (body["params"].get("_meta") or {}).get("progressToken")

        def stream():
            yield _sse({"jsonrpc": "2.0", "method": "notifications/progress",
                        "params": {"progressToken": token, "progress": 1, "total": 2}})
            yield _sse({"jsonrpc": "2.0", "id": "srv-e1", "method": "elicitation/create",
                        "params": _FORM})
            if self.replied.wait(_GUARD_S):
                yield _sse({"jsonrpc": "2.0", "id": rid, "result": {"content": [
                    {"type": "text", "text": json.dumps(self.client_replies[-1])}]}})

        return httpx.Response(200, headers={"content-type": "text/event-stream"},
                              content=stream())

    def transport(self) -> HttpTransport:
        return HttpTransport("http://pack.test/mcp", client_factory=lambda: httpx.Client(
            transport=httpx.MockTransport(self.handler)))


async def test_http_declares_capabilities_and_answers_a_scoped_elicitation():
    server = _HttpServer()
    client = MCPClient(server.transport(), name="remote")
    a = _Answers()
    with interaction_scope(a.scope("call-9")):
        result = await client.call_tool("x", {})
    assert server.init_params["capabilities"] == {"elicitation": {}, "sampling": {}}
    assert server.call_params["_meta"] == {"progressToken": "call-9"}
    assert json.loads(result["content"][0]["text"]) == {
        "jsonrpc": "2.0", "id": "srv-e1",
        "result": {"action": "accept", "content": {"client": "Ada"}}}
    assert a.elicited == [("remote", _FORM)]
    await asyncio.sleep(0.02)
    assert [(f["pack"], f["progress"], f["total"]) for f in a.frames] == [("remote", 1, 2)]


def test_http_without_a_scope_declines_inside_the_stream():
    server = _HttpServer()
    result = server.transport().request("tools/call", {"name": "x", "arguments": {}})
    assert json.loads(result["content"][0]["text"])["result"] == {"action": "decline"}
    assert "_meta" not in server.call_params


# --------------------------------------------------------------------------- #
# Prompts and resources: paged, -32601 = none.
# --------------------------------------------------------------------------- #
def _pages(key, pages):
    def answer(params):
        return pages[params.get("cursor")]

    return answer


async def test_list_prompts_and_resources_follow_next_cursor():
    fake = FakeTransport({
        "prompts/list": _pages("prompts", {
            None: {"prompts": [{"name": "a"}], "nextCursor": "p2"},
            "p2": {"prompts": [{"name": "b"}, "junk"]}}),
        "resources/list": _pages("resources", {
            None: {"resources": [{"uri": "file:///1"}], "nextCursor": 7},
            7: {"resources": [{"uri": "file:///2"}]}}),
    })
    client = MCPClient(fake, name="docs")
    assert [p["name"] for p in await client.list_prompts()] == ["a", "b"]
    assert [r["uri"] for r in await client.list_resources()] == ["file:///1", "file:///2"]
    assert fake.calls == [("prompts/list", {}), ("prompts/list", {"cursor": "p2"}),
                          ("resources/list", {}), ("resources/list", {"cursor": 7})]


async def test_a_pack_without_prompts_or_resources_lists_none():
    fake = FakeTransport({}, raise_on={"prompts/list", "resources/list"},
                         error=MCPError("-32601: Method not found"))
    client = MCPClient(fake, name="tools-only")
    assert await client.list_prompts() == []
    assert await client.list_resources() == []
    other = FakeTransport({}, raise_on={"prompts/list"}, error=MCPError("-32603: boom"))
    with pytest.raises(MCPError):
        await MCPClient(other, name="broken").list_prompts()


async def test_stdio_tools_only_server_lists_no_prompts():
    transport = _stdio(request_timeout=_GUARD_S)
    try:
        client = MCPClient(transport, name="twoway")
        assert await client.list_prompts() == []
        assert await client.list_resources() == []
    finally:
        transport.close()


async def test_get_prompt_and_read_resource_send_the_spec_params():
    fake = FakeTransport({"prompts/get": {"messages": []},
                          "resources/read": {"contents": []}})
    client = MCPClient(fake, name="docs")
    assert await client.get_prompt("summary", {"year": "2025"}) == {"messages": []}
    assert await client.read_resource("file:///notes.md") == {"contents": []}
    assert await client.get_prompt("bare") == {"messages": []}
    assert fake.calls == [("prompts/get", {"name": "summary", "arguments": {"year": "2025"}}),
                          ("resources/read", {"uri": "file:///notes.md"}),
                          ("prompts/get", {"name": "bare", "arguments": {}})]


# --------------------------------------------------------------------------- #
# The live-client registry.
# --------------------------------------------------------------------------- #
def _cfg(name, fake):
    return {"name": name, "transport_obj": fake}


def test_a_recorded_load_registers_its_client_even_with_zero_tools():
    fake = FakeTransport({"tools/list": {"tools": []}})
    assert _load_one_server(_cfg("docs-only", fake), None, 5.0, True) == []
    client = live_client("docs-only")
    assert client is not None and client.transport is fake and client.name == "docs-only"
    assert fake.pack_name == "docs-only"
    assert set(live_clients()) == {"docs-only"}


def test_a_probe_registers_nothing_and_a_later_load_replaces():
    probe = FakeTransport({"tools/list": {"tools": [{"name": "t"}]}})
    _load_one_server(_cfg("p", probe), None, 5.0, False)
    assert live_client("p") is None
    first = FakeTransport({"tools/list": {"tools": [{"name": "t"}]}})
    second = FakeTransport({"tools/list": {"tools": [{"name": "t"}]}})
    _load_one_server(_cfg("p", first), None, 5.0, True)
    _load_one_server(_cfg("p", second), None, 5.0, True)
    assert live_client("p").transport is second


def test_a_failed_or_closed_client_is_not_handed_out():
    ok = FakeTransport({"tools/list": {"tools": []}})
    _load_one_server(_cfg("p", ok), None, 5.0, True)
    live_client("p").close()
    assert live_client("p") is None and live_clients() == {}
    _load_one_server(_cfg("q", ok), None, 5.0, True)
    bad = FakeTransport({}, raise_on="tools/list")
    _load_one_server(_cfg("q", bad), None, 5.0, True)
    assert live_client("q") is None


def test_when_the_newer_load_loses_and_is_closed_the_winner_is_still_live():
    """A Retry registers B; the slower boot load registers A after it; the
    platform keeps B's tools and CLOSES A. B must be the live client."""
    b = FakeTransport({"tools/list": {"tools": []}})
    a = FakeTransport({"tools/list": {"tools": []}})
    _load_one_server(_cfg("p", b), None, 5.0, True)
    _load_one_server(_cfg("p", a), None, 5.0, True)
    assert live_client("p").transport is a
    live_client("p").close()
    assert live_client("p").transport is b
    assert live_clients()["p"].transport is b
