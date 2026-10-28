"""The effort list: "Extra" where the model takes it, and never a guess.

The user, looking at the effort menu in a coding session: "it shows the
options default, low, medium and high. Default should not be there and there
should be Extra in models that allow for it."

"Extra" is Claude Code's own name for its ``--effort xhigh`` (its effort
picker, read in Claude Code 2.1.311). Whether a model takes it is the CLI's
own answer: every row of its ``initialize`` picker carries
``supportedEffortLevels``. These pins hold the daemon half:

  * the picker's list is kept on the catalog row (``effort_levels``), in the
    CLI's order, and a row without one reads as unknown;
  * ``effort_levels`` reads the CACHED catalog only, never the CLI;
  * ``reasoning_levels`` offers ``xhigh`` for Claude Code ONLY when its picker
    lists it for that model — no other provider ever offers it;
  * ``normalize_level`` keeps ``xhigh`` (and still drops ``max``, which no
    menu offers), the router applies it only where it is offered, the CLI
    gets ``--effort xhigh``, a saved chat keeps it, and ``GET /models`` puts
    it on exactly the rows that take it.
"""

from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes.chat import _clean_setup
from iron_jarvis.providers import claude_models as cm
from iron_jarvis.providers import reasoning as R
from iron_jarvis.providers.adapters.base import LLMMessage
from iron_jarvis.providers.adapters.subprocess_cli import ClaudeCliAdapter
from iron_jarvis.providers.manager import ProviderManager
from iron_jarvis.providers.router import _applied_reasoning

ACCOUNT = {"email": "user@example.test", "subscriptionType": "Claude Max"}
ALL_FIVE = ["low", "medium", "high", "xhigh", "max"]

#: The picker as Claude Code 2.1.311 answers it (the effort fields read off
#: this PC's real ``initialize`` answer; the rest is the v1.300.0 shape).
PICKER = [
    {"value": "default", "resolvedModel": "claude-opus-5-5", "displayName": "Default (recommended)",
     "description": "Opus 5.5 · Best for everyday, complex tasks",
     "supportsEffort": True, "supportedEffortLevels": ALL_FIVE},
    {"value": "sonnet", "resolvedModel": "claude-sonnet-5-5", "displayName": "Sonnet",
     "description": "Sonnet 5.5 · Efficient for routine tasks",
     "supportsEffort": True, "supportedEffortLevels": ["max", "low", "XHIGH", "medium", "high"]},
    # A model the vendor says takes the three base levels only.
    {"value": "haiku", "resolvedModel": "claude-haiku-4-5-20251001", "displayName": "Haiku",
     "description": "Haiku 4.5 · Fastest for quick answers",
     "supportsEffort": True, "supportedEffortLevels": ["low", "medium", "high"]},
    # A model that says it takes NO effort, whatever list it carries.
    {"value": "claude-fable-5-1[1m]", "resolvedModel": "claude-fable-5-1", "displayName": "Fable",
     "description": "Fable 5.1 · Most capable",
     "supportsEffort": False, "supportedEffortLevels": ALL_FIVE},
]

#: An older CLI: no effort fields at all.
OLD_PICKER = [{k: v for k, v in r.items() if not k.startswith("support")} for r in PICKER]


def _stdout(models) -> str:
    return json.dumps({
        "type": "control_response",
        "response": {"subtype": "success", "request_id": "ij-picker",
                     "response": {"models": models, "account": ACCOUNT, "commands": []}},
    }) + "\n"


@pytest.fixture(autouse=True)
def _clean_module():
    cm.reset()
    cm.set_home(None)
    yield
    deadline = time.monotonic() + 5
    while cm._refreshing and time.monotonic() < deadline:
        time.sleep(0.01)
    cm.reset()
    cm.set_home(None)


def _prime(monkeypatch, rows) -> None:
    monkeypatch.setattr(cm, "_run_handshake", lambda timeout: _stdout(rows))
    assert cm.discover(force=True)["error"] is None


def _never_spawn(monkeypatch) -> list[float]:
    calls: list[float] = []

    def boom(timeout):
        calls.append(timeout)
        raise AssertionError("the claude CLI was started")

    monkeypatch.setattr(cm, "_run_handshake", boom)
    return calls


