"""v1.235.0 — the Browser wire protocol is generated, not written twice.

What this file pins, and the silent failure each assertion catches:

**The generated TypeScript matches the Python source of truth, byte for byte.**
``extensions/chrome/src/protocol.ts`` is written by
``iron_jarvis.browser.gen_protocol`` from ``iron_jarvis.browser.protocol``. If the
two are ever allowed to drift, the failure is invisible on both sides of the
socket: the extension sends a frame type the daemon's restricted-state machine has
never heard of, the socket closes with 1008, and nothing anywhere names the string
that was wrong. The extension's own build is not part of this suite, so the Python
gate is the only gate that always runs — which is why the comparison lives here
rather than in a Node script. A hand edit to the committed file, or a change to
``protocol.py`` with no regeneration, fails this test.

**The comparison normalises CRLF at the reader.** GitHub's Windows runners check
out with ``core.autocrlf=true``, so the committed file is read as ``\\r\\n`` there
and ``\\n`` here. A byte comparison without normalisation would pass on every
local run and fail on every CI run — the exact shape that took the v1.232.0
installer down. The generator writes ``\\n`` explicitly for the same reason; both
halves are asserted.

**Every error code carries a non-empty remedy (D15).** The rule is "do not return
opaque generic failures where a recovery action is known". A code added to the enum
without a ``REMEDIES`` entry would answer the model with an empty ``message``,
which is worse than a generic one: the model reads a successful-looking envelope
with nothing in it. The two remedies the plan fixes verbatim
(``STALE_ELEMENT`` and ``STALE_SNAPSHOT``) are pinned as exact strings, because
they are the two the model is expected to *act* on most often and a reworded
"the page changed, try again" loses the name of the call to make.

**Every frame type round-trips through JSON and has a declared shape.** A frame
built by its own builder, serialised and read back, must be unchanged and must
carry a ``type`` the protocol admits. And ``FRAME_SHAPES`` must cover exactly
``ALL_FRAME_TYPES``: a frame type added without a shape reaches the extension
undocumented, and a shape with no type is dead weight the generator still emits.

**The sensitive-field vocabularies are re-exported, never restated.** The content
script scrubs a password or payment field by ``autocomplete`` token (D13B). If the
TypeScript held its own copy of that list, a token added to
``computeruse/policy.py`` would escalate on the Python side while the page happily
sent the field's value across the socket. So the tuples here are asserted to be
the policy module's own sets.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from iron_jarvis.browser import gen_protocol
from iron_jarvis.browser import protocol as P
from iron_jarvis.browser.errors import REMEDIES, BrowserError, BrowserErrorCode, browser_error
from iron_jarvis.computeruse.policy import _PASSWORD_AUTOCOMPLETE, _PAYMENT_AUTOCOMPLETE


def _read_normalised(path: Path) -> str:
    """Read a source file with CRLF folded to LF — normalise at the READER, once."""
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


# --------------------------------------------------------------------------- #
# The generated TypeScript
# --------------------------------------------------------------------------- #


def test_committed_protocol_ts_matches_a_fresh_generation():
    committed = _read_normalised(gen_protocol.OUTPUT_PATH)
    assert committed == gen_protocol.render(), (
        "extensions/chrome/src/protocol.ts is stale or hand-edited. Run "
        "`uv run python -m iron_jarvis.browser.gen_protocol` and commit the result."
    )


def test_generated_file_says_it_is_generated():
    committed = _read_normalised(gen_protocol.OUTPUT_PATH)
    # The needle is a single line: a pin whose needle carries an embedded newline
    # matches locally and never on a CRLF checkout.
    assert "// GENERATED FILE — DO NOT EDIT." in committed
    assert "iron_jarvis.browser.gen_protocol" in committed


def test_generator_writes_lf_even_on_windows(tmp_path):
    written = gen_protocol.write(tmp_path / "protocol.ts")
    raw = written.read_bytes()
    assert b"\r\n" not in raw, "the generator must write LF, or the committed bytes vary by machine"
    assert raw.decode("utf-8") == gen_protocol.render()


def test_every_constant_and_error_code_reaches_the_typescript():
    generated = gen_protocol.render()
    for frame_type in P.ALL_FRAME_TYPES:
        assert json.dumps(frame_type) in generated, f"{frame_type} never reaches the extension"
    for method in P.ALL_METHODS:
        assert json.dumps(method) in generated, f"method {method} never reaches the extension"
    for code in BrowserErrorCode:
        assert json.dumps(code.value) in generated, f"{code.value} never reaches the extension"
    for shape in P.FRAME_TYPEDDICTS:
        assert f"export interface {shape.__name__} {{" in generated


def test_optional_frame_fields_generate_as_optional():
    # `from __future__ import annotations` empties TypedDict.__optional_keys__, so
    # the generator reads NotRequired off get_type_hints(include_extras=True). Get
    # that wrong and `result`/`error` become mandatory in the TypeScript while the
    # daemon sends exactly one of them.
    generated = gen_protocol.render()
    assert "  result?: Record<string, unknown>;" in generated
    assert "  error?: ErrorEnvelope;" in generated
    assert "  params?: Record<string, unknown>;" in generated


# --------------------------------------------------------------------------- #
# Error codes and remedies (D15)
# --------------------------------------------------------------------------- #


def test_all_seventeen_d15_codes_exist():
    assert len(list(BrowserErrorCode)) == 17
    expected = {
        "BROWSER_NOT_CONNECTED",
        "BROWSER_ACCESS_OFF",
        "READ_ONLY_MODE",
        "TAB_NOT_FOUND",
        "PAGE_NOT_READY",
        "ELEMENT_NOT_FOUND",
        "STALE_ELEMENT",
        "STALE_SNAPSHOT",
        "PERMISSION_DENIED",
        "ACTION_TIMEOUT",
        "NAVIGATION_FAILED",
        "DOWNLOAD_FAILED",
        "UNSUPPORTED_PAGE",
        "EXTENSION_ERROR",
        "PAIRING_REQUIRED",
        "AUTHENTICATION_FAILED",
        "CONNECTION_REPLACED",
    }
    assert {code.value for code in BrowserErrorCode} == expected


@pytest.mark.parametrize("code", list(BrowserErrorCode), ids=lambda c: c.value)
def test_every_code_has_a_non_empty_model_actionable_remedy(code):
    assert code in REMEDIES, f"{code.value} has no remedy — the model would get an empty message"
    text = REMEDIES[code]
    assert text.strip(), f"{code.value} has a blank remedy"
    # A remedy that names no next step is the opaque failure D15 forbids.
    assert text.rstrip().endswith("."), f"{code.value}'s remedy is not a sentence"


@pytest.mark.parametrize("code", list(BrowserErrorCode), ids=lambda c: c.value)
def test_browser_error_envelope_is_two_keys_and_never_raises_unformatted(code):
    env = browser_error(code)
    assert set(env) == {"code", "message"}
    assert env["code"] == code.value
    assert env["message"], f"{code.value} produced an empty message with no kwargs"


def test_stale_remedies_are_the_plans_verbatim_wording():
    assert REMEDIES[BrowserErrorCode.STALE_ELEMENT] == (
        "The page changed after the previous snapshot. Call browser_read_page "
        "and retry using the new element ID."
    )
    assert REMEDIES[BrowserErrorCode.STALE_SNAPSHOT] == (
        "No current snapshot for this tab. Call browser_read_page and retry "
        "with the new element ID."
    )


def test_placeholders_are_filled_when_given_and_survive_when_not():
    filled = browser_error(BrowserErrorCode.TAB_NOT_FOUND, tab_id=42)
    assert "No tab 42 is open." in filled["message"]
    # A caller that forgot the kwarg still gets a usable next step rather than a
    # KeyError traceback replacing the failure it was reporting.
    bare = browser_error(BrowserErrorCode.TAB_NOT_FOUND)
    assert "browser_list_tabs" in bare["message"]


def test_unknown_code_degrades_instead_of_raising():
    env = browser_error("NOT_A_CODE")
    assert env["code"] == "NOT_A_CODE"
    assert env["message"], "an unknown code must still say something"


def test_browser_error_exception_carries_the_code_and_the_envelope():
    exc = BrowserError(BrowserErrorCode.STALE_ELEMENT)
    assert exc.code == "STALE_ELEMENT"
    assert exc.envelope() == browser_error(BrowserErrorCode.STALE_ELEMENT)
    # An explicit message wins, so the extension's own words can be relayed
    # without inventing an eighteenth code.
    relayed = BrowserError(BrowserErrorCode.EXTENSION_ERROR, "chrome.tabs.get threw")
    assert relayed.envelope() == {
        "code": "EXTENSION_ERROR",
        "message": "chrome.tabs.get threw",
    }


# --------------------------------------------------------------------------- #
# Frames
# --------------------------------------------------------------------------- #


def _one_of_every_frame() -> dict[str, dict]:
    """One built frame per declared frame type, via the module's own builders."""
    return {
        P.FRAME_COMMAND: P.command_frame("req_1", P.METHOD_READ_PAGE, {"tab_id": 42}),
        P.FRAME_DIRECTIVE: P.directive_frame("req_2", P.DIRECTIVE_REQUEST_HOST_PERMISSIONS),
        P.FRAME_PAIRED: P.paired_frame("plaintext-once"),
        P.FRAME_PAIRING_REQUIRED: P.pairing_required_frame("pair_abc"),
        P.FRAME_READY: P.ready_frame(True),
        P.FRAME_CONNECTION_REPLACED: P.connection_replaced_frame(),
        P.FRAME_HELLO: P.hello_frame("lgihfomaieifpnemakmpadmggjnoojmm", "1.235.0", True),
        P.FRAME_RESPONSE: P.response_frame("req_1", {"tabs": [], "count": 0}),
        P.FRAME_EVENT: P.event_frame("evt_1", P.EVENT_TAB_ACTIVATED, {"tab_id": 42}),
        P.FRAME_PAIRING_ACK: P.pairing_ack_frame("pair_abc"),
    }


