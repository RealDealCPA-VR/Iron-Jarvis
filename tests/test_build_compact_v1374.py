"""v1.374.0 (IJ harness part 2, Phase 3): how full a Build session's
conversation is, and COMPACT.

* the build turn's done frame — and the reply line the daemon saves — carry
  ``context`` with the PLANNER's own numbers for that turn (``window`` from
  ``_context_window``, ``used`` = the planned transcript), never a guess;
* ``POST /build/sessions/{id}/compact`` writes the summary with the session's
  OWN model (the v1.372.0 hold) on its own account, under its Local only
  scope; the summary is LEDGER-CHECKED (a planted false path and an invented
  quote are removed), billed to the session, stored on the session, and the
  next turn reads it IN PLACE of the older turns — while the transcript the
  user sees is never cut;
* the demo model writes no summary (before a call or after one);
* a rewind to before the summary's coverage drops it; one after keeps it;
* while Compact runs, a message is refused in its own words.

Driven through the REAL app factory and routes; only the model (the router)
is a scripted double.
"""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta

import pytest
from sqlmodel import select

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.ids import utcnow
from iron_jarvis.core.models import AgentRun, ToolInvocation
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes import build as build_routes
from iron_jarvis.daemon.routes import chat as chat_routes
from iron_jarvis.harness import compact as C
from iron_jarvis.harness import sessions as S
from iron_jarvis.providers import egress
from iron_jarvis.providers.adapters.base import LLMResponse
from iron_jarvis.providers.hold import current_hold
from iron_jarvis.providers.router import RouteResult
from tests.test_build_sessions_v1367 import (
    CLAUDE,
    Script,
    asgi,
    frames_of,
    messages_of,
    new_session,
    open_turn,
    setup_of,
    wire,
)

#: A small window, pinned the way a user pins one (``model_context_windows``).
WINDOW = 9_000


@pytest.fixture()
def app(tmp_path):
    a = create_app(str(tmp_path / "home"))
    a.state.platform.config.model_context_windows = {f"{CLAUDE[0]}::{CLAUDE[1]}": WINDOW}
    return a


@pytest.fixture()
def work(tmp_path):
    folder = tmp_path / "work"
    folder.mkdir()
    (folder / "app.py").write_text("x = 1\n", encoding="utf-8")
    return folder


TURNS = [
    ("t1", "What is the deploy codename?",
     'The deploy codename is "CORAL-TELESCOPE-17", from docs/deploy-notes.md.'),
    ("t2", "Add a --dry-run flag to src/app.py.", "Added the --dry-run flag to src/app.py."),
    ("t3", "Which port does the dev server use?", "The dev server listens on port 5173."),
    ("t4", "Rename the cache folder.", "Renamed .cache to .build-cache."),
    ("t5", "Run the tests.", "All 42 tests passed."),
    ("t6", "Summarize what changed today.", "A flag, a renamed folder and a green test run."),
]


def seed(app, sid: str) -> list[dict]:
    """Six finished turns, written the daemon's way (question, then reply),
    and the session's ledger: it READ src/app.py (before any turn, so a
    rewind has nothing to put back)."""
    engine = app.state.platform.engine
    for tid, q, a in TURNS:
        S.append_lines(engine, sid, [{"role": "user", "content": q, "turnId": tid}])
        S.append_lines(engine, sid, [{"role": "assistant", "content": a, "turnId": tid}])
    with session_scope(engine) as db:
        db.add(ToolInvocation(
            session_id=S.session_id_for(sid), agent_run_id="chat", tool="read_file",
            args_json=json.dumps({"path": "src/app.py"}), reversibility="readonly",
            created_at=utcnow() - timedelta(hours=1),
        ))
        db.commit()
    return messages_of(app, sid)


SUMMARY = "\n".join([
    "GOAL: Ship today's changes.",
    "FACTS:",
    'The deploy codename is "CORAL-TELESCOPE-17".',
    "src/app.py gained a --dry-run flag.",
    "src/secret_backdoor.py was changed to open a port.",
    'The user said "ship it on Friday night".',
])


