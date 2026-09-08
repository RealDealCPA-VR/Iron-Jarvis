"""The browser side panel — the surface the action click now opens, pinned.

Ship 3 of the sidebar plan (v1.242.0), and the first Iron Jarvis CHAT surface that
lives inside the user's browser. Three things could go wrong here in a way no other
test in this repository can see, and each of them is silent:

* **The popup and the panel cannot both exist.** Chrome IGNORES
  ``chrome.sidePanel.setPanelBehavior({openPanelOnActionClick: true})` while
  ``action.default_popup`` is set — no error, no warning, the click just opens the
  popup forever. So D32 retires the popup, and this file pins the manifest shape
  that makes the call take effect, not merely the call.
* **Five places derive the add-on's filenames by string match**, and they are in
  four languages: ``esbuild.config.mjs`` (what is bundled), ``scripts/build.mjs``
  (what the release verifies exists), ``onboarding/doctor.py`` (what a user's
  install is told is ready to load), and the packaging test. Change the manifest
  without the other four and the build is green, the doctor says ready, and the
  folder Chrome loads has no panel bundle in it at all.
* **The panel must not lie about what Stop does.** A tool already running finishes
  (plan section 8), and a steer note is not taken until the turn's next round
  (section 5). A panel that painted either as done on the press would be making a
  promise the daemon never made.

These are SOURCE pins in the house shape, the same as
``test_browser_addon_ui_v1235.py`` — and they are NOT the whole story any more.
There is no JS test runner in ``extensions/chrome`` and there must not be one, but
a file of source pins alone means the panel's only script is never EXECUTED, and a
runtime break in it ships green past every pin below. So
``dashboard/__tests__/sidepanel-runtime-v1242.test.ts`` runs the BUILT
``dist/sidepanel.js`` against ``dist/sidepanel.html`` in the dashboard's own jsdom
harness and drives the real states. This file's last two tests pin THAT: that the
runtime test exists and refuses to skip, and that CI builds the add-on before the
dashboard suite runs it.

The pins themselves:

* CRLF is normalised once, AT THE READER. GitHub's Windows runners check the tree
  out with ``\\r\\n``, so a needle carrying an embedded newline matches every local
  run and never matches on CI — the trap that took the v1.232.0 installer down.
* No fixed-size windows. Blocks are found by counting braces from a declaration, so
  adding a line or a comment cannot make a pin report "never rendered" when the
  truth is "something moved".
"""

from __future__ import annotations

import json
import re
from importlib import import_module
from pathlib import Path

import pytest

from iron_jarvis.browser import protocol as P

#: Imported by NAME rather than `from ... import doctor`: `onboarding/__init__` binds
#: a `doctor` FUNCTION, which shadows the module and turns every attribute read here
#: into an AttributeError about a function.
doctor_mod = import_module("iron_jarvis.onboarding.doctor")

REPO = Path(__file__).resolve().parents[1]
ADDON = REPO / "extensions" / "chrome"
MANIFEST = ADDON / "manifest.json"
PANEL_HTML = ADDON / "src" / "sidepanel" / "sidepanel.html"
PANEL_TS = ADDON / "src" / "sidepanel" / "sidepanel.ts"
WORKER_TS = ADDON / "src" / "background" / "index.ts"
SOCKET_TS = ADDON / "src" / "bridge" / "socket.ts"
PROTOCOL_TS = ADDON / "src" / "protocol.ts"
ESBUILD = ADDON / "esbuild.config.mjs"
BUILD_WRAPPER = ADDON / "scripts" / "build.mjs"
DOCTOR_PY = REPO / "src" / "iron_jarvis" / "onboarding" / "doctor.py"
PACKAGING_TEST = REPO / "tests" / "test_browser_packaging_v1239.py"
#: The test that EXECUTES the panel, and the two workflows that must build the
#: add-on before it runs. Pinned from here because the ordering is invisible: a
#: reordered workflow is green everywhere until the dashboard job goes red.
RUNTIME_TEST = REPO / "dashboard" / "__tests__" / "sidepanel-runtime-v1242.test.ts"
TESTS_WORKFLOW = REPO / ".github" / "workflows" / "tests.yml"
RELEASE_WORKFLOW = REPO / ".github" / "workflows" / "release.yml"
#: The guided setup window and the card, which are where a user LEARNS the panel
#: exists at all. A sidebar nothing mentions is a sidebar nobody opens.
SETUP_MODAL = REPO / "dashboard" / "components" / "browser" / "BrowserSetupModal.tsx"
BROWSER_CARD = REPO / "dashboard" / "components" / "browser" / "YourBrowserCard.tsx"

