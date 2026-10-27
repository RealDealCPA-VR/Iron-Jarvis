"""v1.354.0 integrate — Wave 4's lanes joined ("plain words everywhere").

Driven through the REAL code: the real app factory's stream lane, the real
orchestrator's failure finalizer and boot reconcile, the real platform
Notifier (its event-bus handler, with a recording channel standing in for
the phone), the real connection registry, /health and the provider manager.

SEAMS
* (a) ONE set of words for one cause. A model that refuses the key: the chat's
  error frame carries ``title`` / ``fix``; the chat line shows ``title. fix``
  (dashboard ``lib/failureLine``); a failed run's summary — the run page and
  the bell row print it — is ``The run failed: title. fix``; the phone line
  carries that summary; a ``provider.failed`` alert says the same sentence. A
  restart: the reconcile's summary is the run page's own sentence
  ("Stopped when Iron Jarvis restarted."), the chat line a chat-started run
  delivers home is that sentence, and a goal's breaker reason says it too.
  The values are written to ``dashboard/__tests__/fixtures/
  wave4-seams-v13540.json`` (``IJ_REGEN_WAVE4_SEAMS=1``) and the dashboard
  side (``wave4-seams-v13540.test.tsx``) renders them through the real chat
  page, the bell and the run page's words.
* The NOTIFIER (wave-1 leftover): every default alert type reaches the phone,
  Telegram, email, Slack and this PC's toast in the bell's words — never an
  event type name, a ``key=value`` pair or an internal id.
* (c) the model-server card reads the router's answer: GET /connections
  ``connected`` == GET /health ``available`` (what the model menu greys by)
  == ``providers.available`` for the keyless endpoint, both ways.
* The media key place and the chat's failure place are ONE constant.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.agents.chat_delivery import content_for_session
from iron_jarvis.agents.orchestrator import RESTART_SUMMARY, Orchestrator
from iron_jarvis.comm.channels import MockChannel
from iron_jarvis.comm.notifier import (
    DEFAULT_ALERT_EVENTS,
    EVENT_WORDS,
    UNKNOWN_EVENT_LINE,
    format_event,
)
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.events import EventType
from iron_jarvis.core.models import AgentType, Session, SessionStatus
from iron_jarvis.daemon.app import create_app
from iron_jarvis.platform import build_platform
from iron_jarvis.providers.adapters.base import ProviderError
from iron_jarvis.providers.failure_words import ACCOUNTS_PLACE

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "dashboard" / "__tests__" / "fixtures" / "wave4-seams-v13540.json"
OPENROUTER_401 = "openrouter API error 401: No auth credentials found"


def _parse_sse(raw: str) -> list[tuple[str, dict]]:
    out: list[tuple[str, dict]] = []
    for block in raw.replace("\r\n", "\n").split("\n\n"):
        event, data = None, None
        for line in block.strip().splitlines():
            if line.startswith("event:"):
                event = line[len("event:"):].strip()
            elif line.startswith("data:"):
                data = json.loads(line[len("data:"):].strip())
        if event is not None:
            out.append((event, data if isinstance(data, dict) else {}))
    return out


def _check_fixture(section: str, actual: dict) -> None:
    """The dashboard reads what the REAL daemon said (regenerate on purpose
    with IJ_REGEN_WAVE4_SEAMS=1)."""
    data = json.loads(FIXTURE.read_text(encoding="utf-8")) if FIXTURE.exists() else {}
    if os.environ.get("IJ_REGEN_WAVE4_SEAMS") == "1":
        data["_about"] = (
            "v1.354.0 integrate: the words the REAL daemon says for one cause. Written by "
            "tests/test_integrate_wave4_v13540.py (IJ_REGEN_WAVE4_SEAMS=1); read by "
            "dashboard/__tests__/wave4-seams-v13540.test.tsx."
        )
        data[section] = actual
        FIXTURE.parent.mkdir(parents=True, exist_ok=True)
        FIXTURE.write_text(json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
        return
    assert section in data, f"fixture has no {section!r}: regenerate it"
    assert data[section] == actual, f"the daemon's {section!r} words no longer match the dashboard's fixture"


def _phone(p) -> MockChannel:
    """A recording destination on the platform's REAL notifier (the event
    bus handler build_platform registered)."""
    ch = MockChannel()
    p.notifier.add_channel("phone", ch)
    return ch


# =========================================================================== #
# (a) one cause, one sentence: chat line, run summary, bell row, phone
# =========================================================================== #


def test_a_refused_key_reads_as_one_sentence_in_chat_on_the_run_and_on_the_phone(tmp_path, monkeypatch):
    exc = ProviderError(OPENROUTER_401, status_code=401)

    # 1. The chat's stream lane (the real app factory).
    client = TestClient(create_app(str(tmp_path / "app")))
    p_app = client.app.state.platform

    async def fail(**kw):
        raise exc

    async def fail_stream(**kw):
        raise exc
        yield  # pragma: no cover

    monkeypatch.setattr(p_app.router, "complete", fail)
    monkeypatch.setattr(p_app.router, "stream", fail_stream)
    r = client.post("/chat/stream", json={"messages": [{"role": "user", "content": "hi"}]})
    [frame] = [d for ev, d in _parse_sse(r.text) if ev == "error"]
    sentence = f"{frame['title']}. {frame['fix']}"
    assert sentence == f"OpenRouter refused your key. Update it in {ACCOUNTS_PLACE}."
    assert frame["detail"] == OPENROUTER_401, "detail is unchanged for headless callers"

    # 2. The same failure ends an agent run: its summary (the run page, the
    #    bell row and the chat line a chat-started run delivers home all
    #    print it) ends with the SAME sentence; the phone line carries it.
    p = build_platform(str(tmp_path / "home"))
    phone = _phone(p)
    orch = Orchestrator(p)

    async def run() -> Session:
        s = await orch.create_session("tidy the inbox", AgentType.BUILDER)
        await orch._finalize_failed(s, exc)
        return s

    s = asyncio.run(run())
    with session_scope(p.engine) as db:
        summary = db.get(Session, s.id).summary
    assert summary == f"The run failed: {sentence}"
    row = SimpleNamespace(status=SessionStatus.FAILED, summary=summary)
    assert content_for_session(row) == summary
    [line] = [m for m in phone.sent if sentence in m]
    assert line == f"A run could not finish — {summary}"
    for leak in ("session.completed", "status=", "summary=", s.id, "No auth credentials"):
        assert leak not in line, leak

    # 3. The router's own provider.failed alert says the chat's sentence.
    provider_line = format_event(
        {"type": EventType.PROVIDER_FAILED, "payload": {"provider": "openrouter", "error": f"ProviderError: {OPENROUTER_401}"}}
    )
    assert provider_line == sentence

    _check_fixture(
        "refused_key",
        {
            "detail": frame["detail"],
            "title": frame["title"],
            "fix": frame["fix"],
            "technical": frame["technical"],
            "sentence": sentence,
            "summary": summary,
            "phone_line": line,
        },
    )


#: What the cut-off run was asked: two lines and an apostrophe, so the
#: continue door's ``repr`` quotes it with double quotes and escapes the newline.
ASKED = "Summarise the lease" + chr(10) + "then email it to Sam's accountant"

#: The bell's Continue message (dashboard components/InterruptedJobs
#: CONTINUE_AFTER_RESTART, pinned equal there by the dashboard side).
CONTINUE_AFTER_RESTART = "Continue where you left off — Iron Jarvis restarted while this was running."


def test_a_restart_reads_as_one_sentence_on_the_run_in_the_chat_and_for_a_goal(tmp_path):
    p = build_platform(str(tmp_path / "home"))
    orch = Orchestrator(p)

    async def make() -> Session:
        return await orch.create_session(ASKED, AgentType.BUILDER)

    s = asyncio.run(make())  # left ACTIVE: a restart cut it off
    fresh = Orchestrator(p)  # the new process: nothing is running
    assert fresh.reconcile_interrupted_sessions() >= 1
    row = fresh.get_session(s.id)
    assert row.status is SessionStatus.FAILED and row.interrupted_at is not None
    assert row.summary == RESTART_SUMMARY == "Stopped when Iron Jarvis restarted."
    # The chat line a chat-started run delivers home is that sentence.
    assert content_for_session(row) == RESTART_SUMMARY
    # A goal's breaker reason (it reaches the phone after "Goal breaker
    # tripped: <name> —") says the same words, mid-sentence.
    from iron_jarvis.goals import engine as goals_engine

    assert goals_engine._INTERRUPTED_REASON == RESTART_SUMMARY[0].lower() + RESTART_SUMMARY[1:].rstrip(".")
    # Nothing a user reads says "daemon" any more.
    assert "daemon" not in RESTART_SUMMARY.lower()

    # The bell's Continue (resume) starts a run asked the APP's words; the
    # run page titles it by the run it continues, read out of this task.
    async def resume() -> Session:
        return await fresh.continue_session(s.id, CONTINUE_AFTER_RESTART, resume=True)

    cont = asyncio.run(resume())
    assert cont.task.startswith(CONTINUE_AFTER_RESTART)
    _check_fixture(
        "restart",
        {
            "summary": RESTART_SUMMARY,
            "goal_reason": goals_engine._INTERRUPTED_REASON,
            "asked": ASKED,
            "continuation_task": cont.task,
        },
    )


# =========================================================================== #
# The notifier: every default alert in the bell's words
# =========================================================================== #

#: A realistic payload for every default alert type (what the publishers send).
ALERT_PAYLOADS: dict[str, dict] = {
    EventType.REVIEW_REQUESTED: {"branch": "ij/session_abc123", "risk": "high", "changed_files": ["a.py", "b.py"]},
    EventType.WORKFLOW_COMPLETED: {"workflow": "nightly digest", "status": "failed", "run_id": "wfr_9", "sessions": ["session_1"]},
    EventType.PROVIDER_FAILED: {"provider": "anthropic", "error": "ProviderError: anthropic API error 429: rate limited"},
    EventType.SESSION_COMPLETED: {
        "status": "completed",
        "summary": "Wrote summary.md",
        "origin": "schedule:morning brief",
        "project_id": None,
        "outcome": "completed",
    },
    EventType.AUTONOMY_EXECUTED: {"proposal_id": "prop_1", "goal_id": "goal_1", "session_id": "session_1"},
    EventType.PROVIDER_FAILOVER: {"from": "openai", "to": "claude-cli", "reason": "timeout"},
    EventType.SKILL_PROPOSAL_CREATED: {"skill_name": "tidy-downloads", "auto": False},
    EventType.APPROVAL_REQUESTED: {"tool": "shell", "session_id": "session_1"},
    "goal.satisfied": {"goal_id": "goal_1", "name": "Inbox zero"},
    "goal.tripped": {"goal_id": "goal_1", "name": "Inbox zero", "reason": "budget exhausted"},
    "goal.iteration_refused": {"goal_id": "goal_1", "name": "Inbox zero", "reason": "paused"},
    # v1.399.0 (moved on purpose, night shift N2): the night's ONE message.
    EventType.BUILD_NIGHT_SUMMARY: {
        "night": "2026-10-26", "label": "Monday, October 26",
        "title": "Last night: 1 failed, 1 done", "body": "Failed: Fix the parser · Done: Make add() add",
        "counts": {"failed": 1, "needs_you": 0, "running": 0, "done": 1, "stopped": 0}, "total": 2,
        "lines": ["• Failed: Fix the parser (ij/fix-the-parser-77aa): The model failed.",
                  "• Done: Make add() add (ij/make-add-add-1a2b): tests passed; 1 file changed."],
    },
}

#: What the line must say for each (the bell's words for the same event).
ALERT_WORDS: dict[str, str] = {
    EventType.REVIEW_REQUESTED: "A change is waiting for your review (2 files changed, high risk).",
    EventType.WORKFLOW_COMPLETED: "Your workflow nightly digest could not finish",
    EventType.PROVIDER_FAILED: "Anthropic is busy. Try again shortly, or pick another model for this chat.",
    EventType.SESSION_COMPLETED: "Your schedule morning brief finished — Wrote summary.md",
    EventType.AUTONOMY_EXECUTED: "Iron Jarvis ran an action on its own",
    EventType.PROVIDER_FAILOVER: "OpenAI couldn't answer (timeout), so Claude Code answered instead.",
    EventType.SKILL_PROPOSAL_CREATED: "New skill suggested: tidy-downloads",
    EventType.APPROVAL_REQUESTED: "An agent is asking to use shell",
    "goal.satisfied": "Goal satisfied: Inbox zero",
    "goal.tripped": "Goal breaker tripped: Inbox zero — budget exhausted",
    "goal.iteration_refused": "Goal run refused: Inbox zero — paused",
    EventType.BUILD_NIGHT_SUMMARY: "Last night: 1 failed, 1 done\n• Failed: Fix the parser",
}


def test_every_default_alert_has_a_payload_here():
    assert set(ALERT_PAYLOADS) == set(DEFAULT_ALERT_EVENTS) == set(ALERT_WORDS)


@pytest.mark.parametrize("etype", sorted(DEFAULT_ALERT_EVENTS))
def test_every_default_alert_reaches_the_phone_in_plain_words(tmp_path, etype):
    """Through the platform's REAL notifier (the bus handler build_platform
    registered): the line names what happened, never the event type, a
    key=value pair or an internal id."""
    p = build_platform(str(tmp_path / "home"))
    phone = _phone(p)
    asyncio.run(p.event_bus.publish(etype, ALERT_PAYLOADS[etype], session_id="session_7b32f746bc3a"))
    assert phone.sent, f"{etype} sent nothing"
    line = phone.sent[-1]
    assert ALERT_WORDS[etype] in line, line
    assert etype not in line, f"the event type leaked: {line}"
    assert not re.search(r"\b[a-z_]+=", line), f"a key=value pair leaked: {line}"
    for engine_word in ("session_7b32f746bc3a", "(session", "wfr_9", "prop_1", "ij/session_abc123", "Iron Jarvis: " + etype):
        assert engine_word not in line, f"{engine_word!r} leaked: {line}"


@pytest.mark.parametrize(
    "payload, words",
    [
        ({"status": "failed", "summary": "The run failed: x", "origin": None, "outcome": None}, "A run could not finish"),
        ({"status": "completed", "summary": "", "origin": "job:mission", "outcome": "needs_you"}, "Your objective finished, but something needs you"),
        ({"status": "completed", "summary": "", "origin": "job:mission", "outcome": "completed"}, "Your objective is done"),
        ({"status": "completed", "summary": "", "origin": "goal:goal_8f2", "outcome": "completed_with_failures"}, "Your goal's run finished, but part of it failed"),
        # v1.358.0 (lane 8A, on purpose): a run that ENDED FAILED could not
        # finish even when an ask expired — the bell's and the row's verdict.
        ({"status": "failed", "summary": "", "origin": "comm:telegram", "outcome": "needs_you"}, "Your request from telegram could not finish"),
        ({"status": "completed", "summary": "", "origin": "comm:telegram", "outcome": "needs_you"}, "Your request from telegram needs you"),
        ({"status": "cancelled", "summary": "", "origin": "reflex:on-invoice", "outcome": None}, "Your reflex on-invoice was stopped"),
        ({"status": "completed", "summary": "", "origin": "autonomy", "outcome": None}, "An autonomy run finished. Check what it did"),
    ],
)
def test_a_run_that_ended_says_how_in_the_bells_words(payload, words):
    line = format_event({"type": EventType.SESSION_COMPLETED, "payload": payload, "session_id": "session_1"})
    assert line.startswith(words), line
    assert "goal_8f2" not in line and "session_1" not in line


def test_a_type_with_no_words_says_so_in_a_sentence_never_by_its_name():
    assert format_event({"type": "zzz.internal_thing", "payload": {"a": 1}}) == UNKNOWN_EVENT_LINE
    line = format_event({"type": "tool.executed", "payload": {"tool": "shell", "ok": True}})
    assert line == "Iron Jarvis: Ran a tool" and "tool.executed" not in line


def test_the_notifiers_event_words_are_the_dashboards():
    """ONE table: the phone line and the Time-travel feed / run page say the
    same words for an event type (dashboard lib/eventWords.EVENT_WORDS)."""
    src = (ROOT / "dashboard" / "lib" / "eventWords.ts").read_text(encoding="utf-8").replace("\r\n", "\n")
    block = src[src.index("export const EVENT_WORDS") : src.index("};", src.index("export const EVENT_WORDS"))]
    ts = dict(re.findall(r'"([a-z_.]+)":\s*"([^"]+)"', block))
    assert ts and ts == EVENT_WORDS


# =========================================================================== #
# (c) the model-server card reads the router's answer
# =========================================================================== #


def test_the_card_the_health_row_and_the_router_tell_one_story(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    p = client.app.state.platform
    r = client.put("/settings", json={"values": {"custom_base_url": "http://127.0.0.1:9/v1", "custom_model": "m-local"}})
    assert r.status_code == 200, r.text

    def story() -> tuple[bool, bool, bool]:
        card = {c["provider"]: c for c in client.get("/connections").json()["connections"]}["custom"]
        health = {h["provider"]: h for h in client.get("/health").json()["providers"]}["custom"]
        return bool(card["connected"]), bool(health["available"]), bool(p.providers.available("custom"))

    assert story() == (True, True, True)
    p.fleet.set_reachable("custom", False)
    assert story() == (False, False, False)
    p.fleet.set_reachable("custom", True)
    assert story() == (True, True, True)


# =========================================================================== #
# One place: a media key and a model key are added in the same words
# =========================================================================== #


def test_the_media_key_place_is_the_failure_words_place():
    from iron_jarvis.daemon.chat_turn import NO_MEDIA_SETTINGS_LINE
    from iron_jarvis.media.registry import ACCOUNTS_AND_KEYS, CONNECT_HINTS

    assert ACCOUNTS_AND_KEYS is ACCOUNTS_PLACE or ACCOUNTS_AND_KEYS == ACCOUNTS_PLACE
    assert CONNECT_HINTS["pixio"] == f"add your Pixio key in {ACCOUNTS_PLACE}"
    assert ACCOUNTS_PLACE in NO_MEDIA_SETTINGS_LINE
    assert "Accounts & keys" not in NO_MEDIA_SETTINGS_LINE


def test_the_failure_headlines_name_providers_like_the_model_menu():
    """failure_words.PLAIN_NAMES is the twin of the dashboard's model-menu
    names (lib/onboarding FRIENDLY): a headline and the menu say one word
    ("Claude Code", never "claude-cli"). The daemon's two extra rows (the
    demo model, Auto) are words the menu says elsewhere."""
    from iron_jarvis.providers.failure_words import PLAIN_NAMES

    src = (ROOT / "dashboard" / "lib" / "onboarding.ts").read_text(encoding="utf-8").replace("\r\n", "\n")
    at = src.index("const FRIENDLY: Record<string, string> = {")
    block = src[at : src.index("};", at)]
    ts = {k.strip('"'): v for k, v in re.findall(r'^\s*("?[a-z-]+"?):\s*"([^"]+)"', block, re.M)}
    assert ts, "the FRIENDLY table moved"
    extra = {k: v for k, v in PLAIN_NAMES.items() if k not in ts}
    assert {k: PLAIN_NAMES[k] for k in ts} == ts
    assert extra == {"mock": "the demo model", "auto": "Auto"}
