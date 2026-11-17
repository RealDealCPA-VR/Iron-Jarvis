"""Third north-star fix plan, group 21 ("done means done, every cost counted"):
BR2, BR4, BR5 (the browser screenshot) and TM13 (a terminal's idle reading).

BR2. In a run kept on one model (a coding session's turn, a model team
member's run: ``providers.hold``) whose model cannot take pictures,
``browser_screenshot`` with a question sent the picture to that model anyway,
and whatever text it wrote came back as the page's "description". The router
does not move a held picture to a model that can see (v1.372.0), so the
screenshot now asks the same question ``view_image`` asks first
(``providers.hold.held_model_cannot_see``) and says plainly that this model
cannot look at pictures, what was kept, and what still works. Proven through
the REAL router with a stand-in adapter marked text-only.

BR4. The tool promised "the result names the provider that saw it" and the
result never did. The result now names the provider and model on a
description, on an empty answer and on a failed call; and the vision call's
token use rides the tool's data (it is on no usage row: see the report).

BR5. ``captureVisibleTab`` photographs whichever tab is on screen in the
window. The add-on now checks right before and right after each capture that
the tab asked for is still the one on screen, and otherwise answers an error
whose own sentence the daemon passes on verbatim. No JS test runner exists in
``extensions/chrome``: source pins, not a run in a browser.

TM13. npm (and pnpm) echo a script before running it (``> app@1.0.0 test`` /
``> vitest run``). The pane classifier read that echo as an agent's idle
prompt, so ``pane_wait`` said a pane was idle while the script had just
started.
"""

from __future__ import annotations

import asyncio
import base64
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from iron_jarvis.artifacts.store import ArtifactStore
from iron_jarvis.browser import screenshot as S
from iron_jarvis.browser.errors import BrowserError
from iron_jarvis.core.events import EventBus
from iron_jarvis.providers import hold as HOLD
from iron_jarvis.providers.adapters.base import LLMAdapter, LLMResponse
from iron_jarvis.providers.router import ModelRouter
from iron_jarvis.terminals.agent_state import AgentState, classify

REPO = Path(__file__).resolve().parents[1]
TABS_TS = REPO / "extensions" / "chrome" / "src" / "background" / "tabs.ts"

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\0" * 64


def _capture() -> dict:
    return {
        "tab_id": 3,
        "media_type": "image/png",
        "data_b64": base64.b64encode(PNG_BYTES).decode("ascii"),
        "tab_title": "Sign in",
    }


# --------------------------------------------------------------------------- #
# BR2: a held run whose model cannot see never "describes" a screenshot
# --------------------------------------------------------------------------- #


class _Adapter(LLMAdapter):
    """A stand-in provider. ``vision`` is what it REPORTS; a text-only one
    still answers in words, the way a real text model handed a picture it
    cannot take does."""

    def __init__(self, provider: str, model: str, *, vision: bool, text: str) -> None:
        self.provider, self.model = provider, model
        self._vision = vision
        self.text = text
        self.pictures_seen: list[int] = []

    def capabilities(self):
        return {"tool_use": True, "vision": self._vision}

    async def complete(self, *, system, messages, tools, **kw):
        self.pictures_seen.append(sum(len(getattr(m, "images", None) or []) for m in messages))
        return LLMResponse(
            text=self.text, tool_calls=[], usage={"input_tokens": 1200, "output_tokens": 40}
        )

    async def stream(self, *, system, messages, tools, **kw):  # pragma: no cover
        yield {"type": "final", "text": self.text, "tool_calls": [], "usage": {}}


class _Manager:
    def __init__(self, adapters):
        self.adapters = adapters

    def available(self, provider):
        return provider in self.adapters

    def has_available_api_provider(self):
        return True

    def get(self, provider, model=None):
        return self.adapters[provider]


def _router(**adapters) -> ModelRouter:
    return ModelRouter(_Manager(adapters), "anthropic", EventBus(), strict_pin=lambda: False)


async def test_br2_a_held_model_that_cannot_see_never_describes_the_screenshot(tmp_path):
    """Through the REAL router: the run is kept on a text-only model, the
    router binds the call to it and (held) never moves the picture. Before
    the fix the text-only model's words came back as the description."""
    blind = _Adapter("grok-cli", "grok", vision=False, text="A sign-in page with two fields.")
    seeing = _Adapter("anthropic", "claude-x", vision=True, text="seen")
    router = _router(**{"grok-cli": blind, "anthropic": seeing})
    store = ArtifactStore(tmp_path / "artifacts")
    token = HOLD.set_hold("grok-cli", "grok", who="member:reviewer")
    try:
        out = await S.capture_screenshot(
            _capture(),
            artifacts=store,
            router_resolver=lambda: router,
            question="What is on this page?",
            tab_url="https://example.test/login",
        )
    finally:
        HOLD.reset_hold(token)

    assert out.answered is False, f"a model that cannot see described the page: {out.answer!r}"
    assert blind.pictures_seen == [], "the picture was sent to a model that cannot take it"
    assert seeing.pictures_seen == [], "the picture left the model the run is kept on"
    assert "cannot look at pictures" in out.refusal
    assert "grok" in out.refusal, "the refusal names the model"
    # What still works: the file is kept, and the page's text can be read.
    assert out.saved.path.read_bytes() == PNG_BYTES
    assert "browser_read_page" in out.refusal
    summary = out.summary()
    assert summary.startswith("Saved a screenshot")
    assert "No description:" in summary
    assert "answer" not in out.to_dict()