#: The one place this file spells the panel's bundle names. Every pin below derives
#: from these rather than repeating them, so a rename is one edit here and a set of
#: failures naming the surfaces that did not follow.
PANEL_HTML_BUNDLE = "dist/sidepanel.html"
PANEL_JS_BUNDLE = "dist/sidepanel.js"


def _read(path: Path) -> str:
    """One CRLF normalisation per file, at the reader. Never at a call site."""
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def _code(path: Path) -> str:
    """The file with its COMMENTS removed, for pins that read behaviour.

    Necessary rather than tidy: this file's own needles (``default_popup``,
    ``setPanelBehavior``) are quoted in the sources' comments, where they explain the
    decision being pinned. A pin that matched prose would go red on a comment and
    green on the bug. The lookbehind keeps ``http://`` inside string literals intact.
    """
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


def _manifest() -> dict:
    return json.loads(_read(MANIFEST))


def _visible_html(path: Path) -> str:
    """Everything the user can read on a page: comments, CSS and script removed."""
    text = _read(path)
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    text = re.sub(r"<style.*?</style>", "", text, flags=re.S)
    return re.sub(r"<script.*?</script>", "", text, flags=re.S)


# --------------------------------------------------------------------------- #
# The manifest: the panel exists, and the popup does not
# --------------------------------------------------------------------------- #


def test_the_addon_ships_the_version_of_the_app_it_came_with():
    """The add-on's own version, pinned to Iron Jarvis's (v1.242.0).

    IT WAS FROZEN AT 1.235.0 while the app moved seven versions past it, and the
    add-on ships INSIDE the installer. That is not cosmetic: Chrome keeps serving
    the copy of an unpacked add-on it already loaded until somebody presses Reload,
    and the previously-loaded copy still declares ``action.default_popup`` — so the
    toolbar click opens the retired popup and the side panel is unreachable, while
    ``chrome://extensions`` reads 1.235.0 before AND after the update and the user
    has nothing to compare. One number, in both places, so the panel's header (which
    prints ``chrome.runtime.getManifest().version``) can be read against the app's.
    """
    from iron_jarvis import __version__

    assert _manifest()["version"] == __version__, (
        "the browser add-on declares a different version from the app that ships it; "
        "a user cannot then tell a stale loaded copy from the current one, and the "
        "stale one has no side panel at all"
    )


def test_the_manifest_declares_the_side_panel():
    manifest = _manifest()
    assert "sidePanel" in manifest["permissions"], (
        "without the sidePanel permission chrome.sidePanel is undefined, the worker's "
        "setPanelBehavior call does nothing, and the action click opens nothing at all"
    )
    assert manifest["side_panel"]["default_path"] == PANEL_HTML_BUNDLE, (
        "the panel's page is named here and nowhere else; the build verifier and the "
        "doctor both read this field to decide whether the add-on is loadable"
    )


def test_the_popup_is_retired_because_chrome_ignores_the_panel_otherwise():  # D32
    """The whole reason the popup had to go, stated as the property.

    Chrome ignores ``setPanelBehavior({openPanelOnActionClick: true})`` while
    ``action.default_popup`` is set — silently, with no console warning — so keeping
    both would cost the user a click forever AND split the status story across two
    surfaces, one of which they would never see again.
    """
    action = _manifest()["action"]
    assert "default_popup" not in action, (
        "action.default_popup is back: while it is set the action click opens the "
        "popup and the side panel is unreachable from the toolbar, which is exactly "
        "the failure D32 retires the popup to avoid"
    )
    assert action["default_title"], (
        "the action key must survive with its title — removing it removes the "
        "toolbar button the panel is opened from"
    )
    assert not (ADDON / "src" / "popup").exists(), (
        "the popup sources are back; two status surfaces is the thing D32 removes, "
        "and the panel header is where that readout lives now"
    )


def test_the_minimum_chrome_version_already_clears_the_side_panel_api():
    """114 is where ``chrome.sidePanel`` arrives; the floor was 120 before this ship.

    Pinned because the support matrix NOT moving is part of the decision: a ship
    that quietly raised the floor would drop users to buy a feature they could
    already have had.
    """
    assert int(_manifest()["minimum_chrome_version"]) >= 114


# --------------------------------------------------------------------------- #
# The action click opens the panel
# --------------------------------------------------------------------------- #


