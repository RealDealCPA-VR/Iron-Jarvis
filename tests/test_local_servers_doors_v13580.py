"""v1.358.0 lane 8D (acceptance 6, the doors half): LM Studio / llama.cpp
found on this PC reach the THREE places a new user meets Ollama's one press —
the chat's empty state and the first-run wizard (ConnectDoors) and the
composer's suggestion chip (ModelSuggestChip). All three read ``GET /health``,
so its ``custom`` row carries ``detected_servers`` =
``local_servers_seen.health_field(config)``: the servers answering on this PC
with a chat model while the model-server slot is free — what ONE press
(``POST /onboarding/use-model {provider: key}``) connects and chooses.

The cached probe only: /health never waits on a port (``seen`` returns the
last answer and refreshes behind it — pinned in
test_connections_honest_v13580.test_seen_never_waits_on_a_port), and a fault
in the probe never fails /health. The dashboard half is
dashboard/__tests__/connect-doors-local-servers-v13580.test.tsx.
"""

from __future__ import annotations

import threading
import time

from iron_jarvis.providers import local_servers_seen as lss
from tests.test_connections_honest_v13580 import (  # noqa: F401 — the lane's harness, reused
    LMSTUDIO_ROWS,
    _fresh,
    _ModelServer,
    _platform,
    client,
    servers,
)


def _custom_row(c) -> dict:
    rows = c.get("/health").json()["providers"]
    return next(r for r in rows if r["provider"] == "custom")


def test_health_names_a_running_lm_studio_on_the_custom_row(client, servers):  # noqa: F811 — pytest fixtures
    """MUTATION: drop the custom-row branch in daemon/app._visible_providers
    -> KeyError 'detected_servers' (red)."""
    lss.probe_now()
    cfg = _platform(client).config
    row = _custom_row(client)
    assert row["detected_servers"] == lss.health_field(cfg)
    assert row["detected_servers"] == [
        {"key": "lmstudio", "label": "LM Studio (on this PC)", "base_url": servers.lm.url}
    ], "a server that is not llama.cpp was offered, or LM Studio was missed"


def test_nothing_is_offered_once_the_slot_holds_an_address(client, servers):  # noqa: F811 — pytest fixtures
    """The press never replaces a saved model server, so /health offers none."""
    lss.probe_now()
    r = client.put("/settings", json={"values": {"custom_base_url": "http://192.168.1.50:8000/v1"}})
    assert r.status_code == 200, r.text
    assert _custom_row(client)["detected_servers"] == []


def test_nothing_answering_is_an_empty_list_not_a_missing_field(client):  # noqa: F811 — pytest fixtures
    """The session guard (conftest) keeps the probe off: what CI and a PC
    without these servers see."""
    assert _custom_row(client)["detected_servers"] == []


def test_health_never_waits_on_a_port(client, monkeypatch):  # noqa: F811 — pytest fixtures
    """/health reads the CACHED answer and refreshes behind it. The proof is
    ORDER: the server holds its reply until a timer lets it go; /health must
    come back before that timer fires. MUTATION: call ``probe_now`` from the
    health row -> /health returns only after the timer released the server
    (red)."""
    hold = threading.Event()
    slow = _ModelServer(LMSTUDIO_ROWS, hold=hold)
    released_by_timer = threading.Event()

    def _release() -> None:
        released_by_timer.set()
        hold.set()

    timer = threading.Timer(20.0, _release)
    monkeypatch.setattr(lss, "PROBE_FORCED_OFF", False)
    monkeypatch.setattr(lss, "SERVERS", (lss.LocalServer("lmstudio", "LM Studio", "LM Studio (on this PC)", slow.url),))
    # A blocking read must wait LONGER than the timer for the order to prove
    # anything (probe_one reads this knob when it runs — pinned in
    # test_connections_honest_v13580.test_the_probe_honours_its_own_timeout_knob).
    monkeypatch.setattr(lss, "PROBE_TIMEOUT_S", 30.0)
    timer.start()
    try:
        row = _custom_row(client)
        assert not released_by_timer.is_set(), "/health waited on the model server's port"
        assert row["detected_servers"] == []
    finally:
        timer.cancel()
        hold.set()
        # Let the refresh behind the read land before the next test's
        # forget(): a late record would leak this server into it.
        deadline = time.monotonic() + 10
        while lss._refreshing and time.monotonic() < deadline:
            time.sleep(0.02)
        slow.stop()


def test_a_probe_fault_never_fails_health(client, monkeypatch):  # noqa: F811 — pytest fixtures
    def _boom(_cfg):
        raise RuntimeError("probe broke")

    monkeypatch.setattr(lss, "health_field", _boom)
    r = client.get("/health")
    assert r.status_code == 200
    row = next(x for x in r.json()["providers"] if x["provider"] == "custom")
    assert row["detected_servers"] == []


def test_the_session_guard_keeps_the_real_ports_unasked():
    """tests/conftest._isolate_local_model_servers_probe: no test reads this
    PC's own LM Studio / llama.cpp (an exact ``usable == []`` pin elsewhere
    would go red on a dev box with LM Studio running). MUTATION: drop the
    fixture -> red (the module default is the env switch, off here)."""
    assert lss.PROBE_FORCED_OFF is True
