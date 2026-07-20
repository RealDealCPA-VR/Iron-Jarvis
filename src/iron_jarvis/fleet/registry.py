"""The fleet node registry — which endpoints exist, and which are routable.

Two ideas carry this module:

**Seeds are derived, never copied.** The two long-standing config slots
(``ollama_base_url`` / ``custom_base_url``) are rendered as fleet nodes on every
read instead of being duplicated into ``fleet_nodes``. So the config keys stay
the single source of truth, a ``PUT /settings`` change shows up in the fleet
with no sync code and no drift, and the page is populated on first open with
zero setup.

**Topology children are never routable.** A proxy's backends are already
reachable through the proxy's own alias; registering them again as providers
would show the same GPU twice in every picker under two different names. They
exist in the registry for observability only.
"""

from __future__ import annotations

import re
from typing import Any, Callable

from ..core.config import persist_config_values
from .adapter import FleetAdapter
from .models import FleetNode

#: Node ids become provider names (``fleet-<id>``), and provider names cannot
#: contain a colon — ``providers/routing.py::parse_pm`` partitions on the first
#: one, so "fleet-a:b" would parse as provider "fleet-a" + model "b".
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,30}$")

_PROVIDER_PREFIX = "fleet-"


def provider_name(node_id: str) -> str:
    return f"{_PROVIDER_PREFIX}{node_id}"


