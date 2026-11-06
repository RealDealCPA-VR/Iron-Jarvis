"""v1.439.0: "Check usage now" through the REAL app (``create_app`` + its
lifespan) and the fake Iron-Proxy, plus the REAL vendored Iron-Proxy bundle.

Pinned here:

* ``POST /build/accounts/{id}/usage/refresh``: 404 unknown, 409 (one
  sentence) for Grok, a signed-out account and Iron-Proxy off; a read answers
  the account row as every list now sees it (the fresh window, its read time,
  ``can_refresh``) — and a second press inside 20 s starts nothing;
* ``GET /build/accounts`` rows carry ``usage_read_at`` and ``can_refresh``;
* ``GET /iron-proxy`` account rows carry ``state.window`` (the window nearest
  its limit), ``usage_read`` and ``can_refresh``;
* the lifespan starts the in-use loop and it reports on ``loop_health``;
* the REAL bundle keeps windows told through ``/signal`` with ``status: 200``
  — nothing parked, no request counted (``reportSignal`` -> ``recordLimits``).
"""

from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

import psutil
import pytest
from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.iron_proxy import usage_read as U

FIXTURES = Path(__file__).parent / "fixtures"
FAKE_CLI = FIXTURES / "fake_usage_cli_v1439.py"
sys.path.insert(0, str(FIXTURES))
from fake_iron_proxy_v1301 import FEATURES_PHASE5, FakeIronProxy  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("IRON_PROXY_DATA_DIR", str(tmp_path / "ipd"))
    monkeypatch.setenv("IRONJARVIS_IRON_PROXY_BUNDLE", str(tmp_path / "no-bundle.mjs"))
    monkeypatch.delenv("IRONJARVIS_IRON_PROXY_NODE", raising=False)
    for k in ("CLAUDE_CONFIG_DIR", "CODEX_HOME", "ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("FAKE_USAGE_LOG", str(tmp_path / "cli-log.jsonl"))
    monkeypatch.delenv("FAKE_USAGE_MODE", raising=False)
    monkeypatch.setattr(U, "_cli_argv", lambda vendor: [sys.executable, str(FAKE_CLI)])
    U.reset()
    yield
    U.reset()
    needle = str(tmp_path / "ipd")
    for proc in psutil.process_iter(["cmdline"]):
        try:
            if needle in " ".join(proc.info.get("cmdline") or []):
                proc.kill()
        except (psutil.Error, OSError):
            pass


@pytest.fixture
def fake(tmp_path):
    f = FakeIronProxy(tmp_path / "ipd", features=list(FEATURES_PHASE5))
    f.add_profile("anthropic", "Work", id="c-work", home=str(tmp_path / "homes" / "c-work"))
    f.add_profile("anthropic", "Old", id="c-gone", home=str(tmp_path / "homes" / "c-gone"))
    f.states["c-gone"]["status"] = "unauthenticated"
    f.add_profile("openai", "Codex", id="x-plus", home=str(tmp_path / "homes" / "x-plus"))
    f.add_profile("xai", "Grok", id="g-one", home=str(tmp_path / "homes" / "g-one"))
    f.start()
    yield f
    f.stop()


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(str(tmp_path / "home"))) as c:
        yield c


def _starts(tmp_path) -> int:
    log = tmp_path / "cli-log.jsonl"
    return len(log.read_text(encoding="utf-8").splitlines()) if log.exists() else 0


def test_the_route_reads_answers_the_row_and_throttles(fake, client, tmp_path):
    assert client.post("/iron-proxy/enable").status_code == 200
    rows = client.get("/build/accounts").json()
    work = next(r for r in rows["claude"] if r["id"] == "c-work")
    assert work["usage_read_at"] is None and work["can_refresh"] is True
    gone = next(r for r in rows["claude"] if r["id"] == "c-gone")
    assert gone["can_refresh"] is False
    assert all(r["can_refresh"] is False for r in rows["grok"])

    r = client.post("/build/accounts/c-work/usage/refresh")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["read"]["ok"] is True and body["read"]["throttled"] is False
    acc = body["account"]
    assert acc["id"] == "c-work" and acc["status"] == "usage" and acc["usage"]["percent"] == 64
    assert acc["usage_read_at"] == body["read"]["at"] and acc["can_refresh"] is True
    # The list and the Connections card see the same number now.
    again = next(r for r in client.get("/build/accounts").json()["claude"] if r["id"] == "c-work")
    assert again["usage_read_at"] == body["read"]["at"] and again["usage"]["percent"] == 64
    card = next(a for a in client.get("/iron-proxy?discover=0").json()["accounts"] if a["id"] == "c-work")
    assert card["state"]["window"]["percent"] == 64 and card["state"]["window"]["window"] == "7 days"
    assert card["usage_read"]["ok"] is True and card["can_refresh"] is True
    assert card["state"]["status"] == "ready"
    # A second press inside 20 s starts nothing.
    second = client.post("/build/accounts/c-work/usage/refresh").json()
    assert second["read"]["throttled"] is True and second["read"]["at"] == body["read"]["at"]
    assert _starts(tmp_path) == 1


