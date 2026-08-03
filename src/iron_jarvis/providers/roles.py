"""Step-aware role → model resolution (v1.135.0).

Multi-step local runs are not one workload: planning and synthesis deserve the
strongest local model, per-document extraction the cheap/fast one, image checks
the vision one — the user's DGX gateway serves exactly those as distinct model
names behind one provider. ``config.model_roles`` maps a step ROLE ("plan",
"synthesize", "extract", "judge", "vision") to ``"provider:model"`` or a bare
``"model"`` (same provider), and :func:`resolve_role` turns that mapping into
the ``(provider, model)`` pair a call site should request.

This is a RESOLUTION helper, not a router bypass: callers feed the resolved
pair into ``router.complete``/``router.stream`` exactly as they fed their old
pair, so the router's failover, health/circuit-breaker, strict-pin, and
v1.131.0 prompted-tools-wrap semantics all stay intact.

Fail-open by construction: an unmapped role, a blank value, an unknown or
unavailable provider, a probe error — ANY miss — returns the caller's own
fallbacks unchanged, so an empty/absent ``model_roles`` keeps every call site
byte-for-byte identical to before. :func:`resolve_role` never raises. Callers
resolve each role ONCE per run (not per call), so the mapped-but-unavailable
warning below logs once per run; the same text rides back on the result's
``note`` for callers that want to publish it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

_log = logging.getLogger("iron_jarvis.roles")

#: The roles the wired call sites resolve (documentation, not an enforced
#: enum — an unknown key in the mapping is simply never looked up).
KNOWN_ROLES = ("plan", "synthesize", "extract", "judge", "vision")


@dataclass(frozen=True)
class RoleResolution:
    """The outcome of one role lookup.

    ``applied`` is True only when the mapping ACTUALLY changed the
    ``(provider, model)`` pair — the flag call sites key their audit-event
    extras (and any extra kwargs to a fake-friendly router) on, so the dormant
    path stays byte-for-byte identical. ``note`` carries the once-per-run
    "role_fallback" message when a MAPPED role missed (unknown/unavailable
    provider); it is empty for the plain unmapped/dormant case.
    """

    role: str
    provider: "str | None"
    model: "str | None"
    applied: bool = False
    note: str = ""


def resolve_role(
    config: Any,
    providers: Any,
    role: str,
    *,
    fallback_provider: "str | None",
    fallback_model: "str | None",
) -> RoleResolution:
    """Resolve ``role`` through ``config.model_roles`` — or return the fallbacks.

    Mapping syntax: ``"provider:model"`` targets that provider (its default
    model when the part after ``:`` is empty); a bare ``"model"`` (no colon)
    keeps the caller's provider — including the router's default route when
    ``fallback_provider`` is ``None`` — and only swaps the model.

    A named provider must exist AND be available: the probe is the manager's
    own ``available()`` inside try/except-False — the same defensive pattern
    ``routing.available_models`` and the router's ``_safe_available`` use (an
    unknown provider already returns False there, so existence and availability
    are one check). A bare-model mapping names no provider, so there is nothing
    to probe — the provider is whatever the call would have used anyway.

    Never raises; any miss or internal error returns the fallbacks unchanged.
    """
    fallback = RoleResolution(role=role, provider=fallback_provider, model=fallback_model)
    try:
        roles = getattr(config, "model_roles", None)
        raw = roles.get(role) if isinstance(roles, dict) else None
        value = raw.strip() if isinstance(raw, str) else ""
        if not value:
            return fallback  # unmapped / blank → dormant
        if ":" in value:
            prov, _, model = value.partition(":")
            prov, model = prov.strip(), model.strip()
        else:
            prov, model = "", value
        if not prov:
            # Bare "model": same provider, new model. No probe (see docstring).
            if model == (fallback_model or ""):
                return fallback  # names the model already in use → no-op
            return RoleResolution(
                role=role, provider=fallback_provider, model=model, applied=True
            )
        try:
            ok = bool(providers.available(prov)) if providers is not None else False
        except Exception:  # noqa: BLE001 — a probe failure just means "not available"
            ok = False
        if not ok:
            note = (
                f"role_fallback: model_roles[{role!r}] = {value!r} skipped — "
                f"provider {prov!r} is unknown or unavailable; keeping "
                f"({fallback_provider!r}, {fallback_model!r})"
            )
            _log.warning(note)
            return RoleResolution(
                role=role,
                provider=fallback_provider,
                model=fallback_model,
                applied=False,
                note=note,
            )
        resolved_model = model or None  # "prov:" → the provider's default model
        if prov == (fallback_provider or "") and resolved_model == fallback_model:
            return fallback  # names the pair already in use → no-op
        return RoleResolution(role=role, provider=prov, model=resolved_model, applied=True)
    except Exception:  # noqa: BLE001 — resolution must NEVER break a call site
        return fallback
