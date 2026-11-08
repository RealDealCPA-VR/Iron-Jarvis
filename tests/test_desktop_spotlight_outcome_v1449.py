"""The Spotlight quick task's final toast says the STATUS TABLE's word (v1.449.0).

The readiness audit (docs/PRODUCT-READINESS-RESULTS.md 0h.5 item 14): the
desktop's quick-task watcher read only ``status`` off the slim session row,
so a completed run whose ledger verdict was ``needs_you`` or
``completed_with_failures`` was announced "Task done", and a stopped task
"Task cancelled" — words no other surface uses (dashboard/lib/statusWords.ts
says Done / Needs you / Part failed / Failed / Stopped everywhere else).

Now ``spotlightOutcome(sess, elapsed, task)`` reads the ROW: a completed row's
``outcome`` leads, and ``SPOTLIGHT_OUTCOME_WORDS`` in desktop/main.js is held
EQUAL to the TS table here (the TOOL_WORDS pattern of
tests/test_desktop_words_v13580.py — main.js cannot import the dashboard's
TypeScript, so the few words are repeated and pinned). A row that carries a
folder names it on the toast and offers "Open folder" (an Electron
notification action — drawn on macOS only; Windows' click still opens the
session page, which has Open folder); a network spelling is never offered.

Every behavioural case runs the REAL lifted functions under node.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

from tests.test_desktop_idle_v1333 import _decl, _lift, _src, requires_node

ROOT = Path(__file__).resolve().parents[1]
STATUS_WORDS_TS = ROOT / "dashboard" / "lib" / "statusWords.ts"


def _ts_status_words() -> dict[str, str]:
    """statusWords.ts STATUS + OUTCOME rows, resolved through its constants."""
    src = STATUS_WORDS_TS.read_text(encoding="utf-8").replace("\r\n", "\n")
    consts = dict(re.findall(r'^const ([A-Z_]+) = w\("([^"]+)",', src, re.M))
    out: dict[str, str] = {}
    for table in ("STATUS", "OUTCOME"):
        block = src[src.index(f"const {table}: Readonly<Record<string, StatusWords>> = {{") :]
        block = block[: block.index("\n};")]
        for key, ref in re.findall(r"^\s+([a-z_]+):\s*([A-Z_]+),", block, re.M):
            out[key] = consts[ref]
        for key, word in re.findall(r'^\s+([a-z_]+):\s*w\("([^"]+)",', block, re.M):
            out[key] = word
    return out


def _desktop_words() -> dict[str, str]:
    decl = _decl("SPOTLIGHT_OUTCOME_WORDS")
    return dict(re.findall(r'([a-z_]+):\s*"([^"]+)"', decl))


def test_the_toast_words_are_the_status_tables_words():
    table = _ts_status_words()
    desk = _desktop_words()
    assert set(desk) == {"completed", "failed", "cancelled", "needs_you", "completed_with_failures"}
    for key, word in desk.items():
        assert table.get(key) == word, f"main.js says {word!r} for {key}, statusWords.ts says {table.get(key)!r}"


def test_the_old_words_are_gone_from_the_desktop():
    src = _src()
    for old in ("Task done", "Task cancelled", "Task failed"):
        assert old not in src, f"main.js still says {old!r}"


_HARNESS = """
const notes = [];
const opened = [];
let cleared = 0;
let tick = null;
function setInterval(fn, ms) { tick = fn; return 1; }
function clearInterval() { cleared += 1; tick = null; }
class Notification {
  constructor(o) { this.o = o; this.handlers = {}; }
  on(name, fn) { this.handlers[name] = fn; }
  show() { notes.push({ title: this.o.title, body: this.o.body, actions: this.o.actions || null, hasAction: !!this.handlers.action }); }
  act() { if (this.handlers.action) this.handlers.action(); }
  click() { if (this.handlers.click) this.handlers.click(); }
}
// Readiness 0j.5 item 18 (moved on purpose): the toast's folder offer
// depends on the platform (Electron draws action buttons on macOS only).
Object.defineProperty(process, "platform", { value: process.argv[3] || "darwin" });
let lastNote = null;
const _Notification = Notification;
const path = require("path");
const shell = { openPath: async (p) => { opened.push(p); return ""; } };
const DASHBOARD_URL = "http://127.0.0.1:8788";
let mainWin = null;
function showMainWindow() {}
const ROW = JSON.parse(process.argv[2]);
function daemonRequest(method, p) {
  return Promise.resolve({ session: ROW, transcript: null });
}
__LIFTED__
const flush = () => new Promise((r) => setImmediate(r));
(async () => {
  watchSpotlightTask("s1", "rename the photos");
  tick(); await flush(); await flush();
  console.log(JSON.stringify({ notes, cleared, opened }));
})();
"""


def _lifted() -> str:
    return "\n".join(
        [
            _decl("SPOTLIGHT_POLL_MS"),
            _decl("SPOTLIGHT_WATCH_CAP_MS"),
            _decl("SPOTLIGHT_OUTCOME_WORDS"),
            _lift("function spotlightFolder("),
            _lift("function spotlightOutcome("),
            _lift("function spotlightToastPlan("),
            _lift("function watchSpotlightTask("),
        ]
    )


def _watch(tmp_path: Path, row: dict, platform: str = "darwin") -> dict:
    lifted = _lifted()
    f = tmp_path / "watch.js"
    f.write_text(_HARNESS.replace("__LIFTED__", lifted), encoding="utf-8")
    proc = subprocess.run(
        ["node", str(f), json.dumps(row), platform],
        capture_output=True, text=True, encoding="utf-8", timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


@requires_node
@pytest.mark.parametrize(
    "row, title",
    [
        ({"status": "completed", "outcome": "needs_you", "summary": "Waiting on an answer."}, "Needs you: rename the photos"),
        ({"status": "completed", "outcome": "completed_with_failures", "summary": "One step failed."}, "Part failed: rename the photos"),
        ({"status": "completed", "outcome": None, "summary": "All set."}, "Done: rename the photos"),
        ({"status": "completed", "outcome": "something_new", "summary": ""}, "Done: rename the photos"),
        ({"status": "failed", "outcome": "needs_you", "summary": "The run failed: x."}, "Failed: rename the photos"),
        ({"status": "cancelled", "summary": ""}, "Stopped: rename the photos"),
    ],
)
def test_a_finished_row_is_announced_by_its_verdict(tmp_path, row, title):
    out = _watch(tmp_path, row)
    assert [n["title"] for n in out["notes"]] == [title]
    assert out["cleared"] == 1


@requires_node
def test_a_needs_you_row_is_never_called_done(tmp_path):
    out = _watch(tmp_path, {"status": "completed", "outcome": "needs_you", "summary": ""})
    assert "done" not in out["notes"][0]["title"].lower()


@requires_node
def test_a_row_with_a_folder_names_it_and_offers_open_folder(tmp_path):
    out = _watch(tmp_path, {"status": "completed", "summary": "All set.", "workspace_path": "C:\\\\Users\\\\x\\\\Documents\\\\Iron Jarvis\\\\2026-11-07 photos"})
    note = out["notes"][0]
    assert note["title"] == "Done: rename the photos"
    assert note["body"] == "All set.\nIn 2026-11-07 photos"
    assert note["actions"] == [{"type": "button", "text": "Open folder"}]
    assert note["hasAction"] is True


@requires_node
def test_a_row_without_a_folder_offers_nothing_to_open(tmp_path):
    out = _watch(tmp_path, {"status": "completed", "summary": "All set."})
    note = out["notes"][0]
    assert note["body"] == "All set."
    assert note["actions"] is None
    assert note["hasAction"] is False


@requires_node
@pytest.mark.parametrize("folder", ["\\\\\\\\nas\\\\share\\\\work", "//nas/share/work"])
def test_a_network_folder_is_never_offered(tmp_path, folder):
    out = _watch(tmp_path, {"status": "completed", "summary": "All set.", "workspace_path": folder})
    note = out["notes"][0]
    assert note["actions"] is None
    assert "In " not in note["body"]


@requires_node
def test_the_open_folder_action_opens_the_rows_folder(tmp_path):
    lifted = _lifted()
    harness = _HARNESS.replace(
        "show() { notes.push(",
        "show() { lastNote = this; notes.push(",
    ).replace(
        'console.log(JSON.stringify({ notes, cleared, opened }));',
        'lastNote.act(); await flush(); console.log(JSON.stringify({ notes, cleared, opened }));',
    )
    f = tmp_path / "act.js"
    f.write_text(harness.replace("__LIFTED__", lifted), encoding="utf-8")
    row = {"status": "completed", "summary": "", "workspace_path": "C:\\\\work\\\\photos"}
    proc = subprocess.run(["node", str(f), json.dumps(row)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    assert out["opened"] == ["C:\\\\work\\\\photos"]


# --------------------------------------------------------------------------- #
# Readiness 0j.5 item 18: "Open folder" works on WINDOWS (no toast buttons)
# --------------------------------------------------------------------------- #


def _click(tmp_path: Path, row: dict, platform: str) -> dict:
    harness = _HARNESS.replace(
        "show() { notes.push(",
        "show() { lastNote = this; notes.push(",
    ).replace(
        'console.log(JSON.stringify({ notes, cleared, opened }));',
        'lastNote.click(); await flush(); console.log(JSON.stringify({ notes, cleared, opened }));',
    )
    f = tmp_path / "click.js"
    f.write_text(harness.replace("__LIFTED__", _lifted()), encoding="utf-8")
    proc = subprocess.run(
        ["node", str(f), json.dumps(row), platform],
        capture_output=True, text=True, encoding="utf-8", timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


@requires_node
def test_on_windows_a_click_on_the_toast_opens_the_folder_it_names(tmp_path):
    row = {"status": "completed", "summary": "All set.", "workspace_path": "C:\\\\work\\\\photos"}
    out = _click(tmp_path, row, "win32")
    note = out["notes"][0]
    assert note["actions"] is None, "Windows draws no toast buttons"
    assert note["body"] == "All set.\nIn photos · click to open the folder"
    assert out["opened"] == ["C:\\\\work\\\\photos"]


@requires_node
def test_on_windows_a_toast_with_no_folder_opens_none(tmp_path):
    out = _click(tmp_path, {"status": "completed", "summary": "All set."}, "win32")
    assert out["opened"] == []
    assert out["notes"][0]["body"] == "All set."


@requires_node
def test_on_windows_a_network_folder_is_never_opened(tmp_path):
    out = _click(tmp_path, {"status": "completed", "summary": "x", "workspace_path": "//nas/share/w"}, "win32")
    assert out["opened"] == []


@requires_node
def test_on_macos_a_click_still_opens_the_session_not_the_folder(tmp_path):
    out = _click(tmp_path, {"status": "completed", "summary": "x", "workspace_path": "C:\\\\work\\\\photos"}, "darwin")
    assert out["opened"] == []
