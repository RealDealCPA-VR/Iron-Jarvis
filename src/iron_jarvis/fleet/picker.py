"""What a fleet endpoint offers in the model PICKERS (v1.350.1).

THE USER'S REPORT: the RTX6000ada endpoint (a LiteLLM proxy) listed seven
models in the chat's model menu — "brain (RTX)", "embed (RTX)", "brain",
"embed", "qwen3.6:27b", "gemma4:26b", "mxbai-embed-large" — "historical
models this endpoint has. Only true endpoint availability should appear."
Read off the proxy (``/v1/model/info``): the seven names are ALIASES in its
config that route to just TWO real servers — ``hosted_vllm/brain`` at
vllm-brain (four names, two of them names the box no longer runs) and
``hosted_vllm/mxbai-embed-large`` at vllm-embed (an embedding model, which
cannot chat at all). ``/v1/models`` answers every alias, so the picker did.

:func:`picker_rows` keeps ONE row per real deployment (the routed model +
its server, from the topology children the sampler already absorbs) and
drops embedding models. The row kept for a deployment is the endpoint's
own default model when it is one of its names, else the name equal to the
deployment's own model id, else the first name the endpoint listed; the
other names ride the row's ``aliases`` (``templates.analyze_requirements``
accepts them, so a template pinned to an old name is not suddenly "not
connected"). A name the proxy routes somewhere the children do not say (no
sampler pass yet, a non-proxy endpoint) is kept as listed. The endpoint's
default model is NEVER dropped, and an endpoint left with nothing offers its
default — the old fallback.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

#: Name shapes of embedding / reranker models (they cannot answer a chat).
#: Matched on the alias AND on the model it routes to.
_EMBED_RE = re.compile(
    r"(?:^|[/_:\-\s.])(?:embed\w*|\w*-embed\w*|bge|e5|gte|minilm|all-minilm|rerank\w*|reranker)(?:$|[/_:\-\s.])"
    r"|embedding|nomic-embed|mxbai-embed|snowflake-arctic-embed|text-embedding",
    re.IGNORECASE,
)


def looks_like_embedding(*names: str) -> bool:
    """True when any of *names* is shaped like an embedding/reranker model."""
    return any(bool(n) and bool(_EMBED_RE.search(str(n))) for n in names)


def _tail(model: str) -> str:
    """``hosted_vllm/brain`` -> ``brain`` (the provider prefix LiteLLM adds)."""
    return str(model or "").rsplit("/", 1)[-1].strip()


def picker_rows(ids: Iterable[str], *, default_model: str = "", children: Iterable[Any] = ()) -> list[dict[str, Any]]:
    """``[{"model": id, "aliases": [...]}]`` — what one endpoint offers in a
    picker, in the order the endpoint listed its names (see the module doc).
    ``children`` are the proxy's topology children (``FleetNode`` with
    ``alias``, ``default_model`` = the routed model, ``base_url`` = its
    server); anything without those attributes is ignored."""
    listed = [str(i) for i in ids if str(i or "").strip()]
    default = str(default_model or "").strip()
    route: dict[str, tuple[str, str]] = {}
    for ch in children or ():
        alias = str(getattr(ch, "alias", "") or "").strip()
        model = str(getattr(ch, "default_model", "") or "").strip()
        if alias and model:
            route[alias] = (model, str(getattr(ch, "base_url", "") or "").strip())

    groups: dict[tuple[str, str], list[str]] = {}
    order: list[tuple[str, str] | str] = []
    for name in listed:
        target = route.get(name)
        routed_model = target[0] if target else ""
        if name != default and looks_like_embedding(name, routed_model):
            continue
        if target is None:
            order.append(name)  # nothing says where it routes: kept as listed
            continue
        if target not in groups:
            groups[target] = []
            order.append(target)
        groups[target].append(name)

    rows: list[dict[str, Any]] = []
    for item in order:
        if isinstance(item, str):
            rows.append({"model": item, "aliases": []})
            continue
        names = groups[item]
        if default in names:
            keep = default
        else:
            own = _tail(item[0])
            keep = own if own in names else names[0]
        rows.append({"model": keep, "aliases": [n for n in names if n != keep]})
    if not rows:
        rows.append({"model": default or (listed[0] if listed else "default"), "aliases": []})
    return rows