# ==========================================================================
# 1. the catalog keeps the picker's list
# ==========================================================================


def test_the_catalog_row_keeps_the_pickers_effort_list_in_the_clis_order(monkeypatch):
    _prime(monkeypatch, PICKER)
    rows = {r["id"]: r for r in cm.discover()["models"]}
    assert rows["claude-opus-5-5"]["effort_levels"] == ALL_FIVE
    # Case and order are the CLI's, not the row's.
    assert rows["claude-sonnet-5-5"]["effort_levels"] == ALL_FIVE
    assert rows["claude-haiku-4-5-20251001"]["effort_levels"] == ["low", "medium", "high"]
    # supportsEffort False wins over a list.
    assert rows["claude-fable-5-1"]["effort_levels"] == []


def test_an_older_picker_with_no_effort_fields_reads_as_unknown(monkeypatch):
    _prime(monkeypatch, OLD_PICKER)
    rows = {r["id"]: r for r in cm.discover()["models"]}
    assert all(r["effort_levels"] == [] for r in rows.values())
    assert cm.effort_levels("claude-opus-5-5") is None


# ==========================================================================
# 2. effort_levels reads the cache, never the CLI
# ==========================================================================


def test_effort_levels_answers_from_the_cached_picker(monkeypatch):
    _prime(monkeypatch, PICKER)
    calls = _never_spawn(monkeypatch)
    assert cm.effort_levels("claude-opus-5-5") == tuple(ALL_FIVE)
    # A picker value and a 1M spelling find the same row.
    assert cm.effort_levels("sonnet") == tuple(ALL_FIVE)
    assert cm.effort_levels("claude-sonnet-5-5[1m]") == tuple(ALL_FIVE)
    assert cm.effort_levels("claude-haiku-4-5-20251001") == ("low", "medium", "high")
    # The plan's own default reads the CLI default's row.
    for placeholder in ("subscription", "default", ""):
        assert cm.effort_levels(placeholder) == tuple(ALL_FIVE), placeholder
    # A model that takes no effort, and a model the picker never named.
    assert cm.effort_levels("claude-fable-5-1") is None
    assert cm.effort_levels("claude-opus-4-8") is None
    assert calls == []


def test_effort_levels_with_no_picker_yet_is_unknown_and_starts_nothing(monkeypatch):
    calls = _never_spawn(monkeypatch)
    assert cm.effort_levels("claude-opus-5-5") is None
    assert cm.effort_levels("subscription") is None
    assert R.reasoning_levels("claude-cli", "claude-opus-5-5") == R.LEVELS
    assert calls == []


# ==========================================================================
# 3. the one table
# ==========================================================================


def test_claude_code_offers_extra_only_where_its_picker_lists_it(monkeypatch):
    _prime(monkeypatch, PICKER)
    calls = _never_spawn(monkeypatch)
    extra = R.LEVELS + ("xhigh",)
    assert R.reasoning_levels("claude-cli", "claude-opus-5-5") == extra
    assert R.reasoning_levels("claude-cli", "subscription") == extra
    assert R.reasoning_levels("claude-cli", "sonnet") == extra
    assert R.reasoning_levels("claude-cli", "claude-haiku-4-5-20251001") == R.LEVELS
    assert R.reasoning_levels("claude-cli", "claude-fable-5-1") == R.LEVELS
    assert R.reasoning_levels("claude-cli", "claude-opus-4-8") == R.LEVELS
    # "max" is the CLI's too, but no menu offers it.
    assert "max" not in R.reasoning_levels("claude-cli", "claude-opus-5-5")
    assert calls == []


@pytest.mark.parametrize(
    "provider,model",
    [
        ("anthropic", "claude-opus-5-5"),
        ("codex-cli", "subscription"),
        ("codex-cli", "gpt-6"),
        ("openai", "gpt-5"),
        ("google", "gemini-2.5-pro"),
        ("custom", "qwen3-32b"),
    ],
)
def test_no_other_provider_ever_offers_extra(monkeypatch, provider, model):
    _prime(monkeypatch, PICKER)
    assert "xhigh" not in R.reasoning_levels(provider, model)
    assert not R.supports(provider, model, "xhigh")


