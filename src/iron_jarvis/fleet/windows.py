"""A local model's CONTEXT WINDOW, read off the fleet's last probe (v1.421.0, D3).

The context-window ladder (``daemon/chat_turn._ladder_window_source``) has had
an ``"endpoint"`` rung since v1.146.0 that walked ``fleet.nodes()`` reading
``node.models`` / ``m.name``. A :class:`~.models.FleetNode` is CONFIG and
carries no models at all — what a node serves is OBSERVATION, held by the
sampler on its :class:`~.models.NodeSnapshot` (``ModelEntry.id``, not
``name``). So the rung never answered: a local model planned at the 32k
default unless a LiteLLM gateway stated ``max_input_tokens`` (the rung above
it), and an Ollama model loaded with a 4,096-token window was sent 32k
prompts it silently cut from the FRONT (its system prompt first). The tests
that pinned the rung handed it fakes in the dead shape, so they stayed green.

:func:`probed_window` reads the sampler's LAST snapshot of the node that
serves the provider — a cached read under the sampler's lock, never a request:

* the provider names the node exactly as the registry's own lookups do
  (:meth:`~.registry.FleetRegistry._node_id_for`): ``custom`` / ``ollama``
  (the two settings slots) and ``fleet-<id>`` (a saved node — the slots' seed
  twins ``fleet-custom`` / ``fleet-ollama`` included). A proxy's topology
  child is never a provider and never answers here;
* the model is matched by ``id`` (Ollama's ``name`` and ``name:latest`` are
  ONE model — Ollama's own rule), else by ``hf_id`` (vLLM's ``root``);
* the window is the SERVER's own number: ``max_model_len`` (vLLM / an
  OpenAI-compatible server's ``/v1/models`` — the limit the server enforces),
  else ``context_length`` (Ollama's ``/api/ps`` — the allocation the LOADED
  runner really has; a model that is installed but not loaded has none);
* a LiteLLM proxy's own entries are ALIASES with no window: the alias is
  followed to the deployment(s) that serve it — the topology children the
  sampler absorbs (``alias`` → the routed ``default_model`` at ``base_url``,
  the shape ``fleet/picker`` groups by) — each matched on its OWN snapshot by
  the routed model, with and without LiteLLM's ``provider/`` prefix
  (``hosted_vllm/zai-org/GLM`` is sent to vLLM as ``zai-org/GLM``). Several
  deployments under one alias: the SMALLEST window, and only when EVERY one
  is known (a request may land on any of them).

UNKNOWN STAYS UNKNOWN: no sampler, no snapshot yet, a snapshot taken of an
address the node no longer has, a switched-off node, no matching model, or no
number → ``None``, and the ladder answers exactly what it answered before.
Never raises.
"""

from __future__ import annotations

from typing import Any, Iterable

from .probes import normalize_root


def _window_of(entry: Any) -> int | None:
    """``max_model_len``, else ``context_length`` — a positive int, or None."""
    for field in ("max_model_len", "context_length"):
        n = getattr(entry, field, None)
        if isinstance(n, int) and not isinstance(n, bool) and n > 0:
            return n
    return None


def _same_model(listed: str, wanted: str) -> bool:
    """One model by id: equal, or Ollama's implicit ``:latest`` tag."""
    if not listed or not wanted:
        return False
    return listed == wanted or listed == f"{wanted}:latest" or wanted == f"{listed}:latest"


def _entry_for(models: Iterable[Any], names: Iterable[str]) -> Any:
    """The first listed model matching *names* (in that order): by ``id``
    first, then by ``hf_id``. None when nothing matches."""
    listed = list(models or [])
    for name in [n for n in names if n]:
        for m in listed:
            if _same_model(str(getattr(m, "id", "") or ""), name):
                return m
        for m in listed:
            hf = str(getattr(m, "hf_id", "") or "")
            if hf and hf == name:
                return m
    return None


def _routed_names(routed: str) -> list[str]:
    """The names a deployment's own server may list for LiteLLM's routed
    model: the whole string, then without LiteLLM's ``provider/`` prefix (what
    LiteLLM sends), then its last segment."""
    r = str(routed or "").strip()
    out = [r]
    if "/" in r:
        out.append(r.split("/", 1)[1].strip())
        out.append(r.rsplit("/", 1)[1].strip())
    seen: list[str] = []
    for n in out:
        if n and n not in seen:
            seen.append(n)
    return seen


def _snapshot(sampler: Any, node: Any) -> Any:
    """The sampler's last snapshot of *node*, only while it describes the
    node's CURRENT address (a node re-pointed elsewhere has none until the
    sampler reads the new one — the routing table's rule)."""
    snap = sampler.latest(node.id)
    if snap is None:
        return None
    then = normalize_root(str(getattr(getattr(snap, "node", None), "base_url", "") or ""))
    if then != normalize_root(str(getattr(node, "base_url", "") or "")):
        return None
    return snap


def probed_window(registry: Any, sampler: Any, provider: str, model: str) -> int | None:
    """The window (tokens) the fleet's last probe says *model* on *provider*
    has, or None when nothing says (see the module doc). A cached read; never
    a request; never raises."""
    try:
        if registry is None or sampler is None:
            return None
        wanted = str(model or "").strip()
        if not wanted:
            return None
        from .registry import FleetRegistry

        node_id = FleetRegistry._node_id_for(provider)
        if not node_id:
            return None
        node = registry.get(node_id)
        if node is None or not getattr(node, "enabled", True) or getattr(node, "parent_id", ""):
            return None
        snap = _snapshot(sampler, node)
        if snap is None:
            return None
        entry = _entry_for(getattr(snap, "models", None) or [], [wanted])
        if entry is not None:
            own = _window_of(entry)
            if own:
                return own
        # A model PROXY: its entry is an alias — follow it to the server(s).
        alias = str(getattr(entry, "id", "") or "") if entry is not None else wanted
        children_of = getattr(registry, "children_of", None)
        kids = list(children_of(node_id)) if callable(children_of) else []
        deployments = [c for c in kids if str(getattr(c, "alias", "") or "").strip() == alias]
        if not deployments:
            return None
        windows: list[int] = []
        for child in deployments:
            csnap = _snapshot(sampler, child)
            if csnap is None:
                return None
            found = _entry_for(
                getattr(csnap, "models", None) or [],
                _routed_names(str(getattr(child, "default_model", "") or "")),
            )
            win = _window_of(found) if found is not None else None
            if not win:
                return None
            windows.append(win)
        return min(windows)
    except Exception:  # noqa: BLE001 — unknown stays unknown
        return None


__all__ = ["probed_window"]
