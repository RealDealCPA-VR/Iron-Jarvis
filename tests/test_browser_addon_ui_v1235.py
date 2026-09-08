"""The browser add-on's PANEL is pinned — the words, and the button's honesty.

Ship 1 of the Browser capability (v1.235.0), MOVED TO THE SIDE PANEL at v1.242.0.
The popup is gone (D32: Chrome ignores ``openPanelOnActionClick`` while
``action.default_popup`` is set), and its status readout moved into the side panel's
header rather than being rewritten there — so every pin below moved with it, none
was deleted, and the two findings they encode are still the findings.

The panel is the only Iron Jarvis surface that lives inside the user's browser, and
every word it renders is written at runtime by ``sidepanel.ts``. Two defects in the
reviewed diff lived exactly there:

* the "Not connected" state offered a button labelled **Disconnect**, and pressing
  it persisted ``ij.browser.suspended`` — a state no other surface reported and no
  visible control undid, so the cure a user reached for (reload the add-on) landed
  them in the same place and pairing looked broken;
* the **Access** row printed the placeholder "Set in Jarvis" in every state,
  including Connected, because ``browser.ready`` never carried an ``access`` field
  at all. A placeholder sitting where a mode belongs reads AS the mode.

There is no test harness for the add-on's TypeScript in this repo (``extensions/
chrome`` has no vitest project and must not grow one), so these are SOURCE pins, the way this repo pins
untestable surfaces — plus real executable assertions on the Python half of the
wire, which is where the ``access`` field is actually defined.

How the pins are written, because the shape matters more than the assertions:

* The reader normalises CRLF once (``_read``). GitHub's Windows runners check the
  tree out with ``\\r\\n``, and a needle carrying an embedded newline matches
  locally and never on CI — the trap that took the v1.232.0 installer down.
* No fixed-size windows. Function and block bodies are found by counting braces
  from the declaration, so adding a line or a comment cannot make a pin report
  "never rendered" when the truth is "something moved".
* The pins assert BEHAVIOUR the user can see: which word each state puts on the
  button, that the word is derived from the same function the service worker acts
  on, that a suspended browser always shows the way back, and that an unreported
  access mode is named as unknown rather than dressed as a value.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import NotRequired, get_args, get_origin, get_type_hints

import pytest

from iron_jarvis.browser import protocol as P
from iron_jarvis.browser.service import ACCESS_LEVELS

REPO = Path(__file__).resolve().parents[1]
ADDON = REPO / "extensions" / "chrome" / "src"
PANEL_TS = ADDON / "sidepanel" / "sidepanel.ts"
PANEL_HTML = ADDON / "sidepanel" / "sidepanel.html"
SETUP_HTML = ADDON / "setup" / "setup.html"
SOCKET_TS = ADDON / "bridge" / "socket.ts"
WORKER_TS = ADDON / "background" / "index.ts"
PROTOCOL_TS = ADDON / "protocol.ts"

#: What each bridge state's one button must DO. The whole point of the finding: a
#: state that is not connected may not offer to disconnect, and a state the user
#: cannot leave on their own may not be reachable at all.
EXPECTED_ACTIONS = {
    "connected": "disconnect",
    "pairing": "disconnect",
    "replaced": "connect",
    "suspended": "connect",
    "offline": "none",
}


def _read(path: Path) -> str:
    """One CRLF normalisation per file, at the reader. Never at a call site."""
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def _code(path: Path) -> str:
    """The file with its COMMENTS removed, for pins that read behaviour.

    Necessary rather than tidy: this file's own needles (``state === "suspended"``,
    the old ``"Set in Jarvis"`` placeholder) are quoted in the source's comments,
    where they explain the defect being pinned. A pin that matched prose would go
    red on a comment and green on the bug. The lookbehind keeps ``http://`` and
    ``chrome://`` inside string literals intact.
    """
    text = _read(path)
    text = re.sub(r"/\*[\s\S]*?\*/", "", text)
    return re.sub(r"(?<!:)//.*", "", text)


def _block(text: str, opener: str) -> str:
    """The braced body that follows ``opener``, found by counting braces.

    Not a fixed window: a declaration that gains a line, a comment or a parameter
    still yields its whole body, so a pin over it fails when the BEHAVIOUR changes
    rather than when the formatting does.
    """
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


def _switch_returns(body: str) -> dict[str, str]:
    """``case "x":`` -> the first string literal ``return``ed at or after it.

    Searching FORWARD from each label is what makes fall-through read correctly: two
    labels sharing one ``return`` both resolve to it, which is how the source is
    actually written.
    """
    labels = [(m.group(1), m.end()) for m in re.finditer(r'case "([a-z_]+)":', body)]
    out: dict[str, str] = {}
    for name, pos in labels:
        found = re.search(r'return "([a-z_]*)"', body[pos:])
        assert found, f'case "{name}" returns no string literal'
        out[name] = found.group(1)
    return out


def _record_literal(text: str, name: str) -> dict[str, str]:
    """A ``Record<string, string>``-shaped const, as a dict."""
    body = _block(text, name)
    return {m.group(1): m.group(2) for m in re.finditer(r'(\w+): "([^"]*)"', body)}


def _string_array(text: str, name: str) -> list[str]:
    """The string members of ``export const <name> = [...]``."""
    start = text.index(f"const {name} =")
    end = text.index("]", start)
    return re.findall(r'"([a-z_]+)"', text[start:end])


# --------------------------------------------------------------------------- #
# The one button: its word is what it does
# --------------------------------------------------------------------------- #


def test_every_bridge_state_maps_to_the_action_its_button_promises():
    """``toggleAction`` is the single mapping both surfaces read."""
    socket = _code(SOCKET_TS)
    actions = _switch_returns(_block(socket, "export function toggleAction("))
    states = _string_array(socket, "BRIDGE_STATES")
    assert set(states) == set(EXPECTED_ACTIONS), (
        "a bridge state exists that this pin does not know about; every state the "
        "panel can be in has to say what its button does"
    )
    for state in states:
        assert actions.get(state) == EXPECTED_ACTIONS[state], (
            f"state {state!r} maps to {actions.get(state)!r}, expected "
            f"{EXPECTED_ACTIONS[state]!r} — the button would offer the wrong thing"
        )


def test_a_state_that_is_not_connected_never_offers_to_disconnect():
    """The finding, stated as the property that was violated.

    While the daemon was merely restarting, the panel said "Not connected" over a
    button labelled Disconnect, and the press persisted a suspend flag.
    """
    socket = _code(SOCKET_TS)
    actions = _switch_returns(_block(socket, "export function toggleAction("))
    labels = _record_literal(socket, "TOGGLE_LABELS")
    assert labels["none"] == "", "no action must render no word, so no button is drawn"
    for state in ("offline", "suspended", "replaced"):
        word = labels[actions[state]]
        assert "disconnect" not in word.lower(), (
            f"state {state!r} would put {word!r} on the button while nothing is connected"
        )
    assert "disconnect" in labels[actions["connected"]].lower(), (
        "a connected browser must still be able to be disconnected"
    )


def test_a_suspended_browser_always_shows_the_way_back():
    """The suspend flag is PERSISTED, so a state with no Connect button strands the user."""
    socket = _code(SOCKET_TS)
    actions = _switch_returns(_block(socket, "export function toggleAction("))
    labels = _record_literal(socket, "TOGGLE_LABELS")
    for state in ("suspended", "replaced"):
        assert actions[state] == "connect"
        assert "connect" in labels[actions[state]].lower(), (
            f"state {state!r} offers no visible way back onto the bridge"
        )
    panel = _code(PANEL_TS)
    suspended = _block(panel, 'case "suspended":')
    assert "press Connect" in suspended, (
        "the suspended state's note names the button the user must press; if the "
        "button's word changes this sentence has to change with it"
    )


def test_the_panel_never_writes_a_button_label_of_its_own():
    """The label comes from the action, so the two cannot disagree."""
    panel = _code(PANEL_TS)
    describe = _block(panel, "export function describe(")
    assert "TOGGLE_LABELS[toggleAction(" in describe, (
        "describe() must derive the button's word from toggleAction; a per-branch "
        "literal is how the offline branch came to say Disconnect"
    )
    assert not re.search(r'toggle:\s*"', panel), (
        "a hardcoded toggle label is back in sidepanel.ts — it can be wrong about "
        "what the press will do, and nothing else would notice"
    )


def test_the_button_is_hidden_when_there_is_nothing_for_it_to_do():
    """D28's disconnected panel offers Open Jarvis only."""
    panel = _code(PANEL_TS)
    assert 'el.toggle.hidden = view.toggle === "";' in panel, (
        "the button must be hidden exactly when the action is none; a visible "
        "button that does nothing reads as a broken add-on"
    )
    html = _read(PANEL_HTML)
    tag = re.search(r'<button[^>]*id="toggle"[^>]*>(.*?)</button>', html, flags=re.S)
    assert tag, "sidepanel.html no longer declares the toggle button"
    assert "hidden" in tag.group(0), (
        "the button ships hidden: it is painted before the worker answers, and a "
        "static label is a promise the panel may be about to contradict"
    )
    assert tag.group(1).strip() == "", (
        f"sidepanel.html hardcodes the button's label ({tag.group(1).strip()!r}); the "
        "word belongs to the state, not to the markup"
    )


