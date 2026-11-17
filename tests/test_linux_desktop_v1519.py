"""The desktop shell on Linux: start at sign-in, the tray icon, and a restart that works.

Three things read wrong or did nothing on Linux, found by reading the shell and
confirmed on GitHub's Ubuntu (2026-11-16):

* "Start at login" (the tray, the app menu and Settings) said On and did
  nothing: Electron has no login item on Linux. It now writes the desktop's own
  autostart entry, and reads the switch back from that file.
* The tray icon was Windows' ``.ico``, which Linux cannot read (a blank tray).
* A restart from inside the single-file app started a program in a folder that
  is gone once the app closes.

Windows keeps its login item, its ``.ico`` and Electron's own restart: each pin
below runs the Windows path too, on whatever machine runs the suite.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import pytest

from tests.test_update_channel_v1260 import _lift, _src, requires_node

_HARNESS = r"""
const fs = require("fs"), path = require("path");
const scenario = JSON.parse(process.argv[2]);
Object.defineProperty(process, "platform", { value: scenario.platform });
const IS_PACKAGED = scenario.packaged !== false;
const START_WITH_WINDOWS_KEY = "start_with_windows";
const loginItem = [];
let loginOn = false;
const app = {
  getLoginItemSettings: () => ({ openAtLogin: loginOn }),
  setLoginItemSettings: (o) => { loginItem.push(o); loginOn = !!o.openAtLogin; },
};
function refreshMenus() {}
function desktopLog() {}
function writeDesktopSetting() {}
function noteDesktopSwitchByUser() {}
__LOGIN__
const file = linuxAutostartFile();
const text = () => { try { return fs.readFileSync(file, "utf8"); } catch { return null; } };
const out = { file, works: startAtLoginWorksHere(), steps: [] };
for (const step of scenario.steps) {
  if (step.target !== undefined) process.env.APPIMAGE = step.target;
  if (step.do === "on") setStartAtLogin(true);
  if (step.do === "off") setStartAtLogin(false);
  if (step.do === "refresh") refreshLinuxAutostart();
  out.steps.push({ on: getStartAtLogin(), text: text() });
}
out.loginItem = loginItem;
process.stdout.write(JSON.stringify(out) + "\n");
"""


def _run(tmp_path: Path, monkeypatch, platform: str, steps: list[dict], *, packaged: bool = True,
         appimage: str | None = "/home/pat/Apps/Iron-Jarvis-x86_64.AppImage") -> dict:
    config = tmp_path / "config"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config))
    if appimage is None:
        monkeypatch.delenv("APPIMAGE", raising=False)
    else:
        monkeypatch.setenv("APPIMAGE", appimage)
    script = tmp_path / "login.js"
    script.write_text(
        _HARNESS.replace("__LOGIN__", _lift("// --- Start at login", "function setStartAtLoginFromMenu")),
        encoding="utf-8",
    )
    scenario = {"platform": platform, "packaged": packaged, "steps": steps}
    proc = subprocess.run(
        ["node", str(script), json.dumps(scenario)],
        capture_output=True, text=True, encoding="utf-8", timeout=60, env={**os.environ},
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


@requires_node
def test_on_linux_start_at_login_writes_the_desktops_own_entry_and_reads_it_back(tmp_path, monkeypatch):
    out = _run(tmp_path, monkeypatch, "linux", [{"do": "none"}, {"do": "on"}, {"do": "off"}])
    assert out["works"] is True
    assert Path(out["file"]) == tmp_path / "config" / "autostart" / "iron-jarvis.desktop"
    before, on, off = out["steps"]
    assert before == {"on": False, "text": None}
    assert on["on"] is True
    assert on["text"].splitlines() == [
        "[Desktop Entry]",
        "Type=Application",
        "Name=Iron Jarvis",
        'Exec="/home/pat/Apps/Iron-Jarvis-x86_64.AppImage" --hidden',  # quietly, in the tray
        "Terminal=false",
        "X-GNOME-Autostart-enabled=true",
    ]
    assert off == {"on": False, "text": None}  # off removes it: nothing starts, the switch reads Off
    assert out["loginItem"] == [], "Electron's login item does nothing on Linux and is never asked"


@requires_node
def test_on_windows_the_login_item_is_used_and_no_file_is_written(tmp_path, monkeypatch):
    out = _run(tmp_path, monkeypatch, "win32", [{"do": "on"}, {"do": "off"}, {"do": "refresh"}])
    assert out["works"] is True
    assert out["loginItem"] == [
        {"openAtLogin": True, "args": ["--hidden"]},
        {"openAtLogin": False, "args": ["--hidden"]},
    ]
    assert [s["text"] for s in out["steps"]] == [None, None, None]
    assert [s["on"] for s in out["steps"]] == [True, False, False]
    assert not (tmp_path / "config").exists()


@requires_node
def test_a_moved_app_file_is_named_again_at_start_and_an_off_switch_stays_off(tmp_path, monkeypatch):
    moved = "/home/pat/Desktop/Iron-Jarvis-x86_64.AppImage"
    out = _run(tmp_path, monkeypatch, "linux", [
        {"do": "refresh"},                    # off: a start writes nothing
        {"do": "on"},
        {"do": "refresh", "target": moved},   # the file was moved since
    ])
    off, on, refreshed = out["steps"]
    assert off == {"on": False, "text": None}
    assert 'Exec="/home/pat/Apps/Iron-Jarvis-x86_64.AppImage" --hidden' in on["text"]
    assert f'Exec="{moved}" --hidden' in refreshed["text"] and "/Apps/" not in refreshed["text"]


@requires_node
def test_a_path_with_spaces_and_shell_marks_is_quoted_as_the_entry_rules_ask(tmp_path, monkeypatch):
    odd = '/home/pat/My "Apps" $HOME `x`/Iron Jarvis.AppImage'
    out = _run(tmp_path, monkeypatch, "linux", [{"do": "on"}], appimage=odd)
    line = [ln for ln in out["steps"][0]["text"].splitlines() if ln.startswith("Exec=")][0]
    assert line == 'Exec="/home/pat/My \\"Apps\\" \\$HOME \\`x\\`/Iron Jarvis.AppImage" --hidden'


@requires_node
def test_a_dev_run_and_a_mac_have_no_switch_to_set(tmp_path, monkeypatch):
    assert _run(tmp_path, monkeypatch, "linux", [], packaged=False)["works"] is False
    assert _run(tmp_path, monkeypatch, "darwin", [])["works"] is False


def test_the_shell_asks_one_question_about_where_start_at_login_works():
    src = _src()
    switches = src[src.index("// --- The desktop app's own switches"):src.index("function installDesktopSwitches")]
    assert switches.count("startAtLoginWorksHere()") == 4
    assert 'process.platform === "win32"' not in switches, "a Linux copy would read On and do nothing again"
    assert "refreshLinuxAutostart(); //" in src
    # The first-run question is Windows' (its words name Windows): never asked elsewhere.
    offer = src[src.index("function maybeOfferStartWithWindows()"):][:300]
    assert 'if (!IS_PACKAGED || process.platform !== "win32" || START_HIDDEN) return;' in offer


def test_the_tray_icon_is_the_png_on_linux_and_the_ico_everywhere_else():
    src = _src()
    tray = src[src.index("function createTray()"):][:700]
    assert re.search(
        r'const iconPath = process\.platform !== "linux" && fs\.existsSync\(icoPath\)\s*\? icoPath\s*'
        r': path\.join\(__dirname, "assets", "icon\.png"\);',
        tray,
    ), tray
    assert (Path(__file__).resolve().parents[1] / "desktop" / "assets" / "icon.png").is_file()


def test_a_restart_inside_the_single_file_app_starts_the_file_the_user_runs():
    src = _src()
    wrap = src[src.index("// LINUX ONLY, the single-file app:"):][:900]
    assert 'if (process.platform === "linux" && process.env.APPIMAGE) {' in wrap
    assert "relaunchOwn({ execPath: process.env.APPIMAGE, args: process.argv.slice(1), ...(options || {}) })" in wrap
    # Before anything that could restart, and every restart still says app.relaunch().
    assert src.index("// LINUX ONLY, the single-file app:") < src.index('const windowState = require("./windowState");')
    assert src.count("app.relaunch();") >= 4


# --------------------------------------------------------------------------- #
# Edge under its Linux names
# --------------------------------------------------------------------------- #


def test_the_linux_names_are_looked_for_off_windows_only():
    import importlib

    # The package re-exports a FUNCTION named doctor: reach the module by path.
    doctor = importlib.import_module("iron_jarvis.onboarding.doctor")

    if os.name == "nt":
        assert doctor._LINUX_EDGE_NAMES == () and doctor._LINUX_EDGE_PLACES == ()
    else:
        assert doctor._LINUX_EDGE_NAMES == ("microsoft-edge", "microsoft-edge-stable")
        assert doctor._LINUX_EDGE_PLACES == ("/opt/microsoft/msedge/msedge",)


def test_a_linux_pc_with_edge_is_no_longer_told_to_install_edge(monkeypatch):
    """Edge's Linux package puts ``microsoft-edge`` on PATH, not ``msedge``: the
    setup steps said no browser was installed and the agent's own browser said
    "Microsoft Edge was not found"."""
    import shutil

    from iron_jarvis.harness import page_browser
    import importlib

    # The package re-exports a FUNCTION named doctor: reach the module by path.
    doctor = importlib.import_module("iron_jarvis.onboarding.doctor")

    monkeypatch.setattr(doctor, "_LINUX_EDGE_NAMES", ("microsoft-edge", "microsoft-edge-stable"))
    monkeypatch.setattr(doctor, "_LINUX_EDGE_PLACES", ("/opt/microsoft/msedge/msedge",))
    here = {"/usr/bin/microsoft-edge"}
    monkeypatch.setattr(shutil, "which", lambda name, *a, **k: "/usr/bin/microsoft-edge" if name == "microsoft-edge" else None)
    monkeypatch.setattr(os.path, "exists", lambda p: str(p) in here)
    monkeypatch.setattr(os.path, "isfile", lambda p: str(p) in here)
    for env in ("ProgramFiles", "ProgramFiles(x86)", "LocalAppData"):
        monkeypatch.delenv(env, raising=False)
    doctor.installed_browsers.cache_clear()
    try:
        assert list(doctor.installed_browsers()) == ["Microsoft Edge"]
        assert doctor._find_browser() == "/usr/bin/microsoft-edge"
        assert page_browser.edge_executable() == "/usr/bin/microsoft-edge"
        # Installed under /opt only (no name on PATH): still found.
        here.clear()
        here.add("/opt/microsoft/msedge/msedge")
        monkeypatch.setattr(shutil, "which", lambda name, *a, **k: None)
        doctor.installed_browsers.cache_clear()
        assert list(doctor.installed_browsers()) == ["Microsoft Edge"]
        assert page_browser.edge_executable() == "/opt/microsoft/msedge/msedge"
    finally:
        doctor.installed_browsers.cache_clear()
    assert doctor._browser_label("/usr/bin/microsoft-edge-stable") == "Microsoft Edge"
    assert doctor._browser_label("/usr/bin/google-chrome-stable") == "Google Chrome"


# --------------------------------------------------------------------------- #
# the health check says true things off Windows
# --------------------------------------------------------------------------- #


def test_the_health_check_never_sends_a_linux_user_to_windows_settings(monkeypatch):
    """It said: add the OCR language in Settings > Time & language, install
    Microsoft Word, and gave a PowerShell line for uv. None can be done there."""
    import importlib

    doctor = importlib.import_module("iron_jarvis.onboarding.doctor")
    local_ocr = importlib.import_module("iron_jarvis.documents.local_ocr")
    legacy = importlib.import_module("iron_jarvis.documents.legacy")

    monkeypatch.setattr(local_ocr, "self_test", lambda: {"ok": False, "detail": "Windows OCR exists only on Windows"})
    monkeypatch.setattr(legacy, "word_available", lambda: False)
    monkeypatch.setattr(legacy, "xls_available", lambda: True)
    monkeypatch.setattr(doctor, "_which", lambda *names: None)

    def said(on_windows: bool) -> str:
        monkeypatch.setattr(doctor, "_ON_WINDOWS", on_windows)
        rows = [doctor.check_local_ocr(), doctor.check_legacy_office(), doctor.check_uv()]
        return " | ".join(f"{r.get('detail')} -> {r.get('fix')}" for r in rows)

    assert doctor._ON_WINDOWS is (os.name == "nt")
    linux = said(False)
    for wrong in ("Time & language", "winrt", "Install Microsoft Word", "PowerShell", "install.ps1"):
        assert wrong not in linux, wrong
    assert "Scanned pages need a vision-capable model instead." in linux
    assert "Save .doc files as .docx before using them." in linux
    assert "curl -LsSf https://astral.sh/uv/install.sh | sh" in linux
    # Windows' words are exactly what they were.
    windows = said(True)
    assert "Windows OCR is not usable here (Windows OCR exists only on Windows)" in windows
    assert "Add the OCR language in Settings → Time & language" in windows
    assert "Install Microsoft Word, or save .doc files as .docx before using them." in windows
    assert "(PowerShell: `irm https://astral.sh/uv/install.ps1 | iex`)." in windows