def test_every_frame_type_round_trips_through_json_unchanged():
    frames = _one_of_every_frame()
    assert set(frames) == set(P.ALL_FRAME_TYPES), "a frame type has no builder here"
    for frame_type, frame in frames.items():
        assert frame["type"] == frame_type
        assert json.loads(json.dumps(frame)) == frame, f"{frame_type} does not survive JSON"


def test_frame_shapes_cover_exactly_the_declared_frame_types():
    assert set(P.FRAME_SHAPES) == set(P.ALL_FRAME_TYPES)
    assert set(P.FRAME_SHAPES.values()) | {P.ErrorEnvelope} == set(P.FRAME_TYPEDDICTS)


def test_the_two_directions_are_disjoint():
    # A type in both directions makes the restricted-state filter meaningless: the
    # daemon could not tell a frame it sent from a frame it received.
    assert not set(P.DAEMON_TO_EXTENSION) & set(P.EXTENSION_TO_DAEMON)
    assert len(set(P.ALL_FRAME_TYPES)) == len(P.ALL_FRAME_TYPES)


def test_a_restricted_socket_may_send_only_the_pairing_ack():
    assert P.RESTRICTED_INBOUND_FRAMES == (P.FRAME_PAIRING_ACK,)
    assert set(P.RESTRICTED_INBOUND_FRAMES) <= set(P.EXTENSION_TO_DAEMON)


