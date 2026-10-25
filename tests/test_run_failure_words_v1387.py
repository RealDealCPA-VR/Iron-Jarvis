"""Readiness 12A (G28) — a failure nothing recognised reads in plain words
everywhere a RUN is read, with the raw text folded away.

THE GAP (docs/PRODUCT-READINESS-RESULTS.md 0d.6, carried through 0e and 0f):
``failure_words.plain_reason`` fell back to the failure's OWN text when its
classifier did not know the shape, so a run that ended on

    codex-cli: CLI exited 1: error: unexpected status 400 Bad Request: model is not supported

read exactly that on its run page, its Schedules row ("The run failed:
codex-cli: CLI exited 1: …"), the bell and the phone. The chat had already
split such a failure into ``title`` / ``fix`` / ``technical``
(``chat_turn.error_payload``); a run had one string.

NOW (one rule, ``failure_words.run_failure_words``): a run's summary names
WHAT failed — the provider by its plain name, or Iron Jarvis itself — and what
to do next; a failure that was already written for a person (Iron-Proxy's
account sentences, a local-only refusal, a held Build session, Emergency stop)
keeps its own sentence; the raw text is kept apart on the row
(``Session.failure_detail``), served by ``GET /sessions/{id}`` only, and
folded under the run page's summary ("Details").

Every pin drives the REAL code: the real orchestrator finalizer, the real app
factory (``GET /sessions/{id}``, ``GET /sessions``, a schedule's Run now with
the agent's work standing in for a failing CLI), the real event bus and the
real notifier. The CONTRACT FIXTURE
``dashboard/__tests__/fixtures/run-failure-words-v1387.json`` is written from
those real answers (``IJ_REGEN_RUN_FAILURE_WORDS=1``) and read by
``dashboard/__tests__/run-failure-words-v1387.test.tsx`` through the real run
page, the real Schedules words and the real bell.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from iron_jarvis.agents.orchestrator import (
    EMERGENCY_STOP_REFUSAL,
    EmergencyStopOn,
    Orchestrator,
)
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.events import EventType
from iron_jarvis.core.models import AgentType, EventRecord, Session
from iron_jarvis.daemon.app import create_app
from iron_jarvis.platform import build_platform
from iron_jarvis.providers import failure_words as fw
from iron_jarvis.providers.adapters.base import ProviderError
from iron_jarvis.providers.hold import HeldProviderRefused
from iron_jarvis.scheduling import run_outcome as ro
from tests.test_schedule_outcomes_v13510 import _add_task, _mock_channel, _schedule_messages

_ROOT = Path(__file__).resolve().parents[1]
FIXTURE = _ROOT / "dashboard" / "__tests__" / "fixtures" / "run-failure-words-v1387.json"

#: The audit's own example (0d.6): an adapter's untyped CLI failure.
CODEX_400 = "codex-cli: CLI exited 1: error: unexpected status 400 Bad Request: model is not supported"
#: A secret-shaped value inside raw text must never reach a person unmasked.
PLANTED_KEY = "sk-ant-api03-" + "A" * 40

CODEX_REASON = (
    "Codex couldn't finish this task. Run it again. If it happens again, "
    "choose another model in Settings."
)
OWN_REASON = (
    "Iron Jarvis couldn't finish this task. Run it again. If it happens again, "
    "open the run to see the details."
)


# --------------------------------------------------------------------------- #
# The rule itself
# --------------------------------------------------------------------------- #
def test_an_unrecognised_cli_failure_names_what_failed_and_what_to_do():
    """RED on the old code: the reason WAS the raw CLI text."""
    exc = ProviderError(CODEX_400)
    assert fw.plain_reason(exc) == CODEX_REASON
    reason, technical = fw.run_failure_words(exc)
    assert reason == CODEX_REASON
    assert technical == CODEX_400, "the raw text is kept, apart"
    for raw in ("codex-cli", "CLI exited", "400", "Bad Request", "unexpected status"):
        assert raw not in reason, raw


def test_a_failure_no_model_caused_names_iron_jarvis_and_where_the_details_are():
    exc = RuntimeError("sqlite3.OperationalError: database disk image is malformed")
    reason, technical = fw.run_failure_words(exc)
    assert reason == OWN_REASON
    assert technical == "sqlite3.OperationalError: database disk image is malformed"
    assert "sqlite" not in reason and "OperationalError" not in reason


def test_the_generic_run_sentence_fits_a_bell_row_whole():
    """The bell clips a row's body at 140 characters (NotificationBell
    ``clip``); a generic run sentence must be read whole there, never "…say
    what went wr…" (the first cut of this wave did not fit)."""
    ids = [k for k in fw.PLAIN_NAMES if k not in ("auto", "mock")]
    longest = max(ids, key=lambda k: len(fw.PLAIN_NAMES[k]))
    named = fw.plain_reason(ProviderError(f"{longest}: something new went wrong"))
    assert named.startswith(fw.heading(fw.PLAIN_NAMES[longest])), named
    for reason in (OWN_REASON, CODEX_REASON, named):
        assert len(f"The run failed: {reason}") <= 140, reason


