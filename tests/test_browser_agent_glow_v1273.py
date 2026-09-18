"""v1.273.0 — the tab the agent is working in glows.

THE REQUEST (2026-09-18): "when using the browser extension, I want to have a
light glow around the tab it is controlling so I can visually see the tab that is
being operated by the agent."

The worker answers every command the daemon sends, and every command that worked
in a tab answers with that tab's id — so the worker is the one place that always
knows which tab the agent has. ``background/glow.ts`` paints a soft accent-coloured
outline into that page and clears it when the turn ends.

Source pins here (the worker has no runtime harness); ``glowTarget`` is lifted and
run under node against the generated protocol's own method names.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from iron_jarvis.browser import protocol as P

ROOT = Path(__file__).resolve().parents[1]
ADDON = ROOT / "extensions" / "chrome" / "src"
GLOW_TS = ADDON / "background" / "glow.ts"
WORKER_TS = ADDON / "background" / "index.ts"
DISPATCH_TS = ADDON / "bridge" / "dispatch.ts"
SIDEBAR_TSX = ROOT / "dashboard" / "components" / "Sidebar.tsx"

requires_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")


def _src(path: Path) -> str:
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def _fn(src: str, name: str) -> str:
    """The body of a top-level function, from its line to the closing brace at column 0."""
    start = src.index(f"function {name}(")
    end = src.index("\n}\n", start) + 3
    return src[start:end]


# --------------------------------------------------------------------------- #
# 1. What is painted, and what it must never do
# --------------------------------------------------------------------------- #


def test_the_glow_is_visual_only_and_reads_nothing():
    src = _src(GLOW_TS)
    assert 'export const GLOW_ID = "ij-agent-glow";' in src
    paint = _fn(src, "paintGlow")
    assert "pointer-events:none" in paint, "the glow would swallow the user's clicks"
    assert "position:fixed;inset:0" in paint and "z-index:2147483647" in paint
    assert "box-shadow:inset" in paint, "not an edge glow"
    # Nothing on the page is read: two nodes are created, nothing is queried or read.
    for forbidden in (".innerText", ".innerHTML", "querySelector(", "querySelectorAll(", "getComputedStyle("):
        assert forbidden not in paint, f"paintGlow touches the page beyond its own nodes: {forbidden}"
    assert paint.count("document.createElement(") == 2
    # Idempotent: a second paint finds the first.
    assert re.search(r"if \(document\.getElementById\(id\)\) \{\s*return;", paint)
    # Self-contained: the injected function names nothing from the module (the id is an argument).
    assert "GLOW_ID" not in paint and "GLOW_METHODS" not in paint
    clear = _fn(src, "clearGlow")
    assert 'getElementById(id)?.remove()' in clear and 'getElementById(id + "-style")?.remove()' in clear
    assert "GLOW_ID" not in clear


def test_the_glow_is_the_apps_own_accent():
    """The dashboard's accent is `--accent-rgb: 34 211 238`; the glow is the same colour."""
    dash = _src(SIDEBAR_TSX) if SIDEBAR_TSX.exists() else ""
    globals_css = ROOT / "dashboard" / "app" / "globals.css"
    accent_home = dash + (_src(globals_css) if globals_css.exists() else "")
    assert "34 211 238" in accent_home or "34,211,238" in accent_home or "#22d3ee" in accent_home.lower(), (
        "the dashboard's accent moved; move the glow with it"
    )
    assert "rgba(34,211,238" in _fn(_src(GLOW_TS), "paintGlow")


# --------------------------------------------------------------------------- #
# 2. Which commands glow — lifted and run under node with the protocol's names
# --------------------------------------------------------------------------- #


def _lifted_glow_target() -> str:
    src = _src(GLOW_TS)
    start = src.index("export const GLOW_METHODS")
    end = src.index("]);", start) + 3
    methods = src[start:end]
    target = src[src.index("export function glowTarget("):]
    target = target[: target.index("\n}\n") + 3]
    code = methods + "\n" + target
    code = code.replace("export ", "")
    code = re.sub(r": ReadonlySet<string>", "", code)
    code = re.sub(r"\(method: string, result: Record<string, unknown> \| null \| undefined\): number \| null", "(method, result)", code)
    return code


