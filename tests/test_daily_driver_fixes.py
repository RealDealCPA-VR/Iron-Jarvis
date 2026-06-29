"""Regression tests for the daily-driver audit fixes (connections, trust, recovery).

Covers the B1/B2/B5/B6/D1-D4 fixes: the mock-trap auto-promote + downgrade signal,
live default routing, real connection probing, lost-key detection, atomic config
writes, key-inclusive backups, and Ollama URL normalization. All offline.
"""

from __future__ import annotations

import asyncio
import tarfile

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.agents.orchestrator import Orchestrator
from iron_jarvis.connections.probe import live_probe
from iron_jarvis.core.config import _read_toml, persist_config_values
from iron_jarvis.core.events import EventBus, EventType
from iron_jarvis.core.models import SessionStatus
from iron_jarvis.core.updates import RunResult, apply_update
from iron_jarvis.daemon.app import create_app
from iron_jarvis.maintenance import run_auto_backup
from iron_jarvis.providers.adapters.base import LLMMessage
from iron_jarvis.providers.manager import ProviderManager, _normalize_ollama_url
from iron_jarvis.providers.router import ModelRouter


# --- D2: Ollama URL normalization ---------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("http://localhost:11434", "http://localhost:11434/v1/chat/completions"),
        ("http://localhost:11434/", "http://localhost:11434/v1/chat/completions"),
        ("http://localhost:11434/v1", "http://localhost:11434/v1/chat/completions"),
        ("http://x/v1/chat/completions", "http://x/v1/chat/completions"),
        (None, None),
        ("", ""),
    ],
)
def test_ollama_url_normalized(raw, expected):
    assert _normalize_ollama_url(raw) == expected


def test_ollama_available_with_host_only_url():
    m = ProviderManager(ollama_base_url="http://localhost:11434")
    assert m.available("ollama") is True


# --- D3 + B1: live default + mock-trap downgrade signal -----------------------


def test_router_reads_default_provider_live():
    holder = {"p": "mock"}
    r = ModelRouter(ProviderManager(), lambda: holder["p"], EventBus())
    assert r.default_provider == "mock"
    holder["p"] = "anthropic"  # a model switch must be seen without rebuild
    assert r.default_provider == "anthropic"


def test_has_available_api_provider_reflects_credentials():
    no_cred = ProviderManager()
    assert no_cred.has_available_api_provider() is False
    with_cred = ProviderManager(
        credential_resolver=lambda n: "sk-x" if n == "anthropic" else None
    )
    assert with_cred.has_available_api_provider() is True


def test_router_emits_downgrade_on_mock_trap():
    """Default is mock while a REAL provider is connected → loud downgrade event."""
    seen: list[str] = []
    bus = EventBus()
    bus.add_handler(lambda ev: seen.append(ev.type))
    mgr = ProviderManager(
        credential_resolver=lambda n: "sk-x" if n == "anthropic" else None
    )
    r = ModelRouter(mgr, "mock", bus)
    asyncio.run(
        r.complete(
            system="", messages=[LLMMessage(role="user", content="hi")], tools=[]
        )
    )
    assert EventType.PROVIDER_DOWNGRADED in seen


def test_router_silent_when_only_mock_available():
    """Pure offline (no real provider) keeps the old behavior: no downgrade noise."""
    seen: list[str] = []
    bus = EventBus()
    bus.add_handler(lambda ev: seen.append(ev.type))
    r = ModelRouter(ProviderManager(), "mock", bus)
    asyncio.run(
        r.complete(
            system="", messages=[LLMMessage(role="user", content="hi")], tools=[]
        )
    )
    assert EventType.PROVIDER_DOWNGRADED not in seen


# --- B1: connecting a real provider auto-promotes the default -----------------


