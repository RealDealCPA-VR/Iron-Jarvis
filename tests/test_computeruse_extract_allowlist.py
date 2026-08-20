"""web_extract honours the ACTION allowlist like every sibling CU tool.

Regression for the finding: ``WebExtractTool.execute`` checked only the opt-in
gate and then called ``browser.extract`` directly, so an install that narrowed
``action_allowlist`` to exclude ``"extract"`` still got extraction performed.
"""

from __future__ import annotations

import pytest

from iron_jarvis.computeruse.approvals import ApprovalQueue
from iron_jarvis.computeruse.browser import FakeBrowser
from iron_jarvis.computeruse.policy import ComputerUsePolicy
from iron_jarvis.computeruse.tools import CUContext, WebExtractTool
from iron_jarvis.core.db import init_db, make_engine


class _Ctx:  # minimal ToolContext stand-in
    workspace = None
    session_id = "s1"
    agent_run_id = "r1"


def _pages() -> dict[str, dict]:
    return {
        "https://example.com/dashboard": {
            "text": "Welcome back, your balance is $20.",
            "a11y": [
                {
                    "role": "text",
                    "name": "balance",
                    "text": "your balance is $20",
                }
            ],
        }
    }


def _cu(tmp_path, *, actions: list[str]) -> CUContext:
    engine = make_engine(tmp_path / "cu.db")
    init_db(engine)
    policy = ComputerUsePolicy(
        enabled=True,
        domain_allowlist=["example.com"],
        action_allowlist=actions,
    )
    return CUContext(
        policy=policy,
        browser=FakeBrowser(_pages()),
        approvals=ApprovalQueue(engine),
    )


@pytest.mark.asyncio
async def test_web_extract_denied_when_extract_off_the_allowlist(tmp_path):
    """A narrowed allowlist without 'extract' must REFUSE — and not read the page."""
    cu = _cu(tmp_path, actions=["navigate", "read", "screenshot", "wait"])
    await cu.browser.navigate("https://example.com/dashboard")

    res = await WebExtractTool(cu).execute({"text": "balance"}, _Ctx())

    assert res.ok is False
    assert "action kind not on allowlist: 'extract'" in (res.error or "")
    # Fail closed: the browser was never asked for the element's text.
    assert "$20" not in (res.output or "")
    assert "$20" not in str(res.data or {})


@pytest.mark.asyncio
async def test_web_extract_still_works_on_the_default_allowlist(tmp_path):
    """'extract' is a READ_ONLY_KIND and on the DEFAULT allowlist: still extracts."""
    default_actions = list(ComputerUsePolicy().action_allowlist)
    assert "extract" in default_actions
    cu = _cu(tmp_path, actions=default_actions)
    await cu.browser.navigate("https://example.com/dashboard")

    res = await WebExtractTool(cu).execute({"text": "balance"}, _Ctx())

    assert res.ok is True, res.error
    assert "your balance is $20" in res.output
    assert (res.data or {}).get("raw") == "your balance is $20"