@requires_node
def test_glow_target_under_node_with_the_generated_method_names(tmp_path):
    names = [
        "METHOD_STATUS", "METHOD_LIST_TABS", "METHOD_ACTIVE_TAB", "METHOD_READ_PAGE", "METHOD_GET_ELEMENTS",
        "METHOD_SCREENSHOT", "METHOD_ACTIVATE_TAB", "METHOD_CREATE_TAB", "METHOD_CLOSE_TAB", "METHOD_NAVIGATE",
        "METHOD_CLICK", "METHOD_TYPE_TEXT", "METHOD_PRESS_KEY", "METHOD_SCROLL",
    ]
    consts = "\n".join(f'const {n} = {json.dumps(getattr(P, n))};' for n in names)
    cases = [
        ("click", {"tab_id": 7}, 7),
        ("read_page", {"tab_id": 42}, 42),
        ("navigate", {"tab_id": 3, "url": "https://x/"}, 3),
        ("create_tab", {"tab_id": 9}, 9),
        ("list_tabs", {"tab_id": 7}, None),
        ("status", {"tab_id": 7}, None),
        ("close_tab", {"tab_id": 7}, None),
        ("click", {}, None),
        ("click", {"tab_id": "7"}, None),
        ("scroll", None, None),
    ]
    script = (
        consts + "\n" + _lifted_glow_target() + "\n"
        + "const cases = " + json.dumps([[m, r] for m, r, _ in cases]) + ";\n"
        + "console.log(JSON.stringify(cases.map(([m, r]) => glowTarget(m, r))));\n"
    )
    f = tmp_path / "glow_target.js"
    f.write_text(script, encoding="utf-8")
    proc = subprocess.run(["node", str(f)], capture_output=True, text=True, encoding="utf-8", timeout=60, env={**os.environ})
    assert proc.returncode == 0, proc.stderr
    got = json.loads(proc.stdout.strip().splitlines()[-1])
    assert got == [expected for _, _, expected in cases], list(zip([m for m, _, _ in cases], got))


def test_the_method_set_names_the_tab_working_commands_and_nothing_else():
    src = _src(GLOW_TS)
    block = src[src.index("export const GLOW_METHODS"): src.index("]);", src.index("export const GLOW_METHODS"))]
    for name in ("METHOD_READ_PAGE", "METHOD_GET_ELEMENTS", "METHOD_SCREENSHOT", "METHOD_ACTIVATE_TAB",
                 "METHOD_CREATE_TAB", "METHOD_NAVIGATE", "METHOD_CLICK", "METHOD_TYPE_TEXT", "METHOD_PRESS_KEY", "METHOD_SCROLL"):
        assert name in block, f"{name} would not glow"
    for name in ("METHOD_STATUS", "METHOD_LIST_TABS", "METHOD_CLOSE_TAB", "METHOD_ACTIVE_TAB"):
        assert name not in block, f"{name} touches no tab and must not glow"


# --------------------------------------------------------------------------- #
# 3. Wiring: results in, turn frames out
# --------------------------------------------------------------------------- #


def test_every_successful_result_reaches_the_glow_and_a_failure_does_not():
    dispatch = _src(DISPATCH_TS)
    assert "onResult?: (method: string, result: Record<string, unknown>) => void;" in dispatch
    body = dispatch[dispatch.index("async dispatch("):]
    ok = body.index("this.onResult?.(method, result)")
    assert ok < body.index("success: true, result"), "the hook runs after the answer is built"
    assert ok > body.index("await handler("), "the hook runs before the handler"
    assert "catch (err) {\n      return fail(id, envelopeFor(err));" in body, "the failure path must not call the hook"
    assert body.index("this.onResult?.(method, result)") < body.index("catch (err)")
    worker = _src(WORKER_TS)
    assert "dispatcher.onResult = (method, result) => {\n  void glow.afterCommand(method, result);\n};" in worker


def test_the_turns_frames_a_lost_socket_and_a_closed_tab_clear_it():
    worker = _src(WORKER_TS)
    assert re.search(r"onPanelEvent: \(event, payload\) => \{[\s\S]{0,300}glow\.notePanelEvent\(event, payload\);", worker), (
        "the sidebar's turn frames never reach the glow"
    )
    assert re.search(r'if \(status\.state !== "connected"\) \{[\s\S]{0,200}void glow\.hideAll\(\);', worker)
    assert re.search(r"chrome\.tabs\.onRemoved\.addListener\(\(tabId\) => \{\s*glow\.forget\(tabId\);", worker)
    src = _src(GLOW_TS)
    note = src[src.index("notePanelEvent("):]
    note = note[: note.index("\n  }\n") + 4]
    assert 'event === "done" || event === "error"' in note and "void this.hideAll();" in note
    assert 'event === "state"' in note and 'payload["running"] === true' in note
    assert "export const GLOW_LINGER_MS = 20_000;" in src
    assert "setTimeout(" in src and "GLOW_LINGER_MS" in src[src.index("armLinger("):]
    # While a sidebar turn runs, the linger never fires.
    assert re.search(r"private armLinger\(\): void \{\s*if \(this\.running\) \{\s*return;", src)


def test_a_paint_that_cannot_happen_is_silent_and_the_hook_cannot_break_a_command():
    src = _src(GLOW_TS)
    show = src[src.index("async show("): src.index("async hideAll(")]
    assert "chrome.scripting.executeScript({ target: { tabId }, func: paintGlow, args: [GLOW_ID] })" in show
    assert "} catch {" in show, "a page closed to add-ons would turn a finished command into an error"
    dispatch = _src(DISPATCH_TS)
    assert re.search(r"try \{\s*this\.onResult\?\.\(method, result\);\s*\} catch \{", dispatch)
