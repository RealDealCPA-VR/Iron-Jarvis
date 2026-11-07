"""v1.443.0: Antigravity's own model list (and whether it is signed in), read
in the background BEFORE any Antigravity session ran
(``hosted.antigravity_catalog``).

MEASURED (agy 1.5.3, a scratch home, no model call): ``agy --log-file F
models`` prints ``<id>\\t<name>`` lines; an ACP session's ``configOptions.model``
groups the same ids (``gemini-3.8-flash-{high,medium,low}`` -> one model with
three levels; ``gpt-oss-120b-medium`` stays whole). Its sign-in lives in
Windows' credential store, so the read runs only once Antigravity's own
onboarding file says a sign-in finished. ``AGY_CLI_DISABLE_AUTO_UPDATE=true``
keeps its updater from starting inside the reading process's Job. The recorded
answer is ``fixtures/build_app_v14430/agy_models.txt``; the agy here is
``fixtures/fake_agy_models_v14430.py`` replaying it — never the real one.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from iron_jarvis.daemon.app import create_app
from iron_jarvis.hosted import antigravity as A
from iron_jarvis.hosted import antigravity_catalog as ACC
from iron_jarvis.hosted import service as S
from iron_jarvis.hosted import words as W
from tests._build_app_helpers_v1409 import client, starts, until

FIX = Path(__file__).parent / "fixtures"
RECORDED = FIX / "build_app_v14430" / "agy_models.txt"
FAKE = FIX / "fake_agy_models_v14430.py"


@pytest.fixture()
def fresh(monkeypatch, tmp_path):
    monkeypatch.setattr(A, "LAST_MODELS", [])
    monkeypatch.setattr(A, "LAST_DEFAULT", [])
    monkeypatch.setattr(ACC, "_FAILED_AT", {})
    monkeypatch.setattr(ACC, "AUTO_PROBE", True)
    agy_home = tmp_path / "agy-home"
    agy_home.mkdir()
    monkeypatch.setenv(A.HOME_ENV, str(agy_home))
    log = tmp_path / "fake-agy.log"
    monkeypatch.setattr(S, "binary_for", lambda harness: [sys.executable, str(FAKE)])
    monkeypatch.setenv("IJ_FAKE_AGY_MODELS", str(RECORDED))
    monkeypatch.setenv("IJ_FAKE_LOG", str(log))
    monkeypatch.setenv("IJ_FAKE_AGY_MODE", "ok")
    return agy_home, log


def signed_in_once(agy_home: Path) -> None:
    """Antigravity's own file after the user signed in through ``agy``."""
    (agy_home / "cache").mkdir(parents=True, exist_ok=True)
    (agy_home / "cache" / "onboarding.json").write_text(json.dumps(
        {"consumerOnboardingComplete": True, "enterpriseOnboardingComplete": False, "onboardingComplete": True}),
        encoding="utf-8")


def runs(log: Path) -> list[dict]:
    if not log.exists():
        return []
    return [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines() if x.strip()]


def agy_row(rows: list[dict]) -> dict:
    return {r["id"]: r for r in rows}["antigravity"]


def read_path(app) -> Path:
    return S.service_for(app.state.platform)._agy_read


def test_the_recorded_answer_groups_the_way_antigravitys_own_menu_does():
    rows = ACC.parse_models(RECORDED.read_text(encoding="utf-8"))
    # The same set an ACP session's configOptions.model offered (measured).
    assert rows == [
        {"id": "gemini-3.8-flash", "label": "Gemini 3.8 Flash", "efforts": ["low", "medium", "high"]},
        {"id": "claude-sonnet-4-6", "label": "Claude Sonnet 4.6 (Thinking)", "efforts": []},
        {"id": "claude-opus-4-6-thinking", "label": "Claude Opus 4.6 (Thinking)", "efforts": []},
        {"id": "gpt-oss-120b-medium", "label": "GPT-OSS 120B (Medium)", "efforts": []},
    ]
    assert ACC.parse_models("Fetching available models...\nError: nope\n") == []
    assert ACC.parse_models("") == []


async def test_a_signed_in_antigravity_lists_its_models_before_any_session(tmp_path, monkeypatch, fresh):
    agy_home, log = fresh
    signed_in_once(agy_home)
    app = create_app(str(tmp_path / "home"))
    path = read_path(app)
    async with client(app) as c:
        await c.get("/build/app/harnesses")
        await until(lambda: path.exists() and not ACC.reading(path))
        row = agy_row((await c.get("/build/app/harnesses")).json()["harnesses"])
    ids = [m["id"] for m in row["models"]]
    assert ids == ["", "gemini-3.8-flash", "claude-sonnet-4-6", "claude-opus-4-6-thinking", "gpt-oss-120b-medium"]
    flash = next(m for m in row["models"] if m["id"] == "gemini-3.8-flash")
    assert flash["efforts"] == ["low", "medium", "high"]
    assert row["models"][0]["efforts"] == ["low", "medium", "high"], "a new session starts on the first model listed"
    assert row["signed_in"] is True and row["signin_finished"] is True
    assert "models_note" not in row and "models_reading" not in row
    (run,) = runs(log)
    assert run["argv"][0] == "--log-file" and run["argv"][-1] == "models"
    home = S.service_for(app.state.platform).home
    assert run["argv"][1] == str(home / "hosted" / "agy-probe.log"), "its log is ours, not ~/.gemini"
    assert run["cwd"] == str(home / "hosted" / "agy-probe"), "an empty folder of our own"
    assert run["no_update"] == "true", "its updater never starts inside the reading process's Job"