def test_a_failed_response_carries_an_error_and_no_result():
    frame = P.error_response_frame("req_9", BrowserErrorCode.STALE_ELEMENT)
    assert frame["success"] is False
    assert "result" not in frame
    assert frame["error"]["code"] == "STALE_ELEMENT"
    ok = P.response_frame("req_9", {"n": 1})
    assert ok["success"] is True and "error" not in ok


def test_command_ids_and_event_ids_use_different_prefixes():
    # A stray response carrying an event id must not be able to resolve a command
    # future, which is exactly what one shared prefix would allow.
    assert P.REQUEST_ID_PREFIX != P.EVENT_ID_PREFIX
    assert P.PAIRING_ID_PREFIX not in (P.REQUEST_ID_PREFIX, P.EVENT_ID_PREFIX)


def test_method_sets_partition_all_methods_by_risk():
    assert set(P.READ_METHODS) | set(P.LOCAL_UI_METHODS) | set(P.PAGE_ACTION_METHODS) == set(
        P.ALL_METHODS
    )
    assert len(P.ALL_METHODS) == len(set(P.ALL_METHODS)) == 14
    assert not set(P.READ_METHODS) & set(P.PAGE_ACTION_METHODS)


def test_command_timeouts_are_bounds_not_performance_claims():
    # Ordering only: no assertion here measures elapsed time.
    assert P.command_timeout_s(P.METHOD_NAVIGATE) > P.command_timeout_s(P.METHOD_LIST_TABS)
    assert P.command_timeout_s(P.METHOD_LIST_TABS) == P.DEFAULT_COMMAND_TIMEOUT_S


