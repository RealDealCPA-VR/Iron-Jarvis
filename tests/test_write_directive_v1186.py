"""v1.186.0 — tell the model to write the file BEFORE it answers.

REPRODUCED FROM A REAL RUN on the user's install (v1.184.0, provider
`fleet-rtx-box` / `brain (RTX)` — a local fleet node). The ask was "create very
specific Excel spreadsheets". The turn armed 10 tools, called `file_search`
once and `read_document` NINE times, and answered in prose. No file.

Nothing was missing. `write_document` and `excel_edit` were both armed — the
note the user received is `_creation_honesty_note`'s *armed* branch, which only
fires when a document-writing tool was in front of the model. The app detected
the failure precisely, then told the user to ask again or switch models: it
handed the work back at the exact moment it knew most about what had gone
wrong.

So the instruction moves to the front of the turn. "Use them when they help" is
a weak order for a weak tool-caller, and reading is the path of least
resistance — every `read_document` call feels like progress while producing
nothing. Four properties, each mutation-proven:

* the directive reaches BOTH lanes (the dashboard STREAMS — a fix that landed
  only in the non-stream lane would have fixed the report and not the user);
* it names the tools ACTUALLY armed, and stays silent when none can write —
  naming an unarmed tool is an order the model cannot follow;
* it fires on a request for a file and NOT on a question about making one;
* the honesty note still runs and still tells the truth, because this is a
  nudge and not a guarantee — and the two share ONE predicate, so a turn can
  never be scolded for disobeying an order it was never given.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.chat_turn import (
    _asked_for_a_file,
    _creation_honesty_note,
    _write_directive,
)
from iron_jarvis.providers.adapters.base import LLMResponse
from iron_jarvis.providers.router import RouteResult

_SRC = Path(__file__).resolve().parents[1] / "src" / "iron_jarvis" / "daemon"

#: The sentence's load-bearing opening, byte-identical in both lanes.
_MARK = "PRODUCE THE FILE:"


def _body(text: str) -> SimpleNamespace:
    return SimpleNamespace(messages=[SimpleNamespace(role="user", content=text)])


@pytest.fixture
def client(tmp_path) -> TestClient:
    return TestClient(create_app(str(tmp_path)))


def _capture_complete(platform, monkeypatch, seen: dict, reply: str = "ok"):
    async def fake_complete(*, provider=None, model=None, system, messages,
                            tools, task_class):
        seen["system"] = system
        seen["tools"] = list(tools or [])
        return RouteResult(LLMResponse(text=reply), "mock", "mock")

    monkeypatch.setattr(platform.router, "complete", fake_complete)


def _capture_stream(platform, seen: dict, reply: str = "ok"):
    async def fake_stream(*, provider=None, model=None, system, messages,
                          tools, session_id=None, task_class=None):
        seen["system"] = system
        yield {"type": "text", "text": reply}
        yield {"type": "final", "response": LLMResponse(text=reply),
               "provider": "mock", "model": "mock"}

    platform.router.stream = fake_stream


# --------------------------------------------------------------------------- #
# 1. The unit: what it says, and when it says nothing.
# --------------------------------------------------------------------------- #


def test_it_names_the_writers_that_are_actually_armed():
    out = _write_directive(
        _body("create an excel spreadsheet of the 2025 depreciation schedule"),
        ["read_document", "excel_edit", "write_document", "file_search"],
    )
    assert _MARK in out
    # Both armed writers named, sorted — a stable prompt across turns, because
    # a section that reshuffles for no reason defeats prefix caching.
    assert "excel_edit, write_document" in out
    # The non-writers are NOT presented as ways to produce a file.
    assert "read_document" not in out and "file_search" not in out
    # It says the two things the measured failure actually did.
    assert "Reading and inspecting files does not create one" in out
    assert "describing" in out


def test_it_stays_silent_when_nothing_armed_can_write():
    """Naming `write_document` when it is absent from `tool_specs` is an order
    the model cannot obey — and it relays that fiction to the user. The honesty
    note's *unarmed* branch owns this case and says the true thing instead."""
    assert (
        _write_directive(
            _body("create an excel spreadsheet"), ["read_document", "file_search"]
        )
        == ""
    )
    assert _write_directive(_body("create an excel spreadsheet"), []) == ""