class FleetRegistry:
    """Nodes = derived config seeds + persisted user nodes + absorbed children."""

    def __init__(
        self,
        config: Any,
        *,
        persist: Callable[..., Any] = persist_config_values,
    ) -> None:
        self.config = config
        self._persist = persist
        #: parent id -> its topology children (IN MEMORY ONLY: re-derived from
        #: the proxy every cycle, so an alias removed there disappears here).
        self._children: dict[str, list[FleetNode]] = {}
        #: node id -> last known reachability, fed by the sampler. Read by
        #: ``reachable`` on the routing hot path, so it is never a network call.
        self._reachable: dict[str, bool] = {}

    # -- seeds ---------------------------------------------------------------

    def seeded(self) -> list[FleetNode]:
        """The two config endpoint slots as fleet nodes (derived every call)."""
        cfg = self.config
        out: list[FleetNode] = []
        if getattr(cfg, "ollama_base_url", None):
            out.append(
                FleetNode(
                    id="ollama",
                    label="Ollama endpoint",
                    base_url=cfg.ollama_base_url,
                    kind="ollama",
                    source="config",
                    routable=True,
                    default_model=getattr(cfg, "ollama_model", "") or "",
                )
            )
        if getattr(cfg, "custom_base_url", None):
            out.append(
                FleetNode(
                    id="custom",
                    label="Custom endpoint",
                    base_url=cfg.custom_base_url,
                    source="config",
                    routable=True,
                    default_model=getattr(cfg, "custom_model", "") or "",
                    api_key_name="custom_api_key",
                )
            )
        return out

    def _stored(self) -> list[FleetNode]:
        out: list[FleetNode] = []
        for raw in getattr(self.config, "fleet_nodes", []) or []:
            try:
                out.append(FleetNode(**raw))
            except Exception:  # noqa: BLE001 — one bad row never hides the rest
                continue
        return out

    # -- reads ---------------------------------------------------------------

    def nodes(self) -> list[FleetNode]:
        """Every known node. A stored node OVERRIDES the seed with its id, so a
        user can label / flag their Ollama box without leaving the config slot."""
        by_id: dict[str, FleetNode] = {n.id: n for n in self.seeded()}
        for node in self._stored():
            by_id[node.id] = node
        out = list(by_id.values())
        for kids in self._children.values():
            out.extend(kids)
        return out

    def get(self, node_id: str) -> FleetNode | None:
        return next((n for n in self.nodes() if n.id == node_id), None)

    def routable_nodes(self) -> list[FleetNode]:
        """Nodes that may back a provider — never topology children."""
        return [n for n in self.nodes() if n.routable and n.enabled and not n.parent_id]

    # -- writes --------------------------------------------------------------

    def _save(self, rows: list[FleetNode]) -> None:
        # TOML has no null, and tomli_w RAISES on one (live-hit while wiring:
        # an unverified node carries tool_use=None/vision=None). Dropping the
        # None keys is lossless — they reload as None via the model defaults,
        # which is exactly what "never verified" means.
        payload = [
            {k: v for k, v in n.model_dump().items() if v is not None} for n in rows
        ]
        self.config.fleet_nodes = payload  # keep the live object in agreement
        self._persist(self.config.home, {"fleet_nodes": payload})

    def add(self, node: FleetNode) -> FleetNode:
        if not _ID_RE.match(node.id or ""):
            raise ValueError(
                "node id must be lowercase letters/digits/hyphens (no colons), "
                "1-31 chars — it becomes the provider name"
            )
        if not (node.base_url or "").strip():
            raise ValueError("base_url is required")
        rows = [n for n in self._stored() if n.id != node.id]
        rows.append(node)
        self._save(rows)
        return node

    def update(self, node_id: str, **fields: Any) -> FleetNode:
        current = self.get(node_id)
        if current is None:
            raise KeyError(node_id)
        merged = current.model_copy(update={k: v for k, v in fields.items() if v is not None})
        # A seed edited for the first time is PROMOTED to a stored node so the
        # label/capability flags survive, while its base_url stays config-driven.
        if current.source == "config":
            merged = merged.model_copy(update={"source": "config"})
        rows = [n for n in self._stored() if n.id != node_id]
        rows.append(merged)
        self._save(rows)
        return merged

    def remove(self, node_id: str) -> None:
        node = self.get(node_id)
        if node is None:
            raise KeyError(node_id)
        if node.source == "config" and not any(n.id == node_id for n in self._stored()):
            raise ValueError(
                "this endpoint is managed in Settings (ollama_base_url / custom_base_url)"
            )
        self._save([n for n in self._stored() if n.id != node_id])

    def absorb_children(self, parent_id: str, children: list[FleetNode]) -> None:
        """Replace a proxy's discovered backends (in memory only)."""
        self._children[parent_id] = list(children)

    # -- reachability (routing hot path) -------------------------------------

    def set_reachable(self, node_id: str, ok: bool) -> None:
        self._reachable[node_id] = bool(ok)

    def reachable(self, name: str) -> bool | None:
        """``ProviderManager.dynamic_available`` hook.

        ``None`` for any non-fleet provider so every other provider keeps its
        existing logic untouched. NEVER makes a network call — ``available()``
        runs per provider per request inside the router's snapshot.
        """
        if not name.startswith(_PROVIDER_PREFIX):
            return None
        node_id = name[len(_PROVIDER_PREFIX) :]
        node = self.get(node_id)
        if node is None:
            # A fleet-prefixed name IS ours: no node record means the endpoint
            # was deleted — report unavailable rather than deferring to a
            # possibly-lingering factory (a ghost provider the router picks).
            return False
        if not node.enabled:
            return False
        # Only the sampler can turn "reachable" into a fact. UNPROBED defers
        # (None) to the manager's own "is a factory registered?" test rather
        # than asserting availability we have not observed — claiming True here
        # made an unregistered node look ready to serve.
        return self._reachable.get(node_id)

    # -- provider registration -----------------------------------------------

    def register_providers(self, manager: Any, secret_resolver: Any = None) -> int:
        """Register ``fleet-<id>`` for each routable node. Returns the count.

        ``secret_resolver`` (name -> value | None) resolves a node's
        ``api_key_name`` from the secrets vault at REQUEST time — without it a
        keyed endpoint is silently sent no Authorization at all. Passed once at
        boot and remembered, so runtime re-registration (add/edit/delete on the
        Connections page) keeps working credentials.

        Per-node try/except: one malformed node must never be able to crash
        daemon boot.
        """
        if secret_resolver is not None:
            self._secret_resolver = secret_resolver
        resolver = getattr(self, "_secret_resolver", None)
        count = 0
        for node in self.routable_nodes():
            try:

                def _cred(n=node):  # noqa: ANN202 — adapter credential thunk
                    if not n.api_key_name or resolver is None:
                        return None
                    try:
                        return resolver(n.api_key_name)
                    except Exception:  # noqa: BLE001 — a vault fault ≠ a crash
                        return None

                manager.register(
                    provider_name(node.id),
                    lambda model=None, n=node, c=_cred: FleetAdapter(
                        node=n, model=model, credential=c
                    ),
                )
                count += 1
            except Exception:  # noqa: BLE001 — skip the bad node, keep the fleet
                continue
        return count
