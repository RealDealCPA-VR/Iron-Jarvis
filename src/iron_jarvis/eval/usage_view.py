"""The ONE merged usage rollup (v1.102.0).

``GET /usage`` learned to fold in the user's OpenCode sessions in v1.94.0 —
local-model work done inside OpenCode never crosses this daemon's router, and on
a real machine it dwarfs everything else. ``GET /fleet/usage`` was written
earlier and calls ``observability.usage_summary`` DIRECTLY, so it never saw that
merge: measured on the machine this was found on, the Local Fleet page reported
141,623 local tokens while the true figure was 53,994,582 — a 380x under-report,
which reads as "my local hardware is barely used" when the opposite is true.

Two callers deriving "usage" from two different views is the bug. This module is
the single source: both routes call :func:`merged_usage`, so a future source
(another CLI, another local runner) is added once and appears everywhere.
"""

from __future__ import annotations

from typing import Any

# Providers whose tokens ran on hardware the user owns, so they cost nothing.
#
# v1.148.0: re-exported from ``providers.local``, which is now the ONE
# definition — this module and ``providers.manager.health()`` had drifted into
# two different answers (see that module's docstring). Both names are kept as
# re-exports so existing importers are unaffected.
from ..providers.local import LOCAL_PREFIXES as LOCAL_PROVIDER_PREFIXES  # noqa: F401
from ..providers.local import LOCAL_PROVIDERS  # noqa: F401
from ..providers.local import is_local_provider as _is_local_name

#: Hosted vendors. Used ONLY to disqualify an ``opencode/<id>`` row from being
#: counted as local — see :func:`is_local_provider`.
HOSTED_VENDORS = frozenset(
    {
        "anthropic", "openai", "google", "gemini", "xai", "grok", "openrouter",
        "groq", "mistral", "deepseek", "cohere", "perplexity", "together",
        "fireworks", "azure", "bedrock", "vertex", "opencode",
    }
)


def is_local_provider(provider: str) -> bool:
    """True when ``provider``'s tokens ran on hardware the user owns.

    ``opencode/<providerID>`` needs care. Iron Jarvis's own OpenCode *connector*
    is local-only by construction, but this rollup reads OpenCode's OWN store —
    sessions the user ran in OpenCode directly, where that restriction does not
    apply. OpenCode can reach hosted models.

    So the sub-provider decides, and it fails toward CLOUD for anything
    recognisably hosted. Getting this backwards is not a rounding error: local
    tokens are priced as "cost you avoided", so counting real Anthropic spend as
    local would report money saved that was in fact money spent. An unrecognised
    id (``spark``, ``fleet``, ``lmstudio``, a machine name) is treated as local —
    that is what a self-hosted runner looks like.
    """
    p = (provider or "").strip().lower()
    if _is_local_name(p):
        return True
    if p.startswith("opencode/"):
        return p[len("opencode/"):].split(":", 1)[0] not in HOSTED_VENDORS
    return False


def merged_usage(platform: Any, days: int = 30) -> dict[str, Any]:
    """``usage_summary`` with the OpenCode store folded in.

    Shape is unchanged (``totals`` / ``by_day`` / ``by_model``) plus an
    ``opencode`` block carrying availability and the attribution caveat. The
    merge NEVER raises: usage is a read-only reporting surface and must not
    break because OpenCode is absent, locked, or on a newer schema.
    """
    out = platform.observability.usage_summary(days)
    try:
        from .opencode_usage import opencode_db_path, opencode_usage

        oc = opencode_usage(opencode_db_path(platform.config), days)
    except Exception:  # noqa: BLE001 — reporting must never break on the merge
        oc = {"available": False, "note": "opencode merge failed"}
    out["opencode"] = oc
    if not oc.get("available"):
        return out

    t, ot = out["totals"], oc["totals"]
    t["input_tokens"] += ot["input_tokens"]
    t["output_tokens"] += ot["output_tokens"]
    t["cost_usd"] += ot["cost_usd"]
    t["runs"] += ot["runs"]
    out["by_model"] = list(out["by_model"]) + list(oc["by_model"])

    merged: dict[str, dict[str, Any]] = {r["day"]: dict(r) for r in out["by_day"]}
    for r in oc["by_day"]:
        m = merged.setdefault(
            r["day"],
            {"day": r["day"], "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0},
        )
        m["input_tokens"] += r["input_tokens"]
        m["output_tokens"] += r["output_tokens"]
        m["cost_usd"] += r["cost_usd"]
    out["by_day"] = sorted(merged.values(), key=lambda r: r["day"])
    return out
