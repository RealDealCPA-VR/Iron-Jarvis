"""v1.175.0 — the desktop window's trust boundary is an ORIGIN, not a prefix.

TWO FINDINGS FROM AN ADVERSARIAL REVIEW, both in ``desktop/main.js``:

1. ``will-navigate`` allowed any URL starting with ``DASHBOARD_URL``, which is
   built WITHOUT a trailing slash (``http://localhost:8788``). So
   ``http://localhost:8788@evil.com/`` passed the check — that string is
   USERINFO and the real host is ``evil.com``. The main window carries the
   preload exposing ``window.ironjarvis`` (the per-install daemon TOKEN,
   clipboard access, the update bridge), and a preload survives navigation, so
   one click on a link in model-authored or fetched content could have handed
   all of it to a remote page.

2. The privileged IPC handlers ignored their ``event``. A handler that never
   looks at its sender answers whoever asks — and the preload is shared by
   every frame in the window, including iframes.

The first test runs the REAL guard (extracted from main.js and executed under
node) against a table of adversarial URLs, so it proves behaviour rather than
the presence of a string. The rest pin the call sites, because a guard nothing
calls is not a guard.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

_MAIN = Path(__file__).resolve().parents[1] / "desktop" / "main.js"
_PORT = 8788

#: (url, expected_is_dashboard). The port is the default 8788.
_CASES: list[tuple[str, bool]] = [
    # --- genuinely ours -----------------------------------------------------
    ("http://localhost:8788", True),
    ("http://localhost:8788/", True),
    ("http://localhost:8788/sessions/abc", True),
    ("http://localhost:8788/chat?thread=1#top", True),
    ("http://127.0.0.1:8788/", True),
    ("http://127.0.0.1:8788/projects", True),
    # --- THE BYPASS: userinfo makes the prefix match a foreign origin --------
    ("http://localhost:8788@evil.com/", False),
    ("http://localhost:8788@evil.com/steal?t=1", False),
    ("http://127.0.0.1:8788@evil.com/", False),
    ("http://localhost:8788%40evil.com/", False),
    # --- neighbouring ports / hosts that a prefix could confuse -------------
    ("http://localhost:87880/", False),
    ("http://localhost:8789/", False),
    ("http://localhost.evil.com:8788/", False),
    ("http://127.0.0.1.evil.com:8788/", False),
    # --- the daemon is a DIFFERENT origin; it is not the dashboard ----------
    ("http://127.0.0.1:8787/health", False),
    # --- schemes that must never navigate in-window -------------------------
    ("https://localhost:8788/", False),
    ("file:///C:/Windows/System32/drivers/etc/hosts", False),
    ("javascript:alert(1)", False),
    ("data:text/html,<script>1</script>", False),
    ("about:blank", False),
    # --- junk --------------------------------------------------------------
    ("", False),
    ("not a url", False),
    ("//localhost:8788/", False),
]


def _guard_source() -> str:
    """The origin set + guard, lifted verbatim from main.js.

    Lifted rather than copied so this test can never drift from the shipped
    implementation — if the real guard changes, this runs the CHANGED one.
    """
    src = _MAIN.read_text(encoding="utf-8")
    start = src.index("const DASHBOARD_ORIGINS")
    fn = src.index("function isDashboardUrl", start)
    end = src.index("\n}", fn) + 2
    return f"const DASHBOARD_PORT = {_PORT};\n" + src[start:end]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_dashboard_origin_guard_rejects_the_userinfo_bypass():
    """Run the real guard over the adversarial table."""
    harness = (
        _guard_source()
        + "\nconst cases = "
        + json.dumps([c[0] for c in _CASES])
        + ";\nconsole.log(JSON.stringify(cases.map((u) => isDashboardUrl(u))));\n"
    )
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", harness],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    got = json.loads(proc.stdout.strip().splitlines()[-1])
    wrong = [
        (url, expected, actual)
        for (url, expected), actual in zip(_CASES, got)
        if expected != actual
    ]
    assert not wrong, "guard verdicts wrong for: " + "\n".join(
        f"  {u!r}: expected {e}, got {a}" for u, e, a in wrong
    )


def test_navigation_handlers_use_the_origin_guard():
    """Both navigation seams go through it — and neither still prefix-matches.

    ``will-redirect`` matters as much as ``will-navigate``: a server-side 302
    never fires the latter, so a dashboard-origin URL redirecting off-origin
    would land in the window with the preload attached.
    """
    src = _MAIN.read_text(encoding="utf-8")
    for event in ("will-navigate", "will-redirect"):
        m = re.search(
            r'on\("' + event + r'", \(event, url\) => \{(.{0,200}?)\}\);', src, re.S
        )
        assert m, f"no {event} handler"
        body = m.group(1)
        assert "isDashboardUrl(url)" in body, f"{event} does not use the origin guard"
        assert "startsWith" not in body, f"{event} still prefix-matches"
        assert "preventDefault" in body and "openExternal" in body


def test_privileged_ipc_handlers_check_their_sender():
    """Reading the clipboard and installing an update are sender-checked."""
    src = _MAIN.read_text(encoding="utf-8")
    for channel in ("clipboard:read", "update:apply"):
        m = re.search(
            r'ipcMain\.handle\("' + channel + r'", \((\w+)\) => \{(.{0,400}?)\n  \}\);',
            src,
            re.S,
        )
        assert m, f"no handler for {channel}"
        arg, body = m.group(1), m.group(2)
        assert arg != "_e", f"{channel} still ignores its event"
        assert (
            f"isTrustedDashboardSender({arg})" in body
        ), f"{channel} does not validate its sender"
        # The refusal must come FIRST, before the privileged work.
        assert body.index("isTrustedDashboardSender") < body.index("return")


def test_sender_guard_reuses_the_origin_check():
    """One definition of "is this our page", so the two cannot disagree."""
    src = _MAIN.read_text(encoding="utf-8")
    m = re.search(
        r"function isTrustedDashboardSender\(event\) \{(.{0,500}?)\n\}", src, re.S
    )
    assert m, "no sender guard"
    body = m.group(1)
    assert "senderFrame" in body, "checks the window, not the sending FRAME"
    assert "isDashboardUrl(url)" in body
    # Fail closed: an unreadable/destroyed frame is NOT trusted.
    assert "return false" in body