def test_the_action_click_opens_the_panel_and_cannot_break_an_older_browser():
    worker = _code(WORKER_TS)
    assert "setPanelBehavior({ openPanelOnActionClick: true })" in worker, (
        "the service worker never asks Chrome to open the panel on the action click, "
        "so the toolbar button does nothing at all now that the popup is gone"
    )
    # The call must be GUARDED. `chrome.sidePanel` does not exist before Chrome 114,
    # and an unguarded throw at the top level of a service worker takes the whole
    # bridge down — every tool call, not just the panel.
    guarded = re.search(
        r"try \{[^}]*setPanelBehavior[\s\S]*?\} catch",
        worker,
    )
    assert guarded, (
        "setPanelBehavior is called outside a try/catch: on a browser with no side "
        "panel API that throw kills the worker, and the add-on loses the socket too"
    )


# --------------------------------------------------------------------------- #
# The panel's controls
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "element_id",
    ["transcript", "ask", "send", "stop", "steer", "approval", "approve", "deny"],
    ids=["transcript", "composer", "send", "stop", "steer", "approval", "approve", "deny"],
)
def test_the_panel_declares_every_control_the_plan_promises(element_id: str):
    """One row per control the user asked for, so none can be quietly dropped."""
    html = _read(PANEL_HTML)
    assert f'id="{element_id}"' in html, (
        f"the side panel has no #{element_id}; the plan's acceptance rows 6-10 are "
        "each driven through one of these"
    )


def test_stop_and_steer_are_shown_only_while_a_turn_is_running():
    """A Stop button over a finished turn is a button whose press does nothing."""
    html = _read(PANEL_HTML)
    idle = re.search(r'body\[data-turn="idle"\][^{]*\{[^}]*\}', html)
    assert idle, "nothing in the panel's stylesheet hides Stop while the turn is idle"
    assert "#stop" in idle.group(0) and "#steer" in idle.group(0), (
        "Stop and Steer must both be hidden in the idle state; the plan asks for a "
        "Stop button visible only while a turn is running"
    )
    panel = _code(PANEL_TS)
    body = _block(panel, "function setTurn(")
    assert 'document.body.dataset["turn"]' in body, (
        "the running state must be ONE attribute the stylesheet reads; five separate "
        "hidden writes are five things that can disagree"
    )


def test_stop_says_what_it_cannot_do():
    """Plan section 8: a tool already executing is not killed by a stop.

    The words are the deliverable here. An interface that offers Stop and says
    nothing implies an abort it cannot perform, and the user learns that the hard
    way — on the write that landed anyway.
    """
    visible = _visible_html(PANEL_HTML).lower()
    assert "stop prevents the next step" in visible, (
        "the panel does not say that Stop prevents the NEXT step; without that "
        "sentence the button promises an abort the daemon cannot perform"
    )
    assert "finishes" in visible, (
        "the panel must say a tool already running finishes — that is the half of "
        "Stop the user is surprised by"
    )


def test_a_steer_note_is_pending_until_the_daemon_says_it_landed():
    """Plan section 5: never tell the user their correction landed before it did."""
    panel = _code(PANEL_TS)
    listener = _block(panel, 'el.steer?.addEventListener("click"')
    assert 'dataset["pending"] = "true"' in listener, (
        "the steer press must mark its note PENDING; a note rendered as taken is a "
        "claim only the daemon can make, at the next tool-round boundary"
    )
    landed = _block(panel, "case PANEL_EVENT_STEERED:")
    assert 'dataset["pending"] = "false"' in landed and "pendingSteers.delete" in landed, (
        "nothing clears the pending mark when the daemon reports the steer was "
        "taken, so a note that DID land would sit there looking unanswered"
    )
    done = _block(panel, "case PANEL_EVENT_DONE:")
    assert 'dataset["pending"] = "false"' in done and "pendingSteers" in done, (
        "a turn that ended without consuming a steer never will; the panel must say "
        "so rather than leave the note pending forever"
    )


