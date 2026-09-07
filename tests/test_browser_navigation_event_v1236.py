"""The add-on must actually SEND the events the daemon is built to receive (v1.236.0).

Ship 2's review found the shape this repository already has a name for: shipping the
mechanism is not shipping the feature (CLAUDE.md, v1.218.0). The daemon handles
``browser.navigation_completed`` carefully — ``ExtensionBackend`` merges it onto the
cached active tab, and drops the page-authored keys the new document did not restate so
that page A's title can never sit beside page B's URL. All of that was correct, and
none of it ever ran, because the add-on listened only to ``chrome.tabs.onActivated``,
which fires when the user SWITCHES tabs.

The consequence was specific rather than vague, which is what made it worth a pin.
Following a link in the tab you are already in left two things asserting the previous
page: the ambient block in every chat turn ("Active tab: <the page you just left>") and
the snapshot cache, which kept a reading of the old document as current. Both are wrong
in the most confident possible way — plausible, specific, and about the one page the
user is actually looking at.

This file pins the EMITTER, from Python, because the add-on has no TypeScript test
harness. It is a source pin, so it reads the file the CI runner checked out with CRLF
and normalises at the reader; no needle carries a newline, and nothing is matched inside
a fixed-size window.

What each assertion catches:

* The listener EXISTS at all. Deleting it restores the original defect, and every
  other browser test stays green — the daemon's handler is exercised by tests that
  publish the event directly, which is exactly why the emitter needs its own pin.
* It emits the name the DAEMON dispatches on. A listener emitting a name nothing routes
  is the same silence with more code.
* It waits for the load to SETTLE. ``onUpdated`` also fires for "loading", so an
  unguarded emitter reports a document that is still moving, and the daemon would cache
  a title from a page mid-navigation.
* It reports the ACTIVE tab only. The daemon merges this onto its active-tab cache, so
  a background tab finishing its load would overwrite the active one with a page the
  user cannot even see.
* It sends null rather than "" without the site grant. Chrome hands back empty strings
  when permission is absent, and an empty title reads to a model as "this page has no
  title" — a lie it then repeats to the user. Ship 1 fixed exactly this for the tab
  list; the same rule has to hold here.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BACKGROUND = REPO / "extensions" / "chrome" / "src" / "background" / "index.ts"
PROTOCOL_TS = REPO / "extensions" / "chrome" / "src" / "protocol.ts"


def _read(path: Path) -> str:
    """One CRLF normalisation per file, at the reader — never at a call site."""
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def _listener_body() -> str:
    """The onUpdated listener, sliced by its own boundaries rather than a byte window.

    A fixed-size window is the trap this repo paid a release for: add one line and the
    window stops reaching the end, so the pin reports "never rendered" instead of "a
    line moved".
    """
    src = _read(BACKGROUND)
    start = src.find("chrome.tabs.onUpdated.addListener")
    assert start != -1, (
        "the add-on has no chrome.tabs.onUpdated listener, so a same-tab navigation is "
        "never reported and the daemon's navigation handler can never run"
    )
    rest = src[start:]
    end = rest.find("\nchrome.")
    tail = rest.find("\nonHostPermissionChanged")
    if end == -1 or (tail != -1 and tail < end):
        end = tail
    return rest if end == -1 else rest[:end]


def test_the_add_on_listens_for_same_tab_navigation():
    body = _listener_body()
    assert "socket.emitEvent" in body, "the listener computes but never sends anything"


def test_it_emits_the_name_the_daemon_dispatches_on():
    body = _listener_body()
    assert "EVENT_NAVIGATION_COMPLETED" in body, (
        "the listener emits some other event name; ExtensionBackend routes on "
        "EVENT_NAVIGATION_COMPLETED and would treat anything else as unknown"
    )
    # And that constant is a real one from the GENERATED protocol, not a literal
    # somebody typed here — the generator is what keeps the two sides in step.
    assert "EVENT_NAVIGATION_COMPLETED" in _read(PROTOCOL_TS)


def test_it_waits_for_the_load_to_settle():
    body = _listener_body()
    assert 'change.status !== "complete"' in body, (
        "the listener does not wait for the load to complete, so it reports a document "
        "that is still moving and the daemon caches a mid-navigation title"
    )


def test_it_reports_only_the_tab_the_user_is_looking_at():
    body = _listener_body()
    assert "active: true" in body, (
        "the listener does not restrict itself to the active tab. The daemon MERGES "
        "this onto its active-tab cache, so a background tab finishing its load would "
        "overwrite the active one with a page the user cannot see"
    )


def test_it_reports_null_rather_than_an_empty_string_without_the_site_grant():
    body = _listener_body()
    assert "hostPermission" in body, "the listener does not consult the site grant"
    # `null` must appear on both page-authored fields, guarded by the grant.
    for field in ("title:", "url:"):
        idx = body.find(field)
        assert idx != -1, f"the payload has no {field} field"
        clause = body[idx : idx + 90]
        assert "hostPermission ?" in clause and "null" in clause, (
            f"{field} is not null-guarded by the site grant. Chrome returns EMPTY "
            "STRINGS without permission, and an empty title reads to a model as 'this "
            "page has no title'"
        )


def test_the_payload_names_the_tab():
    body = _listener_body()
    assert re.search(r"tab_id:\s*tabId", body), (
        "the payload carries no tab_id, so the daemon cannot tell whether the "
        "navigation belongs to the tab it is caching"
    )
