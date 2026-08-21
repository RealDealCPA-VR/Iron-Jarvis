"""v1.194.0 — the desktop clipboard bridge can read an IMAGE, and stays fenced.

A screen snip (Win+Shift+S) lands on the clipboard as a bitmap. In the PACKAGED
app ``navigator.clipboard.read()`` is permission-gated, which is exactly why the
text clipboard already crosses IPC — so without ``clipboard:readImage`` pasting
a snip into the Build page works in a browser tab and silently does nothing in
the app the user runs every day.

The handler is LIFTED VERBATIM from ``desktop/main.js`` and executed under node
against stubbed Electron seams, so this proves BEHAVIOUR (including the sender
refusal) rather than the presence of a string — the idiom of
``test_desktop_trust_boundary_v1175.py``. Two failures must stay distinct:
"the clipboard holds no image" (``null``) and "there WAS an image and it would
not encode" (``{error: "unreadable"}``); collapsing the second into the first
would report a degradation as user error.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

_DESKTOP = Path(__file__).resolve().parents[1] / "desktop"
_MAIN = _DESKTOP / "main.js"
_PRELOAD = _DESKTOP / "preload.js"

_CHANNEL = "clipboard:readImage"


def _handler_source() -> str:
    """The real ``clipboard:readImage`` registration, lifted from main.js."""
    src = _MAIN.read_text(encoding="utf-8")
    start = src.index(f'ipcMain.handle("{_CHANNEL}"')
    end = src.index("\n  });", start) + len("\n  });")
    return src[start:end]


#: (case name, node expression configuring the stubs) -> asserted below.
_HARNESS = """
const registered = {};
const ipcMain = { handle: (ch, fn) => { registered[ch] = fn; } };
let _readImage = () => null;
const clipboard = { readImage: () => _readImage() };
let _trusted = true;
function isTrustedDashboardSender(event) { return _trusted && !!event; }

%(handler)s

const handler = registered["%(channel)s"];
if (typeof handler !== "function") throw new Error("handler not registered");
const sender = { senderFrame: { url: "http://localhost:8788/terminals" } };
const png = () => globalThis.Buffer.from("PNGDATA");
const img = (over) => Object.assign(
  { isEmpty: () => false, getSize: () => ({ width: 1600, height: 900 }), toPNG: png },
  over || {},
);

const out = {};
// 1. an off-origin frame gets nothing, exactly like clipboard:read.
_trusted = false; _readImage = () => img();
out.untrusted = handler(sender);
_trusted = true;
// 2. clipboard holds no image at all.
_readImage = () => img({ isEmpty: () => true });
out.empty = handler(sender);
// 3. the real path: PNG base64 + its size.
_readImage = () => img();
out.ok = handler(sender);
// 4. an image that will not encode is REPORTED, never reported as "nothing".
_readImage = () => img({ toPNG: () => { throw new Error("nope"); } });
out.throws = handler(sender);
// 5. same for an encoder that hands back an empty buffer.
_readImage = () => img({ toPNG: () => globalThis.Buffer.alloc(0) });
out.blank = handler(sender);
console.log(JSON.stringify(out));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_clipboard_read_image_handler_behaviour():
    """Run the shipped handler over the five outcomes that matter."""
    harness = _HARNESS % {"handler": _handler_source(), "channel": _CHANNEL}
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", harness],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    got = json.loads(proc.stdout.strip().splitlines()[-1])

    assert got["untrusted"] is None, "an off-origin frame was handed a screenshot"
    assert got["empty"] is None, "an empty clipboard must read as null"

    ok = got["ok"]
    assert ok and not ok.get("error"), ok
    # base64 of b"PNGDATA" — the bytes really made the crossing.
    assert ok["base64"] == "UE5HREFUQQ=="
    assert ok["bytes"] == 7
    assert (ok["width"], ok["height"]) == (1600, 900)

    for case in ("throws", "blank"):
        bad = got[case]
        assert bad is not None, f"{case}: an unreadable image was reported as none"
        assert bad.get("error") == "unreadable", bad
        assert bad.get("bytes") == 0, bad


def test_clipboard_read_image_is_sender_checked_first():
    """The refusal precedes the clipboard touch, like every privileged handler."""
    src = _MAIN.read_text(encoding="utf-8")
    m = re.search(
        r'ipcMain\.handle\("' + _CHANNEL + r'", \((\w+)\) => \{(.*?)\n  \}\);',
        src,
        re.S,
    )
    assert m, f"no handler for {_CHANNEL}"
    arg, body = m.group(1), m.group(2)
    assert arg != "_e", f"{_CHANNEL} ignores its event"
    assert f"isTrustedDashboardSender({arg})" in body
    assert body.index("isTrustedDashboardSender") < body.index("clipboard.readImage")


def test_preload_exposes_the_image_read_next_to_the_text_read():
    """A handler the renderer cannot reach is not a bridge."""
    src = _PRELOAD.read_text(encoding="utf-8")
    assert re.search(
        r'clipboardReadImage:\s*\(\)\s*=>\s*ipcRenderer\.invoke\("' + _CHANNEL + r'"\)',
        src,
    ), "window.ironjarvis.clipboardReadImage is missing"
    assert "clipboardReadText" in src