def test_refusals_answer_one_sentence(fake, client, tmp_path):
    r = client.post("/build/accounts/c-work/usage/refresh")
    assert r.status_code == 409 and r.json()["detail"] == U.PROXY_OFF  # Iron-Proxy off
    assert client.post("/iron-proxy/enable").status_code == 200
    for pid, status, words in (
        ("nope", 404, U.UNKNOWN_ACCOUNT),
        ("g-one", 409, U.NOT_SUPPORTED),
        ("c-gone", 409, U.SIGN_IN_WORDS),
    ):
        r = client.post(f"/build/accounts/{pid}/usage/refresh")
        assert (r.status_code, r.json()["detail"]) == (status, words)
    card = {a["id"]: a for a in client.get("/iron-proxy?discover=0").json()["accounts"]}
    assert card["g-one"]["can_refresh"] is False and card["c-gone"]["can_refresh"] is False
    assert card["x-plus"]["can_refresh"] is True and "window" not in card["x-plus"]["state"]
    assert _starts(tmp_path) == 0


def test_a_failed_read_is_a_200_with_its_words(fake, client, monkeypatch):
    monkeypatch.setenv("FAKE_USAGE_MODE", "error")
    assert client.post("/iron-proxy/enable").status_code == 200
    r = client.post("/build/accounts/x-plus/usage/refresh")
    assert r.status_code == 200
    assert r.json()["read"] == {"at": r.json()["read"]["at"], "ok": False, "throttled": False,
                                "words": "Codex could not say: not signed in."}


def test_the_lifespan_runs_the_in_use_loop(fake, tmp_path, monkeypatch):
    seen: list = []
    monkeypatch.setattr(U, "poll_in_use", lambda platform: seen.append(platform) or [])
    with TestClient(create_app(str(tmp_path / "home"))) as c:
        deadline = time.monotonic() + 20
        while not seen and time.monotonic() < deadline:
            time.sleep(0.05)
        assert seen and seen[0] is c.app.state.platform
        deadline = time.monotonic() + 10
        lh: dict = {}
        while "account_usage" not in lh and time.monotonic() < deadline:
            lh = dict(getattr(c.app.state.d, "loop_health", None) or {})
            time.sleep(0.05)
        assert lh["account_usage"]["ok"] is True, lh


# --------------------------------------------------------------------------- #
# the REAL vendored bundle keeps a 200 signal's windows
# --------------------------------------------------------------------------- #
_REPO = Path(__file__).resolve().parents[1]
_BUNDLE = _REPO / "desktop" / "vendor" / "iron-proxy" / "iron-proxy.mjs"


@pytest.mark.timeout(150)
@pytest.mark.skipif(not _BUNDLE.is_file(), reason="Iron-Proxy bundle not vendored")
@pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")
def test_the_real_bundle_keeps_the_windows_without_parking_or_counting(tmp_path, monkeypatch):
    monkeypatch.delenv("IRONJARVIS_IRON_PROXY_BUNDLE", raising=False)
    monkeypatch.setattr("iron_jarvis.iron_proxy.service.START_TIMEOUT_S", 60.0)
    from iron_jarvis.iron_proxy import service as _svc_mod

    monkeypatch.setattr(_svc_mod, "HEALTH_TIMEOUT_S", 30.0)
    monkeypatch.setattr(_svc_mod._health, "__defaults__", (30.0,))
    with TestClient(create_app(str(tmp_path / "home"))) as c:
        st = c.post("/iron-proxy/enable").json()["status"]
        assert st["running"] is True and st["error"] is None, json.dumps(st, default=str)
        svc = c.app.state.platform.iron_proxy
        assert svc.has_feature("plan-usage")
        claude = c.post("/iron-proxy/accounts", json={"provider": "anthropic", "title": "C"}).json()
        codex = c.post("/iron-proxy/accounts", json={"provider": "openai", "title": "X"}).json()
        client = svc.client()
        before = client.states()
        # Exactly what a read hands Iron-Proxy (the parsers' own outputs).
        headers = U.claude_headers({"rate_limits": {
            "five_hour": {"utilization": 42, "resets_at": "2100-01-01T00:00:00Z"},
            "seven_day": {"utilization": 63.5, "resets_at": 4102444800},
        }})
        U._tell(svc, claude["id"], "claude", headers)
        rl = U.codex_rate_limits({"rateLimits": {
            "primary": {"usedPercent": 37, "windowDurationMins": 300, "resetsAt": 4102444800},
            "secondary": {"usedPercent": 12.5, "windowDurationMins": 10080, "resetsAt": 4102444800},
            "planType": "plus",
        }})
        U._tell(svc, codex["id"], "codex", rl)
        after = client.states()
        cw = {w["kind"]: w for w in after[claude["id"]]["windows"]}
        assert cw["5h"]["usedPercent"] == 42 and cw["7d"]["usedPercent"] == 63.5
        assert cw["5h"]["resetsAt"].startswith("2100-01-01")
        xw = {w["kind"]: w["usedPercent"] for w in after[codex["id"]]["windows"]}
        assert xw == {"primary": 37, "secondary": 12.5}
        assert after[codex["id"]]["plan"] == "plus"
        for pid in (claude["id"], codex["id"]):
            assert after[pid]["status"] == before[pid]["status"] != "parked"
            assert after[pid].get("served", 0) == before[pid].get("served", 0)
        for u in client.usage():
            assert all((w or {}).get("requests", 0) == 0 for w in (u.get("windows") or {}).values()), u
        # The Connections card reads the window.
        card = {a["id"]: a for a in c.get("/iron-proxy?discover=0").json()["accounts"]}
        assert card[claude["id"]]["state"]["window"]["percent"] == 64
        c.post("/iron-proxy/disable")
