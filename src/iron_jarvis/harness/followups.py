"""NEXT STEP: up to two suggested follow-ups after a Build turn (v1.377.0, IJ
harness part 2 Phase 6).

The chat's follow-ups (``daemon/routes/followups.py``, v1.323.0) for a Build
session — the SAME switch (``chat_followups``, "Suggest follow-up questions",
OFF by default: off answers ``reason: "off"`` and makes NO model call), the
same prompt, parser and prompt-injection screen. What differs, and why:

* THE SESSION'S OWN MODEL AND ACCOUNT. The call runs on the pair the session's
  turns run on (``routes/build._turn_pair``), under the session's Iron-Proxy
  account pin and the model hold (``providers/hold``), PINNED — nothing
  stands in for that model, nothing moves to another provider — and in the
  session's Local only scope. The chat's route reads the provider the reply
  used from the page; a Build session's is the daemon's own record.
* BILLED TO THE SESSION (its usage line counts the extra call — the
  library's and Compact's ``_bill``).
* AT MOST :data:`MAX_BUILD_SUGGESTIONS` (two): a coding session's next step
  is one press, not a menu.
* NEVER WHILE A TURN RUNS, never for the demo model, never a 500: a model
  failure, a refusal or a timeout is ``{suggestions: [], reason: <words>}``.

A press on a suggestion only puts its words in the box (the screen's rule —
it never sends).
"""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from typing import Any

log = logging.getLogger(__name__)

#: A Build session gets at most this many suggestions.
MAX_BUILD_SUGGESTIONS = 2
BUSY_REASON = "A reply is still running."
NO_SESSION_REASON = "no such Build session"


async def suggest(platform: Any, thread_id: str, *, running: bool = False) -> dict[str, Any]:
    """``{suggestions: [str], reason: str}`` for the session's newest reply.
    ``running``: a turn of the session is running or starting (the route
    knows) — nothing is asked then. Never raises for a model failure."""
    from ..daemon.routes import followups as F
    from ..providers import egress as _egress
    from ..providers.adapters.base import LLMMessage
    from ..providers.hold import reset_hold, set_hold
    from . import sessions as S

    cfg = getattr(platform, "config", None)
    if not bool(getattr(cfg, "chat_followups", False)):
        return {"suggestions": [], "reason": F.OFF_REASON}
    if running:
        return {"suggestions": [], "reason": BUSY_REASON}
    engine = platform.engine
    row = await asyncio.to_thread(S.get_row, engine, thread_id)
    if row is None:
        return {"suggestions": [], "reason": NO_SESSION_REASON}
    setup, history = await asyncio.to_thread(lambda: (S._setup(row), S.history(row)))
    window = F.conversation_window([SimpleNamespace(**m) for m in history])
    if not window or window[-1]["role"] != "assistant":
        return {"suggestions": [], "reason": F.NOTHING_REASON}
    last_user = next((m["content"] for m in reversed(window) if m["role"] == "user"), "")

    # THE SESSION'S OWN MODEL ON ITS OWN ACCOUNT (Compact's way).
    from ..daemon.routes.build import _turn_pair, turn_account
    from ..iron_proxy.pin import reset_pin, set_pin
    from ..projects.locate import project_for_path

    try:
        pin, account_provider = await asyncio.to_thread(turn_account, platform, thread_id, setup)
    except Exception as exc:  # noqa: BLE001 — the account's own words (an HTTPException)
        return {"suggestions": [], "reason": str(getattr(exc, "detail", "") or exc)}
    project = await asyncio.to_thread(project_for_path, engine, S.checkout_of(row))
    asked = SimpleNamespace(
        provider=account_provider or str(setup.get("provider") or ""),
        model=str(setup.get("model") or ""),
    )
    provider, model = _turn_pair(platform, asked, project)
    if not provider or provider.strip().lower() == "mock":
        return {"suggestions": [], "reason": F.MOCK_REASON}
    router = getattr(platform, "router", None)
    if router is None:
        return {"suggestions": [], "reason": F.NO_ROUTER_REASON}
    local_only = setup.get("local_only") is True
    if local_only and (provider.lower() == "auto" or not _egress.stays_on_this_network(
        provider, getattr(platform, "providers", None), cfg, model,
    )):
        return {"suggestions": [], "reason": F.LOCAL_ONLY_REASON}

    pin_token = set_pin(pin) if pin is not None else None
    hold_token = set_hold(provider, model)
    bound = asyncio.timeout(F.FOLLOWUP_TIMEOUT_S)
    try:
        async with bound:
            with _egress.local_only_scope(local_only):
                route = await router.complete(
                    provider=provider, model=model or None, system=F.SYSTEM_PROMPT,
                    messages=[LLMMessage(role="user", content=F.build_prompt(window))],
                    tools=[], task_class="chat", pin=True,
                )
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 — a model failure is a reason, never a 500
        if isinstance(exc, TimeoutError) and bound.expired():
            return {"suggestions": [], "reason": "The model took too long, so there are no suggestions this time."}
        log.info("build follow-ups: the model call failed (%s)", type(exc).__name__)
        return {"suggestions": [], "reason": "The model did not answer, so there are no suggestions this time."}
    finally:
        reset_hold(hold_token)
        if pin_token is not None:
            reset_pin(pin_token)

    if F._is_mock(route):
        return {"suggestions": [], "reason": F.MOCK_REASON}
    from .library import _bill

    # Billed to the session (the model ran) — a database write, off the loop.
    await asyncio.to_thread(_bill, platform, route, thread_id)
    text = str(getattr(getattr(route, "response", None), "text", "") or "")
    try:
        got = await asyncio.to_thread(F._finish, text, last_user)
    except Exception:  # noqa: BLE001
        got = []
    got = got[:MAX_BUILD_SUGGESTIONS]
    if not got:
        return {"suggestions": [], "reason": F.EMPTY_REASON}
    return {"suggestions": got, "reason": ""}


__all__ = ["BUSY_REASON", "MAX_BUILD_SUGGESTIONS", "suggest"]
