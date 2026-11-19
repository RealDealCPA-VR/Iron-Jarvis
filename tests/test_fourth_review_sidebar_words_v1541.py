"""Fourth review, group 32 (BS3, BS4): smaller words in the sidebar and in a
screenshot's result.

BS3. Under Local only, a screenshot's question goes to a model that runs off
this network, so the REAL router refuses it (``egress.LocalOnlyRefused``,
before any attempt). ``ask_vision`` read that as a failed call: "The picture
was for anthropic (claude-vision); that call failed before an answer came
back" and "retry", although nothing was sent to anyone, and the refusal's
own words offered "allow X for this one message", a button no tool result
has. Now the result says the picture was not sent anywhere, names no
provider (in the words or in the tool's data), and says where to choose a
model for pictures. Driven through the real ``ModelRouter`` gate.

BS4. The sidebar's line for a failed step repeated the tool's error as the
model reads it: "PAGE_CHANGED: That tab was not the one on screen, so no
picture was taken. Call browser_screenshot again to bring it on screen."
Now the line drops the code and keeps only the tool's first sentence, for
every failed step; the model still reads the whole text.
"""

from __future__ import annotations

import base64
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from iron_jarvis.artifacts.store import ArtifactStore
from iron_jarvis.browser import protocol as P
from iron_jarvis.browser import screenshot as S
from iron_jarvis.browser.errors import BrowserError, BrowserErrorCode, remedy
from iron_jarvis.browser.panel import ANSWER_LOST_STEP, _step_failed_line
from iron_jarvis.core.events import EventBus
from iron_jarvis.core.turns import TURNS
from iron_jarvis.providers import egress
from iron_jarvis.providers.adapters.base import ToolCall
from iron_jarvis.providers.router import ModelRouter
from tests._fakes.panel_harness import RealApp
from tests.test_browser_sidepanel_context_v1270 import _flatten
from tests.test_browser_tab_grant_v1266 import _final, _look_at
from tests.test_local_only_chat_v1332 import LOCAL, _Ad, _Manager

REPO = Path(__file__).resolve().parents[1]
TABS_TS = REPO / "extensions" / "chrome" / "src" / "background" / "tabs.ts"
SETTINGS_SCHEMA = REPO / "src" / "iron_jarvis" / "settings" / "schema.py"
SETTINGS_HOME = REPO / "dashboard" / "components" / "settings" / "SettingsHome.tsx"

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\0" * 64


@pytest.fixture(autouse=True)
def _clean_registry():
    for tid in TURNS.running_ids():
        TURNS.release(tid)
    yield
    for tid in TURNS.running_ids():
        TURNS.release(tid)


# --------------------------------------------------------------------------- #
# BS3: under Local only the picture was not sent anywhere, and nobody is named
# --------------------------------------------------------------------------- #


def _capture() -> dict:
    return {
        "tab_id": 3,
        "media_type": "image/png",
        "data_b64": base64.b64encode(PNG_BYTES).decode("ascii"),
        "tab_title": "A report",
    }


def _real_router(adapters: dict, default: str) -> ModelRouter:
    """The REAL router and its Local only gate; only the providers are fakes."""
    return ModelRouter(
        _Manager(adapters), default_provider=default, event_bus=EventBus(),
        local_policy=lambda: "refuse",
    )


async def _shot(tmp_path: Path, router: ModelRouter, config: Any = None, *, local_only: bool):
    with egress.local_only_scope(local_only):
        return await S.capture_screenshot(
            _capture(),
            artifacts=ArtifactStore(tmp_path / "artifacts"),
            router_resolver=lambda: router,
            config=config,
            question="what is on this page?",
            tab_url="https://example.test/report",
        )


#: Words that must not appear when nothing was sent: who it "was for", a
#: retry of a call the user's own setting refused, and the chat page's
#: one-message button, which a tool result does not have.
_NOT_SAID = ("was for", "retry", "this one message", "failed before an answer")


def _assert_kept_here(out: Any, *names: str) -> None:
    summary = out.summary()
    low = summary.lower()
    for words in _NOT_SAID:
        assert words not in low, f"{words!r} in the result: {summary!r}"
    for name in names:
        assert name.lower() not in low, f"the result names {name!r}: {summary!r}"
    assert "the picture was not sent anywhere" in summary, summary
    assert summary.startswith("Saved a screenshot"), summary
    assert out.saved.path.read_bytes() == PNG_BYTES, "the file was not kept"
    data = out.to_dict()
    assert "provider" not in data and "model" not in data, data
    assert "answer" not in data, data
    for name in names:
        assert name.lower() not in str(data.get("answer_unavailable", "")).lower(), data
    assert out.vision.failed is False
    assert out.refusal == S.LOCAL_ONLY_NO_VISION