def test_frame_byte_cap_is_declared_on_the_wire_contract():
    # The cap has to be readable before json.loads runs, so it lives with the wire
    # protocol rather than with the snapshot code the frame eventually becomes.
    assert P.MAX_FRAME_BYTES == 512 * 1024


# --------------------------------------------------------------------------- #
# Vocabularies (D13B)
# --------------------------------------------------------------------------- #


def test_sensitive_autocomplete_is_the_policy_modules_own_vocabulary():
    assert set(P.PASSWORD_AUTOCOMPLETE) == set(_PASSWORD_AUTOCOMPLETE)
    assert set(P.PAYMENT_AUTOCOMPLETE) == set(_PAYMENT_AUTOCOMPLETE)
    assert set(P.SENSITIVE_AUTOCOMPLETE) == set(_PASSWORD_AUTOCOMPLETE) | set(
        _PAYMENT_AUTOCOMPLETE
    )


def test_vocabularies_are_sorted_so_the_generated_file_is_stable():
    # An unsorted set would make the byte comparison above fail at random between
    # runs, which trains a reader to re-run the gate instead of reading it.
    assert list(P.PASSWORD_AUTOCOMPLETE) == sorted(P.PASSWORD_AUTOCOMPLETE)
    assert list(P.PAYMENT_AUTOCOMPLETE) == sorted(P.PAYMENT_AUTOCOMPLETE)
    assert list(P.SENSITIVE_AUTOCOMPLETE) == sorted(P.SENSITIVE_AUTOCOMPLETE)


@pytest.mark.parametrize(
    "url,expected",
    [
        ("chrome://extensions", "chrome:"),
        ("edge://settings", "edge:"),
        ("about:blank", "about:"),
        ("devtools://devtools/bundled/x.html", "devtools:"),
        ("view-source:https://example.com", "view-source:"),
        ("chrome-extension://abc/popup.html", "chrome-extension:"),
        ("https://chromewebstore.google.com/detail/x", "https:"),
        ("https://example.com/page", ""),
        ("", ""),
    ],
)
def test_unsupported_pages_are_named_by_scheme_before_a_command_is_sent(url, expected):
    # The refusal is daemon-side, so it cannot fail silently inside the page, and
    # it names the scheme because the remedy sentence quotes it back to the model.
    assert P.unsupported_page_scheme(url) == expected
