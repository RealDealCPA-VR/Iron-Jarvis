"""The desktop's dialogs speak in sentences a person can act on (v1.450.0).

Wave D, item 39 of docs/PRODUCT-READINESS-RESULTS.md 0h.5 (ease item 68):

* "reinstall the previous version from the Releases page" named a page the
  user had never seen and no file to take from it. Every dialog that sends the
  user to Releases now says WHERE (the address) and WHICH file
  (Iron-Jarvis-Setup-<version>.exe, under which version), and the one where
  the app is still running says it keeps working meanwhile.
* The window-recovered notice printed Chromium's reason code ("The window
  stopped responding (oom) …"); it says "The window ran out of memory and was
  reloaded." for an out-of-memory reload and what is kept after any reload
  (v1.455.0: never "Nothing was lost" — a draft is saved on pagehide, which
  an out-of-memory renderer never runs). The code goes to renderer.log.
* The startup dialog printed the raw gate error ("Details: …"); it says the
  cause in one sentence and offers Open logs, and the raw text is written to
  the desktop log first (pinned in test_desktop_reliability_v1249).
* Spotlight's failed quick task showed "connect ECONNREFUSED 127.0.0.1:8787";
  it says "Iron Jarvis isn't running yet. Open the main window." and logs the
  raw text.

Every behavioural case runs the REAL lifted functions under node.
"""

from __future__ import annotations

import json
from pathlib import Path

from tests.test_desktop_idle_v1333 import _decl, _lift, _run, _src, requires_node


def _lift_fn(name: str) -> str:
    return _lift(f"function {name}(")


# --------------------------------------------------------------------------- #
# Releases: where, and which file
# --------------------------------------------------------------------------- #


def test_every_releases_dialog_says_where_and_which_file():
    src = _src()
    prev = _decl("RELEASES_PREVIOUS_HOW")
    latest = _decl("RELEASES_LATEST_HOW")
    for how in (prev, latest):
        assert "github.com/RealDealCPA-VR/Iron-Jarvis/releases" in how, how
        assert "Iron-Jarvis-Setup-<version>.exe" in how, how
    assert "just below this one" in prev
    assert "newest version" in latest
    # The old, unactionable wordings are gone.
    for old in (
        "reinstall \" +\n        \"the previous version from the Releases page",
        "Reinstall the previous working version from the Releases page",
        "Reinstall the latest version from the Releases page.",
        "you can also install it manually from the",
    ):
        assert old not in src, old
    # Each dialog uses the one sentence.
    classify = _lift_fn("classifyStartupFailure")
    assert "RELEASES_PREVIOUS_HOW" in classify and "DATA_STAYS" in classify
    startup = _lift_fn("handleStartupFailure")
    assert "RELEASES_PREVIOUS_HOW" in startup and "shell.openExternal(RELEASES_PAGE)" in startup
    corrupt = _lift_fn("handleCorruptInstall")
    assert "RELEASES_LATEST_HOW" in corrupt


def test_the_update_that_did_not_start_says_the_app_keeps_working():
    src = _src()
    start = src.index('title: "Iron Jarvis — update did not install",')
    block = src[start : start + 900]
    assert "keeps working on the current version" in block, block
    # Spelled out here (not the constant): the install-abort harnesses lift
    # this function alone.
    assert "github.com/RealDealCPA-VR/Iron-Jarvis/releases" in block
    assert "Iron-Jarvis-Setup-<version>.exe" in block


# --------------------------------------------------------------------------- #
# The window that was reloaded
# --------------------------------------------------------------------------- #


_RECOVERED = """
__LIFTED__
const reasons = JSON.parse(process.argv[2]);
process.stdout.write(JSON.stringify(reasons.map((r) => recoveredWords(r))) + "\\n");
"""


def _recovered(reasons: list[str], tmp_path: Path) -> list[str]:
    script = _RECOVERED.replace("__LIFTED__", _lift_fn("recoveredWords"))
    f = tmp_path / "recovered.js"
    f.write_text(script, encoding="utf-8")
    import subprocess

    out = subprocess.run(
        ["node", str(f), json.dumps(reasons)], capture_output=True, text=True, encoding="utf-8", timeout=60
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


@requires_node
def test_the_recovered_notice_never_names_chromiums_reason(tmp_path):
    reasons = ["oom", "crashed", "abnormal-exit", "launch-failed", "integrity-failure", "unresponsive"]
    words = _recovered(reasons, tmp_path)
    # v1.455.0 (Wave F, item 17, moved on purpose): no reload promises
    # "Nothing was lost". The chat saves a half-typed message on pagehide,
    # which a renderer that ran out of memory never runs, and the desktop
    # cannot know whether a draft was saved — so every line says what is kept.
    assert words[0] == (
        "The window ran out of memory and was reloaded. Your chats and settings are kept; "
        "anything you were typing may need typing again."
    )
    for reason, line in zip(reasons, words):
        assert f"({reason})" not in line and reason not in line.lower().split(), (reason, line)
    for line in words:
        assert "Nothing was lost" not in line, line
        assert "chats and settings are kept" in line, line


def test_the_notice_logs_the_reason_and_shows_the_words():
    fn = _lift_fn("notifyRecovered")
    assert "rendererLog(`[watchdog] reloaded the window (${reason})`);" in fn
    assert "body: recoveredWords(reason)," in fn
    assert "stopped responding (${reason})" not in _src()


# --------------------------------------------------------------------------- #
# Spotlight
# --------------------------------------------------------------------------- #


_SPOT = """
__LIFTED__
const raws = JSON.parse(process.argv[2]);
process.stdout.write(JSON.stringify(raws.map((r) => spotlightErrorWords(r))) + "\\n");
"""


@requires_node
def test_spotlight_says_iron_jarvis_is_not_running_never_econnrefused(tmp_path):
    raws = [
        "connect ECONNREFUSED 127.0.0.1:8787",
        "read ECONNRESET",
        "socket hang up",
        "Iron Jarvis did not answer in time",
        "HTTP 500",
        "could not start the task",
        "",
        "Emergency stop is on: nothing starts on its own.",  # the daemon's own words
    ]
    script = _SPOT.replace("__LIFTED__", _lift_fn("spotlightErrorWords"))
    f = tmp_path / "spot.js"
    f.write_text(script, encoding="utf-8")
    import subprocess

    out = subprocess.run(
        ["node", str(f), json.dumps(raws)], capture_output=True, text=True, encoding="utf-8", timeout=60
    )
    assert out.returncode == 0, out.stderr
    words = json.loads(out.stdout.strip().splitlines()[-1])
    assert words[0] == words[1] == words[2] == "Iron Jarvis isn't running yet. Open the main window."
    assert words[3] == "Iron Jarvis didn't answer in time. Open the main window and try again."
    assert words[4] == words[5] == words[6] == "The task didn't start. Open the main window and try again."
    assert words[7] == raws[7], "a sentence Iron Jarvis wrote for the user is kept"
    for w in words:
        assert "ECONN" not in w and "8787" not in w and "HTTP" not in w, w


def test_spotlight_logs_the_raw_text_and_returns_the_words():
    src = _src()
    start = src.index('ipcMain.handle("spotlight:submit"')
    block = src[start : start + 700]
    assert 'desktopLog("warn", `[spotlight] the quick task did not start: ${raw}`);' in block
    assert "error: spotlightErrorWords(raw)" in block
    assert "error: (err && err.message) || String(err)" not in block
    html = (Path(__file__).resolve().parents[1] / "desktop" / "spotlight.html").read_text(encoding="utf-8")
    assert "String(err && err.message || err)" not in html