def test_the_worker_acts_on_the_same_mapping_the_panel_labels_from():
    """A label written here and a handler written there is the defect's real shape."""
    worker = _code(WORKER_TS)
    block = _block(worker, 'case "toggle_connection":')
    assert "toggleAction(" in block, (
        "the worker must decide from toggleAction, not from its own reading of the "
        "state — that is how 'Reconnect' came to suspend the browser"
    )
    assert 'action === "connect"' in block and "socket.resume()" in block
    assert 'action === "disconnect"' in block and "socket.suspend()" in block
    assert "} else {" not in block, (
        "an unconditional else persists a suspend flag for any state that is not "
        "explicitly handled, including the merely-offline one"
    )
    assert 'state === "suspended"' not in block, (
        "switching on the state directly is the original bug: every other state, "
        "including replaced and offline, fell into suspend()"
    )


# --------------------------------------------------------------------------- #
# The Access row: a real value, or an honest absence
# --------------------------------------------------------------------------- #


def test_the_ready_frame_can_carry_the_access_mode_and_omits_it_otherwise():
    """Executable, not a source pin: this is the wire, and it is Python."""
    assert P.ready_frame(True, "interactive")["access"] == "interactive"
    assert "access" not in P.ready_frame(True), (
        "an absent mode must be an ABSENT key: an empty string is indistinguishable "
        "from a mode the add-on does not recognise, and the panel would print a blank"
    )
    assert "access" not in P.ready_frame(True, ""), "a falsy mode is still no mode"
    hints = get_type_hints(P.ReadyFrame, include_extras=True)
    assert get_origin(hints["access"]) is NotRequired, (
        "access must be OPTIONAL on the frame: an older daemon and a newer add-on "
        "still have to interoperate"
    )
    assert get_args(hints["access"])[0] is str


