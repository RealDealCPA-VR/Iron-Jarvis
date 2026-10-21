"""Where each name on a model PROXY really goes (v1.362.0).

A LiteLLM proxy on the user's network is local by its ADDRESS, and until this
module that was the whole answer: every name it served read "on this network".
But a proxy routes each name to its OWN upstream. On the daily driver the DGX
Spark's proxy serves ``glm-5.3-flash`` from a vLLM box on the tailnet and
``frontier`` from OpenRouter — so a Local only chat sent to ``frontier`` left
the network while the lock, the receipt and the model menu all said local
(the "LAN proxy that forwards to a cloud" limit of v1.332.0).

LiteLLM answers ``GET /model/info`` with one entry per served name:
``model_name`` (the name a client sends), ``litellm_params.model`` (the
upstream, provider-prefixed) and ``litellm_params.api_base`` (where it is
sent). That is enough to judge each name:

* an ``api_base`` that :func:`~iron_jarvis.providers.egress.private_host`
  accepts → **local** (the vLLM box, an Ollama on the LAN, a tailnet machine);
* no ``api_base`` and a provider LiteLLM serves from the proxy's own machine by
  default (Ollama, LM Studio, llamafile) → **local**;
* anything else → **cloud**. FAIL CLOSED: a name we cannot place is cloud.

A name the table does not list (a hidden alias, like the daily driver's
``fleet``) can only reach a deployment the table DOES list, so it is local
when every listed upstream is local, and cloud as soon as one is not: we
cannot see which one it reaches without sending a message.

A server that is not a LiteLLM proxy (``/model/info`` missing, or not
LiteLLM's shape) has no table, and its address decides exactly as before. So
does a proxy whose table we cannot read (it wants a key we do not send, or it
did not answer): the last table we read is kept, and with none the address
decides. Both are said in the Handbook.

The table is read in the BACKGROUND by the fleet sampler (never on the routing
path — the predicate that reads it runs on every model call) and kept on the
:class:`~iron_jarvis.fleet.registry.FleetRegistry`, which also saves the last
table per node so a restart does not open a window before the first read.
"""

from __future__ import annotations

import fnmatch
import time
from dataclasses import dataclass
from typing import Any

from . import probes
from .models import FleetNode

PLACE_LOCAL = "local"
PLACE_CLOUD = "cloud"

#: LiteLLM providers whose default address is the PROXY'S OWN machine
#: (``ollama`` → ``localhost:11434``, ``lm_studio`` → ``localhost:1234``,
#: ``llamafile`` → ``localhost:8080``). With no ``api_base`` they run where the
#: proxy runs, which is on the user's network (the proxy's own address has
#: already passed the private-host check by the time a table is consulted).
LOCAL_DEFAULT_PROVIDERS: frozenset[str] = frozenset({
    "ollama", "ollama_chat", "lm_studio", "llamafile",
})

#: How long a table read stays fresh before the sampler reads it again, and how
#: long "this server is not a LiteLLM proxy" is believed before it is asked
#: again. A proxy whose config changes is re-read within a minute.
ROUTES_TTL_S = 60.0
NOT_A_PROXY_TTL_S = 600.0

#: The most names one table keeps (a proxy listing thousands of wildcard
#: deployments must not grow the daemon without bound).
MAX_ENTRIES = 500


def _private(url: str) -> bool:
    from ..providers.egress import private_host

    return private_host(url)


def upstream_place(model: str, api_base: str) -> str:
    """:data:`PLACE_LOCAL` or :data:`PLACE_CLOUD` for one LiteLLM deployment
    (its ``litellm_params.model`` and ``api_base``). Never raises; anything
    unreadable is cloud."""
    try:
        base = str(api_base or "").strip()
        if base:
            return PLACE_LOCAL if _private(base) else PLACE_CLOUD
        prefix = str(model or "").strip().split("/", 1)[0].lower()
        return PLACE_LOCAL if prefix in LOCAL_DEFAULT_PROVIDERS else PLACE_CLOUD
    except Exception:  # noqa: BLE001 — fail closed
        return PLACE_CLOUD


@dataclass(frozen=True)
class Upstream:
    """One name a proxy serves and where it goes. No ``api_base`` is kept:
    only the verdict is needed after the read, and an address can carry a
    query string nobody should have to see in a saved file."""

    alias: str
    model: str
    place: str


