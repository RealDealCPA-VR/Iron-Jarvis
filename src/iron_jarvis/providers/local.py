"""What "local" means, and how local models are ordered (v1.148.0).

ONE definition, because there were two and they disagreed:

* ``providers/manager.health()`` called ``grok-cli`` local (it is a client for
  xAI's hosted API — the request leaves the building), and
* ``eval/usage_view.is_local_provider`` did not call ``opencode-cli`` local
  (in THIS daemon it is: the OpenCode connector is restricted to models the
  user's own hardware serves).

That is the same "two callers deriving one concept two ways" bug v1.102.0 fixed
for the usage rollup, and it is disqualifying for local-first routing: a router
that prefers "local" models cannot be trusted if the picker's idea of local is a
different set. Both now delegate here.

The size ladder is the other half. Local fleets are naturally tiered by
parameter count — a 14B, a 32B, a 120B — and "prefer the smallest model that
can do the job" is only expressible if the sizes are known. :func:`model_size_b`
reads the count out of a model id, which is how every local runner names them
(``qwen2.5-coder:14b``, ``gpt-oss-120b``, ``llama-3.3-70b-instruct``).
"""

from __future__ import annotations

import re
from typing import Any

#: Provider NAMES whose inference runs on hardware the user owns.
#:
#: ``opencode-cli`` is here because this daemon's OpenCode connector is offered
#: local-only by construction (``config.opencode_local_models`` gates it to
#: models the user's own machines serve). ``grok-cli`` is deliberately NOT here,
#: despite once being classed local: it is a terminal client for xAI's hosted
#: API, so calling it local would report someone else's GPUs as the user's own.
LOCAL_PROVIDERS = frozenset({"ollama", "custom", "opencode-cli"})

#: Every registered fleet node provider (``fleet-<node>``) is a machine the user
#: added themselves.
LOCAL_PREFIXES = ("fleet-",)


def is_local_provider(name: str) -> bool:
    """True when *name*'s inference runs on the user's own hardware."""
    p = (name or "").strip().lower()
    return p in LOCAL_PROVIDERS or p.startswith(LOCAL_PREFIXES)


#: A parameter count in a model id: ``:14b``, ``-120b``, ``_7B``, ``30b-a3b``.
#: Deliberately requires a non-alphanumeric boundary before the digits so a
#: version like ``llama3.1`` or a date like ``20241022`` cannot read as a size.
_SIZE_RE = re.compile(r"(?:^|[^0-9a-z])(\d+(?:\.\d+)?)\s*b(?![0-9a-z])", re.I)


def model_size_b(model: str) -> float | None:
    """Billions of parameters read from a model id, or None when unknowable.

    Takes the LARGEST match, which is the right reading for the two shapes that
    carry more than one: a mixture-of-experts id names its total and its active
    count (``qwen3-30b-a3b`` → 30, the memory it occupies), and a quantisation
    suffix never exceeds the model's own size. None means "not stated" — the
    caller must not guess, since assuming small would route hard work to a
    model that cannot do it and assuming large would strand a small one.
    """
    if not model:
        return None
    sizes = [float(m.group(1)) for m in _SIZE_RE.finditer(model)]
    return max(sizes) if sizes else None


def local_models(provider_manager: Any, config: Any) -> list[dict[str, Any]]:
    """Connected, AVAILABLE local models as ``[{provider, model, size_b}]``,
    smallest first (unknown sizes last — they cannot be ordered honestly).

    Built on ``providers.routing.connected_real_models`` so the local ladder and
    the Auto router see exactly the same pool; filtering a shared enumeration is
    what keeps them from drifting apart.
    """
    from .routing import connected_real_models

    out: list[dict[str, Any]] = []
    try:
        pool = connected_real_models(provider_manager, config)
    except Exception:  # noqa: BLE001 — routing must never break on enumeration
        return []
    for entry in pool:
        provider = str(entry.get("provider") or "")
        if not is_local_provider(provider):
            continue
        model = str(entry.get("model") or "")
        out.append({"provider": provider, "model": model, "size_b": model_size_b(model)})
    # Smallest first; unknown size sorts last (a stable, documented order beats
    # an invented guess). Ties break on the id so the ladder is deterministic.
    out.sort(key=lambda e: (e["size_b"] is None, e["size_b"] or 0.0, e["model"]))
    return out


def local_ladder(provider_manager: Any, config: Any) -> list[dict[str, Any]]:
    """The escalation ladder the user asked for — 14B → 32B → 120B → cloud —
    as its LOCAL rungs, smallest first.

    A user-configured ladder wins: ``config.routing_local_ladder`` is a list of
    ``"provider:model"`` strings, applied in the order given and filtered to
    what is actually connected (a rung pointing at a machine that is off is
    skipped, not an error). Empty/absent = derive it from what is connected.
    """
    from .routing import parse_pm

    raw = list(getattr(config, "routing_local_ladder", None) or [])
    derived = local_models(provider_manager, config)
    if not raw:
        return derived
    by_key = {(e["provider"], e["model"]): e for e in derived}
    ordered: list[dict[str, Any]] = []
    for item in raw:
        pm = parse_pm(str(item))
        if not pm:
            continue
        # An explicit rung may name a provider without a model — take whatever
        # that provider is serving.
        hit = by_key.get(pm) or next(
            (e for e in derived if e["provider"] == pm[0] and not pm[1]), None
        )
        if hit is not None and hit not in ordered:
            ordered.append(hit)
    return ordered
