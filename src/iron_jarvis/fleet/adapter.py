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


#: Where a model's capability answer came from (v1.424.0, D8): ``checked`` —
#: Iron Jarvis asked THIS model (a live check, or the node's own record for
#: its default model); ``stated`` — the model's server says so in its own
#: ``/model/info``; ``""`` — not known.
CAP_CHECKED, CAP_STATED, CAP_UNKNOWN = "checked", "stated", ""

#: The capability keys a model row carries.
CAP_FLAGS = ("tool_use", "vision")


def is_default_model(node: FleetNode, model: str) -> bool:
    """Is *model* the node's DEFAULT model — the one its node-level record
    (``tool_use`` / ``vision``, written by every check before v1.424.0)
    describes? ``""`` and ``"default"`` (the adapter's own word when the
    node saved none) are the default. Never raises."""
    try:
        name = (model or "").strip()
        default = (getattr(node, "default_model", "") or "").strip()
        return not name or name == "default" or (bool(default) and name == default)
    except Exception:  # noqa: BLE001
        return False


def model_capability(node: FleetNode, model: str, caps: CapsReader | None) -> dict[str, Any]:
    """What *model* on *node* can do, BY ITSELF (v1.424.0, D8):
    ``{"tool_use": True|False|None, "vision": …, "tool_use_from": "checked"|
    "stated"|"", "vision_from": …}``. ``None`` / ``""`` = not known.

    Before v1.424.0 a node had ONE answer for every model behind it: a check
    of the gateway's default model (or a proxy that stated "yes" for one
    name) spoke for every other name — a LiteLLM gateway serving a model
    that calls tools and one that cannot read as "tools ✓" for both, so a
    tool turn ran natively on the one that cannot and stalled. Now each
    model says its own, in this order (first answer wins):

    1. a live check of THIS model (``node.model_caps[model]``, written by
       ``POST /fleet/nodes/{id}/verify``);
    2. the model's server SAYS it can (``supports_vision`` /
       ``supports_function_calling`` true in its ``/model/info``);
    3. the node's own record (``tool_use`` / ``vision``) — for its DEFAULT
       model only, which is the model every check before v1.424.0 asked;
    4. the server says it CANNOT (a false in ``/model/info``);
    5. not known.

    A live check outranks the server's table (it asked the model itself);
    a stated "yes" outranks the node record as before. Never raises."""
    out: dict[str, Any] = {}
    try:
        per = dict(((getattr(node, "model_caps", None) or {}).get((model or "").strip()) or {}))
    except Exception:  # noqa: BLE001
        per = {}
    stated: dict[str, Any] = {}
    if caps is not None:
        try:
            stated = dict(caps(model) or {})
        except Exception:  # noqa: BLE001 — a table fault: nothing stated
            stated = {}
    default = is_default_model(node, model)
    for key in CAP_FLAGS:
        value: bool | None = None
        source = CAP_UNKNOWN
        checked = per.get(key)
        said = stated.get(key)
        record = getattr(node, key, None)
        if isinstance(checked, bool):
            value, source = checked, CAP_CHECKED
        elif said is True:
            value, source = True, CAP_STATED
        elif default and isinstance(record, bool):
            value, source = record, CAP_CHECKED
        elif said is False:
            value, source = False, CAP_STATED
        out[key] = value
        out[f"{key}_from"] = source
    return out


def node_capabilities(node: FleetNode, model: str, caps: CapsReader | None) -> dict[str, bool]:
    """The ``tool_use``/``vision`` a fleet node's adapter reports for *model*
    — :func:`model_capability`, with NOT KNOWN read as False: claiming a
    capability we have not confirmed is what produces the silent stall
    described above. The gateway-key fix's rule stands: a model proxy that
    STATES a capability for this model in its own ``/model/info`` is
    believed. v1.424.0 (D8): per MODEL — the node's record speaks for its
    default model only, a live check of this model wins, and a stated "no"
    counts when nothing else speaks for it."""
    view = model_capability(node, model, caps)
    return {key: bool(view.get(key)) for key in CAP_FLAGS}


class FleetAdapter(OpenAIAdapter):
    """An OpenAI-compatible local endpoint with honestly-declared capabilities."""

    def __init__(
        self,
        *,
        node: FleetNode,
        model: str | None = None,
        credential: Callable[[], str | None] | None = None,
        caps: CapsReader | None = None,
        reply_facts: Any = None,
    ) -> None:
        from ..providers.reply_budget import ReplyBudget

        base_url = _normalize_ollama_url(node.base_url)
        super().__init__(
            model=model or node.default_model or "default",
            base_url=base_url,
            credential=credential,
            provider_name=f"fleet-{node.id}",
            # v1.387.0: a node the user owns gets the LOCAL reply budget
            # (providers/reply_budget.py), never the hosted 4,096.
            reply_budget=ReplyBudget(f"fleet-{node.id}", base_url or "", facts=reply_facts),
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
    reply_facts: Any = None,
) -> Any:
    """The adapter that speaks *node*'s protocol (v1.329.0).

    ``anthropic`` → :class:`~iron_jarvis.fleet.anthropic_compat.AnthropicFleetAdapter`
    (``POST /v1/messages``, ``x-api-key``); anything else → :class:`FleetAdapter`
    (the OpenAI chat-completions shape every node spoke before the field
    existed). Both report the node's recorded capabilities and both are named
    ``fleet-<id>``, so a failing node refuses under its own name like any
    other local provider; nothing here ever substitutes another provider.
    ``caps`` (the gateway-key fix) is the proxy's own per-model statement.
    ``reply_facts`` (v1.387.0) is the platform's reply-budget reader
    (``ProviderManager.reply_facts_of``); both protocols are budgeted."""
    if getattr(node, "protocol", "openai") == "anthropic":
        from .anthropic_compat import AnthropicFleetAdapter

        return AnthropicFleetAdapter(
            node=node, model=model, credential=credential, caps=caps, reply_facts=reply_facts,
        )
    return FleetAdapter(
        node=node, model=model, credential=credential, caps=caps, reply_facts=reply_facts,
    )
