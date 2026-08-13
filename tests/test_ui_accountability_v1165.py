"""The accountability UI is actually WIRED (v1.165.0).

The components (TurnReceipt, ArtifactsRail, PreflightNote) and the stream
plumbing each have their own test files. What none of them can see is the CALL
SITE inside ``dashboard/app/chat/page.tsx`` — a 6,500-line Next page nothing in
vitest imports. This wave's reviews proved twice that the call site is where
features silently die (the draft-card fence, v1.163.0; the ``denied_tools``
field decoded and then dropped, found this wave), so the wiring is pinned here
from Python, the same technique ``test_draft_spacing_v1163.py`` established.

THE INCIDENT THESE SEAMS SERVE: the app knew a provider was down, recorded the
downgrade, owned two honesty affordances — and the user still got a fabricated
"Done. Wrote RESULT.md" with zero signal in the chat module, because the banner
rendered on the Overview and the chip compared against the EXPLICIT pick only.
Every assertion below is one link of the chain that stops that recurring.
"""

from __future__ import annotations

import re
from pathlib import Path

_DASH = Path(__file__).resolve().parents[1] / "dashboard"
_PAGE = (_DASH / "app" / "chat" / "page.tsx").read_text(encoding="utf-8")
_STREAM = (_DASH / "lib" / "useChatStream.ts").read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# The stream hands the fields over (decode alone is not delivery).
# --------------------------------------------------------------------------- #
def test_the_stream_result_carries_route_and_denials():
    """`denied_tools` was decoded from the frame and DROPPED at result assembly
    for six releases — the page could never show a denial. The assembly, not
    the decode, is the seam that matters."""
    assembly = _STREAM[_STREAM.index('case "done":') :]
    assert re.search(r"route:\s*ev\.route", assembly)
    assert re.search(r"deniedTools:\s*ev\.denied_tools", assembly)
    assert re.search(r"usage:\s*ev\.usage", assembly)


# --------------------------------------------------------------------------- #
# The page stores the receipt on the message, in BOTH lanes.
# --------------------------------------------------------------------------- #
def test_both_lanes_persist_the_receipt_fields():
    """A receipt that only exists on the streamed turn vanishes on reload; one
    that only exists in one lane reads as flakiness (the draft-card lesson)."""
    # Stream lane: the `receipt` object spread into the assistant message.
    assert re.search(r"const receipt = \{", _PAGE), "stream lane lost its receipt"
    assert _PAGE.count("...receipt,") >= 2, (
        "the receipt must reach BOTH stream exits (normal + workflow-draft)"
    )
    # POST lane: the mirror copy.
    assert re.search(r"const receiptPost = \{", _PAGE), "POST lane lost its receipt"
    assert "...receiptPost," in _PAGE


def test_the_receipt_component_is_mounted_with_server_truth():
    # Window sized for the v1.168.0 mount (undo-action props grew it past the
    # original 400) — same growth pattern as the ArtifactsRail pin below.
    mount = re.search(r"<TurnReceipt[\s\S]{0,1000}?/>", _PAGE)
    assert mount, "TurnReceipt is never rendered — the whole feature is dark"
    body = mount.group(0)
    for prop in ("route={m.route}", "deniedTools={m.deniedTools}",
                 "documents={m.documents}", "onOpenDocument={openDocPreview}"):
        assert prop in body, f"TurnReceipt mount lost {prop}"


def test_legacy_chip_and_tools_line_yield_to_the_receipt():
    """Both old surfaces render ONLY when the message has no route — showing
    them alongside the receipt would state every fact twice."""
    assert "{!m.route && m.viaProvider && (" in _PAGE
    assert "{!m.route && m.toolsUsed && m.toolsUsed.length > 0 && (" in _PAGE


# --------------------------------------------------------------------------- #
# The artifacts rail replaced the inline block WITHOUT losing affordances.
# --------------------------------------------------------------------------- #
def test_the_artifacts_rail_covers_every_old_affordance():
    # Window sized for the v1.166.0 mount (the downloadHref builder grew when
    # &download=1 and its cross-origin comment moved inline).
    mount = re.search(r"<ArtifactsRail[\s\S]{0,1400}?/>", _PAGE)
    assert mount, "ArtifactsRail is never rendered"
    body = mount.group(0)
    assert "onPreview={openDocPreview}" in body
    assert "onDismiss={dismissThreadDoc}" in body, (
        "dismiss lost in the swap — a file could never be forgotten again"
    )
    assert "downloadHref=" in body and "/documents/file?path=" in body, (
        "download lost in the swap — the old inline block had it"
    )
    # The hand-rolled inline list is gone (one implementation, not two).
    assert 'key={`railfile-${doc}`}' not in _PAGE


# --------------------------------------------------------------------------- #
# Preflight: warned BEFORE typing, watching the provider that will serve.
# --------------------------------------------------------------------------- #
def test_preflight_watches_the_pick_or_the_default():
    mount = re.search(r"<PreflightNote[\s\S]{0,400}?/>", _PAGE)
    assert mount, "PreflightNote is never rendered"
    body = mount.group(0)
    # The explicit pick when there is one, else the DEFAULT provider — the
    # default route is exactly where the mock incident happened. Pinned on the
    # `provider=` prop SPECIFICALLY: a looser body-wide regex survived a
    # mutation that broke only this prop, because the same expression also
    # appears inside `available=` (caught by the mutation sweep).
    assert re.search(
        r"provider=\{splitChoice\(choice\)\.provider \|\| health\.defaultProvider\}",
        body,
    ), "the note stopped watching the default provider"
    assert re.search(
        r"available=\{\s*health\.byProvider\[\s*splitChoice\(choice\)\.provider \|\| health\.defaultProvider",
        body,
    ), "availability is looked up for a different provider than is displayed"
    assert "stale={health.stale}" in body


def test_the_health_hook_is_mounted_once():
    assert _PAGE.count("useProviderHealth(") == 1, (
        "the page should mount ONE health poller (the hook self-polls)"
    )