class Writer:
    """The router's ``complete()`` for the summary call: records what it was
    asked under (the hold, the account pin, the Local only scope) and answers
    ``text`` as ``who``."""

    def __init__(self, text: str = SUMMARY, who: tuple[str, str] = CLAUDE, gate: "asyncio.Event | None" = None,
                 raises: "BaseException | None" = None) -> None:
        self.text, self.who, self.gate, self.raises = text, who, gate, raises
        self.calls: list[dict] = []

    async def complete(self, *, provider=None, model=None, system="", messages=(), tools=None, **kw):
        from iron_jarvis.iron_proxy.pin import ACCOUNT_PIN

        hold = current_hold()
        self.calls.append({
            "provider": provider, "model": model, "system": system,
            "user": messages[0].content if messages else "", "tools": tools, "pin": kw.get("pin"),
            "hold": (hold.provider, hold.model) if hold is not None else None,
            "local_only": egress.LOCAL_ONLY.get().on,
            "account": ACCOUNT_PIN.get(),
        })
        if self.gate is not None:
            await self.gate.wait()
        if self.raises is not None:
            raise self.raises
        return RouteResult(LLMResponse(text=self.text, usage={"input_tokens": 700, "output_tokens": 90}),
                           self.who[0], self.who[1])


def wire_writer(app, writer: Writer) -> Writer:
    app.state.platform.router.complete = writer.complete
    return writer


# --------------------------------------------------------------------------- #
# the meter's numbers are the planner's
# --------------------------------------------------------------------------- #


async def test_the_done_frame_and_the_saved_reply_carry_the_planners_own_numbers(app, work, monkeypatch):
    sid = new_session(app, work)["id"]
    seed(app, sid)
    plans: list = []
    real = chat_routes._plan_context

    def spy(*a, **kw):
        plan = real(*a, **kw)
        plans.append(plan)
        return plan

    monkeypatch.setattr(chat_routes, "_plan_context", spy)
    wire(app, Script(["Done for now."]))
    status, raw = await open_turn(app, sid, {"text": "one more question", "turn_id": "bt-ctx"})
    assert status == 200, raw
    done = dict(frames_of(raw))["done"]
    [plan] = plans
    # The frame: the window the planner planned against (the pin) and what
    # the planned transcript used.
    assert done["context"]["window"] == plan.window == WINDOW
    assert done["context"]["used"] == plan.used_tokens > 0
    # The saved reply keeps exactly the frame's numbers.
    reply = [m for m in messages_of(app, sid) if m.get("turnId") == "bt-ctx" and m["role"] == "assistant"][-1]
    ctx = reply["context"]
    assert ctx["window"] == WINDOW and ctx["used"] == plan.used_tokens
    assert ctx["percent"] == done["context"]["percent"] and ctx["level"] == done["context"]["level"]
    assert ctx["suggest_at"] == 70
    # ...and the session view hands it to the screen.
    status, view = await asgi(app, "GET", f"/build/sessions/{sid}")
    assert status == 200
    assert [m for m in view["messages"] if m.get("turnId") == "bt-ctx"][-1]["context"] == ctx


def test_a_reading_with_no_window_is_not_kept():
    assert build_routes.context_receipt({"used": 10}) is None
    assert build_routes.context_receipt(None) is None
    got = build_routes.context_receipt({"window": 9000, "used": 6400, "tokens": 7000, "percent": 78,
                                        "level": "suggest", "suggest_at": 70, "recap": True})
    assert got == {"window": 9000, "used": 6400, "tokens": 7000, "percent": 78, "level": "suggest",
                   "suggest_at": 70}


# --------------------------------------------------------------------------- #
# compact: checked, on the session's own model, stored, used, transcript kept
# --------------------------------------------------------------------------- #


