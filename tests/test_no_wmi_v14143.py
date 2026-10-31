"""v1.414.3: the daemon never sends Python's platform WMI queries.

Found on the user's PC: the installed daemon sat at about two full CPU cores
for as long as it ran. Its two busiest threads were Windows thread-pool
workers inside COM's RPC endpoint (``\RPC Control\OLE…``), answering the
process's own calls in a loop, and the daemon's ``_wmi.pyd`` query thread was
still alive from startup; a quiet daemon's had finished. The query comes from
SQLAlchemy calling ``platform.machine()`` on import, which Python 3.12 answers
over WMI/COM. ``iron_jarvis/__init__.py`` turns that path off; the platform
module then answers from ``sys.getwindowsversion()``.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="WMI is Windows-only")

PROBE = textwrap.dedent(
    """
    import _wmi, json, sys, tempfile
    calls = []
    real = _wmi.exec_query
    _wmi.exec_query = lambda q: calls.append(q) or real(q)
    import iron_jarvis
    from iron_jarvis.daemon.app import create_app
    from fastapi.testclient import TestClient
    app = create_app(tempfile.mkdtemp())
    with TestClient(app) as c:
        c.get("/health")
    import platform
    print(json.dumps({"calls": calls, "machine": platform.machine(), "release": platform.release(),
                      "version": platform.version(), "edition": platform.win32_edition()}))
    """
)


def test_starting_the_daemon_sends_no_wmi_query():
    out = subprocess.run([sys.executable, "-c", PROBE], capture_output=True, text=True, timeout=240)
    assert out.returncode == 0, out.stderr[-2000:]
    import json

    got = json.loads(out.stdout.strip().splitlines()[-1])
    assert got["calls"] == [], "a WMI query at startup can leave COM spinning two cores"
    wv = sys.getwindowsversion()
    assert got["version"] == f"{wv.major}.{wv.minor}.{wv.build}"
    assert got["machine"] in ("AMD64", "ARM64", "x86")
    assert got["release"] and got["edition"] is not None


def test_platform_wmi_path_refuses_after_import():
    import platform

    import iron_jarvis  # noqa: F401

    with pytest.raises(OSError):
        platform._wmi_query("OS", "Version")  # type: ignore[attr-defined]
