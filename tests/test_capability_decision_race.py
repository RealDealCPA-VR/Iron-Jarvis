"""A capability proposal is DECIDED ONCE, and the world agrees with the record.

``approve`` has held a process-local claim since v1.185.0 so two clicks cannot
both create the tool. ``reject`` held nothing — and it is the more dangerous
half, because ``approve`` creates the capability inside ``_apply`` BEFORE it
reopens a transaction to stamp APPROVED. A reject committing in that window left
the tool persisted and registered, the row reading "rejected", and the approver
told the approval failed (409): a live capability the user had explicitly turned
down. The routes are sync ``def`` handlers, which FastAPI runs in worker
THREADS, so the interleaving is genuine rather than theoretical.

Driven deterministically — the reject is fired FROM INSIDE ``_apply`` and joined
before the approval continues, so the window is entered on every run instead of
on an unlucky one. No sleeps.
"""

from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from iron_jarvis.capability import store as _cap_store
from iron_jarvis.capability.routes import register as register_capability
from iron_jarvis.platform import build_platform


@pytest.fixture
def platform(tmp_path):
    return build_platform(str(tmp_path))


def _cap_client(platform) -> TestClient:
    app = FastAPI()
    register_capability(app, SimpleNamespace(platform=platform))
    return TestClient(app)


def _file_proposal(platform, name="wc_lines") -> str:
    record = platform.capabilities.create(
        kind="tool",
        name=name,
        rationale="counting lines came up four times today",
        scope="a tax folder survey",
        spec={"command": ["wc", "-l", "{path}"]},
    )
    assert record is not None
    return record.id


def test_a_reject_landing_mid_apply_cannot_orphan_a_created_tool(platform):
    """THE RECORD AND THE WORLD MUST AGREE. The assertion that matters is not
    which side wins the race — it is that a proposal reading "rejected" never
    coexists with a registered tool. Without the claim on ``reject`` the reject
    commits, ``approve``'s second transaction finds the row decided and raises,
    and ``custom:wc_lines`` is live anyway."""
    pid = _file_proposal(platform)
    store = platform.capabilities
    real_apply = store._apply
    reject_outcome: dict[str, str] = {}

    def _apply_then_reject(**kw):
        # Exactly the bug's window: approve's first txn has closed, the APPROVED
        # stamp has not been written, and the capability is about to be created.
        def _reject() -> None:
            try:
                store.reject(pid)
                reject_outcome["result"] = "rejected"
            except ValueError as exc:
                reject_outcome["result"] = f"refused: {exc}"

        thread = threading.Thread(target=_reject)
        thread.start()
        thread.join(timeout=30)
        assert not thread.is_alive(), "the claim must refuse, never block"
        return real_apply(**kw)

    store._apply = _apply_then_reject

    approve_error: ValueError | None = None
    result = None
    try:
        _record, result = store.approve(pid)
    except ValueError as exc:  # the 409 the approver used to get
        approve_error = exc

    status = store.get(pid).status
    tool_exists = bool(store.known_tool("wc_lines"))

    assert reject_outcome["result"].startswith("refused"), reject_outcome
    assert approve_error is None, f"the approver was refused: {approve_error}"
    assert result is not None and result.ok
    assert status == "approved"
    # The invariant, stated as an invariant: a decided-rejected proposal may
    # never leave a callable tool behind.
    assert tool_exists is (status == "approved"), (
        f"proposal is {status} but the tool exists={tool_exists} — "
        "a capability the user turned down"
    )


def test_a_reject_arriving_under_a_live_claim_is_an_honest_409(platform):
    """The route half. A reject that cannot take the claim must answer the same
    409 a second approver gets and leave the row PENDING — never a silent status
    flip behind a decision already in flight."""
    pid = _file_proposal(platform)
    client = _cap_client(platform)

    with _cap_store._claimed(pid):
        response = client.post(f"/capability/proposals/{pid}/reject")

    assert response.status_code == 409, response.text
    assert "being decided" in response.json()["detail"]
    assert platform.capabilities.get(pid).status == "pending"


def test_reject_releases_its_claim(platform):
    """A leaked claim would be its own outage: every later decision on that
    proposal would 409 forever. Both the success and the refusal path release."""
    pid = _file_proposal(platform)
    store = platform.capabilities

    assert store.reject(pid).status == "rejected"
    assert pid not in _cap_store._CLAIMS

    # And the refusal path: an already-decided proposal still releases.
    with pytest.raises(ValueError):
        store.reject(pid)
    assert pid not in _cap_store._CLAIMS
