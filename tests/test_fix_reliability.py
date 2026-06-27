"""Daily-driver reliability: Host/Origin guard + restart survival (reconcile + review rehydrate)."""

from __future__ import annotations

import subprocess

from fastapi.testclient import TestClient

from iron_jarvis.agents.orchestrator import Orchestrator
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import AgentType, PendingReviewRecord, SessionStatus
from iron_jarvis.daemon.app import create_app
from iron_jarvis.platform import build_platform


# --- Host / Origin guard (anti drive-by RCE) --------------------------------
def test_host_guard_rejects_non_loopback(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    assert client.get("/health", headers={"host": "evil.example.com"}).status_code == 403


def test_origin_guard_rejects_untrusted(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    r = client.get("/health", headers={"origin": "https://evil.example.com"})
    assert r.status_code == 403


def test_guard_allows_loopback_and_dashboard(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    assert client.get("/health").status_code == 200  # testserver host, no origin
    assert client.get("/health", headers={"origin": "http://localhost:3000"}).status_code == 200
    # any loopback origin (a browser can only send one from a local page)
    assert client.get("/health", headers={"origin": "http://127.0.0.1:5173"}).status_code == 200


# --- restart survival -------------------------------------------------------
async def test_reconcile_interrupted_sessions(platform):
    orch = Orchestrator(platform)
    sess = await orch.create_session("interrupted run", AgentType.BUILDER)  # left ACTIVE
    assert orch.get_session(sess.id).status is SessionStatus.ACTIVE
    n = orch.reconcile_interrupted_sessions()
    assert n >= 1
    refreshed = orch.get_session(sess.id)
    assert refreshed.status is SessionStatus.FAILED
    assert "interrupted" in refreshed.summary.lower()
    assert refreshed.finished_at is not None


async def test_review_persists_and_rehydrates_after_restart(tmp_path):
    repo = tmp_path / "proj"
    repo.mkdir()

    def _git(*a):
        subprocess.run(["git", *a], cwd=str(repo), capture_output=True, text=True, check=True)

    _git("init")
    _git("config", "user.email", "t@t")
    _git("config", "user.name", "t")
    (repo / "README.md").write_text("hi")
    _git("add", "-A")
    _git("commit", "-m", "base")

    p = build_platform(str(repo))
    p.config.git_native = True
    orch = Orchestrator(p)
    sess = await orch.run("write a result file", AgentType.BUILDER)  # git worktree -> review
    assert sess.id in orch._reviews  # review built in memory
    with session_scope(p.engine) as db:
        assert db.get(PendingReviewRecord, sess.id) is not None  # persisted

    # Simulate a daemon restart: a fresh orchestrator has empty in-memory state.
    orch2 = Orchestrator(p)
    assert sess.id not in orch2._reviews and sess.id not in orch2._git_sessions
    assert orch2.rehydrate_reviews() >= 1
    assert sess.id in orch2._reviews and sess.id in orch2._git_sessions  # approvable again


# --- webhook fail-closed (HIGH re-audit finding) ----------------------------
async def test_inbound_webhook_fails_closed_when_secret_unresolvable(platform):
    from iron_jarvis.webhooks.inbound import InboundWebhooks

    fired: list = []

    async def handler(body):
        fired.append(body)
        return {"ok": True}

    # A secret was configured (secret_name="k") but the resolver can't resolve it
    # (vault outage, or a legacy row that stored the slug instead of the key).
    wh = InboundWebhooks(platform.engine, secret_resolver=lambda name: None)
    wh.register("gh", handler, secret="topsecret", secret_name="k")
    res = await wh.dispatch("gh", {"x": 1}, raw=b'{"x":1}')  # unsigned
    assert res.get("ok") is False and "unavailable" in res.get("error", "")
    assert fired == []  # handler must NOT run on an unverified request


async def test_inbound_webhook_open_when_no_secret(platform):
    from iron_jarvis.webhooks.inbound import InboundWebhooks

    fired: list = []

    async def handler(body):
        fired.append(body)
        return {"ok": True}

    wh = InboundWebhooks(platform.engine, secret_resolver=lambda name: None)
    wh.register("open", handler)  # no secret -> open by design
    res = await wh.dispatch("open", {"y": 2}, raw=b'{"y":2}')
    assert res.get("ok") is True and fired == [{"y": 2}]
