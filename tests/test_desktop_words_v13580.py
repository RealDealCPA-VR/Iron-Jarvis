"""v1.358.0 lane 8E — the desktop shell speaks plainly (G30) and its
closed-window ask toast uses the approval card's own words and names the
run's task (the re-audit's L7 "Approval flow" / K "Why is Jarvis doing it?").

G30 (the re-audit, file:line on v1.357.0): the tray tooltip said "Iron Jarvis —
the daemon keeps stalling", the watchdog toast "The daemon stopped answering
repeatedly…", the crash-loop tooltip "Iron Jarvis — daemon is restarting
repeatedly" and toast "The daemon keeps crashing…", and the startup dialog's
window title "Iron Jarvis — daemon did not start". VOCABULARY.md: the program
the window talks to is **Iron Jarvis**; the window's own server is "the Iron
Jarvis dashboard".

THE TOAST: desktop/main.js kept its own six-entry table, so `edit_file` read
"use edit file" (the card: "change a file") and `mcp__github__create_issue`
read "use mcp  github  create issue"; and it named no task at all ("Jarvis is
waiting for you — save a file — click to open the run"). Now the table is the
card's (dashboard/lib/toolWords.ts), PINNED EQUAL here by reading both files,
and the body says which task is asking.

House idiom: the shipped functions are lifted out of main.js verbatim and run
under node (tests/test_desktop_reliability_v1249.py, _v1229.py), so a revert
turns these red.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from tests.test_desktop_reliability_v1226 import _gate
from tests.test_desktop_reliability_v1249 import _asks, _src, requires_node
from tests.test_desktop_supervisor_v1229 import _EXTRACT, _PERIODIC, _run

ROOT = Path(__file__).resolve().parents[1]
TOOL_WORDS_TS = ROOT / "dashboard" / "lib" / "toolWords.ts"
FIXTURE = ROOT / "dashboard" / "__tests__" / "fixtures" / "desktop-tool-words-v13580.json"

DAEMON = re.compile(r"\bdaemons?\b", re.I)

#: Desktop entries AHEAD of the card: the card's table (lib/toolWords.ts)
#: has no words for the document writers yet, so the card reads "run
#: write_document" (re-audit L7). These two may exist on the desktop only —
#: and the moment the card gains them, they must say the same words.
DESKTOP_AHEAD = {
    "write_document": "the card has no words for it yet (re-audit L7); notes ask the integrator to add it",
    "excel_edit": "the card has no words for it yet (re-audit L7); notes ask the integrator to add it",
}


# --------------------------------------------------------------------------- #
# G30 — tray, toasts and dialogs say "Iron Jarvis", never "daemon"
# --------------------------------------------------------------------------- #

_CRASH = """
__EXTRACT__
const path = require("path");
const notes = [];
let tray = { setToolTip(t) { notes.push("tooltip:" + t); } };
class Notification { constructor(o) { this.o = o; } show() { notes.push("toast:" + this.o.title + "|" + this.o.body); } }
const userDataDir = "C:/Users/x/AppData/Roaming/Iron Jarvis";
eval(decl("_services")); eval(decl("_trayDegraded"));
for (const f of ["markTrayDegraded", "markTrayHealthy", "notifyCrashLoop", "notifyWatchdogExhausted"]) eval(fnSource(f));
const out = {};
for (const label of ["daemon", "dashboard"]) {
  notes.length = 0; _trayDegraded.clear();
  _services[label] = { capped: false };
  notifyCrashLoop(label);
  _services[label] = { capped: true };
  notifyCrashLoop(label, (who) => `${who} crashed 10 times in 15 minutes and is no longer being restarted.`);
  notifyCrashLoop(label, "a plain body still works");
  out[label] = notes.slice();
}
notes.length = 0; _trayDegraded.clear();
notifyWatchdogExhausted();
out.watchdog = notes.slice();
process.stdout.write(JSON.stringify(out) + "\\n");
"""


@requires_node
def test_the_crash_loop_and_the_watchdog_say_iron_jarvis_never_daemon(tmp_path):
    out = _run(_CRASH.replace("__EXTRACT__", _EXTRACT), tmp_path, "crash_words")
    daemon = out["daemon"]
    # The code's label "daemon" is the program the window talks to: Iron Jarvis.
    assert "tooltip:Iron Jarvis is restarting repeatedly (check logs)" in daemon, daemon
    assert "tooltip:Iron Jarvis stopped (crashed too often; Restart from the tray)" in daemon, daemon
    assert any(n.startswith("toast:Iron Jarvis — problem|Iron Jarvis keeps crashing") for n in daemon), daemon
    assert any(
        n.endswith("|Iron Jarvis crashed 10 times in 15 minutes and is no longer being restarted.")
        for n in daemon
    ), "a caller's (who) => body must get the plain subject"
    assert any(n.endswith("|a plain body still works") for n in daemon), "a string body is unchanged"
    # The dashboard child keeps its own name, the startup classifier's words.
    dash = out["dashboard"]
    assert "tooltip:Iron Jarvis — the dashboard is restarting repeatedly (check logs)" in dash, dash
    assert any(n.startswith("toast:Iron Jarvis — problem|The Iron Jarvis dashboard keeps crashing") for n in dash)
    watchdog = out["watchdog"]
    assert "tooltip:Iron Jarvis keeps stalling (Restart from the tray)" in watchdog, watchdog
    assert any("|Iron Jarvis stopped answering repeatedly" in n for n in watchdog), watchdog
    for note in daemon + dash + watchdog:
        assert not DAEMON.search(note), f"the shell said the code's word to a person: {note!r}"


@requires_node
def test_the_day_window_toast_with_the_daemon_label_says_iron_jarvis(tmp_path):
    """The real exit ladder (startService's onGone, lifted verbatim) for a
    child labelled "daemon" dying every six minutes: its third death in a day
    toasts — in the program's name."""
    out = _run(_PERIODIC.replace("__EXTRACT__", _EXTRACT), tmp_path, "periodic_words")
    assert len(out["toasts"]) == 1, out["toasts"]
    assert out["toasts"][0].startswith("Iron Jarvis has crashed 3 times in the last 24 hours"), out["toasts"]
    for line in out["toasts"] + out["tooltips"]:
        assert not DAEMON.search(line), line


@requires_node
def test_the_startup_gate_reasons_shown_under_details_say_iron_jarvis(tmp_path):
    """The gate's reasons reach the startup dialog's "Details:" line. The
    existing pins' words stay ("port in use?", "different version", "not
    ours", "stop it or change its port"); "daemon" does not."""
    early = _gate({"exitCode": 1}, tmp_path)["result"]["message"]
    other = _gate({"healthVersion": "1.200.0"}, tmp_path)["result"]["message"]
    foreign = _gate({"owner": "notoken", "foreignNotified": True}, tmp_path)["result"]["message"]
    assert early.startswith("Iron Jarvis stopped while starting") and "port in use?" in early, early
    assert "different version" in other and "Iron Jarvis did not answer within" in other, other
    assert "not ours" in foreign and "stop it or change its port" in foreign, foreign
    for msg in (early, other, foreign):
        assert not DAEMON.search(msg), msg


def test_the_startup_failure_dialog_title_is_the_programs_name():
    src = _src()
    assert '"Iron Jarvis did not start",' in src
    assert "daemon did not start" not in src
    # Anti-vacuity: the dashboard's own failure keeps its own title.
    assert '"Iron Jarvis — dashboard did not start",' in src


# --------------------------------------------------------------------------- #
# The toast: ONE table with the approval card, and the task it asks for
# --------------------------------------------------------------------------- #


def _ts_words() -> dict[str, str]:
    """dashboard/lib/toolWords.ts WORDS, read from the file (never copied)."""
    src = TOOL_WORDS_TS.read_text(encoding="utf-8").replace("\r\n", "\n")
    block = src[src.index("const WORDS: Record<string, string> = {") :]
    block = block[: block.index("\n};")]
    return dict(re.findall(r'^\s+([a-z_]+):\s*"([^"]+)",', block, re.M))


def _ts_browser_tools() -> set[str]:
    src = TOOL_WORDS_TS.read_text(encoding="utf-8").replace("\r\n", "\n")
    block = src[src.index("export const BROWSER_TOOLS") :]
    block = block[: block.index("]);")]
    return set(re.findall(r'"(browser_[a-z_]+)"', block))


def _desktop_words() -> dict[str, str]:
    src = _src()
    block = src[src.index("const TOOL_WORDS = {") :]
    block = block[: block.index("\n};")]
    return dict(re.findall(r'^\s+([a-z_]+):\s*"([^"]+)",', block, re.M))


def _desktop_browser_tools() -> set[str]:
    src = _src()
    block = src[src.index("const ASK_BROWSER_TOOLS = new Set([") :]
    block = block[: block.index("]);")]
    return set(re.findall(r'"(browser_[a-z_]+)"', block))


def test_the_toast_table_is_the_approval_cards_table():
    card = _ts_words()
    desk = _desktop_words()
    assert len(card) >= 25, f"read too little of lib/toolWords.ts: {sorted(card)}"
    missing = {k: v for k, v in card.items() if k not in desk}
    assert not missing, f"the toast cannot say what the card says for: {missing}"
    differ = {k: (desk[k], card[k]) for k in card if k in desk and desk[k] != card[k]}
    assert not differ, f"the toast and the card describe one ask two ways: {differ}"
    extra = set(desk) - set(card)
    assert extra <= set(DESKTOP_AHEAD), f"desktop-only words with no reason: {sorted(extra - set(DESKTOP_AHEAD))}"
    assert _desktop_browser_tools() == _ts_browser_tools() and len(_ts_browser_tools()) == 14


def test_the_cross_check_fixture_covers_every_kind_of_tool():
    rows = json.loads(FIXTURE.read_text(encoding="utf-8"))["rows"]
    kinds = {r["kind"] for r in rows}
    assert {"table", "browser", "app", "unknown"} <= kinds, kinds
    # every card phrase is in the fixture, so the vitest half checks them all
    # ("ahead" rows count: once the card gains those words they must agree,
    # which both halves already check)
    table = {r["tool"] for r in rows if r["kind"] in ("table", "ahead")}
    assert set(_ts_words()) <= table, sorted(set(_ts_words()) - table)


@requires_node
def test_the_toast_speaks_the_fixtures_words_for_every_tool(tmp_path):
    """The same table of ids the vitest half feeds to lib/toolWords
    (dashboard/__tests__/desktop-tool-words-v13580.test.ts): a tool the card
    describes reads the card's words; one it does not falls back to its id."""
    rows = json.loads(FIXTURE.read_text(encoding="utf-8"))["rows"]
    approvals = [{"id": f"a{i}", "tool": r["tool"], "count": 1} for i, r in enumerate(rows)]
    out = _asks({"steps": [{"approvals": approvals, "now": 0}]}, tmp_path)["out"]
    bodies = [n["body"] for n in out[0]["notify"]]
    assert len(bodies) == len(rows)
    for row, body in zip(rows, bodies):
        words = row["card"] if row["card"] is not None else row["desktop"]
        assert body == f"{words} — click to open the run.", (row, body)


@requires_node
def test_edit_file_and_an_app_tool_read_like_the_card_now(tmp_path):
    out = _asks(
        {
            "steps": [
                {
                    "approvals": [
                        {"id": "e", "tool": "edit_file", "count": 1},
                        {"id": "g", "tool": "mcp__github__create_issue", "count": 1},
                        {"id": "b", "tool": "browser_click", "count": 2},
                        {"id": "s", "tool": "edit_file", "count": 3},
                    ],
                    "now": 0,
                }
            ]
        },
        tmp_path,
    )["out"]
    bodies = [n["body"] for n in out[0]["notify"]]
    assert bodies[0] == "change a file — click to open the run.", bodies
    assert bodies[1] == "use github (create issue) — click to open the run.", bodies
    assert bodies[2] == "act in a browser tab (2 times) — click to open the run.", bodies
    assert bodies[3] == "change 3 files — click to open the run.", bodies


@requires_node
def test_the_toast_names_the_task_that_is_asking(tmp_path):
    """The listing's `task` (the daemon's first line, already masked) is the
    bell's "for: …" line; the toast says it too — first notice AND reminder.
    A chat ask has no task and keeps its own words; a control character or a
    long task never breaks the one-line toast."""
    long_task = "Draft the quarterly letter to the bank about the line of credit " * 3
    asks = [
        {"id": "a", "tool": "write_file", "count": 1, "task": "Draft the Q3 letter\nto the bank", "session_id": "s1"},
        {"id": "b", "tool": "shell", "count": 1, "task": long_task},
        {"id": "c", "tool": "write_file", "count": 1, "task": "   "},
        {"id": "chat:t1", "kind": "chat", "thread_id": "t1", "tool": "write_file", "task": "ignored"},
    ]
    out = _asks(
        {"steps": [{"approvals": asks, "now": 0}, {"approvals": asks, "now": 60 * 60 * 1000 + 5}]},
        tmp_path,
    )["out"]
    first = [n["body"] for n in out[0]["notify"]]
    assert first[0] == "save a file for “Draft the Q3 letter to the bank” — click to open the run.", first
    assert first[1].startswith("run a command for “Draft the quarterly letter") and "…” — click" in first[1], first
    quoted = first[1].split("“", 1)[1].split("”", 1)[0]
    assert len(quoted) <= 80, quoted
    assert first[2] == "save a file — click to open the run.", "a blank task is no task"
    assert first[3] == "save a file — click to open the chat.", "a chat ask is unchanged"
    reminder = [n["body"] for n in out[1]["notify"]]
    assert reminder[0] == (
        "save a file for “Draft the Q3 letter to the bank” has been waiting 1 hour — click to open the run."
    ), reminder
    assert out[0]["notify"][0]["sessionId"] == "s1", "the click still opens the run"
