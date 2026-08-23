"""Envelope routes: read a model's capability profile, start a quick probe.

Wave A2 of the IronCore integration (docs/IRONCORE-INTEGRATION.md, v1.201.0).
Two endpoints, provider+model addressed:

``GET /envelope/{provider}/{model:path}``
    Cloud/CLI providers answer the ``trusted`` profile by construction (full
    scores, never probed, zero loop-bending — see ``envelope/profile.py``).
    Local/custom providers answer the STORED measurement when one exists,
    else the conservative floor default, honestly labeled ``source="default"``.

``POST /envelope/{provider}/{model:path}/probe``
    Starts the quick battery IN THE BACKGROUND and returns ``{"started": true}``
    immediately (the ``POST /workflows/run`` convention). REFUSES honestly:
    400 for a trusted provider (nothing to measure), 400 for a provider with
    no configured base_url (nothing to probe), 409 while a probe for the same
    provider+model is already in flight. Publishes ``envelope.probe_started``
    and ``envelope.probe_completed`` events, both ``{provider, model, source}``
    (completed adds ``error`` when the battery itself blew up).

Design decisions, recorded here because each one had a wrong alternative:

* **Model ids carry ``/`` and ``:``** (``qwen3:30b``, ``x-ai/grok-code-fast-1``,
  ``qwen3:30b/instruct``). The route uses the ``{model:path}`` converter so
  both survive: ``:`` is a legal path character and the greedy path converter
  absorbs every ``/`` (Starlette backtracks past the literal ``/probe``
  suffix, same mechanism ``/creative/file/{name:path}`` relies on). The one
  unaddressable shape is a model id that itself ENDS in ``/probe`` — no such
  id exists in the wild, and it would misparse rather than escape.
* **The trusted verdict comes from ONE oracle** —
  ``d.platform.providers.is_trusted_provider(name)``
  (``providers/manager.py``, Wave A3, which names itself THE oracle every
  surface must consume). This route deliberately derives NOTHING itself: an
  earlier private copy here already disagreed with the manager on ``mock``
  and would have drifted on every future CLI. Consequence: ``mock`` is
  trusted (the offline suite and the first-run demo see zero envelope
  behavior, the manager's own reasoning), so ``POST /envelope/mock/.../probe``
  refuses through the trusted-400 branch, not the no-base-url one.
* **The probe transport is the adapter, DIRECTLY** —
  ``manager.get(provider, model)`` then ``adapter.complete(...)``. Not
  ``router.complete`` (failover, capability rewraps, mock semantics — a probe
  that silently measured a DIFFERENT provider would poison the profile) and
  not ``_one_shot_complete`` (cross-provider failover is its whole job). The
  adapter path records NOTHING: chat history, the ToolInvocation ledger, and
  Usage accounting all live in the layers this deliberately bypasses, so
  probe traffic cannot masquerade as user conversation or billable turns.
  Probes may pass ``response_format`` (the strict_json rung). Since Wave C
  (v1.203.0) the transport FORWARDS ``response_format`` / ``tool_choice`` /
  ``extra_body`` to ``adapter.complete`` — the additive guided-decoding
  kwargs — but only when the adapter's signature can accept them (checked
  once per adapter; ``**kwargs`` counts): a fake or third-party adapter
  still on the Wave-A three-argument shape must keep probing (the probes
  contract allows ignoring), never TypeError mid-battery. The profile's
  ``probe_generation`` is what records which semantics scored a battery —
  the binding Wave-A reviewer note under C2 in docs/IRONCORE-INTEGRATION.md.
* **Background = ``asyncio.create_task``, not ``d._spawn_bg``**: the spawn
  governor parks work FIFO under ``max_concurrent_sessions`` and returns
  ``None`` when parked — right for agent sessions, wrong for a bounded
  (~2min ceiling, ``runner.DEFAULT_TOTAL_TIMEOUT``) measurement that should
  never queue behind a long agent run or read as drain-refused. Strong refs
  are kept in a closure-level set so a pending task can never be GC'd.
* **Event types are plain strings** — the bus takes any string and
  ``workflow.waiting`` / ``slack.event`` / ``desktop.incident`` already ship
  without ``EventType`` members, so ``core/events.py`` (a shared file) is not
  touched.
* Everything blocking is off-loop (v1.153.1): the store read hops through
  ``asyncio.to_thread``; the battery's one disk write already does inside
  ``run_quick_battery``.
"""