async def test_bs3_a_cloud_vision_role_is_refused_by_the_real_gate_and_names_nobody(tmp_path):
    cloud = _Ad("anthropic", model="claude-vision")
    local = _Ad("ollama", endpoint=LOCAL, vision=False)
    router = _real_router({"anthropic": cloud, "ollama": local}, "ollama")
    config = SimpleNamespace(model_roles={"vision": "anthropic:claude-vision"})

    out = await _shot(tmp_path, router, config, local_only=True)
    assert cloud.calls == 0 and local.calls == 0, "the gate let a call through"
    _assert_kept_here(out, "anthropic", "claude-vision", "Claude")

    # Anti-vacuity: outside Local only the same route reaches the model.
    out2 = await _shot(tmp_path, router, config, local_only=False)
    assert cloud.calls == 1 and out2.answered, out2.summary()
    assert out2.to_dict()["provider"] == "anthropic"


async def test_bs3_a_picture_the_local_default_cannot_see_names_nobody(tmp_path):
    """No vision role: the default is a text-only model on this PC, the one
    model that can see runs off the network, and the real router refuses to
    move the picture there (a ``LocalOnlyRefused`` naming that model)."""
    local = _Ad("ollama", endpoint=LOCAL, vision=False)
    cloud = _Ad("claude-cli")
    router = _real_router({"ollama": local, "claude-cli": cloud}, "ollama")

    out = await _shot(tmp_path, router, local_only=True)
    assert cloud.calls == 0 and local.calls == 0, "the gate let a call through"
    _assert_kept_here(out, "claude-cli", "Claude", "ollama")


async def test_bs3_a_real_failure_still_names_who_the_picture_was_for(tmp_path):
    """BR4's promise for a REAL failure is kept: only a Local only refusal
    loses the name."""
    boom = RuntimeError("upstream closed")
    cloud = _Ad("anthropic", model="claude-vision", fail=boom)
    router = _real_router({"anthropic": cloud}, "anthropic")
    config = SimpleNamespace(model_roles={"vision": "anthropic:claude-vision"})
    out = await _shot(tmp_path, router, config, local_only=False)
    assert cloud.calls >= 1
    assert "The picture was for anthropic" in out.summary(), out.summary()
    assert out.vision.failed is True


def test_bs3_the_settings_place_in_the_sentence_is_the_one_on_screen():
    """The sentence names the row as the Settings page draws it: the group
    "Models" (``schema.GROUPS``), the fold "More options" (``moreOptionsLabel``)
    and the row "Model roles" (``schema.SETTINGS``), whose key is the role."""
    schema = SETTINGS_SCHEMA.read_text(encoding="utf-8").replace("\r\n", "\n")
    assert '("models", "Models",' in schema
    assert 'S("model_roles", "Model roles", "models", "dict",' in schema
    assert "advanced=True" in schema[schema.index('S("model_roles"'):][:300]
    home = SETTINGS_HOME.read_text(encoding="utf-8").replace("\r\n", "\n")
    assert "`More options (${count})`" in home
    from iron_jarvis.providers.roles import KNOWN_ROLES

    assert "vision" in KNOWN_ROLES
    words = S.LOCAL_ONLY_NO_VISION
    assert "Settings › Models › More options › Model roles" in words, words
    assert '"vision"' in words, words


# --------------------------------------------------------------------------- #
# BS4: the step line drops the code and keeps the tool's first sentence
# --------------------------------------------------------------------------- #

_FMT = {
    "tab_id": 7,
    "element_id": "e3",
    "snapshot_id": "s1",
    "url": "https://example.test/report",
    "scheme": "chrome://",
    "detail": "the add-on stopped",
}


@pytest.mark.parametrize("code", list(BrowserErrorCode), ids=lambda c: c.value)
def test_bs4_every_codes_step_line_has_no_code_and_no_next_step(code):
    model_text = f"{code.value}: {remedy(code, **_FMT)}"
    line = _step_failed_line("click 'Next'", model_text)
    assert line.startswith("Could not click 'Next'. "), line
    assert code.value not in line, line
    assert "browser_" not in line, line
    assert "Ask the user" not in line and "retry" not in line.lower(), line
    # Exactly the tool's first sentence, after "Could not click 'Next'.".
    reason = line[len("Could not click 'Next'. "):]
    assert model_text[len(code.value) + 2:].startswith(reason), (reason, model_text)
    assert reason.endswith((".", "…")), reason


