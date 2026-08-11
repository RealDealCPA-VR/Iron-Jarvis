"""An unconnected model NEVER answers with the mock (v1.162.0).

THE REPORT. A user's chat kept returning:

    "Done. Wrote RESULT.md summarizing the task."

with the app's own honesty note appended underneath saying no document tool had
run and that the file did not exist. Their default provider was a local fleet
endpoint (a DGX Spark over Tailscale) that was unreachable; the ledger showed
``provider.downgraded {"requested": "fleet-custom", "used": "mock"}``.

WHY THE OLD BEHAVIOUR LOOKED DEFENSIBLE AND WASN'T. The router drew a line
between a real call that FAILS mid-flight (never substitute — that is
fabrication) and a provider that is NOT CONNECTED before the call (downgrade to
the offline mock and raise a banner). That line is invisible from the user's
seat: both produce a confident sentence describing work that never happened.
Only an EXPLICIT pick was refused, and only under the strict pin — while chat
sends no provider at all, so every chat turn took the default route, which is
precisely the branch that fell through to the mock.

THE PART THAT MAKES IT WORSE THAN A WRONG SENTENCE. The mock does not merely
claim it wrote the file. Handed a write_file tool it EMITS a write_file call
(`providers/adapters/mock.py`), so with a document tool armed the fabrication
lands on DISK, in a workspace holding real client tax documents. The user only
noticed because no tool was armed that turn, so the ledger-backed honesty note
caught the claim.

NO AUTOMATIC SUBSTITUTE, BY THE USER'S EXPLICIT CHOICE. Five real providers
were connected and could have absorbed the turn, but silently moving a chat off
a local endpoint onto a cloud API is a privacy decision about client data, not
a routing fallback. Asked directly, the user chose "never auto-switch — just
tell me". An explicit pick in the UI still routes exactly as before.

WHAT STILL USES THE MOCK: a mock the user actually chose. A fresh install ships
``default_provider = "mock"``, so the offline demo and this whole test suite are
untouched — `downgraded` is only ever set when a REAL provider was wanted and is
not available.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.platform import build_platform
from iron_jarvis.providers.adapters.base import LLMMessage

MOCK_FABRICATION = "Done. Wrote RESULT.md"


def _only_mock_is_connected(platform, default_provider: str):
    """Model the user's box: a real default configured, nothing real reachable.

    Shadows `available` on the manager instance rather than deleting keys, so
    the test does not depend on which CLIs happen to be installed on the machine
    running it — the same divergence `_isolate_opencode_store` exists to stop.
    """
    platform.config.default_provider = default_provider
    platform.providers.available = lambda name: name == "mock"
    return platform


def _client(tmp_path, default_provider: str):
    client = TestClient(create_app(str(tmp_path)))
    _only_mock_is_connected(client.app.state.platform, default_provider)
    return client


# --------------------------------------------------------------------------- #
# (1) THE REPORTED BUG, end to end through the route chat actually calls.
# --------------------------------------------------------------------------- #
def test_chat_refuses_when_the_DEFAULT_provider_is_not_connected(tmp_path):
    """Chat sends NO provider, so this is the default route — the branch that
    produced the user's fabricated reply."""
    with _client(tmp_path, "fleet-custom") as client:
        r = client.post("/chat", json={"messages": [{"role": "user", "content": "summarize my notes"}]})

    assert r.status_code != 200, f"a turn was answered by nothing real: {r.text[:300]}"
    assert MOCK_FABRICATION not in r.text, "the mock's scripted answer reached the user"
    assert "isn't connected" in r.text, f"the error does not say what is wrong: {r.text[:300]}"


def test_the_error_names_the_provider_that_is_down(tmp_path):
    """"Something went wrong" sends the user hunting. The message has to name the
    thing to fix — theirs was a Tailscale endpoint that had gone offline, and
    nothing in the reply pointed at it."""
    with _client(tmp_path, "fleet-custom") as client:
        r = client.post("/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    assert "fleet-custom" in r.text


def test_a_mock_the_user_CHOSE_still_answers(tmp_path):
    """The offline path must survive: a fresh install ships default_provider
    'mock', and refusing there would break first-run and every offline demo."""
    with _client(tmp_path, "mock") as client:
        r = client.post("/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 200, r.text
    assert (r.json().get("reply") or "").strip()


def test_an_explicit_mock_pick_still_answers(tmp_path):
    with _client(tmp_path, "fleet-custom") as client:
        r = client.post("/chat", json={"messages": [{"role": "user", "content": "hi"}], "provider": "mock"})
    assert r.status_code == 200, r.text


# --------------------------------------------------------------------------- #
# (2) THE HALF THAT REACHES THE DISK.
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_an_unconnected_model_can_never_fabricate_a_FILE(tmp_path):
    """The mock answers a write_file-armed request by EMITTING a write_file
    call, so the old downgrade could write a fabricated RESULT.md into a
    workspace that holds real client documents. This assertion keeps that shut."""
    platform = _only_mock_is_connected(build_platform(str(tmp_path)), "fleet-custom")

    with pytest.raises(Exception, match="isn't connected"):
        await platform.router.complete(
            system="",
            messages=[LLMMessage(role="user", content="write up the result")],
            tools=platform.registry.specs(["write_file"]),
        )
    assert not list(tmp_path.rglob("RESULT.md")), "a fabricated file was written"


# --------------------------------------------------------------------------- #
# (3) BOTH LANES. The streaming one is the one the user watches.
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_the_streaming_lane_refuses_too(tmp_path):
    """A fabricated STREAM is the most convincing lie the app can tell — tokens
    arriving one by one read as a model genuinely working. MIRROR NOTE: the
    guard is duplicated in router.complete/stream by design."""
    platform = _only_mock_is_connected(build_platform(str(tmp_path)), "fleet-custom")

    with pytest.raises(Exception, match="isn't connected"):
        async for _ in platform.router.stream(
            system="", messages=[LLMMessage(role="user", content="hi")], tools=[]
        ):
            pass


# --------------------------------------------------------------------------- #
# (4) THE SIGNAL SURVIVES THE REFUSAL.
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_the_connect_a_model_banner_still_fires(tmp_path):
    """The refusal replaced the mock ANSWER, not the notification. The dashboard
    banners on provider.downgraded, and losing it would leave the user holding an
    error with no route to the fix."""
    platform = _only_mock_is_connected(build_platform(str(tmp_path)), "fleet-custom")

    seen: list[tuple[str, dict]] = []
    original = platform.event_bus.publish

    async def _spy(etype, payload=None, **kw):
        seen.append((str(getattr(etype, "value", etype)), dict(payload or {})))
        return await original(etype, payload, **kw)

    platform.event_bus.publish = _spy

    with pytest.raises(Exception, match="isn't connected"):
        await platform.router.complete(
            system="", messages=[LLMMessage(role="user", content="hi")], tools=[]
        )

    downgrades = [p for t, p in seen if t == "provider.downgraded"]
    assert downgrades, f"the connect-a-model banner never fired: {seen}"
    assert downgrades[0].get("requested") == "fleet-custom"
    assert "not connected" in (downgrades[0].get("reason") or "")
    # "used" must NOT claim a mock answered — nothing answered at all.
    assert downgrades[0].get("used") == "none", downgrades[0]