def test_a_broken_catalog_offers_the_base_levels(monkeypatch):
    def broken(model_id):
        raise RuntimeError("unreadable")

    monkeypatch.setattr(cm, "effort_levels", broken)
    assert R.reasoning_levels("claude-cli", "claude-opus-5-5") == R.LEVELS


@pytest.mark.parametrize(
    "raw,level",
    [("xhigh", "xhigh"), (" XHIGH ", "xhigh"), ("high", "high"), ("max", ""), ("Extra", ""), ("", "")],
)
def test_the_wire_vocabulary_holds_xhigh_and_nothing_else_new(raw, level):
    assert R.normalize_level(raw) == level


# ==========================================================================
# 4. the router, the CLI, a saved chat
# ==========================================================================


def test_the_router_applies_extra_only_for_a_model_that_takes_it(monkeypatch):
    _prime(monkeypatch, PICKER)
    opus = SimpleNamespace(provider="claude-cli", model="claude-opus-5-5")
    haiku = SimpleNamespace(provider="claude-cli", model="claude-haiku-4-5-20251001")
    codex = SimpleNamespace(provider="codex-cli", model="subscription")
    assert _applied_reasoning(opus, "xhigh") == "xhigh"
    assert _applied_reasoning(SimpleNamespace(inner=opus), "xhigh") == "xhigh"
    assert _applied_reasoning(haiku, "xhigh") == ""
    assert _applied_reasoning(codex, "xhigh") == ""
    assert _applied_reasoning(opus, "max") == ""


def test_claude_code_is_started_with_effort_xhigh():
    seen: list[list[str]] = []
    events = [
        {"type": "assistant", "message": {"role": "assistant", "stop_reason": "end_turn",
                                          "content": [{"type": "text", "text": "answer"}]}},
        {"type": "stream_event", "event": {"type": "message_stop"}},
        {"type": "result", "subtype": "success", "usage": {"input_tokens": 1, "output_tokens": 1}},
    ]

    def runner(argv, stdin=None):
        seen.append(list(argv))
        return 0, "\n".join(json.dumps(e) for e in events), ""

    a = ClaudeCliAdapter(model="claude-opus-4-8", runner=runner, which=lambda b: "claude.exe")
    msgs = [LLMMessage(role="user", content="hi")]
    asyncio.run(a.complete(system="s", messages=msgs, tools=[], reasoning="xhigh"))
    assert seen[0][seen[0].index("--effort") + 1] == "xhigh"


def test_a_saved_chat_keeps_extra():
    assert json.loads(_clean_setup({"reasoning": "xhigh"}))["reasoning"] == "xhigh"
    assert "reasoning" not in json.loads(_clean_setup({"reasoning": "max", "tools": ["read_file"]}))


# ==========================================================================
# 5. GET /models
# ==========================================================================


def test_the_model_list_puts_extra_on_exactly_the_rows_that_take_it(tmp_path, monkeypatch):
    for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(ProviderManager, "_cli_binary_present", staticmethod(lambda b: b == "claude"))
    _prime(monkeypatch, PICKER)
    models = TestClient(create_app(str(tmp_path))).get("/models").json()["models"]
    cli = {m["model"]: m for m in models if m["provider"] == "claude-cli"}
    assert cli["claude-opus-5-5"]["reasoning"] == ["low", "medium", "high", "xhigh"]
    assert cli["claude-sonnet-5-5"]["reasoning"] == ["low", "medium", "high", "xhigh"]
    assert cli["claude-haiku-4-5-20251001"]["reasoning"] == ["low", "medium", "high"]
    assert cli["subscription"]["reasoning"] == ["low", "medium", "high", "xhigh"]
    # The keyless Anthropic API served by Claude Code: the API's own words.
    anth = [m for m in models if m["provider"] == "anthropic"]
    assert anth and all("xhigh" not in (m.get("reasoning") or []) for m in anth)
    # The default the chip names is never Extra.
    assert all(m.get("reasoning_default") != "xhigh" for m in models)
