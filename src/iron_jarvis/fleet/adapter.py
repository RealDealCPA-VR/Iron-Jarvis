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

#: ``model -> {"vision", "tool_use", "context_window"}`` as the model proxy's
#: own ``/model/info`` states them (``FleetRegistry.model_caps``); ``None``
#: values = not stated.
CapsReader = Callable[[str], dict]


def node_capabilities(node: FleetNode, model: str, caps: CapsReader | None) -> dict[str, bool]:
    """The ``tool_use``/``vision`` a fleet node's adapter reports for *model*.

    The node RECORD speaks first (``None`` — never verified — reads False:
    claiming a capability we have not confirmed is what produces the silent
    stall described above). THE GATEWAY-KEY FIX: a model proxy that STATES a
    capability for this model in its own ``/model/info``
    (``supports_vision`` / ``supports_function_calling`` true) is believed —
    the user's LiteLLM gateway serves a ``vision`` model, and the Ollama-way
    read never found it, so a picture sent to it was rerouted or refused. A
    table that says False, or says nothing, changes nothing: the record's
    answer stands (unknown stays unknown, never guessed)."""
    out = {"tool_use": bool(node.tool_use), "vision": bool(node.vision)}
    if caps is None:
        return out
    try:
        stated = caps(model) or {}
    except Exception:  # noqa: BLE001 — a table fault leaves the record's answer
        return out
    if stated.get("vision") is True:
        out["vision"] = True
    if stated.get("tool_use") is True:
        out["tool_use"] = True
    return out


class FleetAdapter(OpenAIAdapter):
    """An OpenAI-compatible local endpoint with honestly-declared capabilities."""

    def __init__(
        self,
        *,
        node: FleetNode,
        model: str | None = None,
        credential: Callable[[], str | None] | None = None,
        caps: CapsReader | None = None,
    ) -> None:
        super().__init__(
            model=model or node.default_model or "default",
            base_url=_normalize_ollama_url(node.base_url),
            credential=credential,
            provider_name=f"fleet-{node.id}",
        )
        self.node = node
        self._caps = caps

    def capabilities(self) -> dict[str, Any]:
        """Capabilities asserted by the node record — never assumed — or
        stated for this model by the proxy itself (:func:`node_capabilities`)."""
        caps = super().capabilities()
        caps.update(node_capabilities(self.node, self.model, self._caps))
        return caps


def adapter_for(
    node: FleetNode,
    model: str | None = None,
    credential: Callable[[], str | None] | None = None,
    caps: CapsReader | None = None,
) -> Any:
    """The adapter that speaks *node*'s protocol (v1.329.0).

    ``anthropic`` → :class:`~iron_jarvis.fleet.anthropic_compat.AnthropicFleetAdapter`
    (``POST /v1/messages``, ``x-api-key``); anything else → :class:`FleetAdapter`
    (the OpenAI chat-completions shape every node spoke before the field
    existed). Both report the node's recorded capabilities and both are named
    ``fleet-<id>``, so a failing node refuses under its own name like any
    other local provider; nothing here ever substitutes another provider.
    ``caps`` (the gateway-key fix) is the proxy's own per-model statement."""
    if getattr(node, "protocol", "openai") == "anthropic":
        from .anthropic_compat import AnthropicFleetAdapter

        return AnthropicFleetAdapter(node=node, model=model, credential=credential, caps=caps)
    return FleetAdapter(node=node, model=model, credential=credential, caps=caps)
