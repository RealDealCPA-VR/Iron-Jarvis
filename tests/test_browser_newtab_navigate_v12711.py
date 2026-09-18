"""v1.271.1 — navigating AWAY from a browser-internal tab (the new-tab page) works.

THE REPORT (2026-09-18, the user, on Edge, from a fresh tab): "Could not open a
page. UNSUPPORTED_PAGE: edge: pages are closed to add-ons by Chrome. Ask the user
to switch to a normal tab." The ledger: `browser_navigate {"url": ...}` with no
tab_id → refused; the model then navigated a DIFFERENT, background tab and the
job ran where the user was not looking (its screenshot failed for that reason).

What was true: ``prepare_action`` resolved the tab through ``resolve_page_tab``,
which refuses a tab whose CURRENT page is closed to add-ons — right for a read,
a click, a scroll (a content script must run there), wrong for a navigation:
leaving ``edge://newtab/`` for a real URL is the most ordinary thing a sidebar
does, and only the DESTINATION can be closed to add-ons (``navigate_params``
checks that). Activating or closing such a tab is likewise fine.

Now ``resolve_page_tab(..., page_required=False)`` resolves the tab and skips the
page check; navigate, activate_tab and close_tab ask for that. Read, click, type,
press_key, scroll and screenshot keep the check. And the refusal names "the
browser", not Chrome — the user was on Edge.
"""

from __future__ import annotations

import pytest

from iron_jarvis.browser import protocol as P
from iron_jarvis.browser.errors import BrowserErrorCode
from iron_jarvis.browser.tools import (
    BrowserActivateTabTool,
    BrowserClickTool,
    BrowserCloseTabTool,
    BrowserNavigateTool,
    BrowserReadPageTool,
    BrowserScrollTool,
)
from tests.test_browser_actions_v1237 import ActRuntime, FakeTab, ScriptedBrowser, _ctx, _peer, _run, _sent

NEW_TAB = "edge://newtab/"


def _newtab_runtime() -> ActRuntime:
    """The user's browser as it was: a fresh Edge tab in front, a normal tab behind."""
    page = ScriptedBrowser(
        [
            FakeTab(id=7, title="New tab", url=NEW_TAB, active=True),
            FakeTab(id=8, title="Notes", url="https://notes.example/", text="Notes."),
        ]
    )
    return ActRuntime(_peer(page))


def test_navigating_away_from_the_new_tab_page_works(tmp_path):
    """THE REPORT. No tab_id = the tab the user is looking at, which is the new-tab page."""
    runtime = _newtab_runtime()
    result = _run(BrowserNavigateTool(runtime), _ctx(tmp_path), {"url": "https://example.test/"})
    assert result.ok, result.error
    sent = _sent(runtime, P.METHOD_NAVIGATE)
    assert len(sent) == 1 and sent[0]["tab_id"] == 7 and sent[0]["url"] == "https://example.test/"
    assert result.data["tab_id"] == 7


def test_navigating_TO_an_internal_page_is_still_refused(tmp_path):
    """The destination check is the one that matters, and it stays."""
    runtime = _newtab_runtime()
    for url in ("edge://settings", "chrome://extensions", "about:blank"):
        result = _run(BrowserNavigateTool(runtime), _ctx(tmp_path), {"url": url})
        assert not result.ok, url
        assert result.data["code"] == BrowserErrorCode.UNSUPPORTED_PAGE.value, url
    assert _sent(runtime, P.METHOD_NAVIGATE) == []


@pytest.mark.parametrize(
    "tool_cls, args, method",
    [
        (BrowserReadPageTool, {}, P.METHOD_READ_PAGE),
        (BrowserScrollTool, {"direction": "down"}, P.METHOD_SCROLL),
        (BrowserClickTool, {"target": {"element_id": "e1"}}, P.METHOD_CLICK),
    ],
)
def test_reading_scrolling_and_clicking_the_new_tab_page_are_still_refused(tmp_path, tool_cls, args, method):
    """A content script cannot run there; the page check stays for everything that needs one."""
    runtime = _newtab_runtime()
    result = _run(tool_cls(runtime), _ctx(tmp_path), args)
    assert not result.ok, tool_cls.name
    assert result.data["code"] == BrowserErrorCode.UNSUPPORTED_PAGE.value, result.error
    assert _sent(runtime, method) == []


def test_activating_and_closing_an_internal_tab_are_allowed(tmp_path):
    """Neither touches the page; the tab only has to exist."""
    runtime = _newtab_runtime()
    activated = _run(BrowserActivateTabTool(runtime), _ctx(tmp_path), {"tab_id": 7})
    assert activated.ok, activated.error
    assert _sent(runtime, P.METHOD_ACTIVATE_TAB) and _sent(runtime, P.METHOD_ACTIVATE_TAB)[0]["tab_id"] == 7
    closed = _run(BrowserCloseTabTool(runtime), _ctx(tmp_path), {"tab_id": 7})
    assert closed.ok, closed.error
    assert _sent(runtime, P.METHOD_CLOSE_TAB) and _sent(runtime, P.METHOD_CLOSE_TAB)[0]["tab_id"] == 7


def test_a_missing_tab_is_still_a_missing_tab_for_a_navigation(tmp_path):
    """Skipping the PAGE check must not skip the TAB check."""
    runtime = _newtab_runtime()
    result = _run(BrowserNavigateTool(runtime), _ctx(tmp_path), {"tab_id": 999, "url": "https://example.test/"})
    assert not result.ok
    assert result.data["code"] == BrowserErrorCode.TAB_NOT_FOUND.value
    assert _sent(runtime, P.METHOD_NAVIGATE) == []


def test_the_refusal_names_the_browser_not_chrome(tmp_path):
    """The user was on Edge and read 'closed to add-ons by Chrome'."""
    runtime = _newtab_runtime()
    result = _run(BrowserReadPageTool(runtime), _ctx(tmp_path), {})
    assert not result.ok
    assert "closed to add-ons by the browser" in result.error, result.error
    assert "by Chrome" not in result.error
    assert "switch to a normal tab" in result.error
