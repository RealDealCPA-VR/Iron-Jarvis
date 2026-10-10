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
from .adapter import FleetAdapter, adapter_for  # noqa: F401 — FleetAdapter re-exported
from .models import NODE_PROTOCOLS, FleetNode

#: Node ids become provider names (``fleet-<id>``), and provider names cannot
#: contain a colon — ``providers/routing.py::parse_pm`` partitions on the first
#: one, so "fleet-a:b" would parse as provider "fleet-a" + model "b".
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,30}$")

_PROVIDER_PREFIX = "fleet-"


def provider_name(node_id: str) -> str:
    return f"{_PROVIDER_PREFIX}{node_id}"


def _check_protocol(value: object) -> None:
    """Refuse a protocol this build cannot speak (v1.329.0), in one sentence."""
    if value not in NODE_PROTOCOLS:
        raise ValueError("protocol must be openai or anthropic")


class ChildNodeError(ValueError):
    """Removing or changing a model a proxy reports (v1.330.0). The proxy names
    its backends again on every sampling pass, so removing one here would be
    undone within seconds; the honest answer is "remove the proxy", in plain
    words. An edit (rename, re-detect, a verify result) is refused with the
    same sentence: saving it used to store the model as a top-level row, so
    ``GET /fleet`` listed it twice and the row outlived its proxy."""


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

    def _top_level(self) -> dict[str, FleetNode]:
        """Seeds + stored nodes by id. A stored node OVERRIDES the seed with its
        id, so a user can label / flag their Ollama box without leaving the
        config slot."""
        by_id: dict[str, FleetNode] = {n.id: n for n in self.seeded()}
        stored = self._stored()
        for node in stored:
            if not node.parent_id:
                by_id[node.id] = node
        # A stored row with a parent is a proxy's model that an older build
        # saved on an edit (v1.330.0 refuses that edit). While its proxy is a
        # node, the proxy's own report is the truth: listing the saved copy as
        # well showed the model twice, and kept it after the proxy was
        # switched off. Only a row whose proxy is gone stays listed, so it can
        # still be removed.
        for node in stored:
            if node.parent_id and node.parent_id not in by_id:
                by_id[node.id] = node
        return by_id

    def nodes(self) -> list[FleetNode]:
        """Every known node: the top-level ones, then each proxy's children.

        A proxy's children are listed ONLY while that proxy is itself listed
        and switched on (v1.330.0). They are the proxy's own report, so once the
        proxy is gone (or off) they are not part of the fleet any more. Before
        this, removing a LiteLLM proxy left its backends on the Fleet page,
        Online, each with a Remove button that did nothing, until a restart.
        """
        by_id = self._top_level()
        out = list(by_id.values())
        for parent_id, kids in self._children.items():
            parent = by_id.get(parent_id)
            if parent is None or not parent.enabled:
                continue
            out.extend(kids)
        return out

    def children_of(self, parent_id: str) -> list[FleetNode]:
        """The topology children currently held for one proxy (a copy)."""
        return list(self._children.get(parent_id, ()))

    def family(self, node_id: str) -> list[str]:
        """``node_id`` plus every child id that :meth:`remove` takes with it,
        so a caller can clear what it holds per node (the sampler's readings)."""
        ids = [node_id] + [k.id for k in self.children_of(node_id)]
        ids += [n.id for n in self._stored() if n.parent_id == node_id and n.id not in ids]
        return ids

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
        _check_protocol(node.protocol)
        rows = [n for n in self._stored() if n.id != node.id]
        rows.append(node)
        self._save(rows)
        return node

    def child_refusal(self, node: FleetNode | None) -> str:
        """The one sentence that refuses removing or changing a proxy's model,
        or "" when ``node`` is not one (v1.330.0).

        A row whose proxy is no longer a node (a copy an older build saved) is
        not refused: nothing will report it again, so removing it works.
        """
        if node is None or not node.parent_id:
            return ""
        parent = self._top_level().get(node.parent_id)
        if parent is None:
            return ""
        return (
            f"{node.alias or node.id} comes from the proxy "
            f"{parent.label or parent.id}. Remove the proxy to remove it."
        )

    def update(self, node_id: str, **fields: Any) -> FleetNode:
        current = self.get(node_id)
        if current is None:
            raise KeyError(node_id)
        # v1.330.0: a proxy's model is the proxy's report, rebuilt every pass.
        # Saving an edit stored it as a top-level row: GET /fleet then listed
        # it twice, and the saved copy stayed after the proxy was switched off.
        refusal = self.child_refusal(current)
        if refusal:
            raise ChildNodeError(refusal)
        # model_copy(update=) does not validate, so a bad protocol would be
        # stored as typed and then drop the whole row on the next load.
        if fields.get("protocol") is not None:
            _check_protocol(fields["protocol"])
        merged = current.model_copy(update={k: v for k, v in fields.items() if v is not None})
        # A seed edited for the first time is PROMOTED to a stored node so the
        # label/capability flags survive, while its base_url stays config-driven.
        if current.source == "config":
            merged = merged.model_copy(update={"source": "config"})
        rows = [n for n in self._stored() if n.id != node_id]
        rows.append(merged)
        self._save(rows)
        return merged

    #: Config keys backing each derived seed. Removing a seeded endpoint has to
    #: clear these — the node is RENDERED from them on every read, so deleting a
    #: stored row alone would just let it reappear on the next call.
    _SEED_KEYS: dict[str, tuple[str, ...]] = {
        "ollama": ("ollama_base_url", "ollama_model"),
        "custom": ("custom_base_url", "custom_model"),
    }

    def remove(self, node_id: str) -> list[str]:
        """Remove a node. Returns the config keys cleared (empty for a plain
        user-added node), so the caller can tell the user what else changed.

        A config-seeded endpoint (``ollama`` / ``custom``) used to be refused
        outright with "managed in Settings", which left a dead endpoint pinned to
        the page forever — exactly what happens after moving from Ollama to vLLM.
        Since the seed is derived from the config keys, clearing them IS the
        removal. Note this also retires the matching top-level provider, which is
        the honest reading of "remove this endpoint": the user isn't running it
        any more. Re-entering the URL in Settings brings it straight back.
        """
        node = self.get(node_id)
        if node is None:
            raise KeyError(node_id)

        # v1.330.0: a model a proxy reports is not ours to remove. Its row is
        # rebuilt from the proxy's own list on the next sampling pass, so this
        # used to answer {"ok": true} and change nothing. Say what does work.
        refusal = self.child_refusal(node)
        if refusal:
            raise ChildNodeError(refusal)

        # Drop any stored row first (a promoted seed has one; a user node is
        # one). A child row an older build saved on an edit goes with its
        # proxy too.
        stored = self._stored()
        rows = [n for n in stored if n.id != node_id and n.parent_id != node_id]
        if len(rows) != len(stored):
            self._save(rows)

        # v1.330.0: a proxy's children go with it, from every place they are
        # held here: the topology list and the reachability cache. They used
        # to stay listed, Online, until the daemon restarted.
        for kid in self._children.pop(node_id, []):
            self._reachable.pop(kid.id, None)
        self._reachable.pop(node_id, None)

        if node.source != "config":
            return []

        cleared: list[str] = []
        for key in self._SEED_KEYS.get(node_id, ()):
            if getattr(self.config, key, None):
                setattr(self.config, key, "")  # keep the live object in agreement
                cleared.append(key)
        if cleared:
            self._persist(self.config.home, {k: "" for k in cleared})
        return cleared

    def absorb_children(self, parent_id: str, children: list[FleetNode]) -> None:
        """Replace a proxy's discovered backends (in memory only).

        Ignored when the proxy is no longer a node (v1.330.0): a sampling pass
        that probed it just before the user removed it lands its result after
        the removal, and adopting those children would put the removed proxy's
        backends straight back on the page.
        """
        if parent_id not in self._top_level():
            self._children.pop(parent_id, None)
            return
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

                # v1.329.0: the node's PROTOCOL picks the adapter (OpenAI
                # chat-completions, or the Anthropic Messages API).
                manager.register(
                    provider_name(node.id),
                    lambda model=None, n=node, c=_cred: adapter_for(
                        n, model=model, credential=c
                    ),
                )
                count += 1
            except Exception:  # noqa: BLE001 — skip the bad node, keep the fleet
                continue
        return count
