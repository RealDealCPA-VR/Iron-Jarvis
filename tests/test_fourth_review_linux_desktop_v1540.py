"""Fourth review, group 31 (v1.540.0): the Linux app names the right file, and
its "start at login" entry is written the way the desktop-entry rules read it.

* LX4: the autostart entry names the program path in double quotes (the
  Desktop Entry spec's quoting, for spaces and marks such as ``& ; ( ) #``). A
  path the real Linux readers cannot start (a quote, an apostrophe, ``$``, a
  backtick, a backslash, ``%``, a line break or another control character:
  GLib 2.88 and systemd 259, read on a Linux runner; see ``desktopEntryExecArg``)
  is refused in words, no entry is written, and an entry already there is
  removed at the next start (the switch never reads On while nothing starts).
* LX5: the four Linux recovery sentences ("repair", "go back a version", and
  the update that did not start) name the ``.deb`` or the AppImage, whichever
  this copy is, and send the user to the releases list (the newest release
  gets its Linux files about 40 minutes after the Windows one). Windows keeps
  its exact words.
* C-LX-2: the desktop tells the Updates card how THIS Linux copy updates
  (``install``: appimage, deb or folder). Windows sends no such field.

Each node harness below runs the real lifted functions, and the platform is
set by the scenario, so both the Linux and the Windows path run on any machine.
The real-parser half of LX4 (desktop-file-validate, GLib, systemd's autostart
generator) runs on a Linux runner from ``scripts/linux_autostart_samples.mjs``.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import pytest

from tests.test_update_channel_v1260 import _lift, _src, requires_node

_ROOT = Path(__file__).resolve().parents[1]

# --------------------------------------------------------------------------- #
# A reader of the entry, written from the spec (not from main.js)
# --------------------------------------------------------------------------- #

_STRING_ESCAPES = {"s": " ", "n": "\n", "t": "\t", "r": "\r", "\\": "\\"}


def _string_unescape(value: str) -> str:
    """The spec's string escape (``\\s \\n \\t \\r \\\\``); any other is invalid."""
    out, i = [], 0
    while i < len(value):
        c = value[i]
        if c == "\\":
            assert i + 1 < len(value), f"a lone backslash ends the value: {value!r}"
            nxt = value[i + 1]
            assert nxt in _STRING_ESCAPES, f"invalid string escape \\{nxt} in {value!r}"
            out.append(_STRING_ESCAPES[nxt])
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _field_codes(value: str) -> str:
    """``%%`` is a literal ``%``; any other ``%x`` is a field code a launcher expands."""
    out, i = [], 0
    while i < len(value):
        if value[i] == "%":
            assert value[i + 1 : i + 2] == "%", f"a field code %{value[i + 1:i + 2]} in {value!r}"
            out.append("%")
            i += 2
            continue
        out.append(value[i])
        i += 1
    return "".join(out)


def _exec_args(value: str) -> list[str]:
    """The Exec quoting rule: inside double quotes ``" ` $ \\`` must be escaped
    with a backslash; nothing else may follow a backslash."""
    args, i = [], 0
    while i < len(value):
        if value[i] == " ":
            i += 1
            continue
        if value[i] == '"':
            i += 1
            cur = []
            while True:
                assert i < len(value), f"unclosed quote in {value!r}"
                c = value[i]
                if c == "\\":
                    nxt = value[i + 1]
                    assert nxt in '"`$\\', f"invalid quoted escape \\{nxt} in {value!r}"
                    cur.append(nxt)
                    i += 2
                    continue
                if c == '"':
                    i += 1
                    break
                assert c not in "`$", f"an unescaped {c} inside quotes in {value!r}"
                cur.append(c)
                i += 1
            args.append("".join(cur))
            continue
        j = value.find(" ", i)
        j = len(value) if j < 0 else j
        word = value[i:j]
        assert not re.search(r'[\s"\'\\><~|&;$*?#()`]', word), f"a reserved mark outside quotes: {word!r}"
        args.append(word)
        i = j
    return args


def _read_exec(entry_text: str) -> list[str]:
    lines = [ln for ln in entry_text.split("\n") if ln.startswith("Exec=")]
    assert len(lines) == 1, entry_text
    return _exec_args(_field_codes(_string_unescape(lines[0][len("Exec="):])))


