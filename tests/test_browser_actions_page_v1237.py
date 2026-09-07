"""The add-on's ACTING half is pinned — what it does inside a page, and what it never does.

Ship 3 of the Browser capability (v1.237.0). ``extensions/chrome/src/content/actions.ts``
is the only Iron Jarvis code that CHANGES a page the user is signed in to, and
``background/tabs.ts`` is the only code that closes, opens or navigates one of their
tabs. There is no TypeScript test harness in this repository, so these are SOURCE
pins in the shape ``tests/test_browser_content_script_v1236.py`` established, plus
real executable assertions against the Python half of every contract the two halves
share.

FOUR PINS CARRY THIS FILE.

``test_no_action_path_reads_a_field_value_back`` is the one to break first if you
want to know whether the file still works. Ship 2's no-value rule (plan section 9.4)
gets HARDER in Ship 3, because the obvious way to append text to a field is to read
it and write it back. ``actions.ts`` never does: it types through
``execCommand("insertText")``, which inserts at the caret, and REFUSES when that is
unavailable and the call did not ask to clear. The pin runs Ship 2's whole value
vocabulary over the two new files.

``test_the_typed_text_never_reaches_the_result`` closes the other end of the same
leak. ``browser_type.redact_args`` replaces ``text`` unconditionally in the ledger;
a result that echoed the typed string would write it back to
``ToolInvocation.output`` — on disk, in the user's backups, and read into later
prompts. The pin reads the keys of the returned object literal and compares them
against the ``TypeTextResult`` TypedDict, so a new key has to be a key Python knows
about, and Python has no key for the text.

``test_the_visibility_and_enabled_rules_are_the_same_two_rules_the_snapshot_uses``
exists because ``snapshot.isRendered`` and ``snapshot.isEnabled`` are private to
Ship 2's file and ``actions.ts`` needs both. Two definitions of "visible" that drift
apart give a snapshot that lists a button and an action that refuses it as invisible
— a contradiction the user reads as a broken tool. The pin compares the two bodies
token for token, so editing one and not the other is RED rather than silent.

``test_an_ambiguous_target_is_refused_rather_than_guessed`` pins the rule that keeps
this ship from being dangerous. A ``role``+``name`` or ``css`` target that matches
two elements is refused naming the count. The alternative is acting on whichever the
DOM listed first, on a page where the two candidates are plausibly "Transfer" and
"Transfer all".

How the pins are written, following the file this one is modelled on: the reader
normalises CRLF once (``_read``); comments are stripped for every pin that reads
behaviour (``_code``), because this file's own needles are quoted in the source's
comments where they explain the rule; braced bodies are found by counting braces, so
adding a line cannot make a pin report "never done" when the truth is "it moved";
and no needle carries an embedded newline.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import NotRequired, get_args, get_origin, get_type_hints

import pytest

from iron_jarvis.browser import protocol as P
from iron_jarvis.browser.extension_backend import (
    DOWNLOAD_PAYLOAD_KEYS,
    download_bus_payload,
)

REPO = Path(__file__).resolve().parents[1]
ADDON = REPO / "extensions" / "chrome"
SRC = ADDON / "src"
CONTENT = SRC / "content"
ACTIONS_TS = CONTENT / "actions.ts"
CONTENT_INDEX_TS = CONTENT / "index.ts"
SNAPSHOT_TS = CONTENT / "snapshot.ts"
TABS_TS = SRC / "background" / "tabs.ts"
WORKER_TS = SRC / "background" / "index.ts"
DOWNLOADS_TS = SRC / "background" / "downloads.ts"
ELEMENTS_TS = CONTENT / "elements.ts"

#: The files Ship 3 added acting code to. Every value pin runs over ALL of them: a
#: field read moved into the service worker would be exactly as permanent as one
#: left in the page.
ACTING_FILES = (ACTIONS_TS, CONTENT_INDEX_TS, TABS_TS, WORKER_TS)


def _read(path: Path) -> str:
    """One CRLF normalisation per file, at the reader. Never at a call site."""
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def _code(path: Path) -> str:
    """The file with its COMMENTS removed, for pins that read behaviour."""
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


def _return_object(body: str) -> str:
    """The body of the LAST ``return {`` object literal in ``body``."""
    at = body.rindex("return {")
    return _block(body[at:], "return {")


def _top_level_keys(body: str) -> list[str]:
    """The keys at nesting depth 0 of one object-literal body.

    A character scanner rather than a line scanner, and the difference is not
    cosmetic: ``return { tab_id: raw, closed: true };`` puts two keys on one line,
    and a per-line reader sees only the first — it would report ``closed`` as absent
    from a result that carries it, which is a pin failing on formatting instead of
    on behaviour. Shorthand (``cleared,``) counts too, because a key written
    shorthand is still a key on the wire.
    """
    keys: list[str] = []
    depth = 0
    index = 0
    size = len(body)
    while index < size:
        char = body[index]
        if char in "\"'`":
            index = _past_string(body, index)
            continue
        if char in "{[(":
            depth += 1
            index += 1
            continue
        if char in "}])":
            depth -= 1
            index += 1
            continue
        if depth == 0 and (char.isalpha() or char == "_"):
            word = re.match(r"[A-Za-z_][A-Za-z0-9_]*", body[index:])
            assert word is not None
            after = index + len(word.group(0))
            while after < size and body[after] in " \t\n":
                after += 1
            if after >= size or body[after] == ",":
                keys.append(word.group(0))
                index = after + 1
                continue
            if body[after] == ":":
                keys.append(word.group(0))
                # Skip the VALUE. Without this, `tab_id: raw` would also record
                # `raw` as a key, and every pin that compares the key set against a
                # TypedDict would fail on a name that is not on the wire at all.
                index = _past_value(body, after + 1)
                continue
            index = after
            continue
        index += 1
    return keys


def _past_string(text: str, index: int) -> int:
    """The index just past the string literal starting at ``index``."""
    quote = text[index]
    index += 1
    while index < len(text) and text[index] != quote:
        index += 2 if text[index] == "\\" else 1
    return index + 1


def _past_value(text: str, index: int) -> int:
    """The index just past one object-literal value, at the comma that ends it."""
    depth = 0
    while index < len(text):
        char = text[index]
        if char in "\"'`":
            index = _past_string(text, index)
            continue
        if char in "{[(":
            depth += 1
        elif char in "}])":
            depth -= 1
        elif char == "," and depth == 0:
            return index + 1
        index += 1
    return index


def _shape_keys(shape: type) -> tuple[set[str], set[str]]:
    """``(required, all)`` keys of one TypedDict."""
    hints = get_type_hints(shape, include_extras=True)
    optional = {
        name
        for name, annotation in hints.items()
        if get_origin(annotation) is NotRequired or get_origin(get_args(annotation)[0] if get_args(annotation) else None) is NotRequired
    }
    return set(hints) - optional, set(hints)


def _tokens(body: str) -> list[str]:
    """One function body as its bare tokens, so whitespace cannot hide a change."""
    return re.findall(r"[A-Za-z_$][A-Za-z0-9_$]*|[^\sA-Za-z0-9_$]", body)


# --------------------------------------------------------------------------- #
# Section 9.4, carried into Ship 3: a field's contents are never read
# --------------------------------------------------------------------------- #

#: The other ways a control's contents can be read, from Ship 2's pin. None of them
#: appears anywhere in the acting path either.
FORBIDDEN_FIELD_PROPERTIES = (
    "checked",
    "defaultValue",
    "files",
    "innerText",
    "selectedIndex",
    "selectedOptions",
    "selectionStart",
    "selectionEnd",
    "textContent",
    "valueAsDate",
    "valueAsNumber",
)


def test_no_action_path_reads_a_field_value_back():
    """The rule that gets harder in Ship 3: type into a field, never read one."""
    for path in ACTING_FILES:
        code = _code(path)
        assert not re.search(r"[A-Za-z_$][A-Za-z0-9_$]*\.value\b", code), (
            f"{path.name} reads or writes .value directly; the acting path goes "
            "through the NATIVE prototype setter (a write React's tracker cannot "
            "swallow) and never reads a field at all"
        )
        assert not re.search(r"\)\s*\.value\b", code), (
            f"{path.name} touches .value off a cast or a call result"
        )
        assert not re.search(r"\]\s*\.value\b", code), (
            f"{path.name} touches .value off an indexed expression"
        )
        assert not re.search(r"\?\.\s*value\b", code), (
            f"{path.name} touches .value through an optional chain; the rule is "
            "about the read, not about the punctuation it is written with"
        )
        assert not re.search(r"""\[\s*['\"]value['\"]\s*\]""", code), (
            f"{path.name} touches a value by string index; that is `el.value` spelled "
            "so a receiver list cannot see it"
        )
        assert "Reflect.get" not in code, (
            f"{path.name} reads a property reflectively, which defeats every pin in "
            "this file that names a property"
        )


@pytest.mark.parametrize("prop", FORBIDDEN_FIELD_PROPERTIES)
def test_no_action_path_reads_a_control_state_off_an_element(prop: str):
    """The other reads that would carry a field's contents out of the page."""
    for path in ACTING_FILES:
        assert f".{prop}" not in _code(path), (
            f"{path.name} reads .{prop}; that is a form control's contents by another "
            "name, and section 9.4 collects none of it — in Ship 3 it would also be "
            "the read half of a read-modify-write that actions.ts refuses to do"
        )


def test_the_only_write_to_a_field_goes_through_the_native_prototype_setter():
    """``node.value = x`` is invisible to React; the prototype descriptor is not."""
    setter = _block(_code(ACTIONS_TS), "function valueSetter")
    assert 'Object.getOwnPropertyDescriptor(proto, "value")?.set' in setter, (
        "valueSetter no longer takes the setter off the PROTOTYPE. React installs "
        "its own value accessor on the instance to track changes, so an instance "
        "write leaves the component's state on the old text: the user is told the "
        "field was filled and the form submits empty"
    )
    for name in ("HTMLTextAreaElement.prototype", "HTMLInputElement.prototype"):
        assert name in setter, f"valueSetter no longer covers {name}"
    code = _code(ACTIONS_TS)
    assert code.count("setter.call(node") == 2, (
        "the field is written somewhere other than the two places that go through "
        f"valueSetter (found {code.count('setter.call(node')} calls)"
    )


def test_appending_never_falls_back_to_a_read_modify_write():
    """When ``execCommand`` is refused and ``clear`` is false, the call REFUSES."""
    body = _block(_code(ACTIONS_TS), "function typeInto")
    assert 'document.execCommand("insertText", false, text)' in body, (
        "typeInto no longer inserts at the caret; execCommand is the only insert "
        "that appends without this script reading what the field already holds"
    )
    fallback = _block(body[body.index("if (!inserted)") :], "if (!inserted)")
    assert "if (!clear)" in fallback, (
        "the execCommand fallback no longer distinguishes clear from append. "
        "Overwriting on an append would silently destroy what the user typed, in a "
        "call whose result says 'typed'"
    )
    refusal = _block(fallback[fallback.index("if (!clear)") :], "if (!clear)")
    assert "throw new BridgeError" in refusal, (
        "the append-with-no-insert path no longer refuses, so it now overwrites the "
        "user's own text"
    )
    assert "clear: true" in refusal, "the refusal does not name the retry that would work"


# --------------------------------------------------------------------------- #
# The result never echoes what was typed
# --------------------------------------------------------------------------- #

#: (file, exported function, the wire method whose result shape it must match).
RESULT_BUILDERS = (
    (ACTIONS_TS, "export function click", "click"),
    (ACTIONS_TS, "export function typeText", "type_text"),
    (ACTIONS_TS, "export function pressKey", "press_key"),
    (ACTIONS_TS, "export function scroll", "scroll"),
    (TABS_TS, "export async function activateTab", "activate_tab"),
    (TABS_TS, "export async function closeTab", "close_tab"),
    (TABS_TS, "export async function createTab", "create_tab"),
    (TABS_TS, "export async function navigate", "navigate"),
)


@pytest.mark.parametrize("path,opener,method", RESULT_BUILDERS)
def test_every_acting_result_matches_the_shape_python_declares(
    path: Path, opener: str, method: str
):
    """Each result carries every required key and invents none.

    The comparison is against the PYTHON TypedDict rather than a literal written
    here: the daemon reads these keys, and a result that grew a key Python has never
    heard of would be dropped silently on one side of the wire and relied on by the
    other.
    """
    shape = P.RESULT_SHAPES[method]
    required, allowed = _shape_keys(shape)
    keys = set(_top_level_keys(_return_object(_block(_code(path), opener))))
    assert required <= keys, f"{method} omits {sorted(required - keys)}"
    assert keys <= allowed, (
        f"{method} returns {sorted(keys - allowed)}, which {shape.__name__} does not "
        "declare"
    )


def test_the_typed_text_never_reaches_the_result():
    """``type_text`` reports WHAT it typed into, never what the field now holds."""
    _, allowed = _shape_keys(P.RESULT_SHAPES["type_text"])
    assert "text" not in allowed and "value" not in allowed, (
        "TypeTextResult grew a key for the typed text. browser_type redacts `text` "
        "in the ledger unconditionally; a result that echoed it would write the "
        "password back to ToolInvocation.output, which is on disk, in the user's "
        "backups, and read into later prompts"
    )
    body = _block(_code(ACTIONS_TS), "export function typeText")
    keys = _top_level_keys(_return_object(body))
    assert "typed_into" in keys, "type_text no longer reports what it typed into"
    for banned in ("text", "value", "contents"):
        assert banned not in keys, f"the type_text result carries {banned!r}"
    ref = _block(_code(ACTIONS_TS), "function targetRef")
    assert set(_top_level_keys(_return_object(ref))) == {"element_id", "role", "name"}, (
        "targetRef no longer reports exactly the element id, role and accessible "
        "name — the three facts that identify a field without quoting it"
    )


# --------------------------------------------------------------------------- #
# Target resolution (plan section 8.6)
# --------------------------------------------------------------------------- #


def test_the_three_target_forms_resolve_in_the_order_the_plan_names():
    """``element_id`` preferred, then ``role``+``name``, then ``css``."""
    body = _block(_code(ACTIONS_TS), "export function resolveTarget")
    order = [
        body.index("registry.resolve(elementId"),
        body.index("document.querySelectorAll(css)"),
        body.index("candidates().filter"),
    ]
    assert order[0] < order[1], "a CSS target is resolved before an element_id"
    assert order[0] < order[2], "a role target is resolved before an element_id"
    for constant in ("TARGET_ELEMENT_ID", "TARGET_ROLE", "TARGET_CSS"):
        assert constant in body, (
            f"resolveTarget spells the {constant} key by hand instead of using the "
            "constant generated from protocol.py, so the two halves can disagree "
            "about what a target is"
        )


def test_a_target_naming_two_forms_is_refused_and_the_conflict_is_named():
    """Two forms is an error, never a guess resolved by preference order."""
    body = _block(_code(ACTIONS_TS), "export function resolveTarget")
    assert "forms.length > 1" in body, (
        "resolveTarget no longer refuses a two-form target. A caller that sent both "
        "an element_id and a css is unsure which is right, and honouring the first "
        "acts on the target it did not mean"
    )
    refusal = _block(body[body.index("if (forms.length > 1)") :], "if (forms.length > 1)")
    assert "forms.join" in refusal, "the two-form refusal does not name which forms collided"
    assert "throw noTarget" in refusal, "the two-form case no longer throws"


def test_an_ambiguous_target_is_refused_rather_than_guessed():
    """Two matching elements is a refusal naming the count, never the first match."""
    body = _block(_code(ACTIONS_TS), "function exactlyOne")
    assert "pool.length > 1" in body, (
        "exactlyOne no longer refuses on more than one match, so a role or CSS "
        "target now acts on whichever element the DOM happened to list first — on a "
        "page where the two candidates are plausibly 'Transfer' and 'Transfer all'"
    )
    guard = _block(body[body.index("if (pool.length > 1") :], "if (pool.length > 1")
    assert "throw noTarget" in guard, "the ambiguous case no longer throws"
    assert "element_id" in guard, "the ambiguity refusal does not name the remedy"


def test_a_role_target_only_matches_what_a_snapshot_would_have_shown():
    """Candidates come from the registry's own interactive set, not the whole DOM."""
    body = _block(_code(ACTIONS_TS), "function candidates")
    assert "INTERACTIVE_SELECTOR" in body and "isInteractive(el)" in body, (
        "the candidate set no longer matches the snapshot's registry, so a role "
        "target can resolve to an element browser_read_page never showed the model "
        "— the model would be acting on something it has no description of"
    )


# --------------------------------------------------------------------------- #
# Staleness is decided here (plan section 9.3)
# --------------------------------------------------------------------------- #


def test_staleness_is_decided_by_the_registry_and_never_re_derived():
    """One answer to 'is this element still there', and it is the registry's."""
    body = _block(_code(ACTIONS_TS), "export function resolveTarget")
    assert "registry.resolve(elementId, snapshotId)" in body, (
        "resolveTarget no longer hands the element id to the registry. The registry "
        "owns the order — STALE_SNAPSHOT, then STALE_ELEMENT, then ELEMENT_NOT_FOUND "
        "— and a second answer diverges from the first within a second on a real page"
    )
    code = _code(ACTIONS_TS)
    for own in ("STALE_SNAPSHOT", "STALE_ELEMENT"):
        assert own not in code, (
            f"actions.ts raises {own} itself; that decision belongs to elements.ts, "
            "where the live node and the version it was captured at both are"
        )


def test_the_snapshot_id_the_caller_named_is_carried_into_the_registry():
    """The caller's snapshot id reaches ``registry.resolve``, end to end.

    REWRITTEN after Ship 3's mutation round proved the first version worthless: it
    counted ``params["snapshot_id"]`` occurrences at the three entry points and
    never looked at the registry call, so replacing
    ``registry.resolve(elementId, snapshotId)`` with ``registry.resolve(elementId)``
    — which drops the caller's pin entirely and resolves against whatever the page
    holds now — left it GREEN. A pin whose name is about an argument has to follow
    the argument.

    Both hops are asserted, because the value can be lost at either: an entry point
    that stops reading ``params["snapshot_id"]`` hands ``resolveTarget`` a null, and
    a ``resolveTarget`` that stops passing it on drops it one line later.
    """
    code = _code(ACTIONS_TS)
    for opener in ("export function click", "export function typeText", "export function pressKey"):
        body = _block(code, opener)
        assert 'params["snapshot_id"]' in body, (
            f"{opener} no longer reads the caller's snapshot_id, so a call that "
            "pinned a snapshot resolves against whatever the page holds now"
        )
        assert "resolveTarget(" in body, f"{opener} no longer resolves through resolveTarget"
        assert body.index('params["snapshot_id"]') > body.index("resolveTarget("), (
            f"{opener} reads snapshot_id somewhere other than in the resolveTarget "
            "call, so the pin can no longer tell that the two are connected"
        )
    resolver = _block(code, "export function resolveTarget")
    assert "registry.resolve(elementId, snapshotId)" in resolver, (
        "resolveTarget hands the registry an element id WITHOUT the snapshot the "
        "caller named. The registry then answers from whichever snapshot it happens "
        "to hold, so an id from the read the model actually did resolves against a "
        "later capture and the click lands on a different element"
    )


def test_the_stale_element_branch_is_guarded_by_a_version_the_registry_derives():
    """``moved`` is a derivation, not a constant, and STALE_ELEMENT turns on it.

    The companion to the pin above, and it exists because Ship 3's mutation round
    showed the ORDER pins survive ``get moved() { return false; }`` — a registry
    that never reports movement answers every stale id as fresh, and the order of
    the three refusals is then irrelevant because none of them fires.
    """
    code = _code(ELEMENTS_TS)
    moved = _block(code, "get moved()")
    assert "versionAtCapture" in moved and "this.version" in moved, (
        "ElementRegistry.moved no longer compares the current version against the "
        "one the snapshot was captured at, so a page that changed under a snapshot "
        "reports itself unchanged and every stale element id resolves to a live node"
    )
    resolve = _block(code, "resolve(elementId: string")
    assert "if (this.moved)" in resolve, (
        "resolve no longer gates STALE_ELEMENT on `moved`, so the refusal that "
        "sends a model back to browser_read_page after a re-render is gone"
    )


def test_an_element_that_exists_but_cannot_be_acted_on_says_which():
    """Section 9.3's last row: invisible and disabled fail differently."""
    body = _block(_code(ACTIONS_TS), "function requireActionable")
    assert "isRendered(resolved.node)" in body and "isEnabled(resolved.node)" in body, (
        "requireActionable no longer checks both visibility and enablement"
    )
    assert "not visible" in body, "the invisible case no longer says it is invisible"
    assert "not enabled" in body, "the disabled case no longer says it is disabled"
    assert body.index("not visible") < body.index("not enabled"), (
        "the two notes swapped, so an invisible element is now reported as disabled"
    )
    described = _block(_code(ACTIONS_TS), "function notActionable")
    assert '"ELEMENT_NOT_FOUND"' in described, (
        "an element that is on the page but not actionable no longer answers "
        "ELEMENT_NOT_FOUND, so the model is sent looking for a control that is "
        "right there"
    )


def test_the_visibility_and_enabled_rules_are_the_same_two_rules_the_snapshot_uses():
    """The two mirrored predicates are compared token for token against Ship 2's."""
    for name in ("function isRendered", "function isEnabled"):
        mine = _tokens(_block(_code(ACTIONS_TS), name))
        theirs = _tokens(_block(_code(SNAPSHOT_TS), name))
        assert mine == theirs, (
            f"{name} in actions.ts has drifted from the one in snapshot.ts. A "
            "snapshot that lists a button while an action refuses it as invisible is "
            "a contradiction the user reads as a broken tool. Change both, or export "
            "one and import it"
        )


# --------------------------------------------------------------------------- #
# The events a real page listens for
# --------------------------------------------------------------------------- #


def test_a_click_dispatches_the_pointer_sequence_and_not_only_click():
    """A control built on a pointer library never sees a bare ``click()``."""
    body = _block(_code(ACTIONS_TS), "function clickNode")
    for event in ("pointerdown", "mousedown", "pointerup", "mouseup"):
        assert f'"{event}"' in body, (
            f"clickNode no longer dispatches {event}. HTMLElement.click() fires the "
            "click event and nothing else, so a drag handle or a custom menu does "
            "nothing at all while the user is told the button was pressed"
        )
    assert "clickable.click()" in body, (
        "clickNode no longer calls click(), which is what performs the element's "
        "DEFAULT action — following a link, submitting a form — that a synthesised "
        "click event cannot do"
    )
    assert body.index('"mouseup"') < body.index("clickable.click()"), (
        "click() now runs before the pointer sequence completes"
    )


def test_typing_fires_the_input_and_change_a_framework_listens_for():
    """A value written with no ``input`` event leaves a React form on old state."""
    body = _block(_code(ACTIONS_TS), "function typeInto")
    assert "fireInput(node" in body, "typeInto no longer fires an input event"
    assert "fireChange(node)" in body, (
        "typeInto no longer fires change; a plain HTML form reads change, not input, "
        "so the page would never see the field as edited"
    )
    fire = _block(_code(ACTIONS_TS), "function fireInput")
    assert "new InputEvent" in fire and "bubbles: true" in fire, (
        "the input event is no longer a bubbling InputEvent, which is the only shape "
        "a delegated framework listener sees"
    )


def test_enter_submits_through_requestSubmit_and_never_submit():
    """``form.submit()`` skips validation AND the page's own submit listener."""
    body = _block(_code(ACTIONS_TS), "function pressOn")
    assert "form.requestSubmit()" in body, (
        "Enter no longer submits a form at all. A synthesised key event performs no "
        "default action, so dispatching Enter and reporting submitted: true would be "
        "a lie on the one call where the user most needs the truth"
    )
    assert not re.search(r"\bform\.submit\(", _code(ACTIONS_TS)), (
        "the acting path calls form.submit(), which skips constraint validation and "
        "the page's own submit listener: a form the page would have rejected "
        "client-side is posted anyway"
    )
    assert "HTMLTextAreaElement" in body, (
        "Enter in a textarea no longer inserts a newline, which is what Enter does "
        "there; submitting instead would post a half-written message"
    )


def test_press_enter_is_only_reported_as_submitted_when_something_really_submitted():
    """``submitted`` has exactly two true causes and both are real."""
    body = _block(_code(ACTIONS_TS), "function pressOn")
    assert "return consumed" in body, (
        "pressOn no longer reports a keydown the page CANCELLED as submitted; a "
        "JavaScript form taking the key for itself is one of the two real causes"
    )
    caller = _block(_code(ACTIONS_TS), "export function typeText")
    assert 'params["press_enter"] === true ? pressOn(node, "Enter") : false' in caller, (
        "submitted is no longer derived from an actual Enter press, so it can now be "
        "true on a call that pressed nothing"
    )


# --------------------------------------------------------------------------- #
# What the daemon needs back: page_version, and whether the page moved
# --------------------------------------------------------------------------- #


def test_every_page_action_reports_the_version_after_the_action_not_before():
    """A route change the action caused must move the version it reports."""
    body = _block(_code(ACTIONS_TS), "function pageState")
    assert "registry.noteUrl()" in body, (
        "pageState no longer notices a URL change, so an action that routed a "
        "single-page app would report the PRE-action version, the daemon would "
        "compare equal, and the next call would act against a registry describing "
        "the page that has just been replaced"
    )
    assert body.index("registry.noteUrl()") < body.index("registry.pageVersion"), (
        "the version is read before the URL is re-checked, which is the same bug "
        "written in the other order"
    )
    assert "location.href !== before" in body, "navigated is no longer derived from the URL"


@pytest.mark.parametrize(
    "opener", ("export function click", "export function typeText", "export function pressKey")
)
def test_each_page_action_reports_whether_it_navigated(opener: str):
    """The daemon invalidates its snapshot off this, so it is on every result."""
    body = _block(_code(ACTIONS_TS), opener)
    assert "const before = location.href" in body, f"{opener} no longer records the URL it started at"
    keys = _top_level_keys(_return_object(body))
    assert "navigated" in keys, f"{opener} no longer reports whether the page moved"
    assert "page_version" in keys, f"{opener} no longer reports the resulting page version"


def test_navigate_reports_a_page_version_no_earlier_snapshot_can_match():
    """A brand-new document has no snapshot, and 0 matches no real version."""
    body = _block(_code(TABS_TS), "export async function navigate")
    assert "page_version: 0" in body, (
        "navigate reports a page version other than 0. The document it loads is "
        "BRAND NEW and no snapshot of it exists; any other number lets the daemon "
        "compare equal against a registry describing a page that is gone, and the "
        "next browser_click resolves an element id against it"
    )


# --------------------------------------------------------------------------- #
# The four browser-level actions
# --------------------------------------------------------------------------- #


def test_close_tab_never_falls_back_to_the_tab_the_user_is_looking_at():
    """``pageTab`` resolves a missing id to the ACTIVE tab. Not here."""
    body = _block(_code(TABS_TS), "export async function closeTab")
    assert "pageTab(" not in body, (
        "closeTab resolves its tab through pageTab, which falls back to the ACTIVE "
        "tab when the id is missing or malformed. That fallback is right for a read "
        "and catastrophic here: a dropped id would close whatever the user is "
        "looking at instead of refusing"
    )
    assert 'typeof raw !== "number"' in body and "TAB_NOT_FOUND" in body, (
        "closeTab no longer refuses a non-integer tab id"
    )


#: (function, whether it must refuse without the site grant). The line is drawn by
#: what the RESULT claims: three of the four report a title and a URL, and without
#: the grant Chrome hands back empty strings — "I switched you to a tab I cannot
#: name" is an action taken blind on a real browser. close_tab reports neither.
GRANT_GATED_TAB_ACTIONS = (
    ("export async function activateTab", True),
    ("export async function createTab", True),
    ("export async function navigate", True),
    ("export async function closeTab", False),
)


@pytest.mark.parametrize("opener,gated", GRANT_GATED_TAB_ACTIONS)
def test_a_tab_action_that_names_a_page_refuses_without_the_site_grant(opener: str, gated: bool):
    """Three refuse with the remedy that names the button; one does not need to."""
    body = _block(_code(TABS_TS), opener)
    if gated:
        assert "if (!hostPermission)" in body and "PERMISSION_DENIED" in body, (
            f"{opener} no longer refuses without the site grant, so it now reports a "
            "title and a URL that Chrome returned as empty strings — a model reads "
            "that as 'this page has no title'"
        )
    else:
        assert "hostPermission" not in body, (
            f"{opener} now demands the site grant. Refusing to close a tab the user "
            "asked to close, because its title cannot be read, is a refusal with no "
            "reason the user can act on"
        )


def test_navigate_and_create_tab_refuse_a_page_chrome_closes_to_add_ons():
    """The backstop for section 9.7, at the last point before Chrome is asked."""
    for opener in ("export async function navigate", "export async function createTab"):
        body = _block(_code(TABS_TS), opener)
        assert "unsupportedScheme(" in body and "UNSUPPORTED_PAGE" in body, (
            f"{opener} no longer refuses a chrome:// or Web Store URL, so the model "
            "would be handed a tab id it can never read or act on"
        )


def test_the_wait_for_a_page_to_load_can_never_hang_and_never_leaks_its_listener():
    """A hang here reports ACTION_TIMEOUT for a browser that answered."""
    body = _block(_code(TABS_TS), "async function settle")
    assert "setTimeout(" in body, (
        "settle no longer bounds its wait, so a page that never finishes loading "
        "burns the daemon's whole command timeout and reports 'your browser did not "
        "answer' — false, and the wrong remedy"
    )
    assert "chrome.tabs.onUpdated.removeListener(onUpdated)" in body, (
        "settle leaks its onUpdated listener; a service worker accumulates them "
        "across calls and is evicted while holding them"
    )
    assert "clearTimeout(timer)" in body, "settle leaves its timer running after it resolves"
    assert "reject" not in body, (
        "settle can now reject. A closed tab and a slow page are both normal, and "
        "turning either into an exception loses the tab's real state"
    )


def test_scroll_refuses_a_direction_outside_the_four():
    """A silent no-op reads to a model as 'done', and it reasons from that."""
    body = _block(_code(ACTIONS_TS), "export function scroll")
    for constant in ("SCROLL_UP", "SCROLL_DOWN", "SCROLL_TOP", "SCROLL_BOTTOM"):
        assert constant in body, (
            f"scroll spells {constant} by hand instead of using the constant "
            "generated from protocol.py"
        )
    assert "} else {" in body and "EXTENSION_ERROR" in body, (
        "scroll no longer refuses an unknown direction, so a direction outside the "
        "four falls through every branch, scrolls nowhere and answers success"
    )
    assert set(P.SCROLL_DIRECTIONS) == {"up", "down", "top", "bottom"}, (
        "the scroll vocabulary changed in Python without this pin being updated"
    )


# --------------------------------------------------------------------------- #
# Routing: the daemon can actually reach all eight
# --------------------------------------------------------------------------- #

#: The eight methods Ship 3 adds, derived from the protocol rather than retyped.
ACTING_METHODS = tuple(P.LOCAL_UI_METHODS) + tuple(P.PAGE_ACTION_METHODS)


def test_ship_3_adds_exactly_the_eight_methods_the_plan_names():
    """The vocabulary is Python's; this pin only checks nobody added a ninth."""
    assert set(ACTING_METHODS) == {
        "activate_tab",
        "scroll",
        "create_tab",
        "close_tab",
        "click",
        "type_text",
        "press_key",
        "navigate",
    }
    assert set(P.ALL_METHODS) - set(P.READ_METHODS) == set(ACTING_METHODS)


@pytest.mark.parametrize("method", P.ALL_METHODS)
def test_the_service_worker_registers_every_method_the_protocol_declares(method: str):
    """An unregistered method answers EXTENSION_ERROR, which reads as a broken add-on."""
    constant = f"METHOD_{method.upper()}"
    code = _code(WORKER_TS)
    assert f"dispatcher.register({constant}," in code, (
        f"the service worker does not register {method}. The daemon's tool exists, "
        "the frame is sent, and the add-on answers 'does not implement it yet' — "
        "which the user reads as Iron Jarvis being broken rather than incomplete"
    )


@pytest.mark.parametrize("method", P.PAGE_ACTION_METHODS + ("scroll",))
def test_the_page_reader_routes_every_operation_that_touches_a_page(method: str):
    """``navigate`` is the one exception: it is a tab operation, not a page one."""
    if method == "navigate":
        pytest.skip("navigate changes the tab, not the document, so it never enters a page")
    constant = f"METHOD_{method.upper()}"
    body = _block(_code(CONTENT_INDEX_TS), "function handle")
    assert f"request.op === {constant}" in body, (
        f"content/index.ts does not dispatch {method}, so the frame reaches the page "
        "and comes back as 'the page reader does not implement it yet'"
    )


@pytest.mark.parametrize("method", ("click", "type_text", "press_key", "scroll"))
def test_every_page_action_goes_through_the_one_door_that_holds_the_gate(method: str):
    """``runInPage`` holds the host gate, the unsupported-page refusal and the injection."""
    name = {
        "click": "export async function clickInPage",
        "type_text": "export async function typeTextInPage",
        "press_key": "export async function pressKeyInPage",
        "scroll": "export async function scrollInPage",
    }[method]
    body = _block(_code(TABS_TS), name)
    assert "runInPage(" in body, (
        f"{method} no longer goes through runInPage, so it has its own copy of the "
        "host-permission gate and the unsupported-page refusal — or, worse, neither"
    )


def test_every_acting_method_reads_the_site_grant_fresh_on_every_call():
    """A cached grant sends a click at a page the add-on may no longer touch."""
    code = _code(WORKER_TS)
    for method in ACTING_METHODS:
        if method == "close_tab":
            # It needs no grant at all; see the tab-action pin above.
            continue
        constant = f"METHOD_{method.upper()}"
        body = _block(code[code.index(f"dispatcher.register({constant},") :], "async (params)")
        assert "await hasHostPermission()" in body, (
            f"{method} no longer reads the site grant fresh. The user can revoke it "
            "from chrome://extensions between two calls, and a cached true surfaces "
            "as Chrome's own wording instead of the remedy naming the button"
        )


def test_the_page_actions_are_synchronous_so_a_navigating_click_still_answers():
    """An awaited action is killed mid-await by exactly the clicks that worked."""
    code = _code(ACTIONS_TS)
    assert "await " not in code, (
        "actions.ts now awaits something. A click can navigate the document, and a "
        "navigating document tears the content script down: an action that awaited "
        "before responding would be killed on exactly the clicks that succeeded, and "
        "the model would be told the browser never answered a call that in fact "
        "signed the user in"
    )
    assert "async function" not in code, "an acting entry point became async; see above"
    assert "setTimeout" not in code, (
        "actions.ts schedules work for after it has replied, which is work that runs "
        "in a page nobody is waiting on and whose result nothing can report"
    )


# --------------------------------------------------------------------------- #
# The caret: an append lands at the END (Ship 3 review, S2)
# --------------------------------------------------------------------------- #


def test_an_append_positions_the_caret_before_it_inserts():
    """Otherwise ``" Jr."`` lands in FRONT of ``"John Smith"`` and the form submits.

    ``focus()`` on a control the user has never clicked into leaves the caret at
    offset 0 with nothing selected, and ``execCommand("insertText")`` inserts at the
    caret. So an append with no caret placement prepends — silently, in a call whose
    result says ``typed_into`` — and where the page or a previous call left a
    selection, ``insertText`` REPLACES it: the exact destruction of the user's own
    text that ``typeInto``'s ``if (!clear)`` refusal exists to prevent, reached by
    the path that refusal never runs on.

    ORDER IS THE ASSERTION. The call has to sit after ``focus`` (a focus change
    resets the selection) and before both the clear and the insert.
    """
    body = _block(_code(ACTIONS_TS), "function typeInto")
    assert "collapseToEnd(node)" in body, (
        "typeInto no longer places the caret before inserting, so an append lands at "
        "position 0 on a programmatically focused field and any selection the page "
        "left is silently overwritten"
    )
    assert body.index("focus({ preventScroll: false })") < body.index("collapseToEnd(node)"), (
        "the caret is placed BEFORE focus, and focusing resets the selection, so the "
        "placement is undone by the next line"
    )
    assert body.index("collapseToEnd(node)") < body.index("if (clear)"), (
        "the caret placement moved after the clear branch; it must run for the "
        "append path, which is the path that needs it"
    )
    assert body.index("collapseToEnd(node)") < body.index('document.execCommand("insertText"'), (
        "the caret is placed after the insert, which is the same as not placing it"
    )


def test_the_caret_is_moved_by_writes_that_read_nothing_out_of_the_field():
    """Section 9.4 holds through the fix: no length, no offset, no contents.

    ``setSelectionRange(MAX_SAFE_INTEGER, MAX_SAFE_INTEGER)`` is the whole trick —
    the platform clamps both offsets to the end of the content, so the caret moves
    without this script ever learning how long the text is. The obvious alternative,
    ``node.setSelectionRange(node.value.length, ...)``, reads the field.
    """
    body = _block(_code(ACTIONS_TS), "function collapseToEnd")
    assert "setSelectionRange(Number.MAX_SAFE_INTEGER" in body, (
        "collapseToEnd no longer clamps through MAX_SAFE_INTEGER. Any spelling that "
        "needs the length reads the field, which section 9.4 forbids and which is "
        "the read half of the read-modify-write typeInto refuses to do"
    )
    for read in (".value", ".length", ".selectionStart", ".selectionEnd", ".textContent"):
        assert read not in body, (
            f"collapseToEnd reads {read}; the caret has to move without the field's "
            "contents or its size entering this script"
        )
    assert "collapseToEnd()" in body and "selectAllChildren" in body, (
        "collapseToEnd no longer handles a contenteditable region, so an append into "
        "one still lands at the front"
    )
    assert body.count("try {") >= 2, (
        "collapseToEnd no longer guards its two APIs. setSelectionRange throws "
        "InvalidStateError on input types with no selection, and getSelection() is "
        "null in a document without one; a caret that cannot be moved is not a "
        "reason to fail the call"
    )


# --------------------------------------------------------------------------- #
# The name in a result is the LIVE one (Ship 3 review, S2)
# --------------------------------------------------------------------------- #


def test_the_name_in_an_acting_result_is_read_off_the_live_node():
    """The daemon shows a CACHED label; only the page can say what it really pressed.

    The gap this closes is real and was driven end to end by the review: a page may
    rename a control between ``browser_read_page`` and the click (``aria-label`` is
    not in ``WATCHED_ATTRIBUTES``, so ``page_version`` does not move), and the risk
    vocabulary and the approval card are both scanned against the daemon's cached
    name. The page half's whole contribution is to report what it ACTUALLY acted on,
    read off the live node at the moment of the action, so a daemon-side comparison
    is possible at all and so ``ActionTarget.target_ref`` — which already prefers
    the echo — has something true to prefer.

    THE DIVERGENCE IS THE DAEMON'S TO ACT ON, and this file records the intended
    answer so the next reader is not left guessing: a PAGE_ACTION whose echoed name
    differs from the label the user approved must be surfaced as a failure and the
    model made to re-read, never quietly reported as done. That comparison lives in
    ``src/iron_jarvis/browser/tools.py``; this pin guarantees the evidence it needs
    exists and is not the request read back.
    """
    ref = _block(_code(ACTIONS_TS), "function targetRef")
    assert "nameOf(resolved.node)" in ref, (
        "targetRef no longer reads the accessible name off the LIVE node. A result "
        "that echoed the request back would agree with the daemon's cached label by "
        "construction, and the one signal that a control was renamed between the "
        "snapshot and the click would be gone"
    )
    assert "roleFor(resolved.node)" in ref, "targetRef no longer reads the live role"
    assert "params" not in ref, (
        "targetRef reads the caller's params; the echo has to come from the node"
    )
    for opener, key in (
        ("export function click", "clicked"),
        ("export function typeText", "typed_into"),
    ):
        body = _block(_code(ACTIONS_TS), opener)
        assert "targetRef(resolved)" in body, (
            f"{opener} no longer builds its {key} from the resolved live node"
        )
        assert body.index("resolveTarget(") < body.index("targetRef(resolved)"), (
            f"{opener} builds its reference before resolving, which cannot be right"
        )


# --------------------------------------------------------------------------- #
# Plan 10.3: the download rides the result of the action that started it
# --------------------------------------------------------------------------- #


def _click_handler() -> str:
    """The service worker's ``click`` registration body."""
    code = _code(WORKER_TS)
    at = code.index("dispatcher.register(METHOD_CLICK,")
    return _block(code[at:], "async (params)")


def test_the_click_handler_actually_waits_for_the_download_it_may_have_started():
    """Plan 10.3's ONE agent-facing capability, and it only exists at this call site.

    ``awaitDownload`` shipped in Ship 3 with no caller anywhere in the add-on, so a
    click that produced a file answered "Clicked link ... in tab 42." and nothing
    more: the model had no path, could not hand one to ``read_document``, and the
    ledger row for that click never named the file it produced. The Python half was
    verified by tests that FABRICATED a result frame already carrying ``download``,
    which proves the daemon checks a payload if one arrives and proves nothing about
    anything putting one there. This pin reads the call site instead.
    """
    body = _click_handler()
    assert "awaitDownload(" in body, (
        "the click handler no longer awaits a download, so plan 10.3's one "
        "agent-facing capability is unreachable again: a click that downloads a file "
        "answers without the path, and the model cannot hand it to read_document"
    )
    assert "download" in _top_level_keys(_return_object(body)), (
        "the click handler no longer puts the download on the result it returns"
    )
    assert "download" in P.ClickResult.__annotations__, (
        "ClickResult stopped declaring `download`, so the key the worker sets is one "
        "the daemon does not read"
    )
    # The pre-fix shape, named so it cannot come back by accident: the handler WAS
    # `return clickInPage(params, await hasHostPermission());`, which returns the
    # page's answer verbatim and can never carry a download. A source pin cannot see
    # a wait that has been made unreachable by an early return, so the one spelling
    # that makes it unreachable is forbidden outright.
    assert "return clickInPage(" not in body, (
        "the click handler returns the page's answer directly again, so whatever "
        "follows is dead code and no click can ever carry a download"
    )
    assert body.count("clickInPage(") == 1, (
        "the click handler clicks more than once, or the pin can no longer tell "
        "which call the download wait belongs to"
    )


def test_the_download_window_opens_before_the_click_not_after():
    """A stale file must not be reported as this click's file.

    ``takeDownload`` returns the newest UNCLAIMED completion, so without a floor a
    click that downloaded nothing would claim a file the user downloaded themselves
    ten minutes earlier — and ``BrowserClickTool.render`` would print "It downloaded
    a file to ..." to the model about a click that did no such thing.
    """
    body = _click_handler()
    assert "Date.now()" in body, "the click handler no longer stamps when it began"
    assert body.index("Date.now()") < body.index("clickInPage("), (
        "the click handler stamps the time AFTER the click, so a download that "
        "completed during the click is younger than the stamp and is missed"
    )
    assert body.index("clickInPage(") < body.index("awaitDownload("), (
        "the handler waits for a download before it has clicked anything"
    )
    take = _block(_code(DOWNLOADS_TS), "export function takeDownload")
    assert "record.at < since" in take, (
        "takeDownload no longer refuses a completion older than the asking action, "
        "so a click that started nothing claims whatever the user downloaded last"
    )
    assert "since" in _code(DOWNLOADS_TS)[
        _code(DOWNLOADS_TS).index("export function takeDownload") :
    ][:120], "takeDownload no longer takes the moment its caller began"


def test_an_ordinary_click_does_not_pay_the_settle_bound():
    """Almost every click starts nothing; none of them may cost five seconds.

    Two bounds, not one. The short one asks whether Chrome even CREATED a download —
    ``onCreated`` fires within a network round trip of the click that caused it — and
    only a click that really started a transfer goes on to wait out the settle bound.
    A single bound would add ``DOWNLOAD_SETTLE_MS`` to every button press in the
    product, which is how a correct feature becomes an unusable one.
    """
    code = _code(DOWNLOADS_TS)
    body = _block(code, "export async function awaitDownload")
    assert "awaitStart(" in body, (
        "awaitDownload no longer asks whether a download started before waiting for "
        "one to finish, so every ordinary click now waits out the settle bound"
    )
    assert "awaitCompletion(" in body, "awaitDownload no longer waits for the completion"
    assert body.index("awaitStart(") < body.index("awaitCompletion("), (
        "awaitDownload waits for a completion before it knows anything started"
    )
    assert "return null;" in body, "awaitDownload no longer answers 'no download'"
    assert "DOWNLOAD_START_MS" in code and "DOWNLOAD_SETTLE_MS" in code, (
        "one of the two bounds is gone"
    )
    created = _block(code, "function noteCreation")
    assert "startWaiters" in created, (
        "onCreated no longer wakes a waiting click, so the start bound is a sleep "
        "rather than a wait and every download misses its own click"
    )
    watcher = _block(code, "export function watchDownloads")
    assert "noteCreation();" in watcher, (
        "watchDownloads no longer records that a download began, so awaitStart can "
        "never answer true and no click will ever carry a download again"
    )


# --------------------------------------------------------------------------- #
# The add-on is the least trusted component: it cannot forge a verified path
# --------------------------------------------------------------------------- #


def test_local_path_is_not_a_key_the_add_on_is_allowed_to_send():
    """The whole forged-path defence, asserted for the first time.

    ``DOWNLOAD_PAYLOAD_KEYS`` is derived from ``protocol.DownloadPayload``, and
    ``download_bus_payload`` copies exactly those keys off the wire. On the branch
    where ``filename`` does NOT verify it returns early WITHOUT overwriting
    ``local_path`` — so the only thing keeping a forged path out is that the key is
    not declared on the wire type. Nothing asserted that. Declaring
    ``local_path: NotRequired[str]`` would hand the add-on a path that is published
    on ``browser.download_completed``, persisted as an ``EventRecord``, and printed
    to the model as "you can read that path with read_document".
    """
    assert "local_path" not in DOWNLOAD_PAYLOAD_KEYS, (
        "local_path is the DAEMON's word for a path it verified. Declaring it on "
        "protocol.DownloadPayload puts it in the whitelist download_bus_payload "
        "copies from, and on the unverified branch a forged value survives all the "
        "way to the model"
    )
    assert "local_path" not in P.DownloadPayload.__annotations__, (
        "the wire type grew local_path; see above"
    )


def test_a_forged_local_path_never_reaches_the_bus_payload():
    """Behaviour, not shape: the forged value is driven through the real function."""
    forged = "/etc/shadow" if not str(Path.cwd()).startswith("C:") else "C:\\Windows\\System32\\config\\SAM"
    payload, problem = download_bus_payload(
        {
            "download_id": 1,
            "filename": "Downloads/statement.pdf",
            "source_url": "https://bank.example/statement.pdf",
            "local_path": forged,
        }
    )
    assert problem, "a relative filename verified, which is the check this rests on"
    assert "local_path" not in payload, (
        "an unverified download report carries a local_path the daemon never "
        "checked. Every consumer downstream reads that key as verified: it is "
        "published on the bus, stored in an EventRecord, and printed to the model "
        "as a path it may hand to read_document"
    )
    verified, ok = download_bus_payload(
        {
            "download_id": 2,
            "filename": str(Path.cwd() / "statement.pdf"),
            "source_url": "https://bank.example/statement.pdf",
            "local_path": forged,
        }
    )
    assert ok == ""
    assert verified["local_path"] != forged, (
        "the add-on's forged local_path survived a report whose filename DID verify, "
        "so the daemon's own derivation lost to the wire"
    )
    assert verified["local_path"] == str(Path.cwd() / "statement.pdf")


def test_the_add_on_never_writes_a_local_path_at_all():
    """The other end of the same rule, read off the source the add-on actually ships."""
    body = _block(_code(DOWNLOADS_TS), "export function downloadPayload")
    literal = _block(body, "const payload: DownloadPayload =")
    keys = set(_top_level_keys(literal))
    assert "local_path" not in keys, (
        "downloadPayload sends a local_path. The add-on says what the browser "
        "reported; the daemon says what it verified, and one channel saying both is "
        "how a compromised add-on hands the file tools a path of its choosing"
    )
    assert keys <= set(P.DownloadPayload.__annotations__), (
        f"downloadPayload sends {sorted(keys - set(P.DownloadPayload.__annotations__))}, "
        "which the wire type does not declare"
    )


def test_a_rename_between_the_snapshot_and_the_click_invalidates_the_snapshot():
    """A page must not be able to change what the user approved (v1.237.0).

    The risk gate scans an element's ACCESSIBLE NAME and the approval card shows it,
    so the name is the thing the user actually consents to. A page that renames a
    control after the snapshot and before the click gets consent for "Sign in" and
    receives a click on whatever it renamed the control to -- and every other signal
    looks untouched: same element, same position, same page_version.

    The defence is that a rename BUMPS page_version, so the next action is refused
    as stale and the model has to read the page again. That only works if the
    observer watches the attributes an accessible name is computed from. It watched
    presence and usability (disabled, hidden, href, role, type) and none of the
    naming ones, which is the hole this pins shut.

    A source pin because the add-on has no TypeScript harness: CRLF normalised at
    the reader, matched on content, no fixed-size window.
    """
    # Sliced by its own brackets, not by `_block`, which counts BRACES and would
    # hand back the next function's body for an array declaration.
    src = _code(ELEMENTS_TS)
    start = src.index("export const WATCHED_ATTRIBUTES")
    open_bracket = src.index("[", start)
    watched = src[open_bracket : src.index("]", open_bracket)]
    for attribute in ("aria-label", "aria-labelledby", "title"):
        assert f'"{attribute}"' in watched, (
            f"{attribute} is not watched, so a page can change an element's "
            "accessible name without bumping page_version -- the user approves one "
            "label and a differently-named control is clicked, with nothing stale"
        )