def test_a_panel_press_that_never_left_the_browser_is_reported_as_such():
    """The panel is a view over a socket that can be down.

    Painting the question into the transcript and sending nothing is a panel that
    looks like it is thinking forever — the failure mode this whole surface is most
    likely to produce, because the socket's absence is invisible from the page.
    """
    panel = _code(PANEL_TS)
    body = _block(panel, "): Promise<boolean> {")
    assert "Boolean(reply?.sent)" in body, (
        "post() must report the WORKER's own verdict on whether the frame left; a "
        "truthy reply object is not the same fact, and it is true even when the "
        "socket dropped the action"
    )
    send = _block(panel, 'el.send?.addEventListener("click"')
    assert "if (!sent)" in send, (
        "the composer does not check whether its question reached Iron Jarvis"
    )
    worker = _code(WORKER_TS)
    handler = _block(worker, 'case "panel_action":')
    assert "socket.sendPanel(" in handler and "respond({ sent })" in handler, (
        "the worker must answer the panel with whether the frame went out; a bare "
        "respond() leaves the page unable to tell a sent action from a dropped one"
    )


# --------------------------------------------------------------------------- #
# The wire
# --------------------------------------------------------------------------- #


def test_the_panel_speaks_the_generated_protocols_own_vocabulary():
    """No second spelling of the actions, in any of the three files that use them."""
    generated = _read(PROTOCOL_TS)
    assert f'export const FRAME_PANEL = "{P.FRAME_PANEL}";' in generated
    assert f'export const FRAME_PANEL_EVENT = "{P.FRAME_PANEL_EVENT}";' in generated
    panel = _code(PANEL_TS)
    # Everything AFTER the import block: a constant that appears only in the import
    # list is a constant nothing calls, and the literal it was replaced by would sit
    # in the code unnoticed.
    used = panel.split('} from "../protocol";', 1)[1]
    for action in P.ALL_PANEL_ACTIONS:
        const = f"PANEL_ACTION_{action.upper()}"
        assert const in used, (
            f"the panel never uses {const}; an action spelled as a string literal "
            "here is a second vocabulary the generated protocol cannot keep honest"
        )
    for event in P.ALL_PANEL_EVENTS:
        const = f"PANEL_EVENT_{event.upper()}"
        assert f"case {const}:" in panel, (
            f"the panel has no branch for {const}; an unhandled event is a frame the "
            "daemon sends and the user never sees"
        )


def test_the_socket_hands_panel_events_on_without_reading_them():
    socket = _code(SOCKET_TS)
    assert "case FRAME_PANEL_EVENT:" in socket, (
        "the bridge drops browser.panel_event into its unknown-frame branch, so "
        "every turn the daemon narrates is recorded as an unsupported frame"
    )
    body = _block(socket, "sendPanel(action: string, params: Record<string, unknown>): boolean")
    assert "!this.token" in body, (
        "sendPanel must refuse on an unpaired socket: browser.panel is deliberately "
        "NOT in RESTRICTED_INBOUND_FRAMES, so sending one before pairing earns a 1008 "
        "that takes the whole bridge down"
    )


def test_an_unpaired_socket_may_never_ask_the_daemon_to_run_a_turn():
    """Executable, not a source pin: this is the wire, and it is Python."""
    assert P.FRAME_PANEL not in P.RESTRICTED_INBOUND_FRAMES
    assert P.FRAME_PANEL in P.EXTENSION_TO_DAEMON
    assert P.FRAME_PANEL_EVENT in P.DAEMON_TO_EXTENSION
    assert P.FRAME_SHAPES[P.FRAME_PANEL] is P.PanelFrame
    assert P.FRAME_SHAPES[P.FRAME_PANEL_EVENT] is P.PanelEventFrame


# --------------------------------------------------------------------------- #
# Access off: the panel says it can run nothing
# --------------------------------------------------------------------------- #


def test_the_panel_is_honest_when_browser_access_is_off():
    """Acceptance row 10. The daemon refuses a panel turn outright when access is
    ``off``, so a composer offered in that state can only produce a refusal."""
    html = _read(PANEL_HTML)
    assert 'id="empty"' in html, "the panel has no access-off state at all"
    rule = re.search(r'body\[data-access="off"\][^{]*\{[^}]*\}', html)
    assert rule, "nothing hides the composer while Browser access is off"
    assert "footer" in rule.group(0), (
        "the composer is still offered with access off; every question typed into it "
        "would be refused by the daemon, which reads as a broken panel"
    )
    visible = _visible_html(PANEL_HTML).lower()
    assert "browser access is off" in visible
    assert "can run nothing" in visible, (
        "the empty state must say the panel can run NOTHING; 'limited' or 'read only' "
        "words here would have the user waiting for an answer that cannot come"
    )
    panel = _code(PANEL_TS)
    assert 'document.body.dataset["access"]' in panel, (
        "the empty state must be driven by the access mode the daemon reported, not "
        "by a guess the panel makes"
    )