from __future__ import annotations

import asyncio
import inspect
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException

from ...envelope.probes import ProbeReply
from ...envelope.profile import CapabilityProfile, trusted_profile
from ...envelope.runner import run_quick_battery
from ...envelope.seed import seed_profile
from ...envelope.store import load_profile
from ...providers.adapters.base import LLMMessage

#: Event types (plain strings — see the module docstring).
PROBE_STARTED = "envelope.probe_started"
PROBE_COMPLETED = "envelope.probe_completed"

_FLEET_PREFIX = "fleet-"


#: The guided-decoding kwargs the transport forwards when the adapter can
#: take them (the v1.203.0 additive ``LLMAdapter.complete`` parameters).
_GUIDED_KWARGS: tuple[str, ...] = ("response_format", "tool_choice", "extra_body")


def _accepted_guided_kwargs(adapter: Any) -> frozenset[str]:
    """Which of :data:`_GUIDED_KWARGS` this adapter's ``complete`` can accept
    — named parameters or a ``**kwargs`` catch-all. Computed ONCE per
    transport (not per trial), and an unsignaturable callable answers none:
    the conservative reading keeps a probe running instead of crashing it."""
    try:
        params = inspect.signature(adapter.complete).parameters
    except (TypeError, ValueError):  # builtins/mocks with no signature
        return frozenset()
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
        return frozenset(_GUIDED_KWARGS)
    return frozenset(name for name in _GUIDED_KWARGS if name in params)


def probe_transport(adapter: Any):
    """An async ``complete(messages, **kw) -> ProbeReply`` over ONE resolved
    adapter — the battery's transport seam. The adapter is resolved by the
    caller (``manager.get(provider, model)``), so the pin is total: no
    failover, no mock fallback, no router.

    Guided-decoding kwargs (Wave C): ``response_format`` / ``tool_choice`` /
    ``extra_body`` are FORWARDED to ``adapter.complete`` so the strict_json
    trials run under real constrained decoding — that forwarding is what the
    probe-generation bump (``CURRENT_PROBE_GENERATION == 2``) records. An
    adapter whose ``complete`` cannot accept them (a test fake or third-party
    shim on the Wave-A three-argument shape) gets them dropped instead of a
    mid-battery TypeError — the probes contract allows a transport to ignore
    kwargs it cannot honor, and the rung is then scored on what the bare
    prompt produces."""
    accepted = _accepted_guided_kwargs(adapter)

    async def transport(messages: list[dict[str, Any]], **kw: Any) -> ProbeReply:
        system_parts: list[str] = []
        msgs: list[LLMMessage] = []
        for m in messages:
            role = str(m.get("role") or "user")
            content = str(m.get("content") or "")
            if role == "system":
                system_parts.append(content)
            else:
                msgs.append(LLMMessage(role=role, content=content))
        guided = {
            name: kw[name]
            for name in _GUIDED_KWARGS
            if name in accepted and kw.get(name) is not None
        }
        resp = await adapter.complete(
            system="\n\n".join(system_parts),
            messages=msgs,
            tools=list(kw.get("tools") or []),
            **guided,
        )
        return ProbeReply(
            text=resp.text or "",
            tool_calls=[
                {"name": c.name, "arguments": dict(c.arguments or {})}
                for c in resp.tool_calls
            ],
            usage=dict(resp.usage or {}),
        )

    return transport