def test_connecting_real_provider_autopromotes_default(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    assert client.get("/settings").json()["settings"]["default_provider"] == "mock"

    r = client.post("/connections/anthropic/key", json={"key": "sk-ant-test"})
    assert r.status_code == 200
    assert r.json()["promoted_default"] is True

    s = client.get("/settings").json()["settings"]
    assert s["default_provider"] == "anthropic"
    assert s["default_model"] == "claude-opus-4-8"


def test_autopromote_only_fires_once(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    client.post("/connections/anthropic/key", json={"key": "sk-ant-1"})
    # A second connection (now that default is real) must NOT steal the default.
    r = client.post("/connections/openai/key", json={"key": "sk-openai-1"})
    assert r.json()["promoted_default"] is False
    assert client.get("/settings").json()["settings"]["default_provider"] == "anthropic"


# --- B5: the connection Test does a real probe (stubbed offline) ---------------


def test_connection_test_runs_live_probe(platform):
    platform.connections.set_api_key("anthropic", "sk-bad")
    platform.connections._prober = lambda provider, cred: (False, "rejected (401)")
    res = platform.connections.test("anthropic")
    assert res["ok"] is False and "rejected" in res["detail"]

    platform.connections._prober = lambda provider, cred: (True, "reachable")
    assert platform.connections.test("anthropic")["ok"] is True


def test_live_probe_unknown_provider_is_not_a_failure():
    ok, detail = live_probe("mock", "x")
    assert ok is True


# --- B2/B6: lost-key detection + key-inclusive backups ------------------------


def test_key_valid_detects_lost_key(platform):
    platform.secrets.set("anthropic_api_key", "sk-x", kind="api_key")
    assert platform.secrets.key_valid() is True

    # Simulate a key-less restore: the original key disappears.
    (platform.config.home / "secrets" / ".secrets.key").unlink()
    # _fernet regenerates so the daemon still boots, but the new key can't decrypt
    # the old ciphertext — key_valid surfaces the loss instead of masking it.
    assert platform.secrets.key_valid() is False


def test_diagnostics_reports_secrets_key_valid(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    d = client.get("/diagnostics").json()
    assert "secrets_key_valid" in d
    assert d["secrets_key_valid"] is True  # nothing stored yet => valid


def test_auto_backup_includes_keys_by_default(platform):
    home = platform.config.home
    (home / "secrets").mkdir(parents=True, exist_ok=True)
    (home / "secrets" / ".secrets.key").write_text("KEY")
    out = run_auto_backup(home, engine=platform.engine, keep=3)
    assert any(".secrets.key" in n for n in tarfile.open(out).getnames())


# --- D4: atomic + crash-safe config writes ------------------------------------


def test_corrupt_config_falls_back_to_empty(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text("this is = not [[[ valid toml")
    assert _read_toml(p) == {}  # must not raise / abort boot


def test_persist_config_values_atomic_roundtrip(tmp_path):
    persist_config_values(tmp_path, {"default_provider": "anthropic", "skip": None})
    doc = _read_toml(tmp_path / "config.toml")
    assert doc["default_provider"] == "anthropic"
    assert "skip" not in doc  # None values dropped (TOML has no null)
    assert not (tmp_path / "config.toml.tmp").exists()  # temp cleaned up


# --- B3: self-update rollback + test gate -------------------------------------


def _update_runner(fail=None, sha="abc1234"):
    """Fake git/uv runner. Clean tree, returns ``sha`` for rev-parse HEAD; any
    command whose joined argv startswith an entry in ``fail`` returns non-zero."""
    fail = fail or set()
    calls: list[list[str]] = []

    def runner(cmd, cwd):
        calls.append(list(cmd))
        j = " ".join(cmd)
        for pfx in fail:
            if j.startswith(pfx):
                return RunResult(1, "", f"boom: {pfx}")
        if j == "git rev-parse HEAD":
            return RunResult(0, sha + "\n", "")
        return RunResult(0, "", "")

    runner.calls = calls  # type: ignore[attr-defined]
    return runner


def test_apply_update_rolls_back_on_sync_failure(tmp_path):
    runner = _update_runner(fail={"uv sync"})
    res = apply_update(tmp_path, runner=runner, run_tests=False)
    assert res["ok"] is False
    assert res["rolled_back"] is True
    assert ["git", "reset", "--hard", "abc1234"] in runner.calls


def test_apply_update_rolls_back_on_test_failure(tmp_path):
    runner = _update_runner(fail={"uv run pytest"})
    res = apply_update(tmp_path, runner=runner, run_tests=True)
    assert res["ok"] is False
    assert res["rolled_back"] is True
    assert ["git", "reset", "--hard", "abc1234"] in runner.calls


def test_apply_update_passes_test_gate(tmp_path):
    runner = _update_runner()  # everything succeeds
    res = apply_update(tmp_path, runner=runner, run_tests=True)
    assert res["ok"] is True
    assert ["uv", "run", "pytest", "-q"] in runner.calls
    assert res["rolled_back"] is False


def test_apply_update_missing_test_runner_does_not_rollback(tmp_path):
    # rc 127 = test runner absent (packaged install) → warn, don't roll back a pull.
    fail_runner_calls: list[list[str]] = []

    def runner(cmd, cwd):
        fail_runner_calls.append(list(cmd))
        j = " ".join(cmd)
        if j == "git rev-parse HEAD":
            return RunResult(0, "deadbee\n", "")
        if j.startswith("uv run pytest"):
            return RunResult(127, "", "uv: not found")
        return RunResult(0, "", "")

    res = apply_update(tmp_path, runner=runner, run_tests=True)
    assert not any(c[:3] == ["git", "reset", "--hard"] for c in fail_runner_calls)
    # The pull itself succeeded; we just couldn't run the gate.
    assert res["rolled_back"] is False


# --- D6: a crashed run is finalized FAILED, not stranded ACTIVE ----------------


def test_run_session_finalizes_on_unexpected_error(platform):
    orch = Orchestrator(platform)
    seen: list[str] = []
    platform.event_bus.add_handler(lambda ev: seen.append(ev.type))
    session = asyncio.run(orch.create_session("boom task"))

    async def _boom(*a, **k):
        raise RuntimeError("kaboom")

    orch.runtime.run = _boom  # type: ignore[assignment]
    with pytest.raises(RuntimeError):
        asyncio.run(orch.run_session(session.id))

    refreshed = orch.get_session(session.id)
    assert refreshed.status is SessionStatus.FAILED
    assert EventType.SESSION_COMPLETED in seen  # dashboard stops spinning


# --- D7: global exception handler + upload size cap ---------------------------


def test_upload_rejects_oversized(tmp_path, monkeypatch):
    import iron_jarvis.daemon.app as appmod

    client = TestClient(appmod.create_app(str(tmp_path)))
    monkeypatch.setattr(appmod, "_MAX_UPLOAD_BYTES", 10)
    r = client.post(
        "/documents/upload", json={"filename": "big.bin", "content_b64": "AAAA" * 100}
    )
    assert r.status_code == 413


# --- Self-correction surface: doctor runtime checks + repair actions ----------


def test_doctor_includes_runtime_checks(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    names = [c["name"] for c in client.get("/doctor").json()["checks"]]
    assert "provider" in names
    assert "secrets_key" in names
    assert "database" in names


def test_diagnostics_repair_actions(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    assert client.post("/diagnostics/repair", json={"action": "db_integrity"}).json()["ok"] is True
    assert client.post("/diagnostics/repair", json={"action": "backup_now"}).json()["ok"] is True
    assert (
        client.post("/diagnostics/repair", json={"action": "prune_events", "older_than_days": 0}).json()["ok"]
        is True
    )
    assert client.post("/diagnostics/repair", json={"action": "nope"}).status_code == 400


# --- Convergence round 1: workflow scheduling, MCP timeout, rehydration -------


def test_empty_workflow_schedule_raises_instead_of_silent_success(platform):
    # The old behavior ran an empty workflow and reported "completed" (a silent
    # no-op); now a workflow schedule with no steps fails loudly when it fires.
    platform.scheduler.add_task("empty-wf", "0 0 * * *", kind="workflow", payload={})
    with pytest.raises(ValueError):
        asyncio.run(platform.scheduler.run_now("empty-wf"))


def test_scheduled_saved_workflow_runs_its_steps(platform):
    from sqlmodel import select

    from iron_jarvis.core.db import session_scope
    from iron_jarvis.workflows.models import WorkflowRunRecord
    from iron_jarvis.workflows.store import WorkflowStore

    WorkflowStore(platform.engine).save(
        "nightly", [{"name": "s1", "agent": "builder", "task": "write a short note"}]
    )
    platform.scheduler.add_task(
        "run-nightly", "0 0 * * *", kind="workflow", payload={"workflow": "nightly"}
    )
    # Resolves the saved workflow BY NAME and runs its steps (mock provider).
    asyncio.run(platform.scheduler.run_now("run-nightly"))
    with session_scope(platform.engine) as db:
        runs = list(db.exec(select(WorkflowRunRecord)))
    assert any(r.workflow_name == "nightly" for r in runs)


def test_mcp_hanging_server_is_skipped(monkeypatch):
    import time

    monkeypatch.setenv("IRONJARVIS_MCP_CONNECT_TIMEOUT", "1")
    from iron_jarvis.mcp.tools import mcp_tools

    class Hang:
        def request(self, method, params=None):
            time.sleep(30)  # never answers
            return {}

        def close(self):
            pass

    # A non-responding stdio server must be skipped (not hang boot) → no tools.
    assert mcp_tools([{"name": "hang", "transport": Hang()}]) == []


def test_rehydration_steps_recorded_independently(tmp_path):
    # The boot rehydration steps run in independent try-blocks and each records
    # its health, so a failure in one can't silently skip the others.
    with TestClient(create_app(str(tmp_path))) as client:
        loops = client.get("/diagnostics").json()["background_loops"]
    assert loops.get("reconcile_sessions", {}).get("ok") is True
    assert loops.get("rehydrate_webhooks", {}).get("ok") is True