async def test_br2_a_held_model_that_can_see_is_asked_not_the_vision_role(tmp_path):
    """The other half of ``view_image``'s rule: in a held run the picture goes
    to the run's own model. Before the fix the vision role named another
    provider, the router refused it, and the screenshot was never described."""
    held = _Adapter("openai", "gpt-x", vision=True, text="A form with an email field.")
    other = _Adapter("anthropic", "claude-vision", vision=True, text="from the role")
    router = _router(openai=held, anthropic=other)
    store = ArtifactStore(tmp_path / "artifacts")
    config = SimpleNamespace(model_roles={"vision": "anthropic:claude-vision"})
    token = HOLD.set_hold("openai", "gpt-x")
    try:
        out = await S.capture_screenshot(
            _capture(),
            artifacts=store,
            router_resolver=lambda: router,
            config=config,
            question="describe it",
            tab_url="https://example.test/",
        )
    finally:
        HOLD.reset_hold(token)
    assert out.refusal == "", out.refusal
    assert out.answer == "A form with an email field."
    assert held.pictures_seen == [1] and other.pictures_seen == []


# --------------------------------------------------------------------------- #
# BR4: the result names who was sent the picture, and counts its tokens
# --------------------------------------------------------------------------- #


class _Router:
    """A route result shaped like the router's; ``text`` may be empty."""

    def __init__(self, text: str = "A page with a chart.", *, raises: Exception | None = None):
        self.text = text
        self.raises = raises
        self.default_provider = "ollama"
        self.calls: list[dict] = []

    async def complete(self, **kw):
        self.calls.append(kw)
        if self.raises is not None:
            raise self.raises
        return SimpleNamespace(
            response=SimpleNamespace(text=self.text, usage={"input_tokens": 900, "output_tokens": 30}),
            provider="gemini",
            model="gemini-vision",
        )


async def _shot(tmp_path, router, config=None):
    return await S.capture_screenshot(
        _capture(),
        artifacts=ArtifactStore(tmp_path / "artifacts"),
        router_resolver=lambda: router,
        config=config,
        question="what is this?",
        tab_url="https://example.test/",
    )


async def test_br4_a_description_names_the_provider_and_model_it_was_sent_to(tmp_path):
    out = await _shot(tmp_path, _Router())
    summary = out.summary()
    assert "gemini" in summary and "gemini-vision" in summary, summary
    assert out.to_dict()["provider"] == "gemini"


async def test_br4_an_empty_answer_still_names_who_was_sent_the_picture(tmp_path):
    out = await _shot(tmp_path, _Router(text="  "))
    assert out.refusal == S.NO_VISION_ANSWER
    summary = out.summary()
    assert "gemini" in summary and "gemini-vision" in summary, summary
    data = out.to_dict()
    assert data["provider"] == "gemini" and data["model"] == "gemini-vision"


async def test_br4_a_failed_call_names_the_provider_it_was_for(tmp_path):
    class _Manager2:
        def available(self, provider):
            return True

    router = _Router(raises=RuntimeError("upstream closed"))
    router.manager = _Manager2()  # type: ignore[attr-defined]
    config = SimpleNamespace(model_roles={"vision": "anthropic:claude-vision"})
    out = await _shot(tmp_path, router, config=config)
    assert out.answered is False
    summary = out.summary()
    assert "anthropic" in summary and "claude-vision" in summary, summary
    assert out.to_dict()["provider"] == "anthropic"


async def test_br4_a_failed_call_on_the_default_route_names_the_default(tmp_path):
    out = await _shot(tmp_path, _Router(raises=RuntimeError("upstream closed")))
    assert "ollama" in out.summary(), out.summary()


async def test_br4_the_vision_calls_tokens_ride_the_tools_data(tmp_path):
    out = await _shot(tmp_path, _Router())
    assert out.to_dict()["vision_usage"] == {"input_tokens": 900, "output_tokens": 30}
    out2 = await _shot(tmp_path, _Router(text=""))
    assert out2.to_dict()["vision_usage"] == {"input_tokens": 900, "output_tokens": 30}


async def test_br4_no_question_names_no_provider(tmp_path):
    """No question, no model call: nothing to name, nothing spent."""
    router = _Router()
    out = await S.capture_screenshot(
        _capture(), artifacts=ArtifactStore(tmp_path / "artifacts"),
        router_resolver=lambda: router, tab_url="https://example.test/",
    )
    assert router.calls == []
    assert out.summary().count("\n") == 0
    assert "provider" not in out.to_dict() and "vision_usage" not in out.to_dict()


