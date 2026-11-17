"""Third north-star fix plan, group 17: SB11 (the add-on's worker) and BR6 (the scan).

SB11. The add-on's background worker answers ``chrome.runtime`` messages from every
context inside the add-on. That includes the content script it injects into web
pages, which runs beside a page it cannot trust. The worker used to forward a
sidebar action (``panel_action``) to the daemon, and run every other side panel
verb (start a recording, start a pick, open the dashboard, toggle the connection),
for ANY sender. Now a message only the add-on's own side panel (or its microphone
page, or its setup page) sends is answered only when it came from THAT page:
the add-on's own id, that page's own address, and, for the side panel, no tab.

There is no JS test runner in ``extensions/chrome``. These are SOURCE pins in the
house shape (``test_browser_sidepanel_v1242.py``), not a run in a browser: they pin
that the guard exists, that every message kind the listener handles is classified,
and that the guard is checked before the switch that runs the handlers.

BR6. ``computeruse.safety.detect_injection``'s line-start pattern was
``(^|\\n)\\s*(system|assistant|developer)``: ``\\s`` includes the newline, so at
every newline of a long run of blank lines the engine ran to the end of the run
and back, which is quadratic (32,000 blank lines took about 16 seconds). The line
start now spans only whitespace that is NOT a newline, which finds exactly the
same matches (the last newline before the word is always a line start) in linear
time.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

import pytest

from iron_jarvis.computeruse import safety
from iron_jarvis.computeruse.safety import detect_injection

REPO = Path(__file__).resolve().parents[1]
ADDON = REPO / "extensions" / "chrome"
WORKER_TS = ADDON / "src" / "background" / "index.ts"
HOSTPERMS_TS = ADDON / "src" / "background" / "hostperms.ts"
CHANNEL_TS = ADDON / "src" / "content" / "channel.ts"
MANIFEST = ADDON / "manifest.json"
ESBUILD = ADDON / "esbuild.config.mjs"
SRC = ADDON / "src"


def _read(path: Path) -> str:
    """One CRLF normalisation per file, at the reader."""
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def _code(path: Path) -> str:
    """The file with its comments removed, so a pin never matches prose."""
    text = _read(path)
    text = re.sub(r"/\*[\s\S]*?\*/", "", text)
    return re.sub(r"(?<!:)//.*", "", text)


def _block(text: str, opener: str) -> str:
    """The braced body that follows ``opener``, found by counting braces."""
    start = text.index(opener)
    brace = text.index("{", start)
    depth = 0
    for index in range(brace, len(text)):
        char = text[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[brace + 1 : index]
    raise AssertionError(f"unbalanced braces after {opener!r}")


def _listener(worker: str) -> str:
    """The body of the worker's one ``chrome.runtime.onMessage`` listener."""
    return _block(worker, "chrome.runtime.onMessage.addListener(")


def _cases(listener: str) -> list[str]:
    """Every case label of the listener's switch: a string literal or a constant."""
    return re.findall(r'case\s+("[a-z_]+"|[A-Z_]+)\s*:', listener)


#: Every kind ONLY the add-on's own pages send, and which page sends it. Read from
#: the senders: ``sidepanel/sidepanel.ts`` and ``sidepanel/recording.ts`` (the side
#: panel bundle), ``mic/mic.ts`` (the microphone page), ``setup/setup.ts``.
OWN_PAGE_KINDS = {
    '"status"': "SIDEPANEL_PAGE",
    '"open_jarvis"': "SIDEPANEL_PAGE",
    '"toggle_connection"': "SIDEPANEL_PAGE",
    '"request_host_permission"': "SIDEPANEL_PAGE",
    '"request_microphone"': "SIDEPANEL_PAGE",
    '"panel_action"': "SIDEPANEL_PAGE",
    '"pick_element"': "SIDEPANEL_PAGE",
    '"pick_cancel"': "SIDEPANEL_PAGE",
    '"record_start"': "SIDEPANEL_PAGE",
    '"record_stop"': "SIDEPANEL_PAGE",
    '"record_status"': "SIDEPANEL_PAGE",
    '"mic_permission_result"': "MIC_PAGE",
    '"host_permission_result"': "SETUP_PAGE",
}

#: Every kind the content script legitimately sends from inside a web page. Each
#: is answered only for the tab it belongs to (``sender.tab.id``), never relayed
#: to the daemon.
CONTENT_SCRIPT_KINDS = {"PICKED_KIND", "PICK_CANCELLED_KIND", "RECORDED_STEP_KIND", "RECORD_IDLE_KIND"}