def test_it_fires_on_a_request_and_not_on_a_question():
    armed = ["write_document"]
    assert _MARK in _write_directive(_body("make me a csv of these totals"), armed)
    assert _MARK in _write_directive(
        _body("please generate a workbook with one tab per client"), armed
    )
    # Advice ABOUT creating a file is not a request for one — a directive here
    # would push the model to write a file nobody asked for.
    assert _write_directive(_body("how do I create an excel formula?"), armed) == ""
    assert _write_directive(_body("what is a pivot table"), armed) == ""
    # Ordinary work that touches documents but asks for no artifact.
    assert _write_directive(_body("summarize the attached return"), armed) == ""


def test_the_directive_and_the_note_share_one_predicate():
    """They are the same judgement at opposite ends of the turn. If they could
    disagree, a turn could be told to write a file and then not be checked (the
    failure goes silent), or be checked without ever being told (the user is
    told the model ignored an instruction it never received)."""
    for text in (
        "create an excel spreadsheet of the trial balance",
        "how do I create an excel spreadsheet?",
        "what did the 1099 say",
        "please save this as a pdf",
    ):
        body = _body(text)
        directive = bool(_write_directive(body, ["write_document"]))
        # The note's own gate: it fires only when a file was asked for, tools
        # were armed, and nothing wrote.
        note = bool(_creation_honesty_note(body, ["write_document"], ["read_document"]))
        assert directive == note == _asked_for_a_file(body), text


# --------------------------------------------------------------------------- #
# 2. Both lanes carry it — asserted end to end, then pinned at the SOURCE.
# --------------------------------------------------------------------------- #


def test_the_directive_reaches_the_nonstream_prompt(client, monkeypatch):
    seen: dict = {}
    _capture_complete(client.app.state.platform, monkeypatch, seen)

    r = client.post(
        "/chat",
        json={
            "messages": [{"role": "user", "content": "create an excel spreadsheet of Q3"}],
            "tools": ["write_document"],
        },
    )
    assert r.status_code == 200, r.text
    assert _MARK in seen["system"]
    assert "write_document" in seen["system"].split(_MARK, 1)[1][:120]


def test_the_directive_reaches_the_STREAM_prompt(client):
    """The lane the dashboard actually uses, and the lane the reported failure
    happened in. v1.167.0 shipped the PDF sentence to the non-stream lane only
    and the streaming UI went a whole wave without it — the same trap."""
    seen: dict = {}
    _capture_stream(client.app.state.platform, seen)

    with client.stream(
        "POST",
        "/chat/stream",
        json={
            "messages": [{"role": "user", "content": "create an excel spreadsheet of Q3"}],
            "tools": ["write_document"],
        },
    ) as r:
        assert r.status_code == 200
        for _ in r.iter_lines():
            pass
    assert _MARK in seen["system"]


def test_both_lanes_call_the_same_helper_at_the_source():
    """Pinned from PYTHON against the source, because an end-to-end assertion
    can pass while a lane grows its own inline copy of the sentence — and two
    copies of a prompt drift on the first edit. The lock-step pair is the point.
    """
    for lane in ("chat_turn.py", "routes/chat.py"):
        text = (_SRC / lane).read_text(encoding="utf-8")
        assert "_write_directive(body, armed)" in text, lane
    # And the sentence itself exists in exactly ONE place.
    bodies = sum(
        (_SRC / lane).read_text(encoding="utf-8").count(_MARK)
        for lane in ("chat_turn.py", "routes/chat.py")
    )
    assert bodies == 1, "the directive text must live in one function, not per lane"


# --------------------------------------------------------------------------- #
# 3. The nudge does not replace the check.
# --------------------------------------------------------------------------- #


def test_the_honesty_note_still_fires_when_the_model_ignores_the_directive():
    """This is a prompt, and a model free to ignore "use them when they help" is
    equally free to ignore this. The measured run called `read_document` nine
    times; if that happens again the user must still be told the truth rather
    than left to discover a file that was never written."""
    note = _creation_honesty_note(
        _body("create an excel spreadsheet of Q3"),
        ["write_document", "read_document"],
        ["read_document"] * 9,
    )
    assert "no file was actually written this turn" in note


def test_no_note_when_the_file_was_written():
    """A false accusation on a turn that DID the work is its own trust failure —
    and it is the outcome the directive exists to make common."""
    assert (
        _creation_honesty_note(
            _body("create an excel spreadsheet of Q3"),
            ["write_document"],
            ["read_document", "write_document"],
        )
        == ""
    )