@dataclass(frozen=True)
class RouteTable:
    """A proxy's served names and their places, as last read."""

    entries: tuple[Upstream, ...]
    read_at: float = 0.0

    def place(self, model: str) -> str | None:
        """Where a request for *model* goes: :data:`PLACE_LOCAL`,
        :data:`PLACE_CLOUD`, or ``None`` when the table says nothing (it lists
        no names at all — the address then decides). Never raises."""
        try:
            if not self.entries:
                return None
            name = str(model or "").strip()
            for e in self.entries:
                if name and e.alias == name:
                    return e.place
            wild = [e for e in self.entries if "*" in e.alias]
            hits = [e for e in wild if name and fnmatch.fnmatchcase(name, e.alias)]
            if hits:
                return PLACE_CLOUD if any(e.place != PLACE_LOCAL for e in hits) else PLACE_LOCAL
            # Unlisted (a hidden alias, or no name at all): it reaches one of
            # the listed deployments, and we cannot tell which.
            if all(e.place == PLACE_LOCAL for e in self.entries):
                return PLACE_LOCAL
            return PLACE_CLOUD
        except Exception:  # noqa: BLE001 — fail closed
            return PLACE_CLOUD

    def signature(self) -> tuple[tuple[str, str, str], ...]:
        """What the verdicts depend on (the read time is not part of it)."""
        return tuple((e.alias, e.model, e.place) for e in self.entries)

    def cloud_names(self) -> list[str]:
        return [e.alias for e in self.entries if e.place != PLACE_LOCAL]

    def to_json(self) -> dict[str, Any]:
        return {
            "read_at": self.read_at,
            "entries": [{"alias": e.alias, "model": e.model, "place": e.place} for e in self.entries],
        }

    @classmethod
    def from_json(cls, raw: Any) -> "RouteTable | None":
        """A saved table, or ``None`` when *raw* is not one. A saved entry
        whose place is not one of the two words reads as cloud."""
        if not isinstance(raw, dict) or not isinstance(raw.get("entries"), list):
            return None
        out: list[Upstream] = []
        for e in raw["entries"][:MAX_ENTRIES]:
            if not isinstance(e, dict) or not str(e.get("alias") or "").strip():
                continue
            place = e.get("place")
            out.append(Upstream(
                alias=str(e["alias"]).strip(),
                model=str(e.get("model") or ""),
                place=PLACE_LOCAL if place == PLACE_LOCAL else PLACE_CLOUD,
            ))
        try:
            read_at = float(raw.get("read_at") or 0.0)
        except (TypeError, ValueError):
            read_at = 0.0
        return cls(entries=tuple(out), read_at=read_at)


#: What :func:`read_routes` answers besides a table.
NOT_A_PROXY = "not-a-proxy"  # the server answered, and it is not LiteLLM
UNREAD = "unread"  # it did not answer (or refused us): keep what we had


def parse_model_info(payload: Any) -> RouteTable | None:
    """The table in a ``/model/info`` body, or ``None`` when the body is not
    LiteLLM's (no entry carries ``litellm_params``)."""
    if not isinstance(payload, dict):
        return None
    data = payload.get("data")
    if not isinstance(data, list):
        return None
    if not any(isinstance(e, dict) and isinstance(e.get("litellm_params"), dict) for e in data):
        return None
    out: list[Upstream] = []
    seen: dict[str, int] = {}
    for e in data:
        if len(out) >= MAX_ENTRIES:
            break
        if not isinstance(e, dict):
            continue
        alias = str(e.get("model_name") or "").strip()
        if not alias:
            continue
        params = e.get("litellm_params") if isinstance(e.get("litellm_params"), dict) else {}
        model = str(params.get("model") or "")
        place = upstream_place(model, str(params.get("api_base") or ""))
        # One name can be served by several deployments (LiteLLM load-balances
        # them): if ANY of them is cloud, a request may go there.
        if alias in seen:
            i = seen[alias]
            prev = out[i]
            if prev.place == PLACE_LOCAL and place != PLACE_LOCAL:
                out[i] = Upstream(alias=alias, model=model, place=PLACE_CLOUD)
            continue
        seen[alias] = len(out)
        out.append(Upstream(alias=alias, model=model, place=place))
    return RouteTable(entries=tuple(out), read_at=time.time())


def read_routes(
    node: FleetNode, *, get: probes.Getter = probes._get, key: str = ""
) -> "RouteTable | str":
    """Read one node's routing table: a :class:`RouteTable`,
    :data:`NOT_A_PROXY`, or :data:`UNREAD`. BLOCKING (one HTTP GET with the
    probes' short timeout): the sampler calls it from a worker thread, never
    the loop. Never raises.

    *key* (v1.365.2): the node's own saved API key, sent as a Bearer token —
    a proxy with a master key answers ``/model/info`` 401 without it, and the
    table (and with it the cloud verdict) would then never be read. The key
    goes in the request header only; it is never logged or stored here."""
    try:
        root = probes.normalize_root(getattr(node, "base_url", "") or "")
        if not root:
            return NOT_A_PROXY
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        try:
            resp = get(f"{root}/model/info", headers=headers)
        except Exception:  # noqa: BLE001 — no answer: keep what we had
            return UNREAD
        code = int(getattr(resp, "status_code", 0) or 0)
        if code in (404, 405):
            return NOT_A_PROXY
        if not 200 <= code < 300:
            return UNREAD  # 401/403 (a keyed proxy), 5xx: we could not read it
        try:
            body = resp.json()
        except Exception:  # noqa: BLE001 — answered, and not JSON: not LiteLLM
            return NOT_A_PROXY
        table = parse_model_info(body)
        return table if table is not None else NOT_A_PROXY
    except Exception:  # noqa: BLE001
        return UNREAD
