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
from types import SimpleNamespace

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
    assert out.ok and f"{MODEL}: not measured yet; {RM.PRESS_MEASURE}" in out.output
    assert "tried by itself" not in out.output  # never used here: nothing waits in line
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
    assert not out.ok and out.error == f"{MODEL} is being measured already; Model servers shows the result when it finishes."
    assert "envelope" not in out.error and "probe" not in out.error


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


def test_the_report_says_the_true_next_step_per_model(tmp_path, monkeypatch):
    from iron_jarvis.daemon.routes import envelope as E
    from iron_jarvis.envelope import autotrial as T

    app = _app(tmp_path)
    trials = app.state.d.auto_trials
    trials.note_used("ollama", MODEL)
    report = RM.ModelReportTool(app.state.platform)
    monkeypatch.setattr("sys.platform", "win32")
    verdict = {"why": ""}
    monkeypatch.setattr(E, "trial_refusal", lambda d, p, m: verdict["why"])
    out = asyncio.run(report.execute({"model": MODEL}, None))
    assert f"not measured yet; {RM.WILL_BE_TRIED}" in out.output
    # The trial's own check would refuse it, or leave it waiting (the
    # re-check's probe: a proxy holding cloud entries): no promise.
    for why in (E.NEVER_FORWARDED, T.WAIT):
        verdict["why"] = why
        assert RM.PRESS_MEASURE in asyncio.run(report.execute({"model": MODEL}, None)).output
    verdict["why"] = ""
    trials._set("ollama", MODEL, T.LEFT, "cut short 3 times")
    assert RM.PRESS_MEASURE in asyncio.run(report.execute({"model": MODEL}, None)).output
    # Linux and Mac never try a model unasked.
    trials._set("ollama", MODEL, T.PENDING, "")
    monkeypatch.setattr("sys.platform", "linux")
    assert RM.PRESS_MEASURE in asyncio.run(report.execute({"model": MODEL}, None)).output


def test_no_promise_for_a_model_behind_a_proxy_that_holds_cloud_entries(tmp_path, monkeypatch):
    """The re-check's own case: "fleet" on the owner's proxy, queued, Windows."""
    app = _app(tmp_path)
    d = app.state.d
    d.fleet = SimpleNamespace(table_holds_cloud=lambda p: True, answers_itself=lambda p: True, revision=1)
    monkeypatch.setattr(app.state.platform.providers, "upstream_place", lambda p, m: "local")
    d.auto_trials.note_used("ollama", "fleet")
    monkeypatch.setattr("sys.platform", "win32")
    assert RM._next_words(d, "ollama", "fleet") == RM.PRESS_MEASURE


def test_the_measure_button_stays_for_a_model_not_proven_local(tmp_path, monkeypatch):
    """The re-check's decision: a public address (or a name a proxy may pass
    to the cloud) is still measured by the user's own press — with the
    battery Measure ran before release 9, the tool trials only."""
    from fastapi.testclient import TestClient

    from iron_jarvis.daemon.routes import envelope as E
    from iron_jarvis.envelope.probes import ProbeReply

    app = create_app(str(tmp_path / "home"))
    platform = app.state.platform
    platform.config.custom_base_url = "https://pay-per-use.example.test/v1"
    platform.providers.configure_local(custom_base_url="https://pay-per-use.example.test/v1", custom_model="big")
    started: list = []

    async def no_network(*a, **k):  # the background run never reaches a server here
        started.append(k.get("sized"))

    monkeypatch.setattr(E, "run_trial", no_network)

    async def no_seed(*a, **k):
        return None

    monkeypatch.setattr(E, "seed_profile", no_seed)
    r = TestClient(app).post("/envelope/custom/big/probe")
    assert r.status_code == 200 and r.json()["started"] is True
    assert E.measure_refusal(app.state.d, "custom", "big") == E.SIZED_ONLY_LOCAL_WORDS
    got = TestClient(app).get("/envelope/custom/big").json()
    assert got["sized"] is False

    async def press_and_finish():
        await app.state.d.start_measure("custom", "big")
        here = asyncio.current_task()
        await asyncio.gather(*[t for t in asyncio.all_tasks() if t is not here])

    started.clear()
    asyncio.run(press_and_finish())
    assert started == [False]  # the press ran, without the size and speed trials

    # What such a press sends: the tool trials only, never a long made-up text.
    sent: list = []

    class _Adapter:
        async def complete(self, *, system, messages, tools, **kw):
            from iron_jarvis.providers.adapters.base import LLMResponse

            sent.append(" ".join(m.content for m in messages))
            return LLMResponse(text="ok")

    from iron_jarvis.envelope.profile import CapabilityProfile

    monkeypatch.undo()
    base = CapabilityProfile(model_id="big", provider="custom")
    asyncio.run(E.run_trial(app.state.d, base, _Adapter(), "custom", "big", tmp_path, sized=False))
    assert sent and not any("code word" in t or "lighthouse" in t for t in sent)
    assert max(len(t) for t in sent) < 8000
    del ProbeReply


def test_chat_measure_says_size_and_speed_are_local_only(tmp_path):
    from iron_jarvis.daemon.routes import envelope as E

    app = create_app(str(tmp_path / "home"))
    platform = app.state.platform
    platform.config.custom_base_url = "https://pay-per-use.example.test/v1"
    platform.providers.configure_local(custom_base_url="https://pay-per-use.example.test/v1", custom_model="big")

    async def press(provider, model):
        return {"started": True}

    app.state.d.start_measure = press
    out = asyncio.run(RM.ModelMeasureTool(platform).execute({"model": "big"}, None))
    assert out.ok and E.SIZED_ONLY_LOCAL_WORDS in out.output
    assert "on your own model server" not in out.output


def test_the_cards_words_are_the_daemons_words():
    import re as _re
    from pathlib import Path as _P

    from iron_jarvis.daemon.routes import envelope as E

    card = (_P(__file__).resolve().parents[1] / "dashboard/components/connections/EnvelopeCard.tsx").read_text(
        encoding="utf-8").replace(chr(13) + chr(10), chr(10))
    m = _re.search(r'SIZED_ONLY_LOCAL_LINE =\s*((?:"[^"]*"\s*\+?\s*)+);', card)
    assert "".join(_re.findall(r'"([^"]*)"', m.group(1))) == E.SIZED_ONLY_LOCAL_WORDS
