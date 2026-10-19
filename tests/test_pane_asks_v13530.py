"""v1.353.0 lane 3D (G52) — a Build pane that needs you reaches you when the
window is closed to the tray.

A coding CLI in a Build pane that stops on a question ("Allow this edit?")
wore "needs you" ON BUILD and nowhere else: closing the window to the tray
destroys the renderer (and its bell), so a pane could wait all afternoon. The
desktop's ask watcher (``installAskWatcher``) now reads the same tiny
``GET /terminals/activity`` the Build page and the bell poll and raises ONE
toast per pane that turns ``blocked`` while no window is open — "Claude Code
in Build is waiting for you" — whose click opens Build on that pane.

These run the REAL ``planPaneNotifications`` lifted out of main.js under node
(the v1249 harness shape), and pin that the three places that name a CLI —
the daemon's Launch catalog (``terminals/ai_clis.AI_CLIS``), the bell's
``dashboard/lib/paneAsks.ts`` and main.js — say the same name.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from iron_jarvis.terminals.ai_clis import AI_CLIS

_ROOT = Path(__file__).resolve().parents[1]
_MAIN = _ROOT / "desktop" / "main.js"
_PANE_ASKS = _ROOT / "dashboard" / "lib" / "paneAsks.ts"

requires_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")


def _read(path: Path) -> str:
    # CRLF on the CI runner, LF here: normalise at the READER.
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def _lift(start_marker: str, end_after: str) -> str:
    """main.js from ``start_marker`` to the column-0 ``}`` closing the
    function that begins at ``end_after``. Lifted, never copied."""
    src = _read(_MAIN)
    start = src.index(start_marker)
    tail = src.index(end_after, start)
    end = src.index("\n}\n", tail) + 3
    return src[start:end]


def _names_in(src: str, decl: str) -> dict[str, str]:
    """The ``{id: "Label"}`` pairs of one object literal declared as ``decl``."""
    start = src.index(decl)
    body = src[src.index("{", start) + 1 : src.index("\n};", start)]
    pairs = re.findall(r'^\s*"?([\w-]+)"?:\s*"([^"]+)",?\s*$', body, re.M)
    assert pairs, f"no pairs read from {decl}"
    return dict(pairs)


def _run(script: str, tmp_path: Path, *argv: str) -> dict:
    f = tmp_path / "panes.js"
    f.write_text(script, encoding="utf-8")
    proc = subprocess.run(
        ["node", str(f), *argv],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        env={**os.environ},
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


_HARNESS = """
__LIFTED__
const s = JSON.parse(process.argv[2]);
let seen = {};
const out = [];
for (const step of s.steps) {
  const plan = planPaneNotifications(seen, step.panes, !!step.windowOpen);
  seen = plan.seen;
  out.push({ notify: plan.notify, seen: Object.keys(plan.seen).sort() });
}
process.stdout.write(JSON.stringify({ out }) + "\\n");
"""


def _plan(steps: list[dict], tmp_path: Path) -> list[dict]:
    script = _HARNESS.replace(
        "__LIFTED__", _lift("const PANE_CLI_NAMES", "function planPaneNotifications")
    )
    return _run(script, tmp_path, json.dumps({"steps": steps}))["out"]


def _pane(pid: str = "t1", state: str = "blocked", **over) -> dict:
    row = {"id": pid, "name": None, "agent_cli": "claude", "state": state, "alive": True}
    row.update(over)
    return row


# ==========================================================================
# One name per CLI, everywhere
# ==========================================================================


def test_the_bell_and_the_desktop_name_each_cli_as_the_launch_catalog_does():
    catalog = {c["id"]: c["label"] for c in AI_CLIS}
    assert _names_in(_read(_PANE_ASKS), "export const PANE_CLI_NAMES") == catalog
    assert _names_in(_read(_MAIN), "const PANE_CLI_NAMES") == catalog


# ==========================================================================
# The plan: once per blocked episode, only with no window
# ==========================================================================


@requires_node
def test_a_pane_that_turns_blocked_with_no_window_is_announced_once(tmp_path):
    out = _plan(
        [
            {"panes": [_pane("t1", "working")], "windowOpen": False},
            {"panes": [_pane("t1")], "windowOpen": False},
            {"panes": [_pane("t1")], "windowOpen": False},
        ],
        tmp_path,
    )
    assert out[0]["notify"] == [], "a working pane is waiting on nobody"
    assert out[1]["notify"] == [
        {
            "id": "pane:t1",
            "paneId": "t1",
            "title": "Claude Code in Build is waiting for you",
            "body": "Click to open the terminal.",
        }
    ]
    assert out[2]["notify"] == [], "the same wait was announced twice"
    assert out[2]["seen"] == ["t1"]


@requires_node
def test_while_a_window_is_open_the_bell_owns_it_and_closing_does_not_reannounce(tmp_path):
    out = _plan(
        [
            {"panes": [_pane("t1")], "windowOpen": True},
            {"panes": [_pane("t1")], "windowOpen": False},
        ],
        tmp_path,
    )
    assert out[0]["notify"] == [], "a toast fired over the bell the user can see"
    assert out[0]["seen"] == ["t1"], "the wait was not recorded while the window was open"
    assert out[1]["notify"] == [], "closing the window re-announced a wait the bell already showed"


@requires_node
def test_a_pane_that_leaves_blocked_and_comes_back_is_a_new_episode(tmp_path):
    out = _plan(
        [
            {"panes": [_pane("t1")], "windowOpen": False},
            {"panes": [_pane("t1", "working")], "windowOpen": False},
            {"panes": [_pane("t1")], "windowOpen": False},
        ],
        tmp_path,
    )
    assert len(out[0]["notify"]) == 1
    assert out[1]["notify"] == [] and out[1]["seen"] == []
    assert len(out[2]["notify"]) == 1, "the second question on the same pane was never announced"


@requires_node
def test_words_name_the_cli_and_the_pane_and_never_read_the_screen(tmp_path):
    out = _plan(
        [
            {
                "panes": [
                    _pane("t1", agent_cli="codex", name="tests", state_line="password: hunter2"),
                    _pane("t2", agent_cli="my-cli"),
                    _pane("t3", agent_cli=None),
                    _pane("t4", alive=False),
                    _pane("", agent_cli="claude"),
                    {"state": "blocked"},
                ],
                "windowOpen": False,
            }
        ],
        tmp_path,
    )
    notes = out[0]["notify"]
    assert [n["paneId"] for n in notes] == ["t1", "t2", "t3"], "a dead or nameless pane was announced"
    assert notes[0]["title"] == "Codex in Build is waiting for you"
    assert notes[0]["body"] == "tests: click to open the terminal."
    assert notes[1]["title"] == "my-cli in Build is waiting for you"
    assert notes[2]["title"] == "A Build terminal is waiting for you"
    assert not any("hunter2" in json.dumps(n) for n in notes), "screen text reached a Windows toast"


@requires_node
def test_a_junk_listing_announces_nothing_and_never_throws(tmp_path):
    out = _plan(
        [{"panes": None, "windowOpen": False}, {"panes": "x", "windowOpen": False}],
        tmp_path,
    )
    assert out == [{"notify": [], "seen": []}, {"notify": [], "seen": []}]


# ==========================================================================
# Shipping the mechanism is not shipping the feature (v1.218.0)
# ==========================================================================


def test_the_watcher_reads_the_pane_listing_and_the_toast_opens_that_pane():
    src = _read(_MAIN)
    assert "installAskWatcher();" in src
    fn = _lift("function installAskWatcher", "function installAskWatcher")
    assert '"/terminals/activity"' in fn, "the watcher never reads the panes"
    assert "planPaneNotifications(paneBlockedSeen, panes, windowOpen)" in fn
    assert "paneBlockedSeen = plan.seen" in fn, "the plan's memory is dropped every tick"
    assert "showAskNotification(item)" in fn
    # The ask half is untouched (v1249 pins it too).
    assert '"/chat/approvals/pending"' in fn
    click = _lift("function showAskNotification", "function showAskNotification")
    assert "item.paneId" in click
    assert "/terminals?focus=${encodeURIComponent(item.paneId)}" in click
    assert "showMainWindow();" in click
