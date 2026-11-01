"""v1.422.0 review (Wave 2 "Never lose work", B1): what the patience must
never wait out, read against the failures the adapters really raise.

THE DEFECT (found in review). ``harness/patience.why_waited`` read "the
account's own usage limit" ONLY from the typed facts (``failure_facts.limit``:
a 429 marked not transient, or one whose ``Retry-After`` is five minutes or
more). Codex's ``usageLimitExceeded`` with no rate-limit snapshot (its reset
not known) is a 429 with NO ``retry_after`` — read as "busy", so a coding
reply on a ChatGPT account at its Codex usage limit waited 1 min 50 s and
asked Codex four more times before it failed, its live line saying "Codex is
busy (too many requests). Trying again…". The same for an OpenAI key out of
quota (``insufficient_quota``, a 429). Now a failure whose own words say an
ACCOUNT's limit is never waited out; a passing rate limit still is.

Driven through the REAL Codex error mapping (``codex_native.protocol.
turn_failure``) and the real stream lane; only the model is a double.
"""

from __future__ import annotations

import pytest

from iron_jarvis.harness import patience as P
from iron_jarvis.providers.adapters.base import ProviderError
from iron_jarvis.providers.adapters.codex_native import protocol as CX
from tests.test_build_patience_v14220 import _body, _stream, env, waits  # noqa: F401 — fixtures


def _codex(variant: str, message: str) -> Exception:
    return CX.turn_failure({"message": message, "codexErrorInfo": variant}, emitted=False).error


@pytest.mark.parametrize("exc", [
    # Codex's own usage limit, its reset NOT known (no snapshot).
    _codex("usageLimitExceeded", "You've hit your usage limit. Upgrade to Pro or try again later."),
    # An OpenAI key out of quota (a 429 that is billing, not traffic).
    ProviderError(
        "openai API error 429: You exceeded your current quota, please check your plan and billing"
        " details. (insufficient_quota)", status_code=429,
    ),
    # Claude Code's own limit words.
    ProviderError("claude-cli: You've hit your session limit · resets 3:45pm", status_code=429),
    ProviderError("claude-cli: You're out of usage credits", status_code=429),
])
def test_an_accounts_own_limit_is_never_waited_out(exc):
    assert P.why_waited(exc) == ""


@pytest.mark.parametrize("exc", [
    # A passing rate limit (per minute) is still waited out.
    _codex("rateLimitExceeded", "Rate limit reached for requests per min. Please try again in 2s."),
    ProviderError("anthropic API error 429: rate_limit_error: Number of request tokens has exceeded"
                  " your per-minute rate limit", status_code=429),
    ProviderError("anthropic API error 529: overloaded_error", status_code=529),
])
def test_a_passing_limit_is_still_waited_out(exc):
    assert P.why_waited(exc) in ("busy", "overloaded")


async def test_a_coding_reply_at_its_codex_usage_limit_fails_at_once(env, waits):  # noqa: F811
    platform, ws, wire = env
    err = _codex("usageLimitExceeded", "You've hit your usage limit. Upgrade to Pro or try again later.")
    model = wire([err, err, err, err, err, "never asked"], who=("codex-cli", "gpt-5.5"))
    frames = await _stream(platform, _body(ws), harness=True)
    assert [e for e, _ in frames if e == "error"] == ["error"]
    assert not [d for e, d in frames if e == P.FRAME], "the live line said it would try again"
    assert waits == [] and len(model.calls) == 1


# --------------------------------------------------------------------------- #
# ONE alert per failed round, not one per try (the real router)
# --------------------------------------------------------------------------- #
#
# THE DEFECT (found in review). Every try of a round that the patience asks
# again goes through the router, and the router publishes ``provider.failed``
# for each failed try — a DEFAULT ALERT (``comm.notifier``): "This PC" toasts
# it and a phone gets it. A round that waited out a 529 storm sent up to five
# "Claude Code couldn't answer. Press Retry." notices in two minutes (before
# the patience, a failed reply sent one). Now a try the patience made AFTER a
# failure carries ``retry`` on its ``provider.failed`` payload (the ledger
# keeps every one) and the notifier says only the first failure of the round.


async def test_a_round_that_waited_says_its_failure_once_not_once_per_try(tmp_path, monkeypatch, waits):  # noqa: F811
    import json

    from sqlmodel import select

    from iron_jarvis.comm.channels import DesktopChannel
    from iron_jarvis.core.db import session_scope
    from iron_jarvis.core.models import EventRecord
    from iron_jarvis.daemon.app import create_app
    from tests.test_build_accounts_v1368 import Behaviour, isolate, wire_doubles
    from tests.test_build_sessions_v1367 import asgi, frames_of, open_turn

    iso = isolate(tmp_path, monkeypatch)
    next(iso)
    try:
        monkeypatch.setattr(DesktopChannel, "sink", lambda title, msg: None)
        app = create_app(str(tmp_path / "home"))
        work = tmp_path / "work"
        work.mkdir()
        beh = wire_doubles(app, monkeypatch, Behaviour(["Done after the storm."]))
        # Each router call is the adapter tried three times (the router's own
        # same-adapter retries): two router calls fail, the third answers.
        beh.overload["claude-cli"] = 6
        status, view = await asgi(app, "POST", "/build/sessions", {
            "folder": str(work), "provider": "claude-cli", "model": "claude-sonnet-4-5",
        })
        assert status == 201, view
        status, raw = await open_turn(app, view["id"], {"text": "hi", "turn_id": "t-alert"})
        assert status == 200 and frames_of(raw)[-1][0] == "done", raw[-300:]
        assert waits == [5.0, 15.0]
        with session_scope(app.state.platform.engine) as db:
            failed = [json.loads(r.payload_json) for r in db.exec(select(EventRecord)).all()
                      if r.type == "provider.failed"]
        # The ledger keeps every failed try...
        assert len(failed) == 2, failed
        # ...and only the round's FIRST failure is a notice.
        notifier = app.state.platform.notifier
        said = [notifier.on_event({"type": "provider.failed", "payload": p}) for p in failed]
        assert sum(s is not None for s in said) == 1, failed
        assert [p for p, s in zip(failed, said) if s is not None][0].get("retry") is None
        assert [p.get("retry") for p, s in zip(failed, said) if s is None] == [2], failed
    finally:
        with __import__("contextlib").suppress(StopIteration):
            next(iso)


async def test_both_lanes_mark_the_tries_after_the_first(waits):  # noqa: F811
    """Lock-step: the stream lane's ``patient_frames`` and the POST lane's
    ``patient_call`` mark the SAME tries (the first never; every later one
    with its number) — what the router reads for ``provider.failed``."""
    from iron_jarvis.providers import retry_round as RT

    seen_post: list[int] = []

    async def call():
        seen_post.append(RT.attempt())
        if len(seen_post) < 3:
            raise ProviderError("x API error 503: unavailable", status_code=503)
        return "answer"

    assert await P.patient_call(call, patient=True) == "answer"
    assert seen_post == [0, 2, 3]
    assert RT.attempt() == 0, "the mark leaked out of the round"

    seen_stream: list[int] = []

    async def frames():
        seen_stream.append(RT.attempt())
        if len(seen_stream) < 3:
            raise ProviderError("x API error 503: unavailable", status_code=503)
        yield {"type": "final", "response": None}

    out = [f async for f in P.patient_frames(frames)]
    assert seen_stream == [0, 2, 3]
    assert out[-1]["type"] == "final"
    assert RT.attempt() == 0