def test_the_runs_provider_names_a_provider_failure_that_names_none():
    """A typed provider failure with no provider in its words is named by the
    run's own provider (the hint), never by Auto or the demo model."""
    exc = ProviderError("something new went wrong")
    assert fw.plain_reason(exc, provider="claude-cli").startswith("Claude Code couldn't finish this task.")
    assert fw.plain_reason(exc, provider="auto").startswith("Iron Jarvis couldn't finish this task.")
    assert fw.plain_reason(exc, provider="mock").startswith("Iron Jarvis couldn't finish this task.")
    # A failure that is not a provider's is never pinned on the run's provider.
    assert fw.plain_reason(RuntimeError("boom"), provider="claude-cli") == OWN_REASON


def test_a_failure_already_worded_for_a_person_keeps_its_own_sentence():
    held = HeldProviderRefused("Claude Code", "OpenAI")
    reason, technical = fw.run_failure_words(held)
    assert reason == str(held) and technical == "", "nothing to fold: the reason IS the words"
    stop = EmergencyStopOn(EMERGENCY_STOP_REFUSAL)
    assert fw.plain_reason(stop) == " ".join(EMERGENCY_STOP_REFUSAL.split())
    # Iron-Proxy's account sentence: the adapter's provider-id prefix goes.
    acct = ProviderError("codex-cli: Every Codex account has hit its usage limit.", transient=False)
    acct.no_failover = True  # the marker iron_proxy.accounts._refusal sets
    assert fw.plain_reason(acct) == "Every Codex account has hit its usage limit."


def test_a_recognised_failure_keeps_its_run_words_and_folds_the_raw_text():
    exc = ProviderError("openrouter API error 401: No auth credentials found", status_code=401)
    reason, technical = fw.run_failure_words(exc)
    assert reason == f"OpenRouter refused your key. Update it in {fw.ACCOUNTS_PLACE}."
    assert technical == "openrouter API error 401: No auth credentials found"


def test_a_secret_in_the_raw_text_is_masked_in_the_fold():
    exc = ProviderError(f"codex-cli: CLI exited 1: bad key {PLANTED_KEY}")
    reason, technical = fw.run_failure_words(exc)
    assert PLANTED_KEY not in technical and PLANTED_KEY not in reason
    assert technical.startswith("codex-cli: CLI exited 1: bad key")


def test_the_chat_keeps_its_own_words_byte_for_byte():
    """Nothing else changed: the chat's failure line (``friendly_error`` on the
    chat surface) is the pre-12A sentence."""
    exc = ProviderError(CODEX_400)
    w = fw.friendly_error(exc)
    assert w == {"title": "Codex couldn't finish this answer", "fix": fw.GENERIC_FIX, "technical": CODEX_400}
    w = fw.friendly_error(RuntimeError("boom"))
    assert (w["title"], w["fix"]) == (fw.GENERIC_TITLE, fw.GENERIC_FIX)


def test_the_router_banner_inside_a_run_says_the_runs_words():
    """``friendly_error`` read inside a run (the router's background-task
    banner) says what the run's summary says — one rule."""
    with fw.run_surface():
        w = fw.friendly_error(ProviderError(CODEX_400))
    assert f"{w['title']}. {w['fix']}" == CODEX_REASON
    assert fw.is_generic_title(w["title"])


# --------------------------------------------------------------------------- #
# Through the real orchestrator and the real app factory
# --------------------------------------------------------------------------- #
async def test_the_finalizer_writes_the_plain_summary_and_keeps_the_raw_text_apart(tmp_path):
    p = build_platform(str(tmp_path / "home"))
    orch = Orchestrator(p)
    s = await orch.create_session("go", AgentType.BUILDER)
    await orch._finalize_failed(s, ProviderError(CODEX_400))
    with session_scope(p.engine) as db:
        row = db.get(Session, s.id)
        assert row.summary == f"The run failed: {CODEX_REASON}"
        assert row.failure_detail == CODEX_400


def _failing_run(exc):
    async def run(session, agent_def, *a, **kw):
        raise exc

    return run