# --------------------------------------------------------------------------- #
# SB11: the worker answers a side panel verb only from the side panel
# --------------------------------------------------------------------------- #


def test_every_message_kind_the_worker_handles_is_classified():
    """A new case added to the listener must say who may send it.

    Pins: the ``OWN_PAGE_FOR_KIND`` table in ``background/index.ts``. A kind
    removed from the table (the guard reverted for it) and not a content script
    kind fails here.
    """
    worker = _code(WORKER_TS)
    cases = _cases(_listener(worker))
    assert cases, "the listener's switch was not found"
    start = worker.index("const OWN_PAGE_FOR_KIND")
    table = worker[start : worker.index("]);", start)]
    entries = dict(re.findall(r'\[\s*("[a-z_]+")\s*,\s*([A-Z_]+)\s*\]', table))
    assert entries == OWN_PAGE_KINDS, (
        "the kinds only the add-on's own pages send, and the page each comes from, "
        f"changed: {entries}"
    )
    for case in cases:
        assert case in entries or case in CONTENT_SCRIPT_KINDS, (
            f"case {case} is handled for any sender: name the page that sends it in "
            "OWN_PAGE_FOR_KIND, or check sender.tab in its case like the content "
            "script kinds"
        )


def test_the_sender_check_runs_before_any_handler():
    """The guard sits ahead of ``switch (kind)`` and answers a refusal itself.

    Pins: the ``if (page !== undefined && !fromOwnPage(sender, page))`` block at the
    top of the listener, and its ``return`` before the async handler runs.
    """
    listener = _listener(_code(WORKER_TS))
    guard = re.search(
        r"const page = OWN_PAGE_FOR_KIND\.get\(kind\);\s*"
        r"if \(page !== undefined && !fromOwnPage\(sender, page\)\) \{(?P<body>[\s\S]*?)\n  \}",
        listener,
    )
    assert guard, "the listener does not check the sender of a page-only kind"
    assert re.search(r"respond\(\{ refused: true \}\);\s*return false;", guard.group("body")), (
        "a refused message must be answered at once (so the sender's promise ends) "
        "and must not reach the handlers"
    )
    assert guard.start() < listener.index("switch (kind)"), "the sender check runs after the handlers"
    assert not re.search(r"\bmessage\b|console\.", guard.group("body")), (
        "the refusal must not log or echo anything from the message itself"
    )


def test_the_own_page_check_reads_the_browser_set_sender_fields():
    """The id and the page's own address.

    ``sender.url`` is filled in by the browser, not by the sending script: a
    content script's message always carries the web page's address, never the
    add-on's own. Pins: each line of ``fromOwnPage``. (The lead's review took
    out a "no tab for the side panel" rule: the address already keeps a page
    script out, and a browser that set ``sender.tab`` for its side panel
    would have had every press in the sidebar refused.)
    """
    worker = _code(WORKER_TS)
    body = _block(worker, "function fromOwnPage(")
    assert "sender.id !== chrome.runtime.id" in body, "another add-on's id is not refused"
    assert "sender.tab" not in body, "the address decides, never whether a tab is set"
    assert "chrome.runtime.getURL(page)" in body, "the page's address is not the add-on's own"
    assert re.search(r"url === want \|\| url\.startsWith\(`\$\{want\}\?`\) \|\| url\.startsWith\(`\$\{want\}#`\)", body), (
        "the address must be exactly the page (a query or a fragment allowed), not any prefix"
    )


def test_the_page_constants_are_the_pages_the_add_on_really_ships():
    """``SIDEPANEL_PAGE`` is the manifest's side panel; the other two are the
    pages ``hostperms.ts`` opens and the build writes."""
    worker = _code(WORKER_TS)
    manifest = json.loads(_read(MANIFEST))
    panel = re.search(r'const SIDEPANEL_PAGE = "([^"]+)";', worker)
    assert panel and panel.group(1) == manifest["side_panel"]["default_path"]
    hostperms = _code(HOSTPERMS_TS)
    assert 'export const SETUP_PAGE = "dist/setup.html";' in hostperms
    assert 'export const MIC_PAGE = "dist/mic.html";' in hostperms
    assert re.search(r"import \{[^}]*\bMIC_PAGE\b[^}]*\bSETUP_PAGE\b[^}]*\} from \"\./hostperms\";", worker)
    esbuild = _read(ESBUILD)
    for page in ("sidepanel.html", "setup.html", "mic.html"):
        assert f'"{page}"]' in esbuild, f"the build no longer writes dist/{page}"