def test_bs4_the_lost_answer_line_and_a_plain_reason_are_unchanged():
    from iron_jarvis.browser.errors import UNKNOWN_OUTCOME_MESSAGE

    lost = f"ACTION_TIMEOUT: {UNKNOWN_OUTCOME_MESSAGE}"
    assert _step_failed_line("click 'Next'", lost) == ANSWER_LOST_STEP.format(what="click 'Next'")
    assert (
        _step_failed_line("click 'Next'", "the page refused: that element is gone")
        == "Could not click 'Next'. the page refused: that element is gone"
    )
    assert _step_failed_line("click 'Next'", "") == "Could not click 'Next'."
    assert _step_failed_line("click 'Next'", None) == "Could not click 'Next'."


def _addon_page_changed_sentence() -> str:
    """The add-on's own PAGE_CHANGED sentence, read out of ``tabs.ts``."""
    src = TABS_TS.read_text(encoding="utf-8").replace("\r\n", "\n")
    body = src[src.index("function notOnScreen("):]
    body = body[: body.index("\n}\n")]
    parts = [p for p in body.split('"')[1::2]]
    sentence = "".join(parts[1:])  # parts[0] is the code "PAGE_CHANGED"
    assert parts[0] == "PAGE_CHANGED" and "no picture was taken" in sentence, parts
    return sentence


def test_bs4_a_failed_screenshot_in_the_real_sidebar_says_one_plain_sentence(tmp_path, monkeypatch):
    """Through the real app, the real chat lane and the real panel: the
    screenshot tool's real refusal (``_refusal_from`` of the BrowserError the
    real ``ExtensionBackend._resolve_response`` builds from the add-on's
    frame). The step line is plain; the model reads the whole text."""
    import asyncio

    from iron_jarvis.browser.extension_backend import ExtensionBackend

    sentence = _addon_page_changed_sentence()

    async def _from_the_addon() -> BrowserError:
        fut = asyncio.get_running_loop().create_future()
        conn = SimpleNamespace(pending={"r1": fut})
        ExtensionBackend()._resolve_response(
            conn, {"id": "r1", "success": False, "error": {"code": "PAGE_CHANGED", "message": sentence}}
        )
        try:
            await fut
        except BrowserError as exc:
            return exc
        raise AssertionError("the add-on's error frame did not raise")

    exc = asyncio.run(_from_the_addon())

    with RealApp(tmp_path, monkeypatch, access="interactive") as app:
        app.pair()
        _look_at(app, 7)
        tool = app.platform.registry.get("browser_screenshot")
        assert tool is not None
        refusal = tool._refusal_from(exc)
        model_text = refusal.output
        assert model_text.startswith("PAGE_CHANGED: ") and "browser_screenshot" in model_text

        async def fake_execute(args, ctx=None, *a, **kw):
            return refusal

        tool.execute = fake_execute  # type: ignore[method-assign]
        app.platform.browser.set_auto_allow(True)

        seen: list[list[dict[str, str]]] = []
        rounds: list = [[ToolCall(id="s1", name="browser_screenshot", arguments={})], "Done."]

        async def fake_stream(*args: Any, **kw: Any):
            seen.append(_flatten(kw.get("messages") or []))
            n = len(seen) - 1
            step = rounds[n] if n < len(rounds) else "All done."
            if isinstance(step, str):
                yield {"type": "text", "text": step}
                yield _final(step)
            else:
                yield _final("", step)

        app.platform.router.stream = fake_stream
        app.panel.send(P.PANEL_ACTION_SEND, text="take a picture of this page")
        app.panel.wait_for(P.PANEL_EVENT_DONE, "the turn never finished")

        finished = [
            p for e, p in app.panel.events()
            if e == P.PANEL_EVENT_TOOL and p.get("name") == "browser_screenshot" and p.get("status") == "finished"
        ]
        assert finished and finished[0]["ok"] is False, finished
        assert finished[0]["text"] == (
            "Could not take a screenshot of the page. "
            "That tab was not the one on screen, so no picture was taken."
        ), finished[0]["text"]
        # The model's own text is unchanged: the code and the next step.
        later = " ".join(row["content"] for row in seen[-1])
        assert "PAGE_CHANGED" in later and "Call browser_screenshot again" in later, seen[-1]