def _seam(tmp_path, monkeypatch) -> dict:
    """A schedule's Run now whose agent work raises the audit's CLI failure,
    read back through every door a person reads it from."""
    app = create_app(str(tmp_path))
    client = TestClient(app)
    platform = app.state.platform
    monkeypatch.setattr(platform.orchestrator.runtime, "run", _failing_run(ProviderError(CODEX_400)))
    _add_task(client, "nightly")
    mock = _mock_channel(app)
    before = len(mock.sent)
    row = client.post("/schedules/nightly/run").json()
    sid = row["last_session_id"]
    detail = client.get(f"/sessions/{sid}").json()
    listing = client.get("/sessions").json()["sessions"]
    with session_scope(platform.engine) as db:
        ev = db.exec(
            select(EventRecord)
            .where(EventRecord.session_id == sid)
            .where(EventRecord.type == EventType.SESSION_COMPLETED)
        ).first()
        payload = json.loads(ev.payload_json) if ev is not None else {}
    return {
        "row": row,
        "session": detail["session"],
        "listed": [s for s in listing if s.get("id") == sid],
        "event": {"type": str(EventType.SESSION_COMPLETED), "payload": payload},
        "phone": _schedule_messages(mock, before, "nightly"),
    }


def test_every_surface_reads_the_plain_words_and_only_the_run_page_folds_the_raw_text(tmp_path, monkeypatch):
    got = _seam(tmp_path, monkeypatch)
    summary = f"The run failed: {CODEX_REASON}"
    # The run page's read: the plain summary, the raw text apart.
    assert got["session"]["status"] == "failed"
    assert got["session"]["summary"] == summary
    assert got["session"]["failure_detail"] == CODEX_400
    # The lists never carry it (up to 2,000 characters per run).
    assert got["listed"] and "failure_detail" not in got["listed"][0]
    # The Schedules row (RED on the old code: "The run failed: codex-cli: …").
    assert got["row"]["last_status"] == "error"
    assert got["row"]["last_detail"] == ro.failure_detail(summary)
    # The bell's event and the phone's line.
    assert got["event"]["payload"]["summary"] == summary
    assert got["phone"] and all("codex-cli" not in m and "CLI exited" not in m for m in got["phone"])
    for surface in (got["row"]["last_detail"], got["event"]["payload"]["summary"], *got["phone"]):
        assert "unexpected status" not in surface and "Bad Request" not in surface


def _normalised(got: dict) -> dict:
    """The fixture's shape: what the dashboard reads, ids made stable."""
    s = got["session"]
    keep = ("status", "summary", "failure_detail", "provider", "model", "task", "outcome")
    return {
        "about": (
            "Readiness 12A (G28): the REAL daemon's answers for a scheduled run whose agent work "
            "failed with an untyped CLI error. Written by tests/test_run_failure_words_v1387.py "
            "(IJ_REGEN_RUN_FAILURE_WORDS=1), read by dashboard/__tests__/"
            "run-failure-words-v1387.test.tsx through the real run page, Schedules words and bell."
        ),
        "session": {k: s.get(k) for k in keep},
        "schedule_row": {
            "name": "nightly",
            "last_status": got["row"]["last_status"],
            "last_detail": got["row"]["last_detail"],
        },
        "event": {
            "type": got["event"]["type"],
            "payload": {
                k: got["event"]["payload"].get(k)
                for k in ("status", "summary", "ok", "origin", "outcome")
            },
        },
        "phone": got["phone"],
    }


def test_the_contract_fixture_is_the_real_daemons_answer(tmp_path, monkeypatch):
    data = _normalised(_seam(tmp_path, monkeypatch))
    if os.environ.get("IJ_REGEN_RUN_FAILURE_WORDS") == "1":
        FIXTURE.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    stored = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert stored == data, "regenerate with IJ_REGEN_RUN_FAILURE_WORDS=1 after an intended change"


@pytest.mark.parametrize("text", [CODEX_400, "boom", "database is locked"])
def test_the_new_sentences_pass_the_retired_word_scan(text):
    """The daemon scan's own reader (VOCABULARY.md's retired words): the new
    run sentences use none of them."""
    from tests.test_vocabulary_beyond_dashboard_v13580 import _hits

    for line in (
        fw.run_failure_words(ProviderError(text))[0],
        fw.run_failure_words(RuntimeError(text))[0],
        fw.GENERIC_FIX_RUN_OWN,
        fw.GENERIC_TITLE_RUN,
    ):
        assert _hits(line) == [], line
