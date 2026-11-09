"""v1.456.0: TEXT SIZE — the user can change how big everything looks in the
app, from Settings, from chat, or with Ctrl + = / Ctrl + - / Ctrl + 0.

Pinned here:

* the ``device.text_size`` row (Appearance, per device, allow tier) and its
  five sizes, held EQUAL to desktop/main.js TEXT_SIZES and
  dashboard/lib/textSize.ts TEXT_SIZES;
* chat can set it on the device that asked, through the real stream lane;
* the trigger arms the writers for "make the text bigger" and never for a
  font size inside the user's own document;
* the desktop half, LIFTED out of main.js and run under node: the steps stop
  at both ends, Ctrl + 0 is normal, a size off the list is refused, and the
  View menu uses these steps, never Chromium's zoom roles (which would leave
  the setting saying something else); preload exposes the bridge.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from iron_jarvis.daemon.app import create_app
from iron_jarvis.settings import schema
from iron_jarvis.settings.trigger import settings_ask
from tests.test_chat_approvals_v1187 import _drive_stream
from tests.test_settings_chat_tools_v1321 import _body, _calls_stream

_ROOT = Path(__file__).resolve().parents[1]
_MAIN = _ROOT / "desktop" / "main.js"
_PRELOAD = _ROOT / "desktop" / "preload.js"
_TS = _ROOT / "dashboard" / "lib" / "textSize.ts"
requires_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
SIZES = [90, 100, 110, 125, 150]


def _src(p: Path) -> str:
    return p.read_text(encoding="utf-8").replace("\r\n", "\n")


def _row() -> schema.SettingDef:
    (row,) = [d for d in schema.SETTINGS if d.key == "device.text_size"]
    return row


def test_the_row_is_a_per_device_appearance_choice_of_five_sizes():
    row = _row()
    assert row.store == "device" and row.group == "appearance" and row.type == "enum"
    assert row.tier == "allow" and not row.floor_when
    assert [int(v) for v, _ in row.options] == SIZES
    assert dict(row.options)["100"].startswith("Normal")
    for v, label in row.options:
        assert f"({v}%)" in label


def test_the_three_lists_of_sizes_are_one_list():
    main = re.search(r"^const TEXT_SIZES = \[([^\]]*)\];", _src(_MAIN), re.M)
    ts = re.search(r"^export const TEXT_SIZES = \[([^\]]*)\] as const;", _src(_TS), re.M)
    assert main and ts
    assert [int(x) for x in main.group(1).split(",")] == SIZES
    assert [int(x) for x in ts.group(1).split(",")] == SIZES
    assert re.search(r"^const DEFAULT_TEXT_SIZE = 100;", _src(_MAIN), re.M)


@pytest.mark.parametrize(
    "text",
    [
        "make the text bigger",
        "change the text size to large",
        "set the font size to 125%",
        "make the font size smaller",
    ],
)
def test_asking_for_bigger_or_smaller_text_arms_the_writers(text):
    ask = settings_ask(text)
    assert ask.reader and ask.writers, text


@pytest.mark.parametrize(
    "text",
    [
        "change the font size in my report to 12",
        "make the text bigger in this document",
        "set the font size of the headings in my resume to 14",
    ],
)
def test_a_font_size_in_the_users_own_document_arms_no_writer(text):
    assert not settings_ask(text).writers, text


@pytest.mark.asyncio
async def test_chat_sets_the_text_size_on_the_device_that_asked(tmp_path):
    app = create_app(str(tmp_path))
    app.state.platform.router.stream = _calls_stream([("config_set", {"key": "device.text_size", "value": "125"})])

    async def never(data):  # pragma: no cover
        raise AssertionError(data)

    await _drive_stream(app, _body("make the text bigger", device_id="dev_desk001"), never)
    w = app.state.platform.config_writer
    assert w.current("device.text_size", device_id="dev_desk001") == "125"
    assert w.current("device.text_size", device_id="dev_phone01") is None


def test_a_size_off_the_list_is_refused_by_the_writer(tmp_path):
    app = create_app(str(tmp_path))
    w = app.state.platform.config_writer
    with pytest.raises(Exception):
        w.apply({"device.text_size": "300"}, actor="test", device_id="dev_desk001")
    assert w.current("device.text_size", device_id="dev_desk001") is None


# ------------------------------------------------------------ desktop half


def _lift(name: str) -> str:
    src = _src(_MAIN)
    start = src.index(f"function {name}(")
    end = src.index("\n}\n", start) + 3
    return src[start:end]


@requires_node
def test_the_steps_stop_at_both_ends_and_ctrl_0_is_normal():
    src = _src(_MAIN)
    consts = re.search(r"^const TEXT_SIZES = .*?;\nconst DEFAULT_TEXT_SIZE = 100;", src, re.M | re.S)
    assert consts
    code = (
        consts.group(0)
        + "\n"
        + _lift("cleanTextSize")
        + _lift("nextTextSize")
        + """
const out = {
  up: [90,100,110,125,150].map((s) => nextTextSize(s, 1)),
  down: [90,100,110,125,150].map((s) => nextTextSize(s, -1)),
  reset: nextTextSize(150, 0),
  junk: nextTextSize(7, 1),
  clean: [cleanTextSize("110"), cleanTextSize(125), cleanTextSize("300"), cleanTextSize(""), cleanTextSize(null)],
};
process.stdout.write(JSON.stringify(out));
"""
    )
    proc = subprocess.run(["node", "-e", code], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert out["up"] == [100, 110, 125, 150, 150]
    assert out["down"] == [90, 90, 100, 110, 125]
    assert out["reset"] == 100
    assert out["junk"] == 110  # an unreadable size counts as normal
    assert out["clean"] == [110, 125, None, None, None]


def test_the_view_menu_steps_the_setting_never_chromiums_zoom():
    src = _src(_MAIN)
    for role in ("resetZoom", "zoomIn", "zoomOut"):
        assert f'role: "{role}"' not in src
    for label, step, key in (("Larger text", "1", "="), ("Smaller text", "-1", "-"), ("Normal text size", "0", "0")):
        i = src.index(f'label: "{label}"')
        item = src[i : i + 220]
        assert f'accelerator: "CommandOrControl+{key}"' in item
        assert f"stepTextSize({step}," in item
    # Ctrl + mouse wheel steps the same sizes; every dashboard page gets the
    # size again when it loads (Chromium does not keep zoom across restarts).
    hooks = _lift("installTextSizeHooks")
    assert '"zoom-changed"' in hooks and "stepTextSize(" in hooks
    assert '"did-finish-load"' in hooks and "applyTextZoom(wc)" in hooks
    assert "installTextSizeHooks();" in src and "loadTextSize();" in src
    # Only a dashboard page is zoomed; a step tells the window, which saves it.
    assert "isDashboardUrl(wc.getURL())" in _lift("applyTextZoom")
    assert 'send("textSize:changed", next)' in _lift("stepTextSize")
    assert 'writeDesktopSetting("textSize", clean)' in _lift("setTextSize")


def test_the_bridge_is_sender_checked_and_exposed():
    src = _src(_MAIN)
    for ch in ("textSize:set", "textSize:get"):
        i = src.index(f'ipcMain.handle("{ch}"')
        assert "isTrustedDashboardSender(event)" in src[i : i + 200]
    pre = _src(_PRELOAD)
    assert "textSize: {" in pre
    assert 'ipcRenderer.invoke("textSize:set"' in pre
    assert 'ipcRenderer.on("textSize:changed"' in pre
