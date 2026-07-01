"""Security fixes: FS-policy enforced at the agent-tool layer (not just HTTP),
and the OAuth callback no longer reflects an unescaped provider (XSS).
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from iron_jarvis.core import fs_policy
from iron_jarvis.daemon.app import create_app
from iron_jarvis.documents.tools import ReadDocumentTool
from iron_jarvis.tools.base import ToolContext


def _ctx(ws):
    return ToolContext(
        workspace=ws, session_id="t", agent_run_id="t",
        config=None, event_bus=None, engine=None,
    )


async def test_read_document_denies_protected_secrets_path(tmp_path):
    secrets = tmp_path / "vault-secrets"
    secrets.mkdir()
    key = secrets / ".secrets.key"
    key.write_text("FERNET-KEY-MATERIAL")
    fs_policy.register_protected_root(secrets)
    ws = tmp_path / "ws"
    ws.mkdir()
    res = await ReadDocumentTool().execute({"path": str(key)}, _ctx(ws))
    assert res.ok is False
    assert "FERNET-KEY-MATERIAL" not in (res.output or "")


async def test_read_document_respects_allowlist(tmp_path, monkeypatch):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("OUTSIDE-SECRET")
    monkeypatch.setenv("IRONJARVIS_FS_ALLOWLIST", str(allowed))
    ws = tmp_path / "ws"
    ws.mkdir()
    res = await ReadDocumentTool().execute({"path": str(outside)}, _ctx(ws))
    assert res.ok is False  # outside the allowlist
    ok_file = allowed / "ok.txt"
    ok_file.write_text("hello inside")
    res2 = await ReadDocumentTool().execute({"path": str(ok_file)}, _ctx(ws))
    assert res2.ok and "hello inside" in res2.output


def test_oauth_callback_escapes_tag_payload(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    # No slash in the payload (path segment can't contain '/').
    r = client.get("/oauth/<img src=x onerror=alert(1)>/callback")
    assert r.status_code == 200
    assert "<img src=x onerror=alert(1)>" not in r.text  # escaped, never raw
    assert "window.opener" in r.text  # legit postMessage script preserved
    assert "content-security-policy" in {k.lower() for k in r.headers}


def test_oauth_callback_no_quote_breakout(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    r = client.get("/oauth/'+alert(1)+'/callback")
    assert r.status_code == 200
    # The provider is encoded via JSON.parse("...") — a double-quoted JS string
    # literal, so the single-quote payload is inert (cannot break out). The old
    # vulnerable single-quoted object-literal injection pattern must be gone.
    assert "JSON.parse(" in r.text
    assert "'provider':'" not in r.text


def test_http_documents_read_denies_protected_key(tmp_path):
    # create_app builds the platform, which registers <home>/secrets as protected.
    client = TestClient(create_app(str(tmp_path)))
    key = tmp_path / ".ironjarvis" / "secrets" / ".secrets.key"
    key.parent.mkdir(parents=True, exist_ok=True)
    key.write_text("FERNET-MASTER-KEY")
    r = client.get("/documents/read", params={"path": str(key)})
    assert r.status_code == 403  # protected-roots enforced at the HTTP layer too
    assert "FERNET-MASTER-KEY" not in r.text


def test_http_documents_read_respects_allowlist(tmp_path, monkeypatch):
    outside = tmp_path / "outside.txt"
    outside.write_text("OUTSIDE")
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    monkeypatch.setenv("IRONJARVIS_FS_ALLOWLIST", str(allowed))
    client = TestClient(create_app(str(tmp_path)))
    r = client.get("/documents/read", params={"path": str(outside)})
    assert r.status_code == 403


# --- Security lens round 0: the confirmed HIGH/MEDIUM fixes --------------------


async def test_read_document_denies_windows_device_prefix_bypass(tmp_path):
    # AGE-1: a \\?\ extended-length path used to bypass the protected-root guard
    # (its anchor differs, so is_relative_to missed it) while open() still read it.
    secrets = tmp_path / "vault2-secrets"
    secrets.mkdir()
    key = secrets / ".secrets.key"
    key.write_text("FERNET-KEY-2")
    fs_policy.register_protected_root(secrets)
    ws = tmp_path / "ws2"
    ws.mkdir()
    res = await ReadDocumentTool().execute({"path": "\\\\?\\" + str(key)}, _ctx(ws))
    assert res.ok is False
    assert "FERNET-KEY-2" not in (res.output or "")
    assert fs_policy.is_protected_path("\\\\?\\" + str(key)) is True
    # The name-based denylist also blocks a key file anywhere, prefix or not.
    assert fs_policy.is_protected_path(str(tmp_path / "elsewhere" / ".secrets.key")) is True


async def test_secret_set_redacts_plaintext_in_transcript(tmp_path):
    # SEC-1: the secret value must be encrypted in the vault but NEVER written to
    # the tool-invocation transcript (DB at rest / export / backups).
    from sqlmodel import select

    from iron_jarvis.core.db import session_scope
    from iron_jarvis.core.models import ToolInvocation
    from iron_jarvis.platform import build_platform

    p = build_platform(str(tmp_path))
    ctx = ToolContext(
        workspace=tmp_path, session_id="s1", agent_run_id="r1",
        config=p.config, event_bus=p.event_bus, engine=p.engine,
    )
    await p.registry.invoke(
        "secret_set",
        {"name": "apikey", "value": "sk-super-secret-XYZ", "kind": "api_key"},
        ctx, p.permissions, agent_overrides={"secret_set": "allow"},
    )
    assert p.secrets.get("apikey") == "sk-super-secret-XYZ"  # stored (encrypted)
    with session_scope(p.engine) as db:
        inv = db.exec(select(ToolInvocation).where(ToolInvocation.tool == "secret_set")).first()
    assert inv is not None
    assert "sk-super-secret-XYZ" not in (inv.args_json or "")  # plaintext never persisted
    assert "REDACTED" in inv.args_json


async def test_delegate_refuses_supervisor_target(tmp_path):
    # DOS-1: a supervisor delegating to another supervisor is the fork-bomb vector.
    from iron_jarvis.agents.delegate_tool import DelegateTool
    from iron_jarvis.platform import build_platform

    p = build_platform(str(tmp_path))
    ctx = ToolContext(
        workspace=tmp_path, session_id="s", agent_run_id="r",
        config=p.config, event_bus=p.event_bus, engine=p.engine,
    )
    res = await DelegateTool(p).execute({"agent_type": "supervisor", "task": "loop"}, ctx)
    assert res.ok is False and "supervisor" in (res.error or "").lower()


def test_rest_integration_refuses_ssrf_metadata_url():
    # SSRF-1: the REST integration must refuse a cloud-metadata / internal target.
    from iron_jarvis.integrations.builtin import RestApiIntegration

    integ = RestApiIntegration(
        {"base_url": "http://169.254.169.254/latest/meta-data/"}, lambda n: None
    )
    res = integ.test_connection()
    assert res["ok"] is False and "unsafe" in res["detail"].lower()


# --- Security lens round 1: residual/backlog burndown -------------------------


def test_web_action_redacts_typed_value():
    # SECDISC-1: a value typed into a DOM field (may be a credential) must not be
    # persisted verbatim to the tool transcript.
    from iron_jarvis.computeruse.tools import WebActionTool

    red = WebActionTool.redact_args(
        WebActionTool.__new__(WebActionTool), {"kind": "type", "value": "hunter2"}
    )
    assert red["value"] == "***REDACTED***"
    # Nothing to redact when there's no typed value.
    assert WebActionTool.redact_args(
        WebActionTool.__new__(WebActionTool), {"kind": "click"}
    ) == {"kind": "click"}


def test_body_limit_middleware_rejects_oversized(tmp_path, monkeypatch):
    # DOS-2 / CONV1-01: an oversized body is rejected (413) before buffering.
    monkeypatch.setenv("IRONJARVIS_MAX_BODY_MB", "1")
    client = TestClient(create_app(str(tmp_path)))
    big = "x" * (2 * 1024 * 1024)  # 2 MB > 1 MB cap
    r = client.post("/documents/write", json={"path": "a.txt", "content": big})
    assert r.status_code == 413


def test_fs_policy_name_denylist_blocks_keys_anywhere(tmp_path):
    # AGE-1 residual hardening: key files are protected by NAME regardless of the
    # directory spelling (drive-letter or UNC), independent of registered roots.
    from iron_jarvis.core import fs_policy

    assert fs_policy.is_protected_path(str(tmp_path / "anywhere" / ".secrets.key")) is True
    assert fs_policy.is_protected_path(str(tmp_path / "x" / ".vault.key.bak")) is True
    assert fs_policy.is_protected_path(str(tmp_path / "x" / "normal.txt")) is False