async def test_compact_is_ledger_checked_held_on_the_sessions_model_billed_and_kept(app, work):
    sid = new_session(app, work)["id"]
    before = seed(app, sid)
    writer = wire_writer(app, Writer())
    status, body = await asgi(app, "POST", f"/build/sessions/{sid}/compact", {})
    assert status == 200, body
    # ONE call, on the session's own pair, HELD there (v1.372.0), no tools.
    [call] = writer.calls
    assert (call["provider"], call["model"]) == CLAUDE and call["hold"] == CLAUDE
    assert call["tools"] == [] and call["pin"] is True and call["local_only"] is False
    # It was shown the older turns — and not the newest six lines.
    assert "CORAL-TELESCOPE-17" in call["user"] and "port 5173" in call["user"]
    assert "Rename the cache folder" not in call["user"] and "42 tests" not in call["user"]
    # THE CHECK: the planted false path and the invented quote are gone; a
    # path the ledger names and a verbatim quote stay.
    assert body["stripped"] == 2
    assert "src/secret_backdoor.py" in body["stripped_claims"]
    assert any("ship it on Friday night" in c for c in body["stripped_claims"])
    assert "secret_backdoor" not in body["summary"] and "Friday" not in body["summary"]
    assert "CORAL-TELESCOPE-17" in body["summary"] and "src/app.py" in body["summary"]
    assert body["through_turn"] == "t3" and body["covers"] == 6 and body["window"] == WINDOW
    # STORED ON THE SESSION, and the view says so.
    rec = setup_of(app, sid)["compaction"]
    assert rec["through_turn"] == "t3" and rec["summary"] == body["summary"]
    status, view = await asgi(app, "GET", f"/build/sessions/{sid}")
    assert view["compaction"]["summary"] == body["summary"] and view["compaction"]["covers"] == 6
    # THE TRANSCRIPT THE USER SEES IS NEVER CUT.
    assert messages_of(app, sid) == before
    # BILLED TO THE SESSION.
    with session_scope(app.state.platform.engine) as db:
        runs = list(db.exec(select(AgentRun).where(AgentRun.thread_id == sid)).all())
    assert len(runs) == 1 and runs[0].input_tokens == 700 and runs[0].output_tokens == 90

    # THE NEXT TURN reads the summary in place of the older turns.
    script = wire(app, Script(["The codename is CORAL-TELESCOPE-17."]))
    status, raw = await open_turn(app, sid, {"text": "remind me of the codename", "turn_id": "bt-next"})
    assert status == 200, raw
    first = script.calls[0]
    sent = "\n".join(str(getattr(m, "content", "")) for m in first["messages"])
    assert "What is the deploy codename?" not in sent and "port 5173" not in sent  # covered
    assert "Rename the cache folder." in sent and "remind me of the codename" in sent  # kept
    assert "CORAL-TELESCOPE-17" in first["system"] and "compacted" in first["system"]
    assert "secret_backdoor" not in first["system"]
    # ...and still nothing is cut from what the user sees.
    after = messages_of(app, sid)
    assert after[: len(before)] == before and len(after) == len(before) + 2


async def test_a_summary_that_survives_no_check_is_not_kept(app, work):
    sid = new_session(app, work)["id"]
    seed(app, sid)
    wire_writer(app, Writer(text="src/secret_backdoor.py was opened.\nThe user said \"never ship\"."))
    status, body = await asgi(app, "POST", f"/build/sessions/{sid}/compact", {})
    assert status == 502 and body["detail"] == C.NOTHING_KEPT
    assert "compaction" not in setup_of(app, sid)


async def test_the_demo_model_writes_no_summary_before_or_after_a_call(app, work):
    # Before a call: a session on the demo model is refused, nothing asked.
    sid = new_session(app, work, provider="mock", model="")["id"]
    seed(app, sid)
    writer = wire_writer(app, Writer())
    status, body = await asgi(app, "POST", f"/build/sessions/{sid}/compact", {})
    assert status == 400 and body["detail"] == C.NO_REAL_MODEL
    assert writer.calls == [] and "compaction" not in setup_of(app, sid)
    # After one: an answer the demo served is never a summary (and not billed).
    sid2 = new_session(app, work)["id"]
    seed(app, sid2)
    wire_writer(app, Writer(who=("mock", "mock")))
    status, body = await asgi(app, "POST", f"/build/sessions/{sid2}/compact", {})
    assert status == 400 and body["detail"] == C.NO_REAL_MODEL
    assert "compaction" not in setup_of(app, sid2)
    with session_scope(app.state.platform.engine) as db:
        assert not list(db.exec(select(AgentRun).where(AgentRun.thread_id == sid2)).all())


async def test_too_little_conversation_is_refused_in_words(app, work):
    sid = new_session(app, work)["id"]
    engine = app.state.platform.engine
    for tid, q, a in TURNS[:3]:
        S.append_lines(engine, sid, [{"role": "user", "content": q, "turnId": tid},
                                     {"role": "assistant", "content": a, "turnId": tid}])
    writer = wire_writer(app, Writer())
    status, body = await asgi(app, "POST", f"/build/sessions/{sid}/compact", {})
    assert status == 400 and body["detail"] == C.NOT_ENOUGH and writer.calls == []


async def test_local_only_is_the_scope_of_the_call_and_its_refusal_is_the_409_shape(app, work):
    sid = new_session(app, work, local_only=True)["id"]
    seed(app, sid)
    writer = wire_writer(app, Writer(raises=egress.LocalOnlyRefused(*CLAUDE)))
    status, body = await asgi(app, "POST", f"/build/sessions/{sid}/compact", {})
    assert writer.calls[0]["local_only"] is True
    assert status == 409 and body.get("code") == "local_only", body
    assert "compaction" not in setup_of(app, sid)