# --------------------------------------------------------------------------- #
# The copy rule
# --------------------------------------------------------------------------- #


def test_the_panel_never_calls_the_add_on_an_extension():
    """In Iron Jarvis "extension" already means an MCP server (house copy rule)."""
    visible = _visible_html(PANEL_HTML).replace("chrome://extensions", "")
    hits = re.findall(r"\bextensions?\b", visible, flags=re.I)
    assert not hits, f"sidepanel.html calls the add-on an extension {len(hits)} time(s)"


# --------------------------------------------------------------------------- #
# The five derivations that must move together
# --------------------------------------------------------------------------- #


def test_the_bundler_builds_and_copies_the_panel():
    config = _code(ESBUILD)
    assert "sidepanel: join(ROOT" in config, (
        "esbuild has no sidepanel entry point, so dist/sidepanel.js is never written "
        "and the panel loads as a blank page with no error the user can see"
    )
    assert '"src/sidepanel/sidepanel.html", "sidepanel.html"' in config, (
        "the panel's HTML is not copied into dist/, so the manifest's "
        "side_panel.default_path names a file that does not exist"
    )
    assert "popup" not in config, (
        "the bundler still builds a popup that no longer has sources; the build "
        "would fail, or worse, ship a surface nothing opens"
    )


def test_the_release_verifier_reads_the_panel_out_of_the_manifest():
    """``scripts/build.mjs`` is what stands between a release and a hollow folder."""
    wrapper = _code(BUILD_WRAPPER)
    assert "manifest.side_panel && manifest.side_panel.default_path" in wrapper, (
        "the build verifier does not read side_panel.default_path, so a missing panel "
        "bundle ships silently — esbuild exits 0 for every entry point it was GIVEN"
    )
    assert "default_popup" not in wrapper, (
        "the verifier still reads action.default_popup, which the manifest no longer "
        "has: it would find nothing, check nothing, and pass on an add-on with no panel"
    )
    required = _block(wrapper, "function requiredFiles(")
    assert 'file.replace(/\\.html$/, ".js")' in required, (
        "the verifier must still derive each HTML surface's own bundle; the page is "
        "useless without it, and a zero-byte script registers fine and does nothing"
    )


def test_the_doctor_tells_a_user_the_panel_is_missing():
    doctor = _read(DOCTOR_PY)
    assert 'panel.get("default_path")' in doctor, (
        "the doctor does not check the side panel, so an install whose dist/ lost "
        "sidepanel.html is reported as built and ready to load"
    )
    assert 'action.get("default_popup")' not in doctor, (
        "the doctor still reads action.default_popup — a field the manifest no longer "
        "carries — so this row silently stopped covering the surface the user clicks"
    )
    # The panel is named by the MANIFEST, so it must not also be hard-coded in the
    # runtime-loaded list: two copies of one filename can disagree in silence.
    assert PANEL_HTML_BUNDLE not in doctor_mod.BROWSER_ADDON_RUNTIME_FILES, (
        "the panel is in BROWSER_ADDON_RUNTIME_FILES as well as in the manifest; that "
        "tuple is for bundles NO manifest field names"
    )


def test_the_packaging_test_names_the_panel_bundle():
    """The one test that really runs the build must check the file it produced."""
    packaging = _read(PACKAGING_TEST)
    assert 'manifest["side_panel"]["default_path"]' in packaging, (
        "test_browser_packaging_v1239 still asserts over the popup, so the executable "
        "build check no longer proves the panel bundle exists"
    )


def test_the_app_tells_the_user_the_sidebar_exists_and_how_to_reach_it():
    """Reachability finding R1 (v1.242.0). A feature nothing mentions is not shipped.

    Before this ship the dashboard contained no "sidebar", no "side panel" and no
    "toolbar" anywhere — and Chrome does not put a newly loaded unpacked add-on on
    the toolbar at all, it files it behind the puzzle-piece menu. So the panel could
    be built, loaded, paired and granted, and still have no icon to click. Both
    surfaces the user actually reads must say it, which is why this pins two files:
    the guided window is read once, the card is read every time.
    """
    for path, what in ((SETUP_MODAL, "the guided setup window"), (BROWSER_CARD, "the Your browser card")):
        # COMMENTS STRIPPED. Both files explain this finding at length in their own
        # docstrings, and a pin that matched prose would stay green on the day the
        # sentence the user reads is deleted and the essay above it is not.
        text = _code(path)
        assert "sidebar" in text.lower(), f"{what} never mentions the sidebar"
        assert "toolbar" in text.lower(), f"{what} never says where the icon is"
        assert "puzzle-piece" in text, (
            f"{what} does not say the icon is hidden behind Chrome's puzzle-piece menu, "
            "which is the difference between the sidebar existing and being reachable"
        )
        assert "chrome://extensions" in text and "Reload" in text, (
            f"{what} gives no remedy for a browser still running an older copy of the "
            "add-on — the copy whose toolbar click opens the retired popup"
        )