def test_the_generated_typescript_carries_the_optional_access_field():
    """The add-on reads the generated file; a field that stops there is not on the wire."""
    ready = _block(_read(PROTOCOL_TS), "export interface ReadyFrame")
    assert "access?: string;" in ready, (
        "regenerate extensions/chrome/src/protocol.ts — the panel's Access row reads "
        "this field and cannot be typed against it otherwise"
    )


def test_the_access_row_renders_the_reported_mode_for_every_level_the_daemon_has():
    panel = _code(PANEL_TS)
    words = _record_literal(panel, "const ACCESS_WORDS")
    assert set(words) == set(ACCESS_LEVELS), (
        f"the add-on words {sorted(words)} do not cover the daemon's access levels "
        f"{sorted(ACCESS_LEVELS)}; a level with no word prints its raw wire value"
    )
    for level, word in words.items():
        assert word and word[0].isupper() and "_" not in word, (
            f"{level!r} renders as {word!r} — the panel must show words, not wire values"
        )
    assert "accessWord(status.access)" in panel, (
        "the Access row must render what the daemon reported; it printed a constant "
        "placeholder in every state, including Connected, for the whole of Ship 1"
    )


def test_an_unreported_access_mode_is_named_as_unknown_not_dressed_as_a_value():
    panel = _code(PANEL_TS)
    unknown = re.search(r'ACCESS_UNKNOWN = "([^"]*)"', panel)
    assert unknown, "sidepanel.ts must name the absent-mode text once, as a constant"
    text = unknown.group(1)
    assert "unknown" in text.lower(), (
        f"the absent mode reads {text!r}, which sits in the Access row looking like "
        "a mode; say it is unknown and say where the answer lives"
    )
    words = _record_literal(panel, "const ACCESS_WORDS")
    assert text not in words.values(), "the absent-mode text must not be a real mode's word"
    assert '"Set in Jarvis"' not in panel, (
        "the old placeholder is back: it was printed in every state including "
        "Connected, and a user reads it as the setting rather than as its absence"
    )
    body = _block(panel, "export function accessWord(")
    assert "ACCESS_UNKNOWN" in body and "ACCESS_WORDS" in body


# --------------------------------------------------------------------------- #
# Copy rule
# --------------------------------------------------------------------------- #


def _visible_html(path: Path) -> str:
    """Everything the user can read on a page: comments, CSS and script removed."""
    text = _read(path)
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    text = re.sub(r"<style.*?</style>", "", text, flags=re.S)
    text = re.sub(r"<script.*?</script>", "", text, flags=re.S)
    return text


@pytest.mark.parametrize("path", [PANEL_HTML, SETUP_HTML], ids=["sidepanel", "setup"])
def test_no_page_of_the_add_on_calls_itself_an_extension(path: Path):
    """In Iron Jarvis "extension" already means an MCP server (copy rule)."""
    visible = _visible_html(path)
    # chrome://extensions is Chrome's own URL, not a name for this add-on.
    visible = visible.replace("chrome://extensions", "")
    hits = re.findall(r"\bextensions?\b", visible, flags=re.I)
    assert not hits, f"{path.name} calls the add-on an extension {len(hits)} time(s)"


def test_no_remedy_the_user_reads_calls_the_add_on_an_extension():
    """These strings reach the Your browser card verbatim, as ``Last problem:``."""
    generated = _read(PROTOCOL_TS)
    remedies = _block(generated, "export const REMEDIES")
    for code, text in re.findall(r'"([A-Z_]+)": "([^"]*)"', remedies):
        cleaned = text.replace("chrome://extensions", "")
        assert not re.search(r"\bextensions?\b", cleaned, flags=re.I), (
            f"the {code} remedy calls the add-on an extension: {text!r}. That string "
            "is rendered to the user on the same screen where extension means an "
            "MCP server — fix errors.py and regenerate protocol.ts"
        )