def _endpoint_base_url(d, provider: str) -> str | None:
    """The configured base_url behind a LOCAL provider, or ``None``.

    ``ollama``/``custom`` read the RAW config slots (the seeder normalizes a
    bare host itself); ``fleet-<id>`` reads the node's registry record. Every
    other name — a typo, a deleted fleet node — answers ``None``, which the
    probe route turns into an honest 400: no endpoint, no probe. (``mock``
    never reaches here: the trusted-provider refusal fires first.)"""
    cfg = getattr(d.platform, "config", None)
    if provider == "ollama":
        return (getattr(cfg, "ollama_base_url", "") or "").strip() or None
    if provider == "custom":
        return (getattr(cfg, "custom_base_url", "") or "").strip() or None
    if provider.startswith(_FLEET_PREFIX):
        fleet = getattr(d, "fleet", None) or getattr(d.platform, "fleet", None)
        if fleet is None:
            return None
        node = fleet.get(provider[len(_FLEET_PREFIX):])
        if node is None:
            return None
        return (getattr(node, "base_url", "") or "").strip() or None
    return None


def register(app: FastAPI, d) -> None:
    """Attach these routes to *app*; ``d`` is the create_app deps object."""

    #: Probes in flight, keyed (provider, model) — the 409 gate. Closure-level
    #: so each registered app owns its own set (the test-isolation shape).
    #: Reserved SYNCHRONOUSLY in the route (no await between check and add),
    #: so two concurrent POSTs cannot both pass the gate.
    inflight: set[tuple[str, str]] = set()
    #: Strong refs to running probe tasks — a pending asyncio task with no
    #: reference can be garbage-collected mid-flight.
    tasks: set[asyncio.Task] = set()

    async def _run_probe(
        key: tuple[str, str],
        provider: str,
        model: str,
        base_url: str,
        home: Path,
        stored: CapabilityProfile | None,
    ) -> None:
        """The background body: seed if nothing is stored, run the battery
        against the pinned adapter, publish the honest outcome. The battery
        itself absorbs per-probe failure; only a failure OUTSIDE it (adapter
        resolution, the seeder raising through its guards) lands in the
        ``error`` branch — reported as ``probe_failed`` with the exception,
        never swallowed."""
        source = "probe_failed"
        error: str | None = None
        try:
            base = stored
            if base is None:
                # seed_profile never raises by contract and answers None when
                # the endpoint ignored both introspection calls — the floor
                # default then keeps the job, honestly labeled.
                base = await seed_profile(provider, model, base_url)
            if base is None:
                base = CapabilityProfile(model_id=model, provider=provider)
            adapter = d.platform.providers.get(provider, model)
            session = await run_quick_battery(
                base, probe_transport(adapter), home=home
            )
            source = session.source
        except asyncio.CancelledError:  # daemon shutdown: release, no event
            inflight.discard(key)
            raise
        except Exception as exc:  # noqa: BLE001 — reported, never swallowed
            error = f"{type(exc).__name__}: {exc}"
        # Release BEFORE publishing: by the time probe_completed is observable
        # the key is free, so "wait for the event, probe again" cannot 409.
        inflight.discard(key)
        payload: dict[str, Any] = {"provider": provider, "model": model, "source": source}
        if error:
            payload["error"] = error
        try:
            await d.platform.event_bus.publish(PROBE_COMPLETED, payload)
        except Exception:  # noqa: BLE001 — a bus fault must not kill the task
            pass

    @app.get("/envelope/{provider}/{model:path}")
    async def envelope_get(provider: str, model: str) -> dict[str, Any]:
        """The capability profile for one provider+model. Trusted providers
        answer by construction; local providers answer the stored measurement
        or the floor. ``stored`` says which, so a UI can render provenance
        without re-deriving it.

        ``effective_window`` (v1.204.0, live finding): the profile's context
        fields are the FLOOR until a deep CTX battery measures them
        (8192/4096, honestly absent from ``measured_fields``), but the card
        rendered them raw — the user read the floor as the window the app
        plans with. This key carries the window chat ACTUALLY uses,
        ``{"value": int|null, "source": "pin"|"measured"|"endpoint"|
        "default"}``, resolved by THE one ladder
        (``chat_turn._context_window_source`` — the same function both chat
        lanes and the agent runtime plan through). Deliberately NOT re-derived
        here: two window ladders drift, same as two trusted oracles did."""
        model = model.strip()
        if not model:
            raise HTTPException(status_code=400, detail="model id required after the provider")
        # Lazy import, the routes/chat.py idiom for chat_turn helpers (the
        # module is heavy and this avoids a cycle at registration time).
        from ..chat_turn import _context_window_source

        # The measured rung stats/reads the envelope store — off the loop.
        value, source = await asyncio.to_thread(
            _context_window_source, d, provider, model
        )
        effective_window = {"value": value, "source": source}
        if d.platform.providers.is_trusted_provider(provider):
            return {
                "provider": provider,
                "model": model,
                "trusted": True,
                "stored": False,
                "profile": trusted_profile(provider, model).to_dict(),
                "effective_window": effective_window,
            }
        home = Path(d.platform.config.home)
        # load_profile never raises, but it reads disk — off the loop (v1.153.1).
        stored = await asyncio.to_thread(load_profile, home, provider, model)
        profile = stored if stored is not None else CapabilityProfile(
            model_id=model, provider=provider
        )
        return {
            "provider": provider,
            "model": model,
            "trusted": False,
            "stored": stored is not None,
            "profile": profile.to_dict(),
            "effective_window": effective_window,
        }

    @app.post("/envelope/{provider}/{model:path}/probe")
    async def envelope_probe(provider: str, model: str) -> dict[str, Any]:
        """Start the quick battery in the background. Refusals are honest and
        specific — see the module docstring for the three shapes."""
        model = model.strip()
        if not model:
            raise HTTPException(status_code=400, detail="model id required after the provider")
        if d.platform.providers.is_trusted_provider(provider):
            # Wording chosen to read honestly for mock too: "gets the trusted
            # envelope by construction" is the manager's verdict verbatim,
            # whereas "is fully capable" would be a strange claim about the
            # offline mock (trusted so the offline suite and demo see zero
            # envelope behavior, not because it is a frontier model).
            raise HTTPException(
                status_code=400,
                detail=(
                    f"nothing to measure — provider '{provider}' gets the trusted "
                    "envelope by construction; probing exists for local/custom endpoints"
                ),
            )
        base_url = _endpoint_base_url(d, provider)
        if not base_url:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"provider '{provider}' has no configured base_url to probe — "
                    "connect the endpoint on the Connections page first"
                ),
            )
        key = (provider, model)
        if key in inflight:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"a probe for {provider}/{model} is already running — "
                    "wait for envelope.probe_completed"
                ),
            )
        inflight.add(key)  # reserved with no await since the check — no race window
        try:
            home = Path(d.platform.config.home)
            stored = await asyncio.to_thread(load_profile, home, provider, model)
            # The STARTED source is what the record says right now ("default"
            # when nothing is stored — a seed that has not run yet is not
            # claimed). The COMPLETED event carries what the battery earned.
            source = stored.source if stored is not None else "default"
            await d.platform.event_bus.publish(
                PROBE_STARTED, {"provider": provider, "model": model, "source": source}
            )
            task = asyncio.create_task(
                _run_probe(key, provider, model, base_url, home, stored)
            )
            tasks.add(task)
            task.add_done_callback(tasks.discard)
        except BaseException:
            inflight.discard(key)  # a failed launch must not wedge the 409 gate
            raise
        return {"started": True, "provider": provider, "model": model, "source": source}
