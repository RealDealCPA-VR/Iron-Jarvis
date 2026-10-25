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


#: What a LiteLLM entry's ``model_info`` says a served name can do (the
#: gateway-key fix): ``supports_vision``, ``supports_function_calling`` and
#: ``max_input_tokens``, read only when the proxy states them. ``None`` =
#: the proxy did not say, which stays unknown — never guessed. v1.387.0:
#: ``max_output`` — ``max_output_tokens`` (else LiteLLM's older
#: ``max_tokens``), the output limit the reply budget prefers
#: (providers/reply_budget.py).
CAP_KEYS = ("vision", "tool_use", "context_window", "max_output")


def _flag(value: Any) -> bool | None:
    """A capability flag as the proxy stated it: a real boolean, else None."""
    return value if isinstance(value, bool) else None


def _window(value: Any) -> int | None:
    """A positive token count, else None (``null``, 0, text, a bool)."""
    if isinstance(value, bool):
        return None
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def _merge_flag(a: bool | None, b: bool | None) -> bool | None:
    """Two deployments of one name: a flag both state the same way, else
    unknown (a request may land on either, so neither claim holds alone)."""
    return a if a is not None and a == b else None


def _merge_window(a: int | None, b: int | None) -> int | None:
    """The smaller of two stated windows; unknown when either is unknown."""
    if a is None or b is None:
        return None
    return min(a, b)


@dataclass(frozen=True)
class Upstream:
    """One name a proxy serves and where it goes. No ``api_base`` is kept:
    only the verdict is needed after the read, and an address can carry a
    query string nobody should have to see in a saved file.

    The capability fields (the gateway-key fix) are what the proxy's own
    ``model_info`` states for the name; ``None`` = it did not say."""

    alias: str
    model: str
    place: str
    vision: bool | None = None
    tool_use: bool | None = None
    context_window: int | None = None
    #: v1.387.0 (LAST, so positional constructions keep their meaning).
    max_output: int | None = None

    def caps(self) -> dict[str, Any]:
        return {
            "vision": self.vision, "tool_use": self.tool_use,
            "context_window": self.context_window, "max_output": self.max_output,
        }


def _merge_caps(entries: "list[Upstream]") -> dict[str, Any]:
    """The capabilities several deployments share (see the two merges)."""
    if not entries:
        return {k: None for k in CAP_KEYS}
    vision, tools, window = entries[0].vision, entries[0].tool_use, entries[0].context_window
    out_cap = entries[0].max_output
    for e in entries[1:]:
        vision = _merge_flag(vision, e.vision)
        tools = _merge_flag(tools, e.tool_use)
        window = _merge_window(window, e.context_window)
        out_cap = _merge_window(out_cap, e.max_output)
    return {"vision": vision, "tool_use": tools, "context_window": window, "max_output": out_cap}


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

    def caps(self, model: str) -> dict[str, Any]:
        """What the proxy's ``model_info`` states *model* can do:
        ``{"vision", "tool_use", "context_window"}``, each ``None`` when the
        table does not say. Matched on the served name; wildcard entries the
        way :meth:`place` treats them (every matching deployment must agree);
        a name the table does not list stays unknown — never guessed. Never
        raises."""
        unknown = {k: None for k in CAP_KEYS}
        try:
            name = str(model or "").strip()
            if not name or not self.entries:
                return unknown
            exact = [e for e in self.entries if e.alias == name]
            if exact:
                return _merge_caps(exact)
            hits = [
                e for e in self.entries if "*" in e.alias and fnmatch.fnmatchcase(name, e.alias)
            ]
            return _merge_caps(hits) if hits else unknown
        except Exception:  # noqa: BLE001 — unknown, never a guess
            return unknown

    def signature(self) -> tuple[tuple[Any, ...], ...]:
        """What the verdicts depend on (the read time is not part of it)."""
        return tuple(
            (e.alias, e.model, e.place, e.vision, e.tool_use, e.context_window, e.max_output)
            for e in self.entries
        )

    def cloud_names(self) -> list[str]:
        return [e.alias for e in self.entries if e.place != PLACE_LOCAL]

    def to_json(self) -> dict[str, Any]:
        rows: list[dict[str, Any]] = []
        for e in self.entries:
            row: dict[str, Any] = {"alias": e.alias, "model": e.model, "place": e.place}
            # Only what the proxy stated (a saved table from before the
            # gateway-key fix has none of these keys and loads as unknown).
            for key, value in e.caps().items():
                if value is not None:
                    row[key] = value
            rows.append(row)
        return {"read_at": self.read_at, "entries": rows}

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
                vision=_flag(e.get("vision")),
                tool_use=_flag(e.get("tool_use")),
                context_window=_window(e.get("context_window")),
                max_output=_window(e.get("max_output")),
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
        info = e.get("model_info") if isinstance(e.get("model_info"), dict) else {}
        vision = _flag(info.get("supports_vision"))
        tools = _flag(info.get("supports_function_calling"))
        window = _window(info.get("max_input_tokens"))
        # v1.387.0: the model's own OUTPUT limit, LiteLLM's current key first,
        # then its LEGACY ``max_tokens`` — which LiteLLM documents as the
        # output limit when the provider states one, else the INPUT limit; a
        # legacy value equal to ``max_input_tokens`` is that second case and
        # says nothing about the output.
        out_cap = _window(info.get("max_output_tokens"))
        if out_cap is None:
            legacy = _window(info.get("max_tokens"))
            out_cap = legacy if legacy and legacy != window else None
        # One name can be served by several deployments (LiteLLM load-balances
        # them): if ANY of them is cloud, a request may go there. A capability
        # holds only when every deployment states it the same way.
        if alias in seen:
            i = seen[alias]
            prev = out[i]
            out[i] = Upstream(
                alias=alias,
                model=prev.model if prev.place != PLACE_LOCAL or place == PLACE_LOCAL else model,
                place=PLACE_CLOUD if PLACE_CLOUD in (prev.place, place) else PLACE_LOCAL,
                vision=_merge_flag(prev.vision, vision),
                tool_use=_merge_flag(prev.tool_use, tools),
                context_window=_merge_window(prev.context_window, window),
                max_output=_merge_window(prev.max_output, out_cap),
            )
            continue
        seen[alias] = len(out)
        out.append(
            Upstream(
                alias=alias, model=model, place=place,
                vision=vision, tool_use=tools, context_window=window,
                max_output=out_cap,
            )
        )
    return RouteTable(entries=tuple(out), read_at=time.time())


def read_routes(
    node: FleetNode,
    *,
    get: probes.Getter = probes._get,
    key: str = "",
    headers: dict[str, str] | None = None,
) -> "RouteTable | str":
    """Read one node's routing table: a :class:`RouteTable`,
    :data:`NOT_A_PROXY`, or :data:`UNREAD`. BLOCKING (one HTTP GET with the
    probes' short timeout): the sampler calls it from a worker thread, never
    the loop. Never raises.

    *key* (v1.365.2): the node's own saved API key, sent as a Bearer token —
    a proxy with a master key answers ``/model/info`` 401 without it, and the
    table (and with it the cloud verdict) would then never be read. The key
    goes in the request header only; it is never logged or stored here.
    *headers* (the gateway-key fix) wins when given: the registry's
    ``headers_for(node)``, the one helper every gateway read goes through."""
    try:
        root = probes.normalize_root(getattr(node, "base_url", "") or "")
        if not root:
            return NOT_A_PROXY
        if headers is None:
            from .endpoint_auth import auth_headers

            headers = auth_headers(key)
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
