"""v1.357.0 (product readiness G76): the daemon and the desktop shell say the
canon's words too.

ACCEPTANCE: "The vocabulary test fails on any new user-visible 'daemon',
'pack', 'mock' or 'pane', and the demo is called the same thing on every
surface."

The dashboard takes the demo's words from ``dashboard/lib/demoWords.ts``
(``dashboard/__tests__/one-vocabulary-v13570.test.tsx`` pins its surfaces).
The review of lane 7C found the sentences IRON JARVIS writes into those same
surfaces still named the demo three other ways: every demo reply in chat ended
"_Note: this is the offline demo …_" right above a receipt that said "Demo
answer. No AI model ran."; a mission's route note said "ran on the mock
model"; the browser sidebar said "the built-in mock model"; the connections
registry named the row "Mock (offline)"; the refusals said "the offline mock".
And the desktop toast still said "A Build pane is waiting for you" while the
bell (``lib/paneAsks.ts``) said "A Build terminal".

These pins read the dashboard's words off its files (CRLF folded) and drive
the real code: the real app factory on a fresh install (whose default IS the
demo model), the real route note, the real sidebar notice.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.agents.mission import _route_note
from iron_jarvis.browser.panel import PanelTurns
from iron_jarvis.core.events import EventType
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.chat_turn import demo_note
from iron_jarvis.onboarding.checklist import provider_label
from iron_jarvis.profile.training import MIN_DERIVE_CHARS
from tests.test_chat_lanes_v13510 import _stream_done

_ROOT = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (_ROOT / rel).read_text(encoding="utf-8").replace("\r\n", "\n")


_DEMO_WORDS = _read("dashboard/lib/demoWords.ts")


def _const(name: str) -> str:
    m = re.search(rf'^export const {name} = "([^"]+)";$', _DEMO_WORDS, re.M)
    assert m, f"{name} is not a plain string in lib/demoWords.ts"
    return m.group(1)


DEMO_MODEL = _const("DEMO_MODEL")  # "demo model"
DEMO_MODEL_LABEL = _const("DEMO_MODEL_LABEL")  # "Demo model (scripted)"
DEMO_REPLIES = _const("DEMO_REPLIES")  # "a scripted demo, not real answers"
DEMO_ANSWER = _const("DEMO_ANSWER")  # "Demo answer. No AI model ran."

#: Every other name the audit counted for the one demo (VOCABULARY.md's
#: retired column for the demo row).
OTHER_DEMO_NAMES = re.compile(r"\bmock\b|offline (?:demo|mock|model)|simulated", re.I)


def test_the_words_read_off_the_dashboard_are_the_canon():
    """Anti-vacuity: the pins below compare against these, so a regex that
    read nothing (or the wrong constant) must fail here first."""
    assert DEMO_MODEL == "demo model"
    assert DEMO_MODEL_LABEL == "Demo model (scripted)"
    assert DEMO_REPLIES == "a scripted demo, not real answers"
    assert DEMO_ANSWER == "Demo answer. No AI model ran."


@pytest.mark.parametrize("tools", [[], ["write_file"]])
def test_the_chat_demo_note_says_the_demo_models_name_and_sentence(tools):
    note = demo_note(tools)
    assert DEMO_MODEL in note and DEMO_REPLIES in note, note
    assert not OTHER_DEMO_NAMES.search(note), note
    assert "Choose a model above to get real answers." in note


@pytest.mark.parametrize("lane", ["post", "stream"])
def test_a_demo_reply_on_either_lane_names_the_demo_once_and_one_way(tmp_path, lane):
    """The acceptance on the most-used surface: a fresh install (the demo
    answers), 'What can you do?' on BOTH chat lanes (MIRROR NOTE)."""
    with TestClient(create_app(str(tmp_path))) as client:
        body = {"messages": [{"role": "user", "content": "What can you do?"}]}
        out = client.post("/chat", json=body).json() if lane == "post" else _stream_done(client, body)
    assert out["route"]["provider"] == "mock" and out["route"]["reason"] == "mock"
    reply = out["reply"]
    assert reply.count(DEMO_MODEL) == 1, reply
    assert DEMO_REPLIES in reply, reply
    assert not OTHER_DEMO_NAMES.search(reply), reply


def test_a_teammate_run_on_the_demo_says_the_receipts_words():
    """The route note under a mission's / helper agent's answer sits under
    "Demo answer. No AI model ran." (MissionOutput, RunResultCard,
    AgentRunReceipt): same name, same second sentence."""
    note = _route_note(
        [(EventType.PROVIDER_DOWNGRADED, "s1", json.dumps({
            "requested": "mock (default)", "used": "mock", "reason": "no model connected",
        }))],
        {"s1": "Researcher"},
    )
    assert note == f"Researcher ran on the {DEMO_MODEL}. No AI model ran."
    assert DEMO_ANSWER.endswith("No AI model ran.")
    assert not OTHER_DEMO_NAMES.search(note), note


def test_the_browser_sidebar_says_the_demo_models_name_and_sentence():
    line = PanelTurns._route_notice({"reason": "mock", "provider": "mock", "model": "mock-1"})
    assert DEMO_MODEL in line and DEMO_REPLIES in line, line
    assert not OTHER_DEMO_NAMES.search(line), line
    # A failover line is untouched by this wave.
    assert PanelTurns._route_notice({"reason": "failover", "provider": "a", "model": "b", "requested": "c"}) == (
        "Answered by a/b — c was unreachable."
    )


def test_the_connections_registry_names_the_demo_like_the_model_menu(tmp_path):
    """Every reader of the registry's status() rows gets the spec's
    display_name, and the model menu says providerDisplay("mock") ==
    DEMO_MODEL_LABEL: one label. (GET /connections itself drops the demo
    row, as it has since v1.7.0; the provider id "mock" stays on the wire.)"""
    with TestClient(create_app(str(tmp_path))) as client:
        rows = client.app.state.platform.connections.status()
        shown = client.get("/connections").json()["connections"]
    demo = [r for r in rows if r.get("provider") == "mock"]
    assert len(demo) == 1, [r.get("provider") for r in rows]
    assert demo[0]["display_name"] == DEMO_MODEL_LABEL
    assert all(r.get("provider") != "mock" for r in shown)


def test_the_settings_sentences_name_the_demo_model():
    """The "can't be chosen with this button" / "You already chose …"
    sentences take their name from provider_label."""
    assert provider_label(None, "mock") == f"the {DEMO_MODEL}"


def test_a_refusal_that_needs_a_real_model_names_the_demo_model(tmp_path):
    """Writing-voice training on a fresh install: enough writing, but only
    the demo model to read it (the honest-mock rule refuses)."""
    with TestClient(create_app(str(tmp_path))) as client:
        text = "I write short, plain sentences and I sign off with thanks. " * (MIN_DERIVE_CHARS // 40 + 2)
        assert client.post("/profile/samples", json={"label": "mine", "text": text}).status_code == 200
        r = client.post("/profile/voice/derive")
    assert r.status_code == 400, r.text
    detail = r.json()["detail"]
    assert DEMO_MODEL in detail and not OTHER_DEMO_NAMES.search(detail), detail


def test_the_desktop_toast_and_the_bell_name_a_waiting_terminal_the_same_way():
    """v1.353.0 rule: the bell (lib/paneAsks.ts) and the Windows toast
    (desktop/main.js) say the same name for a Build terminal waiting on you
    with no CLI named."""
    asks = _read("dashboard/lib/paneAsks.ts")
    main = _read("desktop/main.js")
    subject = re.search(r'if \(!id\) return "([^"]+)";', asks)
    toast = re.search(
        r'function paneWaitingTitle\(cli\) \{.*?if \(!id\) return "([^"]+)";', main, re.S
    )
    assert subject and toast
    assert toast.group(1) == f"{subject.group(1)} is waiting for you"
    assert subject.group(1) == "A Build terminal"
    # The toast's body and the quit dialog say terminal and task too.
    assert "click to open the terminal." in main and "click to open the pane." not in main
    assert "Build terminals are closed, and background tasks are interrupted" in main


def test_the_coachs_no_model_reason_names_the_demo_model():
    """Agents > a custom agent > Coach > Ask the coach on the demo model
    shows this reason verbatim (AgentCoach renders ``reason``)."""
    from iron_jarvis.coach import engine as coach_engine

    reason = coach_engine.REASON_NO_MODEL
    assert DEMO_MODEL in reason and not OTHER_DEMO_NAMES.search(reason), reason