# The odd paths the shell writes: spaces, shell marks, letters outside ASCII.
ODD = {
    "spaces": "/home/pat/My Apps/Iron Jarvis.AppImage",
    "marks": "/home/pat/a&b;c(d)[e]{f}!g=h#i~j*k?l<m>n|o+p,q@r/Iron-Jarvis.AppImage",
    "non_ascii": "/home/pat/Ünï/Iron-Jarvis.AppImage",
}
# The paths it refuses: no Linux sign-in starts them (desktopEntryExecArg).
REFUSED = {
    "quotes": '/home/pat/My "Apps"/Iron-Jarvis.AppImage',
    "apostrophe": "/home/pat/it's/Iron-Jarvis.AppImage",
    "dollar": "/home/pat/$HOME/Iron-Jarvis.AppImage",
    "backtick": "/home/pat/`x`/Iron-Jarvis.AppImage",
    "backslash": "/home/pat/back\\slash/Iron-Jarvis.AppImage",
    "percent": "/home/pat/100%f/Iron-Jarvis.AppImage",
    "line_break": "/home/pat/two\nlines/Iron-Jarvis.AppImage",
    "tab": "/home/pat/tab\there/x.AppImage",
    "bell": "/home/pat/bell\x07/x.AppImage",
    "delete": "/home/pat/del\x7f/x.AppImage",
}


# --------------------------------------------------------------------------- #
# LX4: the entry, through setStartAtLogin / refreshLinuxAutostart
# --------------------------------------------------------------------------- #

_LOGIN = r"""
const fs = require("fs"), path = require("path");
const scenario = JSON.parse(process.argv[2]);
Object.defineProperty(process, "platform", { value: scenario.platform });
const IS_PACKAGED = true;
const START_WITH_WINDOWS_KEY = "start_with_windows";
const logs = [], dialogs = [], noted = [];
const app = { getLoginItemSettings: () => ({ openAtLogin: false }), setLoginItemSettings: () => {} };
const dialog = { showMessageBox: (o) => { dialogs.push(o); return Promise.resolve({ response: 0 }); } };
function refreshMenus() {}
function desktopLog(level, ...a) { logs.push(level + " " + a.join(" ")); }
function writeDesktopSetting() {}
function noteDesktopSwitchByUser(key, value) { noted.push([key, value]); }
__LOGIN__
const file = linuxAutostartFile();
const text = () => { try { return fs.readFileSync(file, "utf8"); } catch { return null; } };
const out = { steps: [] };
for (const step of scenario.steps) {
  if (step.target !== undefined) process.env.APPIMAGE = step.target;
  if (step.write !== undefined) { fs.mkdirSync(path.dirname(file), { recursive: true }); fs.writeFileSync(file, step.write); }
  if (step.do === "on") setStartAtLogin(true);
  if (step.do === "menu-on") setStartAtLoginFromMenu(true);
  if (step.do === "refresh") refreshLinuxAutostart();
  out.steps.push({ on: getStartAtLogin(), text: text() });
}
out.logs = logs;
out.dialogs = dialogs.map((d) => ({ message: d.message, detail: d.detail }));
out.noted = noted;
process.stdout.write(JSON.stringify(out) + "\n");
"""


