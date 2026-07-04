"""Computer-use mindblowing slice: web_look (vision), live view, seeing approvals."""

from __future__ import annotations

import base64

import pytest

from iron_jarvis.computeruse.approvals import ApprovalQueue
from iron_jarvis.computeruse.browser import FakeBrowser
from iron_jarvis.computeruse.policy import ComputerUsePolicy
from iron_jarvis.computeruse.tools import CUContext, WebActionTool, WebLookTool, computeruse_tools
from iron_jarvis.core.db import make_engine, init_db


class _Route:
    def __init__(self, text):
        class _R:  # mimics RouteResult.response
            pass

        self.response = _R()
        self.response.text = text


class _FakeRouter:
    def __init__(self, text="a login form with a blue Submit button"):
        self.text = text
        self.last = None

    async def complete(self, **kw):
        self.last = kw
        return _Route(self.text)


def _cu(tmp_path, *, enabled=True, actions=None, router=None):
    engine = make_engine(tmp_path / "cu.db")
    init_db(engine)
    policy = ComputerUsePolicy(
        enabled=enabled,
        domain_allowlist=["example.com"],
        action_allowlist=actions or ["navigate", "read", "extract", "screenshot", "click", "type"],
    )
    return CUContext(
        policy,
        FakeBrowser({"https://example.com": "hello world"}),
        ApprovalQueue(engine),
        router_resolver=(lambda: router) if router else None,
    )


class _Ctx:  # minimal ToolContext stand-in
    workspace = None
    session_id = "s1"
    agent_run_id = "r1"


@pytest.mark.asyncio
async def test_web_look_sends_screenshot_to_vision_model(tmp_path):
    router = _FakeRouter()
    cu = _cu(tmp_path, router=router)
    res = await WebLookTool(cu).execute({"question": "what buttons exist?"}, _Ctx())
    assert res.ok, res.error
    assert "Submit button" in res.output
    imgs = router.last["messages"][0].images
    assert imgs and imgs[0]["media_type"] == "image/png"
    base64.b64decode(imgs[0]["data_b64"])  # valid b64


@pytest.mark.asyncio
async def test_web_look_without_router_is_honest(tmp_path):
    cu = _cu(tmp_path, router=None)
    res = await WebLookTool(cu).execute({}, _Ctx())
    assert not res.ok and "vision is not wired" in res.error


@pytest.mark.asyncio
async def test_web_look_respects_disabled_and_allowlist(tmp_path):
    cu = _cu(tmp_path, enabled=False, router=_FakeRouter())
    res = await WebLookTool(cu).execute({}, _Ctx())
    assert not res.ok  # disabled refuses

    cu2 = _cu(tmp_path, actions=["navigate", "read"], router=_FakeRouter())
    res2 = await WebLookTool(cu2).execute({}, _Ctx())
    assert not res2.ok and "denied" in res2.error  # screenshot not allowlisted


@pytest.mark.asyncio
async def test_snap_updates_live_view(tmp_path):
    cu = _cu(tmp_path)
    assert cu.last_screen is None
    b64 = await cu.snap()
    assert b64 and cu.last_screen and cu.last_screen["image_b64"] == b64


@pytest.mark.asyncio
async def test_sensitive_action_approval_carries_screenshot(tmp_path):
    cu = _cu(tmp_path)
    # 'type' into a password-ish field trips the sensitive gate -> approval req.
    res = await WebActionTool(cu).execute(
        {"kind": "type", "name": "password", "value": "hunter2"}, _Ctx()
    )
    assert not res.ok and "approval required" in (res.error or "")
    pending = cu.approvals.pending()
    assert pending and pending[0].screenshot_b64  # the human SEES the page


def test_factory_includes_web_look(tmp_path):
    cu = _cu(tmp_path)
    names = {t.name for t in computeruse_tools(cu)}
    assert "web_look" in names