async def test_while_compact_runs_a_message_is_refused_in_its_own_words(app, work):
    sid = new_session(app, work)["id"]
    seed(app, sid)
    gate = asyncio.Event()
    wire_writer(app, Writer(gate=gate))
    task = asyncio.create_task(asgi(app, "POST", f"/build/sessions/{sid}/compact", {}))
    for _ in range(200):
        if sid in build_routes._COMPACTING:
            break
        await asyncio.sleep(0.01)
    assert sid in build_routes._COMPACTING
    status, raw = await open_turn(app, sid, {"text": "hello", "turn_id": "bt-during"})
    assert status == 409 and build_routes.COMPACTING_SENTENCE in raw
    status, body = await asgi(app, "POST", f"/build/sessions/{sid}/compact", {})
    assert status == 409 and body["detail"] == build_routes.COMPACTING_SENTENCE
    gate.set()
    status, body = await task
    assert status == 200, body
    assert sid not in build_routes._COMPACTING and sid not in build_routes._STARTING


# --------------------------------------------------------------------------- #
# rewind
# --------------------------------------------------------------------------- #


async def _compacted(app, work) -> str:
    sid = new_session(app, work)["id"]
    seed(app, sid)
    wire_writer(app, Writer())
    status, body = await asgi(app, "POST", f"/build/sessions/{sid}/compact", {})
    assert status == 200 and body["through_turn"] == "t3", body
    return sid


async def test_a_rewind_to_before_the_summarys_coverage_drops_it(app, work):
    sid = await _compacted(app, work)
    status, body = await asgi(app, "POST", f"/build/sessions/{sid}/rewind", {"to": "t2"})
    assert status == 200 and body["rewound"] is True, body
    assert "compaction" not in setup_of(app, sid)
    # The next turn reads the whole (cut) conversation, no summary.
    script = wire(app, Script(["ok"]))
    status, raw = await open_turn(app, sid, {"text": "again", "turn_id": "bt-after"})
    assert status == 200
    sent = "\n".join(str(getattr(m, "content", "")) for m in script.calls[0]["messages"])
    assert "What is the deploy codename?" in sent and "compacted" not in script.calls[0]["system"]


async def test_a_rewind_that_cuts_exactly_the_last_covered_turn_drops_it(app, work):
    sid = await _compacted(app, work)
    status, _ = await asgi(app, "POST", f"/build/sessions/{sid}/rewind", {"to": "t3"})
    assert status == 200
    assert "compaction" not in setup_of(app, sid)


async def test_a_rewind_to_after_the_coverage_keeps_it(app, work):
    sid = await _compacted(app, work)
    status, body = await asgi(app, "POST", f"/build/sessions/{sid}/rewind", {"to": "t5"})
    assert status == 200 and body["rewound"] is True, body
    assert setup_of(app, sid)["compaction"]["through_turn"] == "t3"


# --------------------------------------------------------------------------- #
# the pure rules
# --------------------------------------------------------------------------- #


def _lines(n_turns: int) -> list[dict]:
    out = []
    for i in range(1, n_turns + 1):
        out += [{"role": "user", "content": f"q{i}", "turnId": f"t{i}"},
                {"role": "assistant", "content": f"a{i}", "turnId": f"t{i}"}]
    return out


def test_coverage_ends_on_a_turn_and_keeps_the_newest_lines():
    lines = _lines(6)
    assert C.coverage(lines) == (6, "t3")
    # A steer note is part of its turn: coverage never ends right before it.
    lines.insert(5, {"role": "user", "content": "keep it short", "turnId": "t3", "steer": True})
    end, through = C.coverage(lines)
    assert lines[end]["role"] == "user" and not lines[end].get("steer") and through == "t3"
    with pytest.raises(C.CompactRefused):
        C.coverage(_lines(4))  # 8 lines: only 2 coverable, fewer than MIN_COVERED


def test_a_second_summary_must_cover_more_than_the_first():
    lines = _lines(6)
    first = {"summary": "s", "through_turn": "t3"}
    with pytest.raises(C.CompactRefused) as e:
        C.coverage(lines, first)
    assert str(e.value) == C.NOTHING_NEW
    assert C.coverage(_lines(8), first) == (10, "t5")


def test_survives_cut_and_the_boundary_of_a_trimmed_turn():
    lines = _lines(6)
    rec = {"summary": "s", "through_turn": "t3"}
    assert C.boundary(lines, rec) == 6
    assert C.survives_cut(rec, lines, 6) and not C.survives_cut(rec, lines, 5)
    # The 200-line cap trimmed the covered turn away: every line left is newer.
    trimmed = lines[6:]
    assert C.boundary(trimmed, rec) == 0 and C.survives_cut(rec, trimmed, 0)
