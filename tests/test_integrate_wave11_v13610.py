"""v1.361.0 integrate — Wave 11, "the final hunt's regressions closed", joined.

Three lanes, three seams, plus the integrator's own changes:

(a) ONE NOTICE THAT NAMES THE THING (lane 11A). Four real scheduled fires
    through the real app factory and the real "Run now": an
    ``on_failure: skip`` agent step that failed, a skipped TOOL step (no run
    of its own, so no bell row), a halted run, and a clean run whose step
    failed once and was retried. The phone gets ONE line that names the
    schedule's step and its reason; "This PC" speaks only where the bell has
    no row. What the REAL daemon sent — GET /audit and the This PC lines as
    the ``comm.desktop`` events its sink publishes ({title, message}) — is
    written to ``dashboard/__tests__/fixtures/wave11-seams-v13610.json``
    (``IJ_REGEN_WAVE11_SEAMS=1``), and
    ``dashboard/__tests__/wave11-seams-v13610.test.tsx`` feeds it through
    the real bell AND the real "This PC" bridge together — in the main
    window, in a pop-out that speaks (the main window in the tray) and in a
    quiet pop-out: one desktop notice per outcome, never none, never two.
(b) THE COMBINED MATCHER TABLE THROUGH BOTH CHAT LANES (lane 11B). Every
    DOOR row and every GUIDE row of ``tests/test_matcher_tables_v13610.py``
    through the real stream lane AND the real POST lane on a fresh install:
    both lanes carry the same door (MIRROR NOTE), a row with no door shows
    none, a row with a door names exactly the table's apps, and the Guide is
    armed exactly where the table says. The one place the door is
    deliberately narrower than the matcher is v1.360.0's: everyday calendar
    words with no calendar app connected offer nothing (the words did not
    say which app).
(c) (integrate, lane 11B's review note) "In my outlook, the market will
    recover" is the idiom for "in my view": no door, and the model is not
    told about Microsoft 365, on either lane — while "find the invoice in my
    outlook, then …" keeps its door.

The dashboard half of seam (c) of the brief — the place words on the reply,
the run page and the menu — is ``wave11-seams-v13610.test.tsx`` (c).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from iron_jarvis.comm.channels import DesktopChannel
from iron_jarvis.comm.notifier import bell_pings
from iron_jarvis.connectors import app_names
from iron_jarvis.connectors.catalog import CATALOG
from iron_jarvis.core.events import EventType
from iron_jarvis.core.turns import TURNS
from iron_jarvis.daemon.app import create_app
import iron_jarvis.daemon.routes.chat as chat_routes
from iron_jarvis.scheduling import run_outcome as ro
from tests.test_chat_matches_meant_v13600 import _post_turn, _section, _stream_turn
from tests.test_matcher_tables_v13610 import APPS_HREF, DOOR, GUIDE_ROWS, M365
from tests.test_no_notice_lost_v13610 import (
    CHECK_TOOL,
    FETCH_HALT,
    FETCH_SKIP,
    INBOX,
    WRITE,
    _audit,
    _fire,
    _stable,
    _with_ts,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "dashboard" / "__tests__" / "fixtures" / "wave11-seams-v13610.json"
REGEN = "IJ_REGEN_WAVE11_SEAMS"


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    # A real key in the developer's shell (this PC has a Pixio key) makes the
    # media app "connected" and hides its door — the tables are read as a
    # FRESH install, exactly as the matcher tests read them.
    for var in ("PIXIO_API_KEY", "OPENAI_API_KEY", "IRONJARVIS_MEDIA_FAKE"):
        monkeypatch.delenv(var, raising=False)
    for tid in TURNS.running_ids():
        TURNS.release(tid)
    yield
    for tid in TURNS.running_ids():
        TURNS.release(tid)


@pytest.fixture()
def app(tmp_path, monkeypatch):
    app = create_app(str(tmp_path / "fresh"))
    monkeypatch.setattr(chat_routes, "_ASK_POLL_S", 0.05)
    return app


# =========================================================================== #
# (a) ONE NOTICE THAT NAMES THE THING — the daemon's half + the fixture
# =========================================================================== #

FETCH_FLAKY = {"name": "Fetch", "kind": "agent", "task": "FLAKY: fetch the inbox", "on_failure": "retry"}

#: key -> the steps of the scheduled workflow "Nightly".
FIRES = {
    "skipped_agent_step": [FETCH_SKIP, WRITE],
    "skipped_tool_step": [dict(CHECK_TOOL, on_failure="skip"), WRITE],
    "halted_by_agent_step": [FETCH_HALT, WRITE],
    "clean_after_a_retried_step": [FETCH_FLAKY, WRITE],
}


def _check_or_write(key: str, value) -> None:
    data = json.loads(FIXTURE.read_text(encoding="utf-8")) if FIXTURE.exists() else {}
    if os.environ.get(REGEN) == "1":
        data.setdefault(
            "_about",
            "v1.361.0 integrate: what the REAL daemon sends for four scheduled workflow "
            "fires (tests/test_integrate_wave11_v13610.py writes it with "
            "IJ_REGEN_WAVE11_SEAMS=1; dashboard/__tests__/wave11-seams-v13610.test.tsx "
            "feeds it through the real bell and the real This PC bridge). `audit` is GET "
            "/audit's answer (newest first), ids and times made stable; `desktop` is what "
            "the This PC sink was handed, in order (it publishes each as a comm.desktop "
            "event {title, message}); `phone` is every other destination's line.",
        )
        data[key] = value
        FIXTURE.write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n"
        )
        return
    assert key in data, f"the fixture has no {key!r}: regenerate with {REGEN}=1"
    assert data[key] == value, f"the daemon's {key!r} changed: if on purpose, regenerate with {REGEN}=1"


def _fire_and_record(key: str, tmp_path: Path, monkeypatch):
    desktop: list[dict[str, str]] = []
    monkeypatch.setattr(
        DesktopChannel, "sink", lambda title, message: desktop.append({"title": title, "message": message})
    )
    out = _fire(tmp_path / key, monkeypatch, FIRES[key])
    names: dict[str, str] = {}
    _check_or_write(
        key,
        {
            "row": {"last_status": out.body["last_status"], "last_detail": out.body["last_detail"]},
            "phone": out.phone,
            "desktop": desktop,
            "audit": _with_ts(_stable(_audit(out.client), names)),
        },
    )
    return out, desktop


def _failed_runs(out) -> list[dict]:
    return [
        e for e in out.events
        if e["type"] == EventType.SESSION_COMPLETED and e["payload"].get("status") == "failed"
    ]


def test_a_skipped_agent_step_is_one_notice_that_names_the_step_and_its_reason(tmp_path, monkeypatch):
    out, desktop = _fire_and_record("skipped_agent_step", tmp_path, monkeypatch)
    assert out.body["last_status"] == ro.STATUS_PARTIAL, out.body
    # Every destination but This PC: ONE line, the step and its own words.
    assert len(out.phone) == 1 and "“Fetch”" in out.phone[0] and INBOX in out.phone[0], out.phone
    # This PC is quiet because the bell rings the step's own run, whose row
    # carries the same reason (its body is the run's summary).
    [step] = _failed_runs(out)
    assert bell_pings(step) and step["payload"]["summary"] == INBOX, step
    assert desktop == [], desktop


def test_a_skipped_tool_step_has_no_bell_row_so_this_pc_says_it_once(tmp_path, monkeypatch):
    out, desktop = _fire_and_record("skipped_tool_step", tmp_path, monkeypatch)
    assert out.body["last_status"] == ro.STATUS_PARTIAL, out.body
    assert _failed_runs(out) == [], out.events  # nothing for the bell to ring
    assert len(out.phone) == 1 and "“Check”" in out.phone[0], out.phone
    assert [d["message"] for d in desktop] == out.phone, (desktop, out.phone)


def test_a_halted_run_is_one_notice_and_the_phone_names_the_step_that_stopped_it(tmp_path, monkeypatch):
    out, desktop = _fire_and_record("halted_by_agent_step", tmp_path, monkeypatch)
    assert out.body["last_status"] == ro.STATUS_ERROR, out.body
    assert len(out.phone) == 1 and "“Fetch”" in out.phone[0] and INBOX in out.phone[0], out.phone
    [step] = _failed_runs(out)
    [wf] = [e for e in out.events if e["type"] == EventType.WORKFLOW_COMPLETED]
    # The bell's ONE row for it: the workflow's, standing in for the step
    # that ended the run (failed_session) — This PC quiet.
    assert wf["payload"]["status"] == "failed" and wf["payload"]["failed_session"] == step["session_id"]
    assert desktop == [], desktop


def test_a_clean_run_after_a_retried_step_says_done_once_beside_the_attempts_own_notice(
    tmp_path, monkeypatch
):
    out, desktop = _fire_and_record("clean_after_a_retried_step", tmp_path, monkeypatch)
    assert out.body["last_status"] == ro.STATUS_OK, out.body
    done = "Schedule “nightly”: done — workflow “Nightly” ran"
    assert out.phone == [done], out.phone
    # Two outcomes, one notice each: the attempt that failed (the bell) and
    # the run that finished (This PC's line).
    [attempt] = _failed_runs(out)
    assert bell_pings(attempt), attempt
    assert [d["message"] for d in desktop] == [done], desktop


# =========================================================================== #
# (b) THE COMBINED MATCHER TABLE THROUGH BOTH REAL CHAT LANES
# =========================================================================== #


def _door_labels(answer: dict) -> list[str]:
    return [d["label"] for d in answer.get("doors") or [] if d.get("href") == APPS_HREF]


def _calendar_words_only(text: str, want: list[str]) -> bool:
    """v1.360.0 (lane 10B, on purpose): "my calendar" with no calendar app
    connected offers nothing — the words did not say which app. The matcher
    still reads Microsoft 365 there (it is the catalog's calendar)."""
    return (
        want == ["microsoft_365"]
        and app_names._GENERIC_CALENDAR_RX.search(text) is not None
        and app_names._CALENDAR_BRAND_RX.search(text) is None
    )


def test_every_door_row_reads_the_same_through_both_chat_lanes(app):
    """Every DOOR row, both lanes, one pass (~900 turns, ~15 s here)."""
    platform = app.state.platform
    names = {c.id: c.name for c in CATALOG}
    differ, wrong_door, wrong_apps = [], [], []
    for text, want in DOOR:
        done, _ = _stream_turn(platform, text)
        res, _ = _post_turn(platform, text)
        stream, post = _door_labels(done), _door_labels(res)
        if stream != post:
            differ.append((text, stream, post))
            continue
        expect = bool(want) and not _calendar_words_only(text, want)
        if bool(stream) != expect:
            wrong_door.append((text, want, stream))
            continue
        if stream:
            [label] = stream
            missing = [cid for cid in want if names[cid] not in label]
            if missing:
                wrong_apps.append((text, want, label))
    assert not (differ or wrong_door or wrong_apps), (
        f"lanes differ: {differ}\n door is not the table's: {wrong_door}\n wrong apps: {wrong_apps}"
    )


def test_every_guide_row_arms_the_guide_the_same_way_on_both_chat_lanes(app):
    """Every GUIDE row, both lanes: the Handbook search the model is shown
    (``guide_search`` — at the lanes' real cap of 6 a busy how-to can push
    ``app_status``, the third lookup, off the list)."""
    platform = app.state.platform
    wrong = []
    for text, want in GUIDE_ROWS:
        _done, sm = _stream_turn(platform, text)
        _res, pm = _post_turn(platform, text)
        stream = "guide_search" in set(sm.tools[0] if sm.tools else [])
        post = "guide_search" in set(pm.tools[0] if pm.tools else [])
        if stream != want or post != want:
            wrong.append((text, want, stream, post))
    assert wrong == [], wrong


# =========================================================================== #
# (c) the "in my outlook," idiom — the integrator's narrow guard
# =========================================================================== #


@pytest.mark.parametrize(
    "text",
    [
        "In my outlook, the market will recover",
        "From my outlook, this looks bad",
        # re-review (v1.361.0): the opening is the idiom without a comma too.
        "in my outlook the market will recover",
        "From my outlook as an investor this is risky",
    ],
)
def test_the_view_idiom_is_not_the_app_on_either_lane(app, text):
    platform = app.state.platform
    done, smodel = _stream_turn(platform, text)
    res, pmodel = _post_turn(platform, text)
    assert _door_labels(done) == [] and _door_labels(res) == [], (done.get("doors"), res.get("doors"))
    for model in (smodel, pmodel):
        assert M365 not in _section(model.systems[0]), _section(model.systems[0])


def test_a_place_in_the_middle_of_a_sentence_keeps_its_door_on_both_lanes(app):
    """The control: only a SENTENCE that opens with the idiom is the idiom."""
    platform = app.state.platform
    text = "find the invoice in my outlook, then forward it to Jim"
    done, _ = _stream_turn(platform, text)
    res, _ = _post_turn(platform, text)
    want = [f"Connect {M365} in Settings"]
    assert _door_labels(done) == want and _door_labels(res) == want, (done.get("doors"), res.get("doors"))
