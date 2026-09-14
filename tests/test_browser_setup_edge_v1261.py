"""v1.261.0 — the guided browser setup is written for the browser this PC has.

The user's report: "when I select Set up browser I only get the instructions for
Chrome, not Edge." v1.259.0 had put Edge in parentheses after every Chrome
sentence; on an Edge-only machine that is still a Chrome page. The daemon now
says which browsers are INSTALLED (``installed_browsers`` on ``GET
/browser/status``, from the doctor's own finder — pure ``os.path.exists`` and
``shutil.which``, nothing launched, cached for the process), and the dashboard
writes every step for the paired browser first, the only installed one second,
Chrome otherwise (``dashboard/components/browser/browserWords.ts``).
"""

from __future__ import annotations

import importlib
import os
import shutil

from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app

doctor = importlib.import_module("iron_jarvis.onboarding.doctor")


def _pretend_installed(monkeypatch, *, on_path: dict[str, str], exists: set[str]):
    """Only the named executables resolve; only the named paths exist."""
    monkeypatch.setattr(shutil, "which", lambda name, *a, **k: on_path.get(name))
    monkeypatch.setattr(os.path, "exists", lambda p: str(p) in exists)
    monkeypatch.setenv("ProgramFiles", r"C:\Program Files")
    monkeypatch.setenv("ProgramFiles(x86)", r"C:\Program Files (x86)")
    monkeypatch.setenv("LocalAppData", r"C:\Users\someone\AppData\Local")
    doctor.installed_browsers.cache_clear()


EDGE_X86 = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
CHROME_PF = r"C:\Program Files\Google\Chrome\Application\chrome.exe"


def test_an_edge_only_pc_reports_exactly_edge(monkeypatch):
    _pretend_installed(monkeypatch, on_path={}, exists={EDGE_X86})
    assert list(doctor.installed_browsers()) == ["Microsoft Edge"]


def test_both_installed_reports_chrome_first_then_edge_once_each(monkeypatch):
    _pretend_installed(monkeypatch, on_path={"msedge": EDGE_X86}, exists={CHROME_PF, EDGE_X86})
    assert list(doctor.installed_browsers()) == ["Google Chrome", "Microsoft Edge"]


def test_nothing_installed_reports_nothing_and_never_launches(monkeypatch):
    _pretend_installed(monkeypatch, on_path={}, exists=set())
    assert list(doctor.installed_browsers()) == []


def test_the_answer_is_cached_for_the_process(monkeypatch):
    _pretend_installed(monkeypatch, on_path={}, exists={EDGE_X86})
    assert list(doctor.installed_browsers()) == ["Microsoft Edge"]
    # The disk changes; the cached answer does not, until the cache is cleared —
    # the card polls every 5 s and must not walk PATH each time.
    monkeypatch.setattr(os.path, "exists", lambda p: False)
    assert list(doctor.installed_browsers()) == ["Microsoft Edge"]
    doctor.installed_browsers.cache_clear()
    assert list(doctor.installed_browsers()) == []


def test_browser_status_carries_installed_browsers_in_the_disconnected_shape(monkeypatch, tmp_path):
    from iron_jarvis.daemon.routes import browser as routes

    monkeypatch.setattr(routes, "installed_browsers", lambda: ["Microsoft Edge"])
    app = create_app(tmp_path)
    with TestClient(app) as client:
        body = client.get("/browser/status").json()
    assert body["installed_browsers"] == ["Microsoft Edge"]
    assert body["connected"] is False


def test_a_finder_that_raises_never_breaks_the_status(monkeypatch, tmp_path):
    from iron_jarvis.daemon.routes import browser as routes

    def boom():
        raise RuntimeError("registry on fire")

    monkeypatch.setattr(routes, "installed_browsers", boom)
    app = create_app(tmp_path)
    with TestClient(app) as client:
        body = client.get("/browser/status").json()
    assert body["installed_browsers"] == []
