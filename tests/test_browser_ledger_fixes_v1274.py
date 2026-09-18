"""v1.274.0 — four browser fixes read off the user's own ledger.

The last 400 browser tool invocations on the daily driver (2026-09-18):

* a screenshot refused because the tab was not on screen, then refused again
  after the model activated it — "Either the '<all_urls>' or 'activeTab'
  permission is required" on a grant that covered every web page;
* a page read refused with a bare add-on error five times in twenty seconds —
  "Frame with ID 0 is showing error page" — the browser's own error page;
* a read of the Edge Add-ons store refused as a bare add-on error;

Each is closed here. The add-on side is source-pinned (the worker has no
runtime harness); the daemon side runs.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from iron_jarvis.browser import protocol as P
from iron_jarvis.browser.errors import REMEDIES, BrowserErrorCode, browser_error

ROOT = Path(__file__).resolve().parents[1]
ADDON = ROOT / "extensions" / "chrome"
TABS_TS = ADDON / "src" / "background" / "tabs.ts"
HOSTPERMS_TS = ADDON / "src" / "background" / "hostperms.ts"
PROTOCOL_TS = ADDON / "src" / "protocol.ts"


def _src(path: Path) -> str:
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def _fn(src: str, name: str) -> str:
    start = src.index(f"function {name}(")
    return src[start: src.index("\n}\n", start) + 3]


# --------------------------------------------------------------------------- #
# 1. A screenshot brings the tab on screen instead of refusing
# --------------------------------------------------------------------------- #


def test_the_screenshot_activates_the_tab_before_capturing():
    body = _fn(_src(TABS_TS), "captureVisible")
    assert "Chrome can only photograph the tab that is on screen" not in body, "the old refusal is back"
    activate = body.index("chrome.tabs.update(tabId, { active: true })")
    focus = body.index("chrome.windows.update(tab.windowId, { focused: true })")
    capture = body.index("chrome.tabs.captureVisibleTab(windowId, attempt)")
    assert activate < focus < capture, "the tab must be on screen, in a focused window, before the capture"
    assert "if (tab.active !== true)" in body, "an already-active tab must not be re-activated"
    assert "activated," in body or "activated: activated" in body, "the result does not say the view moved"
    # Bounded wait for the paint: a loop with a small cap, never an open-ended one.
    assert re.search(r"for \(let i = 0; i < 10; i\+\+\)", body)


# --------------------------------------------------------------------------- #
# 2. <all_urls>: the grant the capture check actually requires
# --------------------------------------------------------------------------- #


def test_the_optional_host_permission_is_all_urls_everywhere():
    manifest = json.loads(_src(ADDON / "manifest.json"))
    assert manifest["optional_host_permissions"] == ["<all_urls>"]
    assert "host_permissions" not in manifest, "Q02: nothing at install time"
    assert 'export const HOST_ORIGINS = ["<all_urls>"];' in _src(HOSTPERMS_TS)
    assert "<all_urls>" in _src(ADDON / "README.md")


# --------------------------------------------------------------------------- #
# 3. The browser's error page has its own code and remedy
# --------------------------------------------------------------------------- #


def test_page_failed_to_load_is_a_code_with_a_remedy_that_forbids_the_retry():
    assert BrowserErrorCode.PAGE_FAILED_TO_LOAD.value == "PAGE_FAILED_TO_LOAD"
    remedy = REMEDIES[BrowserErrorCode.PAGE_FAILED_TO_LOAD]
    assert "Do not read it again" in remedy and "Navigate" in remedy
    env = browser_error(BrowserErrorCode.PAGE_FAILED_TO_LOAD, tab_id=7)
    assert env["code"] == "PAGE_FAILED_TO_LOAD" and "Tab 7" in env["message"]
    assert len(list(BrowserErrorCode)) == 18


def test_the_addon_maps_the_browsers_error_page_to_that_code():
    inject = _fn(_src(TABS_TS), "inject")
    assert re.search(r'if \(/showing error page/i\.test\(detail\)\) \{[\s\S]{0,400}new BridgeError\("PAGE_FAILED_TO_LOAD", \{ tab_id: tabId \}\)', inject), (
        "the error page still reaches the model as a bare add-on error"
    )
    # The generated protocol carries the code and its remedy, so the add-on can say it.
    generated = _src(PROTOCOL_TS)
    assert '"PAGE_FAILED_TO_LOAD"' in generated and "Do not read it again" in generated, "protocol.ts was not regenerated"


# --------------------------------------------------------------------------- #
# 4. The Edge Add-ons store is closed to add-ons
# --------------------------------------------------------------------------- #


def test_the_edge_store_is_an_unsupported_host_on_both_sides():
    assert "microsoftedge.microsoft.com" in P.UNSUPPORTED_HOSTS
    assert P.unsupported_page_scheme("https://microsoftedge.microsoft.com/addons/detail/x") == "https:"
    assert P.unsupported_page_scheme("https://microsoftedge.microsoft.com") == "https:"
    assert P.unsupported_page_scheme("https://www.microsoft.com/edge") == ""
    assert "microsoftedge.microsoft.com" in _src(PROTOCOL_TS), "protocol.ts was not regenerated"
