"""v1.271.0 — the sidebar works in the tab the user is looking at.

THE REPORT (2026-09-18): "when I use the browser extension to do something it
requires opening a new tab and doesn't just work in the tab I already have open.
This is a real flaw. It should simply work in the tab I'm using, not open a new
one unless I tell it to."

What was true, from the user's own ledger: every job began with
``browser_create_tab``. Three things told the model to do that: the navigate
tool's description ("prefer browser_create_tab when the user should keep the
page they are on"), the create-tab tool's description ("Prefer this over
browser_navigate"), and a prompt block that listed the new-tab tool first and
never said where to work. A fourth thing made it worse: the transactional
vocabulary read the WHOLE URL, so a search for "buy NVIDIA DGX Spark" was refused
as a transaction and the model rewrote the query twice.

Now: both descriptions and the prompt say "the tab the user is looking at"; a
PANEL turn's ceiling excludes ``browser_create_tab`` unless the sentence asks for
a new tab (``wants_new_tab``); and the vocabulary reads a navigation's scheme,
host and path — never its query string.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from iron_jarvis.browser import protocol as P
from iron_jarvis.browser.panel import NEW_TAB_PATTERNS, browser_tool_ceiling, wants_new_tab
from iron_jarvis.browser.risk import browser_risk_decision, navigation_words
from iron_jarvis.computeruse.policy import ComputerUsePolicy
from iron_jarvis.core.turns import TURNS
from iron_jarvis.daemon import chat_turn
from tests._fakes.panel_harness import RealApp
from tests.test_browser_agent_v1262 import _ACT_TIER, _NEUTRAL, _READ_TIER

_SRC = Path(__file__).resolve().parents[1] / "src" / "iron_jarvis"


@pytest.fixture(autouse=True)
def _clean_registry():
    for tid in TURNS.running_ids():
        TURNS.release(tid)
    yield
    for tid in TURNS.running_ids():
        TURNS.release(tid)


def _offered(app: RealApp, sentence: str) -> set[str]:
    seen = app.records_the_offer()
    app.panel.send(P.PANEL_ACTION_SEND, text=sentence)
    app.panel.wait_for(P.PANEL_EVENT_DONE, "the turn never finished")
    assert seen, "the model was never called"
    return {str(t.get("name") or "") for t in (seen[0].get("tools") or [])}


# --------------------------------------------------------------------------- #
# 1. The words that ask for a new tab
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "sentence",
    [
        "open a new tab with amazon",
        "open amazon in another tab",
        "open it in a separate tab",
        "open a tab for gmail",
        "put the results in a new window",
        "open one more tab and go to irs.gov",
        "open the listing in its own tab",
        "search that in a tab",
    ],
)
def test_sentences_that_ask_for_a_new_tab(sentence: str):
    assert wants_new_tab(sentence), sentence


@pytest.mark.parametrize(
    "sentence",
    [
        "search for NVIDIA DGX Spark",
        "go to amazon.com and find a monitor",
        "fill this form with my details",
        "what's on the table on this page",
        "list my tabs",
        "close this tab",
        "read the tab I'm on",
        "book the first available slot for tomorrow morning",
        "",
    ],
)
def test_sentences_that_do_not(sentence: str):
    assert not wants_new_tab(sentence), sentence


def test_the_patterns_are_word_bounded():
    assert len(NEW_TAB_PATTERNS) >= 3
    assert not wants_new_tab("a stable of horses"), "'a table'/'stable' must not read as a tab"
    assert not wants_new_tab("another tablet please")


# --------------------------------------------------------------------------- #
# 2. A panel turn's ceiling — the REAL lane, the REAL registry
# --------------------------------------------------------------------------- #


def test_a_plain_sentence_offers_every_acting_tool_but_the_new_tab(tmp_path, monkeypatch):
    """THE REPORT: a job that does not ask for a new tab cannot open one."""
    with RealApp(tmp_path, monkeypatch, access="interactive") as app:
        app.pair()
        offered = _offered(app, _NEUTRAL)
        assert _READ_TIER <= offered, sorted(_READ_TIER - offered)
        assert (_ACT_TIER - {"browser_create_tab"}) <= offered, sorted(_ACT_TIER - offered)
        assert "browser_navigate" in offered
        assert "browser_create_tab" not in offered, "the new-tab tool was offered to a sentence that never asked for one"


def test_a_sentence_that_asks_for_a_new_tab_gets_the_tool(tmp_path, monkeypatch):
    with RealApp(tmp_path, monkeypatch, access="interactive") as app:
        app.pair()
        offered = _offered(app, "open a new tab and book the first available slot")
        assert "browser_create_tab" in offered, sorted(offered)
        assert "browser_navigate" in offered


def test_the_bound_is_the_ceiling_not_only_the_family():
    """Source pin: the shrunk set is passed as BOTH `tool_ceiling` and `arm_family`,
    so neither the autoselect pass nor the family can arm the new-tab tool."""
    src = (_SRC / "browser" / "panel.py").read_text(encoding="utf-8").replace("\r\n", "\n")
    assert 'ceiling = frozenset(ceiling - {"browser_create_tab"})' in src
    run = src[src.index("async def _run("):src.index("def _empty_answer(")]
    assert "tool_ceiling=ceiling" in run and "arm_family=ceiling" in run
    assert run.index("wants_new_tab(text)") < run.index("tool_ceiling=ceiling")


def test_the_ceiling_itself_is_unchanged_for_other_surfaces(tmp_path, monkeypatch):
    """`browser_tool_ceiling` still names the whole family; the shrink is the PANEL turn's."""
    with RealApp(tmp_path, monkeypatch, access="interactive") as app:
        assert "browser_create_tab" in browser_tool_ceiling(app.platform)


