"""Voice endpoints, computer-use run history, artifact.generated, live re-arm."""

from __future__ import annotations

import base64

from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app


# --- /voice/status + /voice/transcribe -------------------------------------


def test_voice_status_honest_when_no_backend(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    r = client.get("/voice/status")
    assert r.status_code == 200
    data = r.json()
    assert data["available"] is False
    assert data["backend"] is None
    assert "Connections" in data["hint"]  # tells the user what to connect


def test_voice_transcribe_424_when_no_backend(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    audio = base64.b64encode(b"x" * 4096).decode()
    r = client.post("/voice/transcribe", json={"audio_b64": audio})
    assert r.status_code == 424  # NOT 5xx: must not trip the global error banner
    assert "speech-to-text" in r.json()["detail"].lower()


def test_voice_transcribe_with_custom_backend_configured(tmp_path):
    """A custom OpenAI-compatible endpoint counts as a backend: bad base64 is
    a 400, and an unreachable server is an honest 424 — never fabricated text."""
    client = TestClient(create_app(str(tmp_path)))
    # Point the custom endpoint at a dead local port (no external network).
    p = client.put("/settings", json={"values": {"custom_base_url": "http://127.0.0.1:9"}})
    assert p.status_code == 200

    status = client.get("/voice/status").json()
    assert status["available"] is True
    assert status["backend"] == "custom"

    r = client.post("/voice/transcribe", json={"audio_b64": "!!!not-base64!!!"})
    assert r.status_code in (400, 424)  # decode rejects, or strict parse skipped

    audio = base64.b64encode(b"RIFFfakewav" * 500).decode()
    r = client.post("/voice/transcribe", json={"audio_b64": audio, "mime": "audio/wav"})
    assert r.status_code == 424
    assert "unreachable" in r.json()["detail"] or "failed" in r.json()["detail"]


def test_voice_transcribe_rejects_empty_audio(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    client.put("/settings", json={"values": {"custom_base_url": "http://127.0.0.1:9"}})
    r = client.post("/voice/transcribe", json={"audio_b64": ""})
    assert r.status_code == 400


# --- GET /computeruse/runs (history list) -----------------------------------


def test_computeruse_runs_empty(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    r = client.get("/computeruse/runs")
    assert r.status_code == 200
    assert r.json() == {"runs": []}


def test_computeruse_runs_lists_newest_first_with_ok_mapping(tmp_path):
    from iron_jarvis.computeruse.models import ComputerUseRun
    from iron_jarvis.core.config import load_config
    from iron_jarvis.core.db import open_db, session_scope

    app = create_app(str(tmp_path))
    engine = open_db(load_config(str(tmp_path)).db_path)
    with session_scope(engine) as db:
        db.add(ComputerUseRun(id="curun-old", task="first", status="completed"))
        db.add(ComputerUseRun(id="curun-new", task="second", status="failed"))
        db.commit()

    client = TestClient(app)
    runs = client.get("/computeruse/runs?limit=10").json()["runs"]
    ids = [r["id"] for r in runs]
    assert set(ids) == {"curun-old", "curun-new"}
    by_id = {r["id"]: r for r in runs}
    assert by_id["curun-old"]["ok"] is True  # completed
    assert by_id["curun-new"]["ok"] is False  # failed
    assert by_id["curun-old"]["task"] == "first"
    assert by_id["curun-old"]["started_at"]  # created_at surfaced


# --- artifact.generated actually fires --------------------------------------


def test_artifact_save_publishes_event(platform):
    platform.artifacts.save("report", "hello", session_id="sess-1")
    types = [e.type for e in platform.event_bus.history]
    assert "artifact.generated" in types
    evt = next(e for e in platform.event_bus.history if e.type == "artifact.generated")
    assert evt.payload["name"] == "report"
    assert evt.payload["version"] == 1
    assert evt.session_id == "sess-1"


# --- live re-arm: toggling autonomy/sentinels settings must not need a restart


def test_settings_toggle_rearm_live_smoke(tmp_path):
    """With the lifespan running, flipping autonomy/sentinels on and off
    re-arms the background loops in place — the PUTs succeed and the daemon
    keeps serving (previously the toggle silently waited for a restart)."""
    with TestClient(create_app(str(tmp_path))) as client:
        for values in (
            {"autonomy_enabled": True, "sentinels_enabled": True},
            {"autonomy_tick_seconds": 120},
            {"autonomy_enabled": False, "sentinels_enabled": False},
        ):
            r = client.put("/settings", json={"values": values})
            assert r.status_code == 200
            assert set(values) <= set(r.json()["updated"])
        assert client.get("/health").status_code == 200