def test_br4_the_tool_description_promises_what_the_result_says():
    from iron_jarvis.browser.tools import BrowserScreenshotTool

    text = BrowserScreenshotTool.description
    assert "the provider that saw it" not in text
    assert "names the provider and model" in text
    assert "hosted provider" in text and "SENDS the image" in text


# --------------------------------------------------------------------------- #
# BR5: a picture is never filed under a tab it is not of
# --------------------------------------------------------------------------- #


def _code(path: Path) -> str:
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    text = re.sub(r"/\*[\s\S]*?\*/", "", text)
    return re.sub(r"(?<!:)//.*", "", text)


def _capture_visible() -> str:
    code = _code(TABS_TS)
    start = code.index("export async function captureVisible(")
    end = code.index("\nexport ", start + 10)
    return code[start:end]


def test_br5_the_tab_on_screen_is_checked_right_before_and_after_each_capture():
    body = _capture_visible()
    loop = body[body.index("for (const attempt of CAPTURE_ATTEMPTS)"):]
    cap = loop.index("captureVisibleTab(")
    before, after = loop[:cap], loop[cap:]
    assert "onScreenTabId(windowId)" in before, "no check right before the capture"
    assert "onScreenTabId(windowId)" in after.split("parsed")[0], "no check right after it"
    assert "!== tabId" in body
    helper = _code(TABS_TS)
    fn = helper[helper.index("async function onScreenTabId("):]
    fn = fn[: fn.index("\n}\n")]
    assert "chrome.tabs.query" in fn and "active: true" in fn and "windowId" in fn


def test_br5_the_refusal_is_a_plain_sentence_and_the_result_names_the_tab_asked_for():
    code = _code(TABS_TS)
    assert "That tab was not the one on screen, so no picture was taken." in code
    body = _capture_visible()
    assert "notOnScreen(" in body
    assert re.search(r"tab_id:\s*tabId\b", body), "the result is filed under the tab checked"


def test_br5_the_daemon_passes_the_add_ons_own_sentence_on_verbatim():
    """The protocol already carries an add-on sentence: ``_resolve_response``
    keeps the add-on's ``message`` (no new code, no daemon change)."""
    from iron_jarvis.browser.extension_backend import ExtensionBackend

    sentence = (
        "That tab was not the one on screen, so no picture was taken. "
        "Call browser_screenshot again to bring it on screen."
    )

    async def go():
        fut = asyncio.get_running_loop().create_future()
        conn = SimpleNamespace(pending={"r1": fut})
        ExtensionBackend()._resolve_response(
            conn, {"id": "r1", "success": False, "error": {"code": "PAGE_CHANGED", "message": sentence}}
        )
        with pytest.raises(BrowserError) as ei:
            await fut
        return ei.value

    err = asyncio.run(go())
    assert err.message == sentence


# --------------------------------------------------------------------------- #
# TM13: npm's echo of a script is not an idle prompt
# --------------------------------------------------------------------------- #

_BANNER = "Welcome to Claude Code\n/help for help\n"


def test_tm13_npms_echo_of_a_script_that_just_started_is_not_idle():
    tail = _BANNER + "> run the tests\n$ npm test\n\n> project@1.0.0 test\n> vitest run\n\n"
    act = classify(tail, cli="claude", seen=True)
    assert act.state not in (AgentState.IDLE, AgentState.DONE), act
    act = classify(tail, cli="claude", seen=False)
    assert act.state not in (AgentState.IDLE, AgentState.DONE), act


def test_tm13_a_scoped_package_and_pnpms_echo_are_not_idle_either():
    for tail in [
        "> @acme/web@0.3.1 build C:\\work\\web\n> next build\n",
        "> app@2.0.0-beta.1 lint\n> eslint .\n",
        "> app@1.0.0 test\n",
    ]:
        act = classify(_BANNER + tail, cli="codex", seen=True)
        assert act.state not in (AgentState.IDLE, AgentState.DONE), (tail, act)


def test_tm13_an_old_prompt_above_the_echo_does_not_count_either():
    tail = _BANNER + ">\n> app@1.0.0 test\n> vitest run\n"
    assert classify(tail, cli="claude", seen=True).state is not AgentState.IDLE


def test_tm13_the_prompt_after_the_script_ended_is_idle_again():
    tail = _BANNER + "> app@1.0.0 test\n> vitest run\n\n Tests  12 passed (12)\n> "
    assert classify(tail, cli="claude", seen=True).state is AgentState.IDLE


def test_tm13_real_prompts_stay_idle():
    for tail in [
        "All set — the tests pass.\n> ",
        "> fix the failing test in app.py",
        "> email me at someone@example.test about it",
        "❯ ",
        "› summarise the notes",
    ]:
        assert classify(tail, cli="claude", seen=True).state is AgentState.IDLE, tail