# --------------------------------------------------------------------------- #
# 3. The prompt and the descriptions say where to work
# --------------------------------------------------------------------------- #


def test_the_prompt_tells_the_model_to_work_in_the_current_tab(tmp_path, monkeypatch):
    assert "WORK IN THE TAB THE USER IS LOOKING AT" in chat_turn.BROWSER_AGENT_BLOCK
    assert "Open a new tab ONLY when the user asks" in chat_turn.BROWSER_AGENT_BLOCK
    with RealApp(tmp_path, monkeypatch, access="interactive") as app:
        app.pair()
        seen = app.records_the_offer()
        app.panel.send(P.PANEL_ACTION_SEND, text=_NEUTRAL)
        app.panel.wait_for(P.PANEL_EVENT_DONE, "the turn never finished")
        system = str(seen[0].get("system") or "")
        assert "WORK IN THE TAB THE USER IS LOOKING AT" in system, "the sentence never reached the panel turn's prompt"


def test_the_tool_descriptions_point_at_the_tab_the_user_is_looking_at(tmp_path, monkeypatch):
    with RealApp(tmp_path, monkeypatch, access="interactive") as app:
        nav = app.platform.registry.get("browser_navigate").description
        new = app.platform.registry.get("browser_create_tab").description
    assert "tab the user is looking at" in nav and "THE way to open a page" in nav
    assert "prefer browser_create_tab" not in nav, "navigate still steers the model to a new tab"
    assert "ONLY when the user asks for a new tab" in new
    assert "Prefer this over browser_navigate" not in new, "create_tab still asks to be preferred"


# --------------------------------------------------------------------------- #
# 4. A search query is not a transaction; a path still is
# --------------------------------------------------------------------------- #


def test_navigation_words_drop_the_query_and_the_fragment():
    assert navigation_words("https://www.google.com/search?q=buy+NVIDIA+DGX+Spark") == "https://www.google.com/search"
    assert navigation_words("https://shop.test/checkout#step2") == "https://shop.test/checkout"
    assert navigation_words("https://bank.test/transfer?to=x#y") == "https://bank.test/transfer"
    assert navigation_words("") == "" and navigation_words(None) == ""


def test_a_search_for_buy_is_allowed_and_a_transactional_path_still_asks():
    policy = ComputerUsePolicy()
    ok = browser_risk_decision(
        "browser_navigate", url="https://www.google.com/search?q=buy+NVIDIA+DGX+Spark+price", policy=policy
    )
    assert ok.allowed and not ok.requires_approval, ok
    asks = browser_risk_decision("browser_navigate", url="https://bank.test/transfer?to=x", policy=policy)
    assert asks.requires_approval and "transfer" in asks.reason, asks
    asks2 = browser_risk_decision("browser_navigate", url="https://shop.test/checkout", policy=policy)
    assert asks2.requires_approval, asks2
    # The create-tab tool WITH a url is judged by navigate's rules (v1.237.0) — the same relief.
    ok2 = browser_risk_decision(
        "browser_navigate", url="https://www.google.com/search?q=purchase+monitor", policy=policy
    )
    assert not ok2.requires_approval, ok2