async def test_before_antigravitys_own_sign_in_nothing_runs_and_it_reads_not_signed_in(tmp_path, monkeypatch, fresh):
    agy_home, log = fresh  # no onboarding file: the user never signed in
    app = create_app(str(tmp_path / "home"))
    async with client(app) as c:
        row = agy_row((await c.get("/build/app/harnesses")).json()["harnesses"])
        again = agy_row((await c.get("/build/app/harnesses?recheck=antigravity")).json()["harnesses"])
    assert runs(log) == [], "agy is never run before its own sign-in finished (a sign-in prompt is possible)"
    # The onboarding file is a hint: the Code view is never greyed out by it
    # (signed_in stays "not known"); Connections reads signin_finished.
    assert row["signed_in"] is None and again["signed_in"] is None
    assert row["signin_finished"] is False
    assert row["models_note"] == W.ANTIGRAVITY_NOT_SIGNED_IN
    assert [m["id"] for m in row["models"]] == [""]


def test_a_sign_in_refusal_reads_not_signed_in_and_its_words_are_masked(tmp_path, monkeypatch, fresh):
    agy_home, log = fresh
    signed_in_once(agy_home)
    monkeypatch.setenv("IJ_FAKE_AGY_MODE", "signin")
    path = tmp_path / "home" / "hosted" / "antigravity_read.json"
    path.parent.mkdir(parents=True)
    assert ACC.read_now(path, tmp_path / "home", [sys.executable, str(FAKE)], agy_home) is False
    doc = ACC.read_doc(path)
    assert doc["signed_in"] is False
    kept = json.dumps(doc)
    assert "SECRETSECRETSECRET" not in kept, "a secret in its words is masked before it is kept"
    assert "GOOGLEKEYPIECES" not in kept, "a Google-shaped key too (the failure mask's own shapes)"
    assert "not logged in" in doc["said"]
    assert "Fetching" not in doc["said"]
    assert ACC.sign_in_state(path, agy_home) is False


async def test_another_failure_says_why_and_is_not_read_again_at_once(tmp_path, monkeypatch, fresh):
    agy_home, log = fresh
    signed_in_once(agy_home)
    monkeypatch.setenv("IJ_FAKE_AGY_MODE", "other")
    app = create_app(str(tmp_path / "home"))
    path = read_path(app)
    async with client(app) as c:
        await c.get("/build/app/harnesses")
        await until(lambda: path.exists() and not ACC.reading(path))
        row = agy_row((await c.get("/build/app/harnesses")).json()["harnesses"])
        assert len(runs(log)) == 1, "a failed read waits before the next"
        assert row["signed_in"] is None, "not known: the failure did not say sign-in"
        assert row["models_note"].startswith("Antigravity did not list its models: ") and "503" in row["models_note"]
        # Connections' "Check again" reads again at once.
        monkeypatch.setenv("IJ_FAKE_AGY_MODE", "ok")
        await c.get("/build/app/harnesses?recheck=antigravity")
        await until(lambda: len(runs(log)) == 2 and not ACC.reading(path))
        row = agy_row((await c.get("/build/app/harnesses")).json()["harnesses"])
    assert row["signed_in"] is True and len(row["models"]) == 5


def test_a_running_sessions_own_list_wins_and_the_read_fills_in():
    read = ACC.parse_models(RECORDED.read_text(encoding="utf-8"))
    live = [{"id": "gemini-3.8-flash", "label": "Gemini 3.8 Flash", "efforts": ["low", "high"]},
            {"id": "gemini-3.1-pro", "label": "Gemini 3.1 Pro", "efforts": ["low", "high"]}]
    got = ACC.merged(read, live)
    assert [r["id"] for r in got] == ["gemini-3.8-flash", "claude-sonnet-4-6", "claude-opus-4-6-thinking",
                                      "gpt-oss-120b-medium", "gemini-3.1-pro"]
    assert got[0]["efforts"] == ["low", "high"], "a session's live answer wins"


def test_the_suite_never_runs_the_real_agy(tmp_path):
    assert ACC.AUTO_PROBE is False, "the suite turns it off (conftest)"
    assert ACC.refresh_soon(tmp_path / "r.json", tmp_path, [sys.executable, str(FAKE)], tmp_path) is False


async def test_an_antigravity_session_starts_with_its_updater_off(tmp_path, monkeypatch):
    from tests.test_build_app_antigravity_v1439 import run_agy

    evs, sid, work, log, view, app = await run_agy(tmp_path, monkeypatch, "antigravity_replies")
    assert starts(log)[0]["env"]["AGY_CLI_DISABLE_AUTO_UPDATE"] == "true"