def _login(tmp_path: Path, monkeypatch, steps: list[dict], target: str) -> dict:
    tmp_path.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("APPIMAGE", target)
    script = tmp_path / "login.js"
    script.write_text(
        _LOGIN.replace("__LOGIN__", _lift("// --- Start at login", "function setStartAtLoginFromMenu")),
        encoding="utf-8",
    )
    proc = subprocess.run(
        ["node", str(script), json.dumps({"platform": "linux", "steps": steps})],
        capture_output=True, text=True, encoding="utf-8", timeout=60, env={**os.environ},
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


@requires_node
@pytest.mark.parametrize("name", sorted(ODD))
def test_each_odd_path_is_written_so_the_entry_rules_read_back_the_same_path_v1540(tmp_path, monkeypatch, name):
    odd = ODD[name]
    out = _login(tmp_path, monkeypatch, [{"do": "on"}], odd)
    step = out["steps"][0]
    assert step["on"] is True, out
    assert _read_exec(step["text"]) == [odd, "--hidden"], step["text"]


@requires_node
def test_the_path_is_written_in_double_quotes_and_nothing_else_v1540(tmp_path, monkeypatch):
    def exec_line(target: str) -> str:
        text = _login(tmp_path, monkeypatch, [{"do": "on"}], target)["steps"][0]["text"]
        return [ln for ln in text.split("\n") if ln.startswith("Exec=")][0]

    assert exec_line("/a/My (x) & y #1") == 'Exec="/a/My (x) & y #1" --hidden'
    assert exec_line("/a/Ünï") == 'Exec="/a/Ünï" --hidden'
    # An ordinary path is byte for byte what it was.
    assert exec_line("/home/pat/Apps/Iron-Jarvis-x86_64.AppImage") == (
        'Exec="/home/pat/Apps/Iron-Jarvis-x86_64.AppImage" --hidden'
    )


@requires_node
@pytest.mark.parametrize("name", sorted(REFUSED))
def test_a_path_no_linux_sign_in_can_start_is_refused_and_never_reads_on_v1540(tmp_path, monkeypatch, name):
    out = _login(tmp_path, monkeypatch, [{"do": "on"}], REFUSED[name])
    assert out["steps"][0] == {"on": False, "text": None}, out
    assert any("cannot start at login from where it is now" in line for line in out["logs"]), out["logs"]


@requires_node
def test_a_start_removes_an_entry_it_cannot_write_safely_v1540(tmp_path, monkeypatch):
    """An entry written by an older version is ON; the file has since been
    moved to a path with a line break. Keeping the old entry would read On and
    start nothing (or the wrong file): it is removed, and the switch reads Off."""
    old = '[Desktop Entry]\nType=Application\nName=Iron Jarvis\nExec="/home/pat/Apps/x.AppImage" --hidden\n'
    out = _login(tmp_path, monkeypatch, [
        {"write": old, "do": "none"},
        {"target": "/home/pat/new\nplace/x.AppImage", "do": "refresh"},
    ], "/home/pat/Apps/x.AppImage")
    before, after = out["steps"]
    assert before["on"] is True
    assert after == {"on": False, "text": None}, out
    assert any("cannot start at login from where it is now" in line for line in out["logs"]), out["logs"]


@requires_node
def test_the_menu_says_why_it_stayed_off_and_records_off_v1540(tmp_path, monkeypatch):
    out = _login(tmp_path, monkeypatch, [{"do": "menu-on"}], "/home/pat/two\nlines/x.AppImage")
    assert out["steps"][0] == {"on": False, "text": None}
    assert out["noted"] == [["start_with_windows", False]], "the settings would read On while nothing starts"
    assert out["dialogs"] == [{
        "message": "Iron Jarvis cannot start at login from where it is now.",
        "detail": "The path to the app has a character Linux cannot start it from at sign-in: a quote, "
                  "an apostrophe, a backslash, $, `, %, a line break or another invisible character. "
                  "Move the app to a folder with an ordinary name, then turn this on again.",
    }], out["dialogs"]
    # An ordinary path from the menu: no dialog, recorded On.
    ok = _login(tmp_path / "ok", monkeypatch, [{"do": "menu-on"}], "/home/pat/Apps/x.AppImage")
    assert ok["steps"][0]["on"] is True and ok["dialogs"] == [] and ok["noted"] == [["start_with_windows", True]]


# --------------------------------------------------------------------------- #
# LX4: the sample entries for the real parsers on a Linux runner
# --------------------------------------------------------------------------- #


@requires_node
def test_the_samples_script_writes_an_entry_per_odd_path_and_a_missing_path_control_v1540(tmp_path):
    out = tmp_path / "samples"
    proc = subprocess.run(
        ["node", str(_ROOT / "scripts" / "linux_autostart_samples.mjs"), "--out", str(out), "--no-files"],
        capture_output=True, text=True, encoding="utf-8", timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    cases = json.loads((out / "cases.json").read_text(encoding="utf-8"))["cases"]
    by = {c["id"]: c for c in cases}
    for label in ("spaces", "marks", "non_ascii", "everything"):
        case = by[label]
        assert case["expect"] == "starts", case
        entry = (out / case["entry"]).read_text(encoding="utf-8")
        assert _read_exec(entry) == [case["target"], "--hidden"], (label, entry)
        assert case["entry"].replace("\\", "/").endswith("/autostart/iron-jarvis.desktop")
    for label in ("quotes", "apostrophe", "dollar", "backtick", "backslash", "percent", "line_break"):
        assert by[label]["expect"] == "refused" and by[label]["entry"] is None, by[label]
    assert by["missing"]["expect"] == "no-unit" and not by["missing"].get("recorder")
    assert (out / "glib_launch.py").is_file()


# --------------------------------------------------------------------------- #
# LX5: the recovery sentences name this copy's file
# --------------------------------------------------------------------------- #


def _decl(name: str) -> str:
    m = re.search(rf"^(?:const|let) {name} = [^\n]*;", _src(), re.M)
    assert m, f"main.js has no top-level decl {name}"
    return m.group(0)


def _fn(name: str, required: bool = True) -> str:
    src = _src()
    m = re.search(rf"^function {name}\(", src, re.M)
    if not m:
        assert not required, f"main.js has no function {name}"
        return ""
    return src[m.start() : src.index("\n}\n", m.start()) + 3]


_DIALOGS = r"""
const fs = require("fs"), path = require("path");
const scenario = JSON.parse(process.argv[2]);
const UPDATE_PLATFORM = scenario.platform;
if (scenario.appimage) process.env.APPIMAGE = scenario.appimage; else delete process.env.APPIMAGE;
process.resourcesPath = scenario.resources;
const DAEMON_PORT = 8787, STARTUP_TIMEOUT_MS = 90000;
const shown = [], opened = [];
let isQuitting = false, updateInstallInFlight = true, shuttingDown = false, pendingUpdateInfo = { version: "9.9.9" };
let tray = null, _autoUpdater = null;
const dialog = { showMessageBoxSync: (o) => { shown.push(o.detail); return 0; } };
const shell = { openExternal: (u) => opened.push(u) };
const app = { quit() {}, relaunch() {}, exit() {} };
function findCachedInstaller() { return null; }
function desktopLog() {} function shutdown() {} function sweepOrphanDaemons() {} function spawn() {}
function readLogTail() { return ""; } function openLogsFolder() {}
function clearUpdatePending() {} function refreshTrayMenu() {} function _emitUpdateState() {}
function friendlyUpdateError(m) { return m; }
__DECLS__
__FNS__
const out = {};
handleCorruptInstall({ missing: ["a.js"], mismatched: [], checked: 10 });
out.corrupt = { detail: shown.pop(), opened: opened.splice(0) };
handleStartupFailure("t", "m", { attempts: 2, version: "9.9.9" }, { label: "daemon", gateError: "x" });
out.startup = { detail: shown.pop(), opened: opened.splice(0) };
out.upgrade = classifyStartupFailure({ logTail: "sqlite3.OperationalError: no such column: x" }).detail;
abortUpdateInstall(new Error("boom"));
out.abort = shown.pop();
process.stdout.write(JSON.stringify(out) + "\n");
"""


def _dialogs(tmp_path: Path, platform: str, kind: str) -> dict:
    resources = tmp_path / f"resources-{platform}-{kind}"
    resources.mkdir(parents=True, exist_ok=True)
    if kind == "deb":
        (resources / "package-type").write_text("deb", encoding="utf-8")
    decls = "\n".join(_decl(n) for n in (
        "RELEASES_PAGE", "RELEASES_PREVIOUS_HOW", "RELEASES_LATEST_HOW", "DATA_STAYS", "STARTUP_LOG_LINE",
    ))
    fns = "\n".join(
        [_fn("releasesHow", required=False), _fn("linuxInstallKind", required=False)]
        + [_fn(n) for n in ("handleCorruptInstall", "classifyStartupFailure", "handleStartupFailure",
                            "abortUpdateInstall")]
    )
    script = tmp_path / f"dialogs-{platform}-{kind}.js"
    script.write_text(_DIALOGS.replace("__DECLS__", decls).replace("__FNS__", fns), encoding="utf-8")
    scenario = {
        "platform": platform,
        "appimage": "/home/pat/Apps/Iron-Jarvis-x86_64.AppImage" if kind == "appimage" else "",
        "resources": str(resources),
    }
    proc = subprocess.run(
        ["node", str(script), json.dumps(scenario)],
        capture_output=True, text=True, encoding="utf-8", timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


_RELEASES = "https://github.com/RealDealCPA-VR/Iron-Jarvis/releases"

LINUX_LATEST = {
    "deb": "Open the Releases page (github.com/RealDealCPA-VR/Iron-Jarvis/releases), download the file named "
           "Iron-Jarvis-amd64.deb under the newest version that has it, then open it to install.",
    "appimage": "Open the Releases page (github.com/RealDealCPA-VR/Iron-Jarvis/releases), download the file named "
                "Iron-Jarvis-x86_64.AppImage under the newest version that has it, then mark it as a program "
                "and run it.",
}
LINUX_PREVIOUS = {
    "deb": "Open the Releases page (github.com/RealDealCPA-VR/Iron-Jarvis/releases), find the newest version below "
           "this one that has a file named Iron-Jarvis-amd64.deb, download it, then open it to install.",
    "appimage": "Open the Releases page (github.com/RealDealCPA-VR/Iron-Jarvis/releases), find the newest version "
                "below this one that has a file named Iron-Jarvis-x86_64.AppImage, download it, then mark it as a "
                "program and run it.",
}


@requires_node
@pytest.mark.parametrize("kind", ["deb", "appimage", "folder"])
def test_every_linux_recovery_sentence_names_this_copys_file_v1540(tmp_path, kind):
    out = _dialogs(tmp_path, "linux", kind)
    words = "appimage" if kind == "folder" else kind  # an unpacked folder is pointed at the single file
    for where in ("corrupt", "startup", "upgrade", "abort"):
        text = out[where]["detail"] if isinstance(out[where], dict) else out[where]
        assert ".exe" not in text, (where, text)
    assert LINUX_LATEST[words] in out["corrupt"]["detail"], out["corrupt"]["detail"]
    assert LINUX_PREVIOUS[words] in out["startup"]["detail"], out["startup"]["detail"]
    assert LINUX_PREVIOUS[words] in out["upgrade"], out["upgrade"]
    lower = LINUX_LATEST[words][0].lower() + LINUX_LATEST[words][1:]
    assert "To install it by hand instead, " + lower in out["abort"], out["abort"]
    # The releases LIST, never /releases/latest (no Linux files there for ~40 min).
    assert out["corrupt"]["opened"] == [_RELEASES] and out["startup"]["opened"] == [_RELEASES], out


@requires_node
def test_windows_keeps_its_exact_words_and_its_latest_page_v1540(tmp_path):
    out = _dialogs(tmp_path, "win32", "folder")
    latest = json.loads(_decl("RELEASES_LATEST_HOW").split(" = ", 1)[1].rstrip(";"))
    previous = json.loads(_decl("RELEASES_PREVIOUS_HOW").split(" = ", 1)[1].rstrip(";"))
    assert "Iron-Jarvis-Setup-<version>.exe" in latest and "Iron-Jarvis-Setup-<version>.exe" in previous
    assert latest + " Your data, settings, and sessions are untouched." in out["corrupt"]["detail"]
    assert out["startup"]["detail"] == (
        "Go back to the version you had, then open Iron Jarvis again. " + previous
        + " Your settings, chats and keys stay where they are."
    )
    assert out["upgrade"].endswith("go back to the version you had: " + previous
                                   + " Your settings, chats and keys stay where they are.")
    assert out["abort"] == (
        "boom\n\nNothing was changed and Iron Jarvis keeps working on the current version meanwhile. "
        "It will look for the update again shortly. To install it by hand instead, open the Releases page "
        "(github.com/RealDealCPA-VR/Iron-Jarvis/releases), download the file named "
        "Iron-Jarvis-Setup-<version>.exe under the newest version and run it."
    )
    assert out["corrupt"]["opened"] == [_RELEASES + "/latest"] and out["startup"]["opened"] == [_RELEASES]


# --------------------------------------------------------------------------- #
# C-LX-2: the update state says how this Linux copy updates
# --------------------------------------------------------------------------- #

_STATE = r"""
const fs = require("fs"), path = require("path");
const scenario = JSON.parse(process.argv[2]);
const UPDATE_PLATFORM = scenario.platform;
if (scenario.appimage) process.env.APPIMAGE = scenario.appimage; else delete process.env.APPIMAGE;
process.resourcesPath = scenario.resources;
__FNS__
__STATE__
process.stdout.write(JSON.stringify(_updateState) + "\n");
"""


def _state(tmp_path: Path, platform: str, kind: str) -> dict:
    resources = tmp_path / f"state-{platform}-{kind}"
    resources.mkdir(parents=True, exist_ok=True)
    if kind == "deb":
        (resources / "package-type").write_text("deb", encoding="utf-8")
    src = _src()
    start = src.index("let _updateState = {")
    state = src[start : src.index("\n};\n", start) + 4]
    script = tmp_path / f"state-{platform}-{kind}.js"
    script.write_text(
        _STATE.replace("__FNS__", _fn("linuxInstallKind", required=False)).replace("__STATE__", state),
        encoding="utf-8",
    )
    scenario = {
        "platform": platform,
        "appimage": "/home/pat/Iron-Jarvis-x86_64.AppImage" if kind == "appimage" else "",
        "resources": str(resources),
    }
    proc = subprocess.run(["node", str(script), json.dumps(scenario)],
                          capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


@requires_node
def test_a_linux_copy_says_how_it_updates_and_windows_sends_nothing_v1540(tmp_path):
    assert _state(tmp_path, "linux", "appimage").get("install") == "appimage"
    assert _state(tmp_path, "linux", "deb").get("install") == "deb"
    assert _state(tmp_path, "linux", "folder").get("install") == "folder"
    windows = _state(tmp_path, "win32", "deb")
    assert "install" not in windows, windows
    assert windows["status"] == "idle"
