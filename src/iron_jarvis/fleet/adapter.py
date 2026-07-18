"""Adapter for one fleet node — an OpenAI-compatible endpoint the user owns.

The ONLY behavioural difference from :class:`OpenAIAdapter` is capability
honesty, and it is load-bearing: ``LLMAdapter.capabilities()`` defaults to
``{"tool_use": True, "vision": True}`` (adapters/base.py) and ``OpenAIAdapter``
never overrides it, so every endpoint is *assumed* tool-capable. Send a
tool-using request (an agent loop, i.e. exactly the coding work this feature
routes) to a local model that cannot emit ``tool_calls`` and it returns an
empty tool-call list forever: the loop stalls with no error, no failover, and
no explanation. That is the silent-stall bug class CLAUDE.md warns about.

So a fleet node reports the capabilities its RECORD asserts, and an unverified
node (``tool_use is None``) reports *not* tool-capable. The router then swaps a
tool-using request onto a provider that can actually serve it instead of
stalling. ``POST /fleet/nodes/{id}/verify`` flips the record to ``True`` only
after a live probe proves the server really returns tool calls.
"""

from __future__ import annotations

from typing import Any, Callable

from ..providers.adapters.openai import OpenAIAdapter
from ..providers.manager import _normalize_ollama_url
from .models import FleetNode


class FleetAdapter(OpenAIAdapter):
    """An OpenAI-compatible local endpoint with honestly-declared capabilities."""

    def __init__(
        self,
        *,
        node: FleetNode,
        model: str | None = None,
        credential: Callable[[], str | None] | None = None,
    ) -> None:
        super().__init__(
            model=model or node.default_model or "default",
            base_url=_normalize_ollama_url(node.base_url),
            credential=credential,
            provider_name=f"fleet-{node.id}",
        )
        self.node = node

    def capabilities(self) -> dict[str, Any]:
        """Capabilities asserted by the node record — never assumed.

        ``None`` (never verified) becomes ``False``: claiming a capability we
        have not confirmed is what produces the silent stall described above.
        """
        caps = super().capabilities()
        caps["tool_use"] = bool(self.node.tool_use)
        caps["vision"] = bool(self.node.vision)
        return caps
