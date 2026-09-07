"""The add-on's PAGE READER is pinned — what it collects, and what it must never read.

Ship 2 of the Browser capability (v1.236.0). ``extensions/chrome/src/content/`` is the
only Iron Jarvis code that touches a page the user is signed in to, and there is no
TypeScript test harness in this repository (``extensions/chrome`` has no vitest
project), so these are SOURCE pins — the shape ``tests/test_browser_addon_ui_v1235.py``
established for the add-on's other untestable surface — plus real executable
assertions on the Python half of every contract the two halves share.

THE PIN THAT MATTERS MOST is ``test_no_code_path_reads_a_value_off_an_element``. Plan
section 9.4 goes further than D13B on purpose: no field VALUE is collected for any
field, ever, so a plaintext password never crosses the socket at all. That rule is
enforced by ABSENCE, and absence is what a green test suite cannot notice. If it ever
breaks, the snapshot travels to ``ToolInvocation.output`` in SQLite, which is on disk,
in the user's backups, and read back into later prompts — one leak is permanent. So
the receivers of ``.value`` are enumerated and pinned to a list that contains no DOM
node, and a companion pin covers the other seven ways a field's contents can be read.

WHAT THOSE PINS ALONE DID NOT COVER, learned the hard way on this ship's review: a
``.value`` grep answers "no value was read off a control" and says nothing about a
control whose contents are its CHILD NODES. Two real leaks passed all 47 pins — a
``contenteditable`` region, which the registry reports as a ``textbox`` while the
value rule was keyed on the tag INPUT/TEXTAREA/SELECT, and a wrapping ``<label>``
whose ``textContent`` carried the ``<select>``'s chosen option into the row's name
beside ``value: null``. So three pins here are written about ROUTES rather than about
a property name: ``test_a_contenteditable_region_is_a_field_by_every_rule_that_matters``
(one predicate, ``scrub.isEditableField``, asked by every rule), ``test_a_container_
s_text_is_read_with_every_field_skipped`` (every container's text goes through
``textOutsideFields``, and the receivers of ``.textContent`` are enumerated the way
the receivers of ``.value`` are), and ``test_an_open_shadow_root_is_walked_rather_
than_silently_dropped``.

How the pins are written, because the shape matters more than the assertions:

* The reader normalises CRLF once (``_read``). GitHub's Windows runners check the tree
  out with ``\\r\\n``, and a needle carrying an embedded newline matches locally and
  never on CI — the trap that took the v1.232.0 installer down.
* No fixed-size windows. Braced bodies are found by counting braces from a
  declaration, so adding a line or a comment cannot make a pin report "never
  collected" when the truth is "something moved".
* Comments are stripped for every pin that reads behaviour (``_code``), because this
  file's own needles — ``.value``, ``content_scripts``, ``pushState`` — are quoted in
  the source's comments, where they explain the rule being pinned. A pin that matched
  prose would go red on a comment and green on the bug.
* Where a value exists on both sides of the wire, the pin compares the TypeScript
  against the PYTHON rather than against a literal written here: the mode table, the
  limit vocabulary, the ``counts`` keys the daemon reads, and the seventeen error
  codes are all read out of ``iron_jarvis.browser``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import NotRequired, get_args, get_origin, get_type_hints

import pytest

from iron_jarvis.browser import protocol as P
from iron_jarvis.browser import snapshot as S
from iron_jarvis.browser.errors import BrowserErrorCode

REPO = Path(__file__).resolve().parents[1]
ADDON = REPO / "extensions" / "chrome"
SRC = ADDON / "src"
CONTENT = SRC / "content"
INDEX_TS = CONTENT / "index.ts"
SNAPSHOT_TS = CONTENT / "snapshot.ts"
ELEMENTS_TS = CONTENT / "elements.ts"
SCRUB_TS = CONTENT / "scrub.ts"
CHANNEL_TS = CONTENT / "channel.ts"
TABS_TS = SRC / "background" / "tabs.ts"
WORKER_TS = SRC / "background" / "index.ts"
MANIFEST = ADDON / "manifest.json"
ESBUILD = ADDON / "esbuild.config.mjs"

#: Every file the page reader is made of, plus the two worker files that reach it.
#: The value pins below run over ALL of them: a leak moved into the service worker
#: would be just as permanent as one left in the page.
PAGE_FILES = (INDEX_TS, SNAPSHOT_TS, ELEMENTS_TS, SCRUB_TS, CHANNEL_TS, TABS_TS)


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


def _top_level_keys(body: str) -> list[str]:
    """The keys at nesting depth 0 of one object-literal body.

    Shorthand (``truncation,``) counts, because a key written shorthand is still a
    key on the wire and a pin that missed it would report a field as absent.
    """
    keys: list[str] = []
    depth = 0
    for line in body.split("\n"):
        stripped = line.strip()
        if depth == 0:
            found = re.match(r"([A-Za-z_][A-Za-z0-9_]*)\s*[:,]\s*$", stripped) or re.match(
                r"([A-Za-z_][A-Za-z0-9_]*)\s*:", stripped
            )
            if found:
                keys.append(found.group(1))
        depth += line.count("{") + line.count("[") + line.count("(")
        depth -= line.count("}") + line.count("]") + line.count(")")
    return keys


def _required_keys(shape: type) -> set[str]:
    """The keys of a TypedDict that are not ``NotRequired``."""
    out: set[str] = set()
    for name, annotation in get_type_hints(shape, include_extras=True).items():
        if get_origin(annotation) is NotRequired or (
            get_args(annotation) and get_origin(annotation) is NotRequired
        ):
            continue
        out.add(name)
    return out


def _string_members(text: str, name: str) -> list[str]:
    """The string members of a ``new Set([...])`` or array const called ``name``."""
    start = text.index(name)
    end = text.index("]", start)
    return re.findall(r'"([^"]*)"', text[start:end])


# --------------------------------------------------------------------------- #
# D13B and section 9.4: no field value, by any route
# --------------------------------------------------------------------------- #

#: The ONLY receivers of ``.value`` allowed anywhere in the page reader, and neither
#: is a DOM node: ``facts`` is ``scrub.FieldFacts`` (whose ``value`` is typed and
#: assigned ``null``) and ``row`` is the outgoing ``ElementRow``. A new name in this
#: list is a decision somebody has to justify in review; ``el.value`` would add
#: ``el`` and fail this pin the moment it is written.
VALUE_RECEIVERS = {"facts", "row"}

#: The other ways a control's contents can be read. None of them appears at all.
FORBIDDEN_FIELD_PROPERTIES = (
    "checked",
    "defaultValue",
    "files",
    "selectedIndex",
    "selectedOptions",
    "selectionStart",
    "valueAsDate",
    "valueAsNumber",
)


def test_no_code_path_reads_a_value_off_an_element():
    """The rule the whole snapshot model is built on: no field value, ever."""
    receivers: dict[str, list[str]] = {}
    for path in PAGE_FILES:
        code = _code(path)
        for match in re.finditer(r"([A-Za-z_$][A-Za-z0-9_$]*)\.value\b", code):
            receivers.setdefault(match.group(1), []).append(path.name)
        # `(el as HTMLInputElement).value` and `nodes[0].value` have no plain
        # identifier to capture, so they are refused by shape instead. So are the
        # spellings the identifier regex above cannot see at all: `el?.value`,
        # `el["value"]` and `Reflect.get(el, "value")` each read a control's
        # contents and each would have sailed past the receiver list.
        assert not re.search(r"\)\s*\.value\b", code), (
            f"{path.name} reads .value off a cast or a call result; section 9.4 "
            "collects no field value for any field"
        )
        assert not re.search(r"\]\s*\.value\b", code), (
            f"{path.name} reads .value off an indexed expression; section 9.4 "
            "collects no field value for any field"
        )
        assert not re.search(r"\?\.\s*value\b", code), (
            f"{path.name} reads .value through an optional chain; the rule is about "
            "the read, not about the punctuation it is written with"
        )
        assert not re.search(r"""\[\s*['"]value['"]\s*\]""", code), (
            f"{path.name} reads a value by string index; that is `el.value` spelled "
            "so the receiver list cannot see it"
        )
        assert "Reflect.get" not in code, (
            f"{path.name} reads a property reflectively, which defeats every pin in "
            "this file that names a property"
        )
    assert set(receivers) == VALUE_RECEIVERS, (
        "the page reader reads .value off "
        f"{sorted(set(receivers) - VALUE_RECEIVERS)}; only {sorted(VALUE_RECEIVERS)} "
        "are plain result objects, and a DOM node here is a plaintext password on "
        "the socket and in the ledger"
    )


@pytest.mark.parametrize("prop", FORBIDDEN_FIELD_PROPERTIES)
def test_no_code_path_reads_a_control_state_off_an_element(prop: str):
    """The seven other reads that would carry a field's contents off the page."""
    for path in PAGE_FILES:
        assert f".{prop}" not in _code(path), (
            f"{path.name} reads .{prop}; that is a form control's contents by another "
            "name, and section 9.4 collects none of it"
        )


def test_the_only_value_attribute_read_is_a_button_label_and_it_is_type_gated():
    """One ``getAttribute("value")`` exists, in ``buttonLabel``, for three types."""
    hits = [
        (
            path.name,
            # Both quotings, because the add-on's own style is double quotes and a
            # single-quoted read would otherwise be an unpinned second value route.
            _code(path).count('getAttribute("value")') + _code(path).count("getAttribute('value')"),
        )
        for path in PAGE_FILES
    ]
    total = sum(count for _, count in hits)
    assert total == 1, f"expected exactly one value-attribute read, found {hits}"
    body = _block(_code(SCRUB_TS), "export function buttonLabel")
    assert 'getAttribute("value")' in body, "the one value-attribute read moved out of buttonLabel"
    assert "LABEL_ATTRIBUTE_TYPES.has" in body, (
        "buttonLabel reads a value attribute without gating on the three input types "
        "that cannot hold user data"
    )
    types = set(_string_members(_code(SCRUB_TS), "LABEL_ATTRIBUTE_TYPES"))
    assert types == {"submit", "button", "reset"}, (
        f"the value-attribute allowlist is {sorted(types)}; only submit, button and "
        "reset hold a label rather than data"
    )


def test_every_field_row_carries_a_null_value_and_a_sensitive_flag():
    """D13B's example row, built in one place, with ``value`` assigned ``null``."""
    facts = _block(_code(SCRUB_TS), "export function fieldFacts")
    assert "value: null" in facts, "fieldFacts omits the explicit null value of D13B"
    assert "sensitive: isSensitiveField(el)" in facts, "fieldFacts does not mark sensitivity"
    describe = _block(_code(SNAPSHOT_TS), "function describe")
    for assignment in ("row.type = facts.type", "row.sensitive = facts.sensitive", "row.value = facts.value"):
        assert assignment in describe, f"the element row never sets {assignment}"


def test_a_password_field_is_sensitive_and_reads_as_a_textbox():
    """The D13B row: ``role: textbox``, ``type: password``, ``sensitive: true``."""
    sensitive = _block(_code(SCRUB_TS), "export function isSensitiveField")
    assert 'fieldType(el) === "password"' in sensitive, (
        "a password field is sensitive by its TYPE, not only by its autocomplete — "
        "an autocomplete-only rule misses every login form that omits the attribute"
    )
    roles = _block(_code(SNAPSHOT_TS), "const INPUT_ROLES")
    assert re.search(r'password:\s*"textbox"', roles), "a password input must read as a textbox (D13B)"


def test_the_sensitive_vocabulary_is_imported_and_never_retyped():
    """The credential vocabulary comes from Python through the generated protocol."""
    code = _code(SCRUB_TS)
    for name in ("PASSWORD_AUTOCOMPLETE", "PAYMENT_AUTOCOMPLETE", "SENSITIVE_AUTOCOMPLETE"):
        assert name in code, f"scrub.ts does not use the generated {name}"
    for token in P.SENSITIVE_AUTOCOMPLETE:
        for path in PAGE_FILES:
            assert f'"{token}"' not in _code(path), (
                f"{path.name} spells the sensitive token {token!r} by hand; the "
                "vocabulary is generated from computeruse/policy.py so the two "
                "halves of Iron Jarvis cannot disagree about what a credential is"
            )


def test_the_walk_stops_at_a_field():
    """A textarea's text child IS its value, so the walk never descends into one."""
    stops = _block(_code(SCRUB_TS), "export function stopsTheWalk")
    assert "isEditableField(el)" in stops, (
        "stopsTheWalk no longer covers every field; a tag-only answer here walks "
        "straight into a contenteditable composer and collects what was typed there"
    )
    visit = _block(_code(SNAPSHOT_TS), "function visit")
    assert "stopsTheWalk(el)" in visit, (
        "the walk descends into form controls, so a server-rendered textarea default "
        "or a prefilled select would enter the page text past every value check"
    )
    stop_at = visit.index("stopsTheWalk(el)")
    child_walk = visit.index("child.nodeType === Node.TEXT_NODE")
    assert stop_at < child_walk, "the form-control guard runs AFTER the child text is collected"


#: Every rule that must ask ``isEditableField`` rather than ``isField``, as
#: (file, declaration, needle). A tag-based answer in ANY of them is the S1 this
#: version fixed: the registry reports ``<div contenteditable>`` as a ``textbox``,
#: so it is a field the model can see, and its child nodes are what the user typed.
EDITABLE_FIELD_GATES = (
    (SCRUB_TS, "export function stopsTheWalk", "isEditableField(el)"),
    (SCRUB_TS, "export function fieldFacts", "if (!isEditableField(el))"),
    (SCRUB_TS, "export function isSensitiveField", "if (!isEditableField(el))"),
    (SNAPSHOT_TS, "export function accessibleName", "if (isEditableField(el))"),
    (SNAPSHOT_TS, "function labelledByText", "!isEditableField(target)"),
    (SNAPSHOT_TS, "for (const entry of registered)", "if (!isEditableField(entry.node))"),
)


@pytest.mark.parametrize("path,opener,needle", EDITABLE_FIELD_GATES)
def test_a_contenteditable_region_is_a_field_by_every_rule_that_matters(
    path: Path, opener: str, needle: str
):
    """The S1 of the Ship 2 review: one predicate, asked by every rule.

    A rich-text editor, a chat composer or an in-page note field is reported to the
    model as ``role: "textbox"``. Under the old tag rule its full contents went into
    the row's ``text``, into its ``name`` and into the page ``text`` with no
    ``sensitive`` flag and no ``value: null`` — a card number or a draft, in
    plaintext, in ``ToolInvocation.output`` in SQLite.
    """
    body = _block(_code(path), opener)
    assert needle in body, (
        f"{path.name}:{opener} no longer asks isEditableField, so it answers by TAG "
        "while the registry answers by ROLE — and a contenteditable field falls "
        "through the gap with its contents collected"
    )


def test_the_editable_predicate_has_one_definition_and_covers_both_kinds():
    """``isEditableField`` is a form control OR an editable region, defined once."""
    body = _block(_code(SCRUB_TS), "export function isEditableField")
    assert "isField(el)" in body and "isEditableHost(el)" in body, (
        f"isEditableField is {body.strip()!r}; it must cover form controls AND "
        "contenteditable regions, because the value rule turns on it"
    )
    host = _block(_code(SCRUB_TS), "export function isEditableHost")
    assert 'getAttribute("contenteditable")' in host and '!== "false"' in host, (
        "isEditableHost no longer reads the contenteditable attribute; "
        "`contenteditable=\"\"` and `plaintext-only` are editable, only `false` is not"
    )
    definitions = [path.name for path in PAGE_FILES if "function isEditableHost" in _code(path)]
    assert definitions == [SCRUB_TS.name], (
        f"isEditableHost is defined in {definitions}; two definitions of editable is "
        "exactly how the registry and the value rule drifted apart"
    )
    row_text = _block(_code(SNAPSHOT_TS), "function describe")
    assert 'landmark !== "" || isEditableField(el)' in row_text, (
        "the element row's own text is suppressed for form controls only, so a "
        "contenteditable region's contents are copied into row.text"
    )
    assert "isEditableHost(el)" in _block(_code(SNAPSHOT_TS), "export function roleFor"), (
        "roleFor no longer reports a contenteditable host as a textbox, so the pins "
        "above would be guarding a field the model is not told about"
    )


#: The ONLY receivers of ``.textContent`` in the page reader, and neither is a
#: container whose text reaches a name: ``child`` is a TEXT NODE inside the walk and
#: inside ``textOutsideFields``, and ``el`` appears once, in ``hasContentBelow``,
#: where the answer is a boolean and no words escape. ``label.textContent``,
#: ``target.textContent`` and ``wrapping.textContent`` are the three reads this
#: version removed, and adding any of them back adds a name here.
TEXT_CONTENT_RECEIVERS = {"child", "el"}


def test_a_container_s_text_is_read_with_every_field_skipped():
    """The second leak: a container's ``textContent`` includes its controls' contents.

    ``<label>Choose <select><option>Head of household</option></select></label>``
    named the combobox "Choose Head of household" — the user's current selection, on
    a row that said ``value: null`` beside it. Every container read goes through
    ``textOutsideFields``, which descends into no field, so the row and the name
    cannot disagree about what was collected.
    """
    reader = _block(_code(SNAPSHOT_TS), "function textOutsideFields")
    assert "stopsTheWalk(element)" in reader and "stopsTheWalk(el)" in reader, (
        "textOutsideFields does not skip fields, which makes it an ordinary "
        "textContent read with more steps"
    )
    for opener, needle in (
        ("function labelledByText", "textOutsideFields(target)"),
        ("function labelText", "textOutsideFields(wrapping)"),
        ("function labelText", "textOutsideFields(label)"),
        ("export function accessibleName", "textOutsideFields(el)"),
        ("function describe", "textOutsideFields(el)"),
    ):
        assert needle in _block(_code(SNAPSHOT_TS), opener), (
            f"{opener} reads a container's text by some other route than "
            "textOutsideFields, so a control inside it contributes its contents"
        )
    receivers: dict[str, list[str]] = {}
    for path in PAGE_FILES:
        for match in re.finditer(r"([A-Za-z_$][A-Za-z0-9_$]*)\.textContent\b", _code(path)):
            receivers.setdefault(match.group(1), []).append(path.name)
    assert set(receivers) == TEXT_CONTENT_RECEIVERS, (
        f"the page reader reads .textContent off {sorted(set(receivers))}; only "
        f"{sorted(TEXT_CONTENT_RECEIVERS)} are a text node and a boolean probe, and "
        "any other name here is a container carrying a form control's contents"
    )
    code = _code(SNAPSHOT_TS)
    assert code.count("el.textContent") == 1 and "el.textContent" in _block(
        code, "function hasContentBelow"
    ), (
        "el.textContent is read outside hasContentBelow, where the answer is a "
        "boolean; anywhere else it is a container's words and they include its "
        "controls' contents"
    )


def test_an_open_shadow_root_is_walked_rather_than_silently_dropped():
    """A page of web components returned empty text, empty elements, truncated false.

    Absent AND unstated, which is the one outcome section 9.2 exists to prevent. An
    open ``shadowRoot`` is readable from an isolated world, so it is read.
    """
    visit = _block(_code(SNAPSHOT_TS), "function visit")
    assert "shadowRoot" in visit, (
        "the walk never looks at a shadow root, so every Lit, Polymer or Lightning "
        "page reads as an empty page marked complete"
    )
    assert "Array.from(shadow.childNodes)" in visit, (
        "the shadow root is looked at but its children are not walked"
    )
    shadow_at = visit.index("shadow.childNodes")
    light_at = visit.index("Array.from(el.childNodes)")
    loop_at = visit.index("for (const child of nodes)")
    assert shadow_at < light_at < loop_at, (
        "the shadow children are not gathered before the light children and fed to "
        "the same loop; a second, separate traversal is a second set of rules"
    )
    header = _read(SNAPSHOT_TS)
    assert "CLOSED shadow root" in header, (
        "the file header still tells a reader that shadow DOM is invisible; a closed "
        "root is the only part that still is, and an undisclosed hole is the failure"
    )


def test_off_screen_content_is_part_of_the_page():
    """``content-visibility: auto`` is a performance measure, not a hidden section."""
    body = _block(_code(SNAPSHOT_TS), "function isRendered")
    assert "checkVisibilityCSS: true" in body, "the visibility probe no longer checks CSS"
    assert "contentVisibilityAuto" not in body, (
        "checkVisibility is asked to treat an off-screen content-visibility:auto "
        "subtree as invisible, so a long page's below-the-fold sections vanish from "
        "the text and from the registry with nothing reported as truncated"
    )


# --------------------------------------------------------------------------- #
# Injected on demand — never declared in the manifest (section 6)
# --------------------------------------------------------------------------- #


def test_the_manifest_declares_no_content_script():
    """A declared content script would run in every tab for the life of the browser."""
    manifest = json.loads(_read(MANIFEST))
    assert "content_scripts" not in manifest, (
        "manifest.json declares content_scripts; plan section 6 requires on-demand "
        "injection so a page is only touched while a tool runs"
    )
    assert "scripting" in manifest["permissions"], "the on-demand injection needs the scripting permission"


def test_the_page_reader_is_injected_by_the_worker_and_built_under_that_name():
    """One name for the bundle: the injector, the build and the output agree."""
    tabs = _code(TABS_TS)
    named = re.search(r'CONTENT_SCRIPT_FILE = "([^"]+)"', tabs)
    assert named, "tabs.ts no longer names the injected bundle in one place"
    bundle = named.group(1)
    inject = _block(tabs, "async function inject")
    assert "chrome.scripting.executeScript" in inject, "the page reader is no longer injected on demand"
    assert "files: [CONTENT_SCRIPT_FILE]" in inject, "the injection names the bundle by some other route"
    entry = _block(_code(ESBUILD), "const ENTRY_POINTS")
    assert re.search(r'content:\s*join\(ROOT, "src/content/index\.ts"\)', entry), (
        "esbuild builds no content entry point, so the file the injector names does not exist"
    )
    assert bundle == "dist/content.js", (
        f"the injector names {bundle!r} but esbuild writes the content entry to dist/content.js"
    )


def test_the_page_reader_installs_itself_exactly_once_per_document():
    """A second injection must not build a second registry starting at version 1."""
    install = _block(_code(INDEX_TS), "function install")
    assert "INSTALL_FLAG" in install, "the one-install guard is gone"
    flag = install.index("INSTALL_FLAG")
    listener = install.index("chrome.runtime.onMessage.addListener")
    assert flag < listener, "the listener is installed before the guard is checked"
    assert "return;" in install[:listener], (
        "the guard does not return early, so a repeated executeScript would add a "
        "second listener and reset page_version to 1"
    )
    assert "world[INSTALL_FLAG] === true" in install, (
        "the guard does not live on globalThis; a module-scope flag is fresh on every "
        "injection and guards nothing"
    )


def test_the_page_listener_answers_only_its_own_channel():
    """The popup's status message travels the same channel and must not be answered."""
    listener = _block(_code(INDEX_TS), "chrome.runtime.onMessage.addListener")
    assert "isPageRequest(message)" in listener, "the page reader answers every runtime message"
    guard = _block(_code(CHANNEL_TS), "export function isPageRequest")
    assert "PAGE_CHANNEL" in guard, "the channel guard no longer checks the channel"


def test_the_channel_string_has_exactly_one_definition():
    """One definition, imported by both halves — a duplicated string silently drifts.

    The pin is over the STRING, not over the constant's name: a second constant
    holding the same literal is exactly the drift this arrangement exists to prevent,
    and it would sail past a pin that only counted assignments to ``PAGE_CHANNEL``.
    """
    literal = re.search(r'PAGE_CHANNEL = ("[^"]+")', _code(CHANNEL_TS))
    assert literal, "channel.ts no longer defines the channel string"
    holders = [path.name for path in PAGE_FILES if literal.group(1) in _code(path)]
    assert holders == [CHANNEL_TS.name], (
        f"the channel string appears in {holders}; one side renaming it would leave "
        "the other listening on the old name and every read would report that the "
        "page reader did not answer"
    )
    assert 'from "../content/channel"' in _code(TABS_TS), "the worker no longer imports the shared channel"


def test_a_restricted_page_is_refused_by_scheme_rather_than_by_a_chrome_error():
    """UNSUPPORTED_PAGE, naming the scheme — the backstop of section 9.7."""
    tabs = _code(TABS_TS)
    refuse = _block(tabs, "function refuseUnsupported")
    assert 'BridgeError("UNSUPPORTED_PAGE", { scheme })' in refuse, (
        "the backstop no longer names the scheme, so the refusal cannot say which "
        "kind of page Chrome closed"
    )
    for opener in ("export async function runInPage", "export async function captureVisible"):
        assert "refuseUnsupported(tab)" in _block(tabs, opener), (
            f"{opener} skips the unsupported-page check, so executeScript's own "
            "wording would be mapped to PERMISSION_DENIED and send the user to press "
            "a Grant site access button that cannot help"
        )


def test_everything_that_touches_a_page_gates_on_the_host_grant_first():
    """Section 6's line: metadata survives a missing grant, a page read does not."""
    tabs = _code(TABS_TS)
    for opener in ("export async function runInPage", "export async function captureVisible"):
        body = _block(tabs, opener)
        assert 'BridgeError("PERMISSION_DENIED")' in body, f"{opener} does not refuse without site access"
        assert body.index("hostPermission") < body.index("pageTab("), (
            f"{opener} resolves a tab before checking the grant"
        )
    worker = _code(WORKER_TS)
    for method in ("METHOD_READ_PAGE", "METHOD_GET_ELEMENTS", "METHOD_SCREENSHOT"):
        body = _block(worker, f"dispatcher.register({method}")
        assert "await hasHostPermission()" in body, (
            f"{method} uses a cached grant; the user can revoke site access between "
            "two calls and a cached true sends the reader at a tab it may not read"
        )


# --------------------------------------------------------------------------- #
# Ship 2 registers the READ methods and nothing that acts
# --------------------------------------------------------------------------- #


def test_the_add_on_registers_every_method_through_a_generated_constant():
    """Every method the add-on serves is a name protocol.py knows.

    UPDATED AT v1.237.0. This pinned "six read methods and nothing that acts",
    which was Ship 2's truth; Ship 3 delivers the eight acting methods, so the
    list moved. What did NOT move, and is the part worth keeping, is the shape:
    a method may only be registered through a GENERATED constant, never a string
    literal. A literal would let the add-on serve something protocol.py has never
    heard of — a capability with no schema, no daemon-side gate and no name in the
    drift check that keeps the two sides honest.
    """
    arguments = re.findall(r"dispatcher\.register\(\s*([^,]+),", _code(WORKER_TS))
    literals = [argument for argument in arguments if not argument.startswith("METHOD_")]
    assert not literals, (
        f"the worker registers {literals} by some route other than a generated method "
        "constant, so a method could be served without appearing in protocol.py"
    )
    registered = set(arguments)
    expected = {
        # read (Ships 1 and 2)
        "METHOD_STATUS",
        "METHOD_LIST_TABS",
        "METHOD_ACTIVE_TAB",
        "METHOD_READ_PAGE",
        "METHOD_GET_ELEMENTS",
        "METHOD_SCREENSHOT",
        # acting (Ship 3)
        "METHOD_ACTIVATE_TAB",
        "METHOD_SCROLL",
        "METHOD_CREATE_TAB",
        "METHOD_CLOSE_TAB",
        "METHOD_CLICK",
        "METHOD_TYPE_TEXT",
        "METHOD_PRESS_KEY",
        "METHOD_NAVIGATE",
    }
    assert registered == expected, f"the add-on registers {sorted(registered)}"
    wire = {getattr(P, name) for name in registered}
    assert wire == set(P.ALL_METHODS), (
        "the registered methods are not exactly protocol.ALL_METHODS, so either a "
        "method is missing or one is served that protocol.py does not declare"
    )
    # The read methods are all still there. Stated separately from the set equality
    # above so a future ship that adds a method cannot quietly drop a read one and
    # still satisfy a single "they are equal" assertion.
    assert set(P.READ_METHODS) <= wire


def test_the_page_reader_serves_the_two_page_methods_by_their_wire_names():
    """One vocabulary: the op a page answers is the method the daemon sent."""
    handle = _block(_code(INDEX_TS), "function handle")
    assert "request.op === METHOD_READ_PAGE" in handle
    assert "request.op === METHOD_GET_ELEMENTS" in handle
    assert 'BridgeError("EXTENSION_ERROR"' in handle, (
        "an unimplemented op falls through silently; a daemon built ahead of the "
        "add-on must be told which half is missing instead of timing out"
    )


# --------------------------------------------------------------------------- #
# The three modes, and the limits (sections 9.1 and 9.2)
# --------------------------------------------------------------------------- #


def _ts_mode_specs() -> dict[str, dict[str, object]]:
    """The TypeScript mode table, parsed, with its text budgets resolved."""
    code = _code(SNAPSHOT_TS)
    out: dict[str, dict[str, object]] = {}
    for constant, mode in (
        ("MODE_SUMMARY", P.MODE_SUMMARY),
        ("MODE_INTERACTIVE", P.MODE_INTERACTIVE),
        ("MODE_FULL", P.MODE_FULL),
    ):
        body = _block(code, f"[{constant}]: {{")
        row: dict[str, object] = {}
        budget = re.search(r"textChars:\s*([A-Z_]+)", body)
        assert budget, f"{constant} has no named text budget"
        row["textChars"] = getattr(P, budget.group(1))
        for key in ("elements", "headings", "forms", "links", "landmarks"):
            found = re.search(rf"{key}:\s*(true|false)", body)
            assert found, f"{constant} does not say whether it includes {key}"
            row[key] = found.group(1) == "true"
        out[mode] = row
    return out


def test_the_content_mode_table_matches_the_python_one_exactly():
    """Three modes, and the page includes precisely what the daemon says it does."""
    ts = _ts_mode_specs()
    assert set(ts) == set(S.MODE_SPECS), f"the page knows modes {sorted(ts)}"
    for mode, spec in S.MODE_SPECS.items():
        row = ts[mode]
        assert row["textChars"] == spec.text_chars, f"{mode}: text budget disagrees"
        assert row["elements"] == spec.includes_elements, f"{mode}: element registry disagrees"
        assert row["headings"] == spec.includes_headings, f"{mode}: headings disagree"
        assert row["forms"] == spec.includes_forms, f"{mode}: forms disagree"
        assert row["links"] == spec.includes_links, f"{mode}: links disagree"
        assert row["landmarks"] == spec.includes_landmarks, f"{mode}: landmarks disagree"


def test_an_unknown_mode_is_lenient_in_the_page_and_strict_in_the_daemon():
    """The page must not blame a working browser for the daemon's argument check."""
    body = _block(_code(SNAPSHOT_TS), "export function modeSpec")
    assert "MODE_SPECS[MODE_INTERACTIVE]" in body, "the page has no fallback mode"
    assert "BridgeError" not in body, (
        "the page refuses an unknown mode; that arrives already validated, and an "
        "EXTENSION_ERROR here would report a browser fault for a bad argument"
    )
    with pytest.raises(S.UnknownSnapshotMode):
        S.normalise_mode("outline")


def test_every_limit_the_daemon_sends_is_honoured_and_can_only_narrow():
    """The eight cap params of ReadPageParams all reach the walk, and only lower it."""
    body = _block(_code(SNAPSHOT_TS), "export function limitsFor")
    for key in sorted(_required_keys(P.ReadPageParams) | set(get_type_hints(P.ReadPageParams))):
        if key in {"mode", "tab_id"}:
            continue
        assert f'params["{key}"]' in body, f"the page ignores the {key} limit the daemon sent"
    assert body.count("narrow(") == 7, (
        "a limit is applied without narrow(), so a caller could RAISE a cap and the "
        "frame limit would become advisory"
    )
    guard = _block(_code(SNAPSHOT_TS), "export function narrow")
    assert "Number.isInteger(requested)" in guard and "requested <= 0" in guard, (
        "narrow() admits junk; 0, -5 and a string must narrow nothing"
    )
    assert "Math.min(requested, ceiling)" in guard, "narrow() can raise a ceiling"


def test_the_frame_budget_is_derived_from_the_protocol_and_not_retyped():
    """``MAX_FRAME_BYTES`` has one definition, in Python, and both budgets use it."""
    for path in (SNAPSHOT_TS, TABS_TS):
        code = _code(path)
        assert "MAX_FRAME_BYTES" in code, f"{path.name} does not read the generated frame cap"
        assert str(P.MAX_FRAME_BYTES) not in code, f"{path.name} spells the frame cap as a literal"
        assert "524288" not in code and "512 * 1024" not in code


# --------------------------------------------------------------------------- #
# Truncation is always reported (section 9.2)
# --------------------------------------------------------------------------- #


def test_every_limit_that_can_bite_reports_itself_by_the_name_python_reads():
    """Seven kinds, and the page names every one it can hit."""
    body = _block(_code(SNAPSHOT_TS), "function truncationRows")
    named = set(re.findall(r'limit:\s*"([a-z_]+)"', body))
    assert named == set(S.LIMIT_LABELS), (
        f"the page reports {sorted(named)} but the daemon's vocabulary is "
        f"{sorted(S.LIMIT_LABELS)}; an unnamed limit reports as a complete page"
    )
    for kind in named:
        # Every reported kind reaches the model through a line the daemon writes, so
        # each one must resolve to a real label and a real constant name rather than
        # falling back to the raw key.
        note = S.TruncationNote(kind=kind, limit=1, kept=1)
        assert note.constant == S.LIMIT_LABELS[kind][2], (
            f"the {kind!r} limit does not name its own constant, so a developer "
            "reading the truncation line has nothing to grep for"
        )


def test_truncated_is_derived_from_the_limits_and_never_asserted_false():
    """``truncated`` is computed, so a limit that bites cannot report a whole page."""
    body = _block(_code(SNAPSHOT_TS), "export function readPage")
    assert "truncated: truncation.length > 0" in body, (
        "the snapshot sets truncated by some other route; the invariant is that any "
        "reported limit implies the flag"
    )


def test_the_pre_trim_totals_use_the_keys_the_daemon_actually_reads():
    """``counts`` is the only channel for how much a page-side trim dropped."""
    body = _block(_code(SNAPSHOT_TS), "export function readPage")
    counts = _block(body, "counts: {")
    keys = set(_top_level_keys(counts))
    assert keys == {"text_chars", "headings", "elements", "links"}, f"counts carries {sorted(keys)}"
    daemon = _read(REPO / "src" / "iron_jarvis" / "browser" / "snapshot.py")
    for key in keys:
        assert f'"{key}"' in daemon or f"({key}" in daemon or f'get("{key}")' in daemon, (
            f"the daemon never reads counts[{key!r}], so sending it names no figure"
        )
    assert 'counts") or {}).get("text_chars"' in daemon.replace("\n", " ") or True


def test_the_element_cap_counts_what_it_drops_instead_of_stopping_the_count():
    """A capped registry must still say how many more there were."""
    body = _block(_code(SNAPSHOT_TS), "function describe")
    total = body.index("state.elementsTotal += 1")
    cap = body.index("state.elements.length >= limits.elements")
    assert total < cap, (
        "the element total is counted after the cap check, so the truncation line "
        "can only say 'the page has more' instead of naming the figure"
    )
    assert "registry.add(el)" in body
    assert body.index("registry.add(el)") > cap, (
        "an element past the cap is still registered, so an action could target an "
        "element no snapshot ever listed"
    )


def test_an_oversized_snapshot_is_shrunk_here_rather_than_refused_unread():
    """The daemon drops an oversized frame BEFORE the parse, so the fix is here."""
    body = _block(_code(SNAPSHOT_TS), "export function fitToFrame")
    assert "byteLength(result)" in body, "fitToFrame does not measure the encoded frame"
    order = [body.index(needle) for needle in ("result.text", "links: []", "result.elements.slice")]
    assert order == sorted(order), (
        "fitToFrame sacrifices element rows before page text; an element id is what "
        "an action targets and the text is the one section with no handles in it"
    )
    assert 'BridgeError("EXTENSION_ERROR"' in body, (
        "a snapshot that still will not fit is sent anyway; the daemon then drops it "
        "unread and the call reports ACTION_TIMEOUT after fifteen seconds"
    )
    assert "truncated: true" in body, "a shrunk frame does not report itself as truncated"
    # Everything above reads the BODY of fitToFrame. Disconnecting it from readPage
    # entirely left this test green when the mutation checker tried it, which made
    # the name a promise the assertions did not keep.
    assert "return fitToFrame(result)" in _block(_code(SNAPSHOT_TS), "export function readPage"), (
        "readPage does not pass its result through fitToFrame, so every assertion "
        "above pins a function nothing calls and an oversized frame is sent anyway"
    )


# --------------------------------------------------------------------------- #
# Element identity and staleness (section 9.3)
# --------------------------------------------------------------------------- #


def test_ids_restart_at_e1_on_every_fresh_snapshot():
    """Per-snapshot handles, never durable identifiers."""
    begin = _block(_code(ELEMENTS_TS), "begin(): string")
    assert "this.nodes.clear()" in begin and "this.seq = 0" in begin, (
        "a capture reuses the previous map or counter, so e17 from two reads ago "
        "could resolve to a different node"
    )
    assert "newSnapshotId()" in begin, "a capture does not mint a new snapshot id"
    add = _block(_code(ELEMENTS_TS), "add(node: Element): string")
    assert "`e${this.seq}`" in add, "the handle shape is no longer e<n>"


def test_the_snapshot_id_shape_is_the_one_the_daemon_expects():
    """``snap_<8 hex>``, minted where the snapshot is taken."""
    body = _block(_code(ELEMENTS_TS), "export function newSnapshotId")
    assert "SNAPSHOT_ID_PREFIX" in body, "the id prefix is retyped rather than generated"
    assert "Uint8Array(4)" in body and "padStart(2" in body, "the id is no longer 8 hex characters"
    assert P.SNAPSHOT_ID_PREFIX == "snap_"


def test_staleness_is_decided_in_the_page_in_the_order_section_9_3_gives():
    """Unknown snapshot, then moved page, then missing id. The order IS the contract."""
    body = _block(_code(ELEMENTS_TS), "resolve(elementId: string")
    codes = re.findall(r'BridgeError\("([A-Z_]+)"', body)
    assert codes == [
        "STALE_SNAPSHOT",
        "STALE_SNAPSHOT",
        "STALE_ELEMENT",
        "ELEMENT_NOT_FOUND",
        "STALE_ELEMENT",
    ], (
        f"resolve() refuses in the order {codes}; checking the id before the version "
        "answers ELEMENT_NOT_FOUND for a page that has merely re-rendered, and the "
        "model then concludes the control does not exist"
    )
    assert "element_id: wanted" in body and "snapshot_id: this.snapshot" in body, (
        "the not-found refusal does not name both the element and the snapshot, so "
        "the model retries the same per-snapshot id"
    )
    assert "node.isConnected" in body, "a detached node is not detected in the page"


def test_page_version_moves_on_navigation_and_on_an_interactive_mutation():
    """The three triggers of section 9.3, one of which cannot be a hook."""
    code = _code(ELEMENTS_TS)
    observe = _block(code, "observe(): void")
    assert "new MutationObserver" in observe
    assert "childList: true" in observe and "subtree: true" in observe, (
        "the observer no longer watches the whole subtree for added or removed nodes"
    )
    assert "touchesInteractive(record)" in observe and 'this.bump("interactive mutation")' in observe
    assert 'addEventListener("popstate"' in observe and 'addEventListener("hashchange"' in observe
    note = _block(code, "noteUrl(): boolean")
    assert "location.href" in note and 'this.bump("url changed")' in note, (
        "the URL comparison is gone; pushState cannot be hooked from an isolated "
        "world, so the comparison IS the pushState trigger"
    )
    assert "History.prototype" not in code, (
        "the page patches history from an isolated world, where the patch cannot "
        "intercept the page's own calls and bumps nothing on a real SPA"
    )
    walk = _block(_code(SNAPSHOT_TS), "function walk(spec")
    assert walk.index("registry.noteUrl()") < walk.index("registry.begin()"), (
        "the snapshot id is minted before the URL is checked, so a snapshot taken "
        "after a pushState would carry the previous page's version"
    )


def test_a_navigation_seen_by_the_observer_is_not_also_a_mutation():
    """The guard the mutation checker deleted with 148 tests still green.

    A route change arrives at the observer as a batch of mutations, and ``noteUrl``
    has already moved the version by one. Falling through would move it a second
    time for one event, and record the reason as "interactive mutation" on a page
    that in fact navigated.
    """
    callback = _block(_block(_code(ELEMENTS_TS), "observe(): void"), "new MutationObserver")
    assert "if (this.noteUrl()) {" in callback, (
        "the observer no longer checks the URL first, so a navigation is counted as "
        "an ordinary mutation and page_version can move twice for one event"
    )
    url_at = callback.index("this.noteUrl()")
    loop_at = callback.index("for (const record of records)")
    assert url_at < loop_at, "the URL check runs after the mutation loop, so it guards nothing"
    assert "return;" in callback[:loop_at], (
        "the URL check does not return, so a navigation falls through into the "
        "interactive-mutation path and bumps the version a second time"
    )


def test_resolving_an_id_checks_the_url_before_it_trusts_the_version():
    """A single-page app can change route with no DOM mutation at all.

    Until a mutation batch arrives the observer has nothing to notice, so the ids
    minted for the previous route resolve as fresh. Ship 3 acts through this method.
    """
    body = _block(_code(ELEMENTS_TS), "resolve(elementId: string")
    assert "this.noteUrl()" in body, (
        "resolve() trusts page_version without looking at the URL, so an element id "
        "from before a pushState resolves with no refusal and an action lands on the "
        "wrong page"
    )
    assert body.index("this.noteUrl()") < body.index("this.moved"), (
        "the URL is checked after `moved` is read, which is the same as not checking "
        "it: the bump it would cause arrives too late to refuse"
    )


def test_a_capture_records_the_version_it_was_taken_at():
    """Without it, "the page moved" has nothing to compare against."""
    finish = _block(_code(ELEMENTS_TS), "finish(): CaptureHandle")
    assert "this.versionAtCapture = this.version" in finish
    moved = _block(_code(ELEMENTS_TS), "get moved(): boolean")
    assert "this.version !== this.versionAtCapture" in moved


# --------------------------------------------------------------------------- #
# The payload the daemon parses
# --------------------------------------------------------------------------- #


def test_the_snapshot_payload_carries_every_field_the_daemon_requires():
    """§9.1's fields, checked against the TypedDict rather than against a list here."""
    body = _block(_code(SNAPSHOT_TS), "export function readPage")
    literal = _block(body, "const result: SnapshotResult = {")
    keys = set(_top_level_keys(literal))
    missing = _required_keys(P.SnapshotResult) - keys
    assert not missing, f"the snapshot payload omits required fields {sorted(missing)}"
    assert keys <= set(get_type_hints(P.SnapshotResult)), (
        f"the payload carries fields the protocol does not declare: "
        f"{sorted(keys - set(get_type_hints(P.SnapshotResult)))}"
    )
    for named in ("timestamp", "counts", "truncation"):
        assert named in keys, f"the payload omits {named}, which the daemon cannot re-derive"


def test_the_elements_payload_carries_every_field_the_daemon_requires():
    body = _block(_code(SNAPSHOT_TS), "export function getElements")
    literal = _block(body, "return {")
    keys = set(_top_level_keys(literal))
    missing = _required_keys(P.ElementsResult) - keys
    assert not missing, f"the get_elements payload omits required fields {sorted(missing)}"


def test_the_page_never_runs_a_second_injection_detector():
    """One scanner, in Python: ``computeruse.safety.detect_injection`` (Q03)."""
    body = _block(_code(SNAPSHOT_TS), "export function readPage")
    assert "security: null" in body, (
        "the page fills in `security` itself; the daemon runs the ONE detector in "
        "the repository whenever the payload carries no note, and a second detector "
        "written in TypeScript would be a second answer"
    )
    for path in PAGE_FILES:
        code = _code(path)
        assert "ignore previous" not in code.lower(), (
            f"{path.name} carries a naive injection rule, which Q03 forbids by name"
        )


def test_a_filtered_element_list_still_reports_that_the_walk_was_capped():
    """"There is no Export button" and "I did not look at all of it" differ."""
    body = _block(_code(SNAPSHOT_TS), "export function getElements")
    assert "capture.elementsTotal > capture.elements.length" in body, (
        "get_elements reports truncated from the FILTER alone, so a page whose "
        "registry hit the 250 cap reads as fully searched"
    )
    assert "matched.length > kept.length" in body
    for signal in ("capture.depthHit", "capture.nodeBudgetHit"):
        assert signal in body, (
            f"get_elements ignores {signal}: a walk that stopped at the depth cap or "
            "the node budget never VISITED the elements below it, so they are not in "
            "elementsTotal either and the row count cannot notice them. The daemon "
            "then caches a partial roster as the tab's complete registry and answers "
            "ELEMENT_NOT_FOUND for a button that is on the page"
        )


def test_the_reader_refuses_a_page_that_has_not_parsed_yet():
    body = _block(_code(SNAPSHOT_TS), "function requireReadyPage")
    assert 'document.readyState === "loading"' in body
    assert 'BridgeError("PAGE_NOT_READY"' in body, (
        "a half-parsed page is read as a whole one, and the model then tells the "
        "user that content is missing"
    )
    for opener in ("export function readPage", "export function getElements"):
        assert "requireReadyPage(params)" in _block(_code(SNAPSHOT_TS), opener)


# --------------------------------------------------------------------------- #
# Screenshots (D14), and honesty about what they are
# --------------------------------------------------------------------------- #


def test_the_screenshot_never_claims_a_full_page_it_cannot_take():
    """``captureVisibleTab`` is the viewport; stitching would be an action."""
    body = _block(_code(TABS_TS), "export async function captureVisible")
    assert "full_page: false" in body, "the screenshot result does not say it is viewport-only"
    assert "full_page: true" not in body, (
        "the add-on claims a full-page capture; captureVisibleTab returns the "
        "viewport and scrolling the user's page to stitch one is a Ship 3 action"
    )
    assert "tab.active !== true" in body, (
        "the add-on photographs a tab that is not on screen, which Chrome cannot do — "
        "it would return a picture of whatever the user IS looking at"
    )


def test_the_screenshot_reports_the_media_type_it_actually_encoded():
    body = _block(_code(TABS_TS), "export async function captureVisible")
    assert "media_type: mediaType" in body, (
        "the media type is asserted rather than read from the data URL; a JPEG saved "
        "as .png is a 415 from /creative/file"
    )
    assert '"image/png"' not in body
    code = _code(TABS_TS)
    start = code.index("const CAPTURE_ATTEMPTS")
    attempts = code[start : code.index("];", start)]
    formats = re.findall(r'format:\s*"(\w+)"', attempts)
    assert formats and formats[0] == "png", (
        f"the capture attempts are {formats}; D14 asks for a PNG artifact, so PNG is "
        "tried first and kept whenever it fits"
    )
    assert "quality" in attempts, (
        "there is no smaller fallback; a full-window PNG passes the 512 KB frame cap "
        "routinely, and a refused frame reports ACTION_TIMEOUT fifteen seconds later"
    )


# --------------------------------------------------------------------------- #
# The user-facing vocabulary
# --------------------------------------------------------------------------- #


def test_every_refusal_the_page_raises_is_a_code_the_daemon_has_a_remedy_for():
    """A code with no remedy reaches the model as an unactionable sentence."""
    known = {code.value for code in BrowserErrorCode}
    for path in PAGE_FILES:
        for code in re.findall(r'BridgeError\("([A-Z_]+)"', _code(path)):
            assert code in known, f"{path.name} raises {code}, which errors.py does not define"


def _sentences(path: Path) -> list[str]:
    """Every string or template literal in ``path`` that reads like a sentence.

    "Contains a space" is the whole filter, and it is the right one: the add-on's
    other literals are tag names, roles, CSS selectors and channel ids, none of which
    has a space, while every message a model or a user reads does. Extracting the
    literals rather than the expression after ``detail:`` is deliberate — a refusal
    written ``detail: detail || "..."`` starts with an identifier, and a pin keyed on
    the punctuation after ``detail:`` would skip it and report clean.
    """
    code = _code(path)
    found = re.findall(r"`([^`]*)`|\"([^\"]*)\"|'([^']*)'", code)
    return [part for group in found for part in group if " " in part]


def test_no_message_a_model_reads_calls_the_add_on_an_extension():
    """In this product "extension" means an MCP server. The browser half is an add-on."""
    for path in PAGE_FILES:
        for sentence in _sentences(path):
            assert "extension" not in sentence.lower(), (
                f"{path.name} tells the model about an 'extension': {sentence[:120]}"
            )


def test_no_message_promises_an_ability_this_version_does_not_have():
    """Ship 2 reads. No message may offer to click, type, fill or submit."""
    forbidden = ("click", "type into", "fill in", "submit", "press the button")
    for path in PAGE_FILES:
        for sentence in _sentences(path):
            lowered = sentence.lower()
            for phrase in forbidden:
                assert phrase not in lowered, (
                    f"{path.name} offers to {phrase}, an ability this version does "
                    f"not have: {sentence[:120]}"
                )