def test_the_senders_of_each_page_only_kind_are_the_pages_named():
    """Read the add-on's sources: a page-only kind is sent from no content script.

    If a content script started sending one of them, the guard would silently
    drop it; this says so here instead.
    """
    content = "\n".join(_code(p) for p in sorted((SRC / "content").glob("*.ts")))
    for kind in OWN_PAGE_KINDS:
        assert f"kind: {kind}" not in content, f"the content script sends {kind}, which the worker now refuses from a page"
    channel = _code(CHANNEL_TS)
    for const in CONTENT_SCRIPT_KINDS:
        assert re.search(rf"export const {const} = ", channel), f"{const} moved out of content/channel.ts"


def test_the_content_script_kinds_are_answered_only_for_their_own_tab():
    """These four are the content script's; each reads ``sender.tab?.id`` first."""
    listener = _listener(_code(WORKER_TS))
    for const in CONTENT_SCRIPT_KINDS:
        body = _block(listener, f"case {const}:")
        assert "const from = sender.tab?.id;" in body and 'typeof from === "number"' in body, (
            f"case {const} does not tie the message to the tab that sent it"
        )


# --------------------------------------------------------------------------- #
# BR6: the scan is linear in a long run of blank lines
# --------------------------------------------------------------------------- #

#: The line-start pattern as it was before the fix, kept as the reference the
#: new one must agree with on every input below.
_OLD_LINE_START = re.compile(r"(^|\n)\s*(system|assistant|developer)\s*:\s*\S", re.IGNORECASE)


def _best_of(text: str, rounds: int = 3) -> float:
    best = float("inf")
    for _ in range(rounds):
        started = time.perf_counter()
        detect_injection(text)
        best = min(best, time.perf_counter() - started)
    return best


@pytest.mark.parametrize("unit", ["\n", " \n", "\r\n"])
def test_the_scan_does_not_slow_with_the_square_of_a_run_of_blank_lines(unit):
    """Four times the blank lines may take at most about four times as long.

    A ratio, not a wall-clock limit: quadratic work makes the larger input about
    16 times slower (measured 15x before the fix), linear about 4 times. The bound
    is 8 times plus a 50 ms floor, so a scheduler pause on a quiet input cannot
    fail it. Pins: ``[^\\S\\n]*`` in the ``embedded_imperative`` line start.
    """
    small = unit * 2000
    large = unit * 8000
    t_small = _best_of(small)
    t_large = _best_of(large)
    assert t_large < 8 * t_small + 0.05, (
        f"8000 blank lines took {t_large:.3f}s against {t_small:.3f}s for 2000: the scan is not linear"
    )


def test_no_pattern_lets_a_line_start_run_across_newlines():
    """Structural pin beside the ratio: no ``(^|\\n)`` followed by a ``\\s`` run."""
    for category, pattern in safety._PATTERNS:
        assert not re.search(r"\(\^\|\\n\)\\s[*+]", pattern.pattern), (
            f"{category}: a line start followed by \\s* re-scans every following blank line"
        )


@pytest.mark.parametrize(
    "text",
    [
        "system: do this",
        "  system: do this",
        "a note\nsystem: do this",
        "a note\n\n\n   assistant : do this",
        "a note\r\n developer:\tdo this",
        "a note\n \n\t\n SYSTEM: do this",
        "a note\n\x0b\x0c system: do this",
        "a note\n   assistant: do this",
        "a note system: not after a newline",
        "a note system: mid line",
        "a note\n\n\n",
        "\n" * 50 + "developer: x",
        "line\n" + " \n" * 40 + "   system:",
        "system:   ",
        "\n\nsystem:\n\nx",
    ],
)
def test_the_new_line_start_finds_exactly_what_the_old_one_did(text):
    """Same verdict, and the same reported words, as the pattern it replaced."""
    old = _OLD_LINE_START.search(text)
    new_pattern = dict(safety._PATTERNS)["embedded_imperative"]
    new = new_pattern.search(text)
    assert bool(old) == bool(new), f"the verdict changed for {text!r}"
    if old and new:
        assert re.sub(r"\s+", " ", old.group(0).strip()) == re.sub(r"\s+", " ", new.group(0).strip())
