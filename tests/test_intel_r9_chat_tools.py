"""The intelligence plan, release 9: chat reaches it.

"What is this model good at?" answers with the same plain lines the Model
servers card shows (each said only when measured); "Measure my model."
presses the same Measure button. Both stay on the user's own models: a cloud
model is never measured. Through the real app (create_app); the model server
is never called (Measure's start is the only thing pressed, and here it is
the route's own refusal path or a recorded press).
"""

from __future__ import annotations

import asyncio

from iron_jarvis.daemon.app import create_app
from iron_jarvis.envelope.outcomes import STUMBLE_RUN
from iron_jarvis.envelope.plain import GUIDED_CARD_LINE, plain_lines
from iron_jarvis.envelope.profile import CURRENT_PROBE_GENERATION, CapabilityProfile
from iron_jarvis.envelope.store import save_profile
from iron_jarvis.settings import records_models as RM

MODEL = "qwen3:8b"


def _measured(**kw) -> CapabilityProfile:
    base = dict(
        model_id=MODEL, provider="ollama", source="probed", probed_at="2026-11-27T03:00:00+00:00",
        tool_protocols={"native": 1.0, "strict_json": 1.0}, probe_generation=CURRENT_PROBE_GENERATION,
        holds_chars=36000, holds_more=0, words_per_second=24.6,
        measured_fields=["tool_protocols.native", "tool_protocols.strict_json", "holds_chars",
                         "holds_more", "words_per_second"],
    )
    base.update(kw)
    return CapabilityProfile(**base)


def _app(tmp_path):
    app = create_app(str(tmp_path / "home"))
    platform = app.state.platform
    # A port nothing listens on: nothing is ever called there.
    platform.config.ollama_base_url = "http://127.0.0.1:9"
    platform.providers.configure_local(ollama_base_url="http://127.0.0.1:9", ollama_model=MODEL)
    return app


def test_the_words_that_bring_them():
    assert RM.model_tools_for("What is this model good at?") == ["model_report"]
    assert RM.model_tools_for("Measure my model.") == ["model_report", "model_measure"]
    assert RM.model_tools_for("how fast does qwen3:8b write?") == ["model_report"]
    assert RM.model_tools_for("measure the kitchen wall") == []
    assert RM.model_tools_for("which model fits this PC") == []


def test_the_python_lines_are_the_cards_lines():
    assert plain_lines(_measured()) == [
        "Uses tools well.", "Holds about 12 pages at once.", "Writes about 25 words a second.",
    ]
    assert plain_lines(_measured(holds_chars=98304, holds_more=1))[1] == "Holds at least 32 pages at once."
    assert plain_lines(_measured(measured_fields=[])) == []
    assert plain_lines(_measured(), guided=True)[0] == GUIDED_CARD_LINE


def test_what_is_this_model_good_at_reads_the_measurement(tmp_path):
    app = _app(tmp_path)
    platform = app.state.platform
    save_profile(platform.config.home, _measured())
    out = asyncio.run(RM.ModelReportTool(platform).execute({"model": MODEL}, None))
    assert out.ok, out.error
    assert f"{MODEL}: Uses tools well. Holds about 12 pages at once. Writes about 25 words a second." in out.output
    assert out.data["door"] == "/fleet"


def test_a_model_not_measured_yet_says_so_and_a_guided_one_says_why(tmp_path):
    app = _app(tmp_path)
    platform = app.state.platform
    out = asyncio.run(RM.ModelReportTool(platform).execute({"model": MODEL}, None))
    assert out.ok and f"{MODEL}: not measured yet." in out.output
    for _ in range(STUMBLE_RUN):
        platform.providers.note_reply_form("ollama", MODEL, "native", False)
    out = asyncio.run(RM.ModelReportTool(platform).execute({}, None))
    assert GUIDED_CARD_LINE in out.output


def test_a_cloud_model_is_never_one_of_yours(tmp_path):
    app = _app(tmp_path)
    out = asyncio.run(RM.ModelReportTool(app.state.platform).execute({"model": "gpt-5"}, None))
    assert not out.ok and "not one of your own models" in out.error
    out = asyncio.run(RM.ModelMeasureTool(app.state.platform).execute({"model": "gpt-5"}, None))
    assert not out.ok and "never a cloud model" in out.error


def test_measure_my_model_presses_the_same_button(tmp_path):
    app = _app(tmp_path)
    d = app.state.d
    pressed: list = []

    async def press(provider, model):
        pressed.append((provider, model))
        return {"started": True}

    d.start_measure = press
    out = asyncio.run(RM.ModelMeasureTool(app.state.platform).execute({"model": MODEL}, None))
    assert out.ok, out.error
    assert pressed == [("ollama", MODEL)]
    assert "not finished yet" in out.output and "made-up text only" in out.output


def test_measure_says_the_buttons_own_refusal(tmp_path):
    app = _app(tmp_path)
    from fastapi import HTTPException

    async def busy(provider, model):
        raise HTTPException(status_code=409, detail="a probe for this model is already running")

    app.state.d.start_measure = busy
    out = asyncio.run(RM.ModelMeasureTool(app.state.platform).execute({"model": MODEL}, None))
    assert not out.ok and "already running" in out.error


def test_the_route_exposes_its_measure_press(tmp_path):
    app = _app(tmp_path)
    assert callable(app.state.d.start_measure)
    assert app.state.d.auto_trials is not None


# --------------------------------------------------------------------------- #
# Through both chat lanes, with the app's DEFAULT switches (the lookup on)
# --------------------------------------------------------------------------- #

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.core import config as _config
from iron_jarvis.providers.adapters.base import LLMResponse
from iron_jarvis.providers.adapters.mock import MockLLMAdapter


@pytest.mark.parametrize("lane", ("/chat", "/chat/stream"))
@pytest.mark.parametrize(
    ("words", "want"),
    [("What is this model good at?", {"model_report"}),
     ("Measure my model.", {"model_report", "model_measure"})],
)
def test_chat_reaches_it_on_both_lanes(tmp_path, monkeypatch, lane, words, want):
    monkeypatch.setattr(_config, "LOOKUP_ON_BY_DEFAULT", True)
    app = create_app(str(tmp_path / "home"))
    offered: list[set] = []

    class _Seeing(MockLLMAdapter):
        async def complete(self, *, system, messages, tools, **kw):
            names = set()
            for t in tools or []:
                names.add(str(t.get("name") or (t.get("function") or {}).get("name") or ""))
            offered.append(names)
            return LLMResponse(text="Here is what I know.")

    app.state.platform.providers.register("mock", lambda model=None: _Seeing())
    body = {"messages": [{"role": "user", "content": words}], "thread_id": f"t-{lane}", "auto_tools": True}
    r = TestClient(app).post(lane, json=body)
    assert r.status_code == 200, r.text
    if lane == "/chat":
        # The POST lane (the phone, API callers) arms only what runs with no
        # card and only reads: the report, never the measure press.
        want = want - {"model_measure"}
    assert offered and any(want <= names for names in offered), [sorted(n) for n in offered]
