"""Frontier reliability signal: free disk space + provider-failure aggregation.

Covers the additive /diagnostics/reliability endpoint (disk + recent provider
failures) and the new free-disk-space runtime check in the doctor. Offline.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.onboarding.doctor import doctor, runtime_checks


def _client(tmp_path):
    return TestClient(create_app(str(tmp_path)))


# --- the endpoint reliability signal -------------------------------------------


def test_reliability_endpoint_reports_disk_and_provider_failures(tmp_path):
    client = _client(tmp_path)
    r = client.get("/diagnostics/reliability")
    assert r.status_code == 200
    body = r.json()

    # Free disk on the state home is present and sane.
    assert "disk" in body
    disk = body["disk"]
    assert "free" in disk and "total" in disk
    assert disk["free"] > 0
    assert disk["total"] >= disk["free"]

    # Recent provider-failure aggregation is present (0 on a clean install).
    assert "recent_provider_failures" in body
    assert isinstance(body["recent_provider_failures"], int)
    assert body["recent_provider_failures"] == 0


# --- the doctor runtime check --------------------------------------------------


def _find(rows, name):
    return next((r for r in rows if r["name"] == name), None)


def test_doctor_has_disk_space_runtime_row(tmp_path):
    client = _client(tmp_path)
    platform = client.app.state.platform

    # runtime_checks emits a disk_space row with the exact normalized shape.
    rows = runtime_checks(platform)
    disk = _find(rows, "disk_space")
    assert disk is not None
    assert set(disk.keys()) == {"name", "ok", "detail", "fix", "level"}
    assert isinstance(disk["ok"], bool)
    assert "GB free" in disk["detail"]

    # doctor(platform) folds the same row in (never raises).
    report = doctor(platform)
    assert _find(report["checks"], "disk_space") is not None