def test_one_name_for_the_page_the_user_is_sent_to():
    """Reachability finding R3. Two names for one place is two places to look."""
    visible = _visible_html(PANEL_HTML)
    assert "Browser page in Iron Jarvis" in visible, (
        "the panel sends the user somewhere other than the Browser page, which is "
        "the name the dashboard's own nav shows"
    )


def test_the_panel_prints_which_build_of_itself_is_running():
    """Reachability finding R2, the half the panel can own.

    Nothing compares versions HERE and nothing should: ``browser.ready`` carries no
    Iron Jarvis version, so the panel would be guessing at what current means. What
    it can do is state which copy answered, from the manifest rather than a constant.
    """
    assert 'id="version"' in _read(PANEL_HTML), "the panel prints no add-on version"
    panel = _code(PANEL_TS)
    assert "chrome.runtime.getManifest().version" in panel, (
        "the panel's version readout is not read from the manifest, so it can print a "
        "number that is not the build the user is actually running"
    )


def test_the_panel_bundle_is_executed_by_a_test_that_cannot_skip():
    """Reachability finding R4, and the repo's named failure mode.

    Every other test in this file is a SOURCE pin. A runtime break in
    ``dist/sidepanel.js`` — a null element read at load, a listener that never
    registers — passes all of them and reaches the user as a blank panel.
    """
    assert RUNTIME_TEST.is_file(), (
        "nothing executes dist/sidepanel.js; the panel's only script would ship green "
        "with a runtime break in it"
    )
    runtime = _read(RUNTIME_TEST)
    assert "dist" in runtime and "sidepanel.js" in runtime, (
        "the runtime test does not read the BUILT bundle, so it proves the TypeScript "
        "is fine while the folder Chrome loads may carry a stale one"
    )
    assert "new Function(source)" in runtime, "the runtime test never runs the bundle"
    assert ".skip" not in runtime and "it.skip" not in runtime, (
        "the runtime test skips somewhere; a test that skips when its subject is "
        "missing is a test that cannot fail"
    )


def test_ci_builds_the_addon_before_the_dashboard_suite_runs_it():
    """The ordering the runtime test depends on, in BOTH workflows.

    ``extensions/chrome/dist`` is gitignored, so it exists on a runner only because
    a build step produced it. The runtime test FAILS rather than skips when it is
    absent — deliberately — which makes this ordering load-bearing and invisible:
    move the build below the vitest step and the dashboard job goes red with a
    message about a missing folder rather than about the change that caused it.
    """
    for workflow in (TESTS_WORKFLOW, RELEASE_WORKFLOW):
        text = _read(workflow)
        build = text.find("node scripts/build.mjs")
        vitest = text.find("run: pnpm test")
        assert build != -1, f"{workflow.name} never builds the browser add-on"
        assert vitest != -1, f"{workflow.name} never runs the dashboard suite"
        assert build < vitest, (
            f"{workflow.name} runs the dashboard suite BEFORE building the add-on, so "
            "sidepanel-runtime-v1242 finds no dist/ and fails on every run"
        )


def test_a_built_addon_folder_carries_the_panel_and_its_script():
    """Skipped rather than failed where nothing has been built.

    ``extensions/chrome/dist`` is gitignored, so on a fresh checkout there is nothing
    to inspect. The `browser-addon` job in tests.yml runs the real build on every
    push, and `test_browser_packaging_v1239` executes it wherever node is present.
    """
    dist = ADDON / "dist"
    if not (dist / "background.js").exists():
        pytest.skip("extensions/chrome/dist is not built here; the CI job builds it")
    for rel in (PANEL_HTML_BUNDLE, PANEL_JS_BUNDLE):
        built = ADDON / rel
        assert built.is_file() and built.stat().st_size > 0, (
            f"{rel} is missing or empty in a built dist/ — the action click would "
            "open a blank panel with no error anywhere the user can see it"
        )
