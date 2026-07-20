"""Local fleet routes (/fleet/*) — the inference-hardware surface.

Reads the FleetSampler's LAST snapshot rather than probing inline: the
dashboard polls this hot, and a request that fans out to four hosts would
turn a slow node into a slow page. Only the explicitly user-driven routes
(add / detect / verify / probe / ?refresh=1) touch the network, and each of
those runs off the event loop.

The rule this module exists to protect: **a metric we could not read is
``null``, never ``0``.** A node bound to localhost on another host is not a
node running zero requests — it is a node we cannot see, and it says so
(``status="not-probeable"``, ``metrics=None``, a bind hint). Same for the
tool-use verification: an unreachable node is an UNKNOWN capability, not a
``False``. And the savings estimate always ships the baseline it was priced
against, so the UI can never render a bare "you saved $X".

``d`` is the create_app deps object: ``d.fleet`` (FleetRegistry),
``d.fleet_sampler`` (FleetSampler), ``d.platform`` (config / providers /
router / observability), ``d._persist_config`` (atomic config.toml write).
The ``fleet.*`` modules are imported lazily inside handlers so importing the
daemon never depends on them being present.
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

#: Priced against this when ``config.fleet_savings_baseline`` is unset. An
#: estimate is only honest when its BASIS is named — every /fleet/usage
#: response carries the comparison provider+model back to the UI.
_DEFAULT_BASELINE = "anthropic:claude-opus-4-8"

#: Agent types that count as "coding work" for code routing. Wave 2 owns the
#: real set + the routing hook; this is the Wave 1 default so /fleet/code-route
#: can answer honestly (and "off") before that lands.
_DEFAULT_CODE_TASK_CLASSES = ("builder", "maintainer", "reviewer")

#: Providers whose usage is LOCAL hardware even though they predate the fleet
#: (the two auto-seeded endpoint slots). Everything else that is not a
#: ``fleet-*`` provider is billed cloud tokens.
_LOCAL_PROVIDERS = frozenset({"ollama", "custom"})

#: The tiny tool a /verify completion is asked to call. Deliberately trivial —
#: we are testing whether the server emits a tool call AT ALL, not reasoning.
_PING_TOOL = {
    "name": "ping",
    "description": "Reply that you are alive. Call this tool exactly once.",
    "input_schema": {
        "type": "object",
        "properties": {"ok": {"type": "boolean"}},
        "required": ["ok"],
    },
}


def _vision_probe_image_b64() -> str:
    """A 96×96 solid-RED JPEG generated in-process — the vision-verify probe
    image (no bundled asset, no network)."""
    import base64
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (96, 96), (220, 30, 30)).save(buf, format="JPEG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


class FleetNodeBody(BaseModel):
    """Add a node. Only ``base_url`` is required; ``id`` is slugged from the
    host when omitted, ``api_key_name`` names a VAULT entry (never a token).
    ``routable`` + ``default_model`` let the Connections page add a custom
    endpoint as a ready-to-route provider in ONE call."""

    base_url: str
    label: str = ""
    id: str = ""
    api_key_name: str = ""
    routable: bool = False
    default_model: str = ""


class FleetNodePatch(BaseModel):
    """Edit a node. Every field is optional — ``None`` means "leave alone", so
    a UI that only toggles ``routable`` can't blank the rest of the record."""

    label: str | None = None
    enabled: bool | None = None
    routable: bool | None = None
    tool_use: bool | None = None
    vision: bool | None = None
    api_key_name: str | None = None
    default_model: str | None = None


class FleetProbeBody(BaseModel):
    """Ad-hoc probe for the add-node form. Nothing is saved."""

    base_url: str
    api_key_name: str = ""


class FleetVerifyBody(BaseModel):
    """Live tool-capability check; ``model`` overrides the node default."""

    model: str = ""


class CodeRouteBody(BaseModel):
    """Configure code routing. ``target`` is "provider:model"; ``task_classes``
    is a CSV override ("" = the built-in set)."""

    enabled: bool | None = None
    target: str | None = None
    task_classes: str | None = None


def _dump(obj: Any) -> Any:
    """Serialize a fleet model to plain JSON-able data.

    The fleet models live in a sibling module written alongside this one, so we
    assume only "it can describe itself": pydantic v2 ``model_dump``, v1
    ``dict``, or an already-plain value.
    """
    if obj is None:
        return None
    for attr in ("model_dump", "dict"):
        fn = getattr(obj, attr, None)
        if callable(fn):
            try:
                return fn()
            except TypeError:  # pragma: no cover - a stray non-model .dict
                pass
    return obj


def _err(exc: BaseException) -> str:
    """Verbatim, bounded error text. Never a friendly lie."""
    return f"{type(exc).__name__}: {exc}"[:300]


def _history_rows(points: Any) -> list[dict[str, Any]]:
    """The sampler's ``(t, NodeMetrics)`` history as chart rows.

    Rates are DERIVED here rather than stored: the sampler retains raw counters
    and ``sampler.derive`` is the single place that knows a counter which went
    backwards means "the server restarted" (rates null + ``counter_reset``), not
    negative throughput. The first row has no window, so its rates are null —
    never zeros. ``t`` is the sampler's logical clock, not wall time.
    """
    try:
        from ...fleet.sampler import derive
    except Exception:  # noqa: BLE001 — a missing rate overlay beats a 500
        derive = None  # type: ignore[assignment]

    rows: list[dict[str, Any]] = []
    prev: tuple[Any, Any] | None = None
    for point in points or []:
        if not (isinstance(point, (tuple, list)) and len(point) == 2):
            continue  # unknown shape: skip the point rather than invent one
        raw_t, metrics = point
        cur = (raw_t, metrics)
        rates = None
        if prev is not None and derive is not None:
            try:
                rates = _dump(derive(prev, cur))
            except Exception:  # noqa: BLE001 — unknown rates, not zero rates
                rates = None
        rows.append(
            {
                "t": float(raw_t) if isinstance(raw_t, (int, float)) else None,
                "metrics": _dump(metrics),
                "rates": rates,
            }
        )
        prev = cur
    return rows


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")


def _derive_id(registry, base_url: str, label: str) -> str:
    """A stable, readable node id from the label or the host:port, made unique
    against the nodes already registered (``spark-049d``, ``spark-049d-2``)."""
    from urllib.parse import urlparse

    base = _slug(label)
    if not base:
        parsed = urlparse(base_url if "//" in base_url else f"http://{base_url}")
        host = parsed.hostname or ""
        base = _slug(f"{host}-{parsed.port}" if parsed.port else host)
    base = base or "node"
    taken = {getattr(n, "id", "") for n in registry.nodes()}
    if base not in taken:
        return base
    for n in range(2, 100):
        if f"{base}-{n}" not in taken:
            return f"{base}-{n}"
    return f"{base}-{int(time.time())}"


def register(app: FastAPI, d) -> None:
    """Attach the /fleet routes to *app*; ``d`` is the create_app deps object."""

    def _node_or_404(node_id: str):
        node = d.fleet.get(node_id)
        if node is None:
            raise HTTPException(status_code=404, detail="unknown node")
        return node

    def _code_route_view() -> dict[str, Any]:
        """Code-routing config + what it RESOLVES to right now.

        ``why`` is plain English because this answers a user question ("is my
        coding work going to the local box?"), and every field it reports is
        read live — a target pointing at a deleted node says so instead of
        looking configured.
        """
        cfg = d.platform.config
        enabled = bool(getattr(cfg, "fleet_code_route_enabled", False))
        target = (getattr(cfg, "fleet_code_target", "") or "").strip()
        raw_classes = (getattr(cfg, "fleet_code_task_classes", "") or "").strip()
        classes = (
            [c.strip() for c in raw_classes.split(",") if c.strip()]
            if raw_classes
            else list(_DEFAULT_CODE_TASK_CLASSES)
        )

        provider = model = ""
        available: bool | None = None
        circuit = "unknown"
        tool_use: bool | None = None
        if target:
            from ...providers.routing import parse_pm

            pm = parse_pm(target)
            if pm:
                provider, model = pm

        if not enabled:
            why = "code routing is off"
        elif not target:
            why = "code routing is on but no target model is set"
        elif not provider:
            why = f"target {target!r} isn't a provider:model pair"
        else:
            node = _node_for_provider(provider)
            mgr = getattr(d.platform, "providers", None)
            try:
                available = bool(mgr.available(provider)) if mgr else None
            except Exception:  # noqa: BLE001 — availability must never 500 a GET
                available = None
            health = getattr(getattr(d.platform, "router", None), "health", None)
            if health is not None:
                try:
                    circuit = "open" if health.is_open(provider) else "closed"
                except Exception:  # noqa: BLE001
                    circuit = "unknown"
            tool_use = getattr(node, "tool_use", None) if node is not None else None
            if node is None:
                why = f"{provider} isn't a routable fleet node"
            elif available is not True:
                # False AND None both land here on purpose: if we cannot CONFIRM
                # the target is available, saying "coding work goes there" is a
                # claim we haven't earned. Unknown is reported as unknown.
                why = (
                    f"{provider} is configured but not currently available"
                    if available is False
                    else f"{provider} is configured but not currently available "
                    "(availability couldn't be checked)"
                )
            elif circuit == "open":
                why = f"{provider}'s circuit is open after repeated failures"
            elif tool_use is False:
                why = f"{provider} can't call tools, so coding work would fail"
            else:
                unverified = " (tool use unverified)" if tool_use is None else ""
                why = f"coding work goes to {target}{unverified}"

        return {
            "enabled": enabled,
            "target": target,
            "task_classes": classes,
            "effective": {
                "provider": provider,
                "model": model,
                "available": available,
                "circuit": circuit,
                "tool_use": tool_use,
                "why": why,
            },
        }

    def _node_for_provider(provider: str):
        """The routable node a provider name refers to (``fleet-<id>``, or the
        bare id). ``None`` when nothing routable matches."""
        want = (provider or "").strip()
        if not want:
            return None
        for node in d.fleet.routable_nodes():
            nid = getattr(node, "id", "")
            if want in (f"fleet-{nid}", nid):
                return node
        return None

    # --- read: served from the sampler, no network ------------------------

    @app.get("/fleet")
    def fleet_overview() -> dict[str, Any]:
        """Every node's LAST snapshot + sampler state + code routing.

        Zero network in the handler — ``touch()`` just tells the sampler a
        human is watching so it speeds up to the active cadence. THIS ROUTE
        NEVER RAISES: the dashboard polls it continuously, so a fault anywhere
        in the fleet stack degrades to an empty list plus the verbatim error
        rather than breaking the page.
        """
        try:
            sampler = d.fleet_sampler
            sampler.touch()
            return {
                "nodes": [_dump(s) for s in sampler.snapshots()],
                "sampling": sampler.status(),
                "code_route": _code_route_view(),
                "error": "",
            }
        except Exception as exc:  # noqa: BLE001 — a fleet fault can't break polling
            return {
                "nodes": [],
                "sampling": {},
                "code_route": {},
                "error": _err(exc),
            }

    @app.get("/fleet/snapshot")
    async def fleet_snapshot(refresh: int = 0) -> dict[str, Any]:
        """Same shape as ``GET /fleet``; ``refresh=1`` forces ONE synchronous
        sampling pass first (in a thread — probing four hosts must never block
        the event loop). Also never raises."""
        try:
            sampler = d.fleet_sampler
            if refresh:
                await asyncio.to_thread(sampler.sample_once)
            sampler.touch()
            return {
                "nodes": [_dump(s) for s in sampler.snapshots()],
                "sampling": sampler.status(),
                "code_route": _code_route_view(),
                "error": "",
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "nodes": [],
                "sampling": {},
                "code_route": {},
                "error": _err(exc),
            }

    @app.get("/fleet/nodes/{node_id}/history")
    def fleet_history(node_id: str, limit: int = 240) -> dict[str, Any]:
        """The retained telemetry series for one node (sparklines).

        ``truncated`` is honest about the window being clipped, so a chart can
        say "last 240 samples" instead of implying it has the whole history.
        Rows are built over the FULL series and clipped afterwards, so the
        oldest row in the window keeps the rates derived from the point before
        it instead of starting the chart with a null.
        """
        _node_or_404(node_id)
        try:
            limit = max(1, min(int(limit), 5000))
        except (TypeError, ValueError):
            limit = 240
        rows = _history_rows(d.fleet_sampler.series(node_id))
        truncated = len(rows) > limit
        return {
            "node_id": node_id,
            "samples": rows[-limit:],
            "truncated": truncated,
        }

    # --- write: explicit user actions, network allowed --------------------

    @app.post("/fleet/nodes")
    async def fleet_add_node(body: FleetNodeBody) -> dict[str, Any]:
        """Add a node: detect what it is, probe it once, persist it.

        A failed probe still SAVES the node — a box that happens to be asleep
        is still part of the fleet — and returns its honest snapshot.
        """
        from ...fleet.models import FleetNode
        from ...fleet.probes import detect_kind, probe_node

        base_url = (body.base_url or "").strip()
        if not base_url:
            raise HTTPException(status_code=400, detail="base_url is required")

        kind, _reason = await asyncio.to_thread(detect_kind, base_url)
        node = FleetNode(
            id=(body.id or "").strip() or _derive_id(d.fleet, base_url, body.label),
            label=(body.label or "").strip(),
            base_url=base_url,
            kind=kind,
            kind_detected_at=time.time(),
            source="user",
            api_key_name=(body.api_key_name or "").strip(),
            routable=bool(body.routable),
            default_model=(body.default_model or "").strip(),
        )
        try:
            node = d.fleet.add(node) or node
        except ValueError as exc:  # invalid/duplicate id — surface the reason
            raise HTTPException(status_code=400, detail=str(exc))
        # A routable node becomes a provider NOW, not on the next boot —
        # register_providers is idempotent over the whole routable set.
        if node.routable:
            try:
                d.fleet.register_providers(d.platform.providers)
            except Exception:  # noqa: BLE001 — registration can't fail the add
                pass

        snapshot, children = await asyncio.to_thread(probe_node, node)
        if children:
            # A LiteLLM proxy names its own backends; adopt them so the fleet
            # shows the real topology instead of one opaque box.
            try:
                d.fleet.absorb_children(node.id, children)
            except Exception:  # noqa: BLE001 — a bad child can't fail the add
                pass
        return {
            "node": _dump(node),
            "snapshot": _dump(snapshot),
            "children": [_dump(c) for c in children],
        }

    @app.patch("/fleet/nodes/{node_id}")
    def fleet_patch_node(node_id: str, body: FleetNodePatch) -> dict[str, Any]:
        """Edit a node. Only the fields actually sent are written."""
        _node_or_404(node_id)
        fields = {k: v for k, v in body.model_dump().items() if v is not None}
        if not fields:
            return {"node": _dump(d.fleet.get(node_id))}
        try:
            node = d.fleet.update(node_id, **fields)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        # Keep the provider registry in step LIVE: a node flipped routable gets
        # its factory now; one flipped off is unregistered so the router can't
        # pick it again this session.
        final = node or d.fleet.get(node_id)
        try:
            if final is not None and final.routable:
                d.fleet.register_providers(d.platform.providers)
            elif final is not None and "routable" in fields:
                d.platform.providers.unregister(f"fleet-{node_id}")
        except Exception:  # noqa: BLE001 — registry sync can't fail the edit
            pass
        return {"node": _dump(final)}

    @app.delete("/fleet/nodes/{node_id}")
    def fleet_delete_node(node_id: str) -> dict[str, Any]:
        """Remove a user-added node. The two auto-seeded endpoint slots are NOT
        deletable here — they're config, and deleting them would silently
        reappear on the next boot. We say where they actually live."""
        _node_or_404(node_id)
        try:
            d.fleet.remove(node_id)
        except ValueError:
            raise HTTPException(
                status_code=404,
                detail=(
                    "this endpoint is managed in Settings "
                    "(ollama_base_url / custom_base_url)"
                ),
            )
        # No ghost providers: drop the factory too (reachable() also answers
        # False for deleted fleet ids — belt and suspenders).
        try:
            d.platform.providers.unregister(f"fleet-{node_id}")
        except Exception:  # noqa: BLE001
            pass
        return {"ok": True}

    @app.post("/fleet/nodes/{node_id}/detect")
    async def fleet_detect_node(node_id: str) -> dict[str, Any]:
        """Re-run kind detection (a box that changed from Ollama to vLLM, or
        one that was asleep when it was added)."""
        node = _node_or_404(node_id)
        from ...fleet.probes import detect_kind

        kind, reason = await asyncio.to_thread(detect_kind, node.base_url)
        updated = d.fleet.update(node_id, kind=kind, kind_detected_at=time.time())
        return {
            "node": _dump(updated or d.fleet.get(node_id)),
            "kind": kind,
            "reason": reason,
        }

    @app.post("/fleet/nodes/{node_id}/verify")
    async def fleet_verify_node(
        node_id: str, body: FleetVerifyBody | None = None
    ) -> dict[str, Any]:
        """LIVE tool-capability check: one tiny completion with a trivial tool.

        Servers advertise an OpenAI-compatible API and then silently drop
        ``tools`` — the only trustworthy answer is to ask. On a provider error
        we return ``tool_use: null`` and the verbatim error and record NOTHING:
        an unreachable node is an UNKNOWN capability, not a node that can't
        call tools. Recording a False here would permanently demote a box that
        was merely asleep.
        """
        _node_or_404(node_id)
        from ...providers.adapters.base import LLMMessage

        model = ((body.model if body else "") or "").strip()
        provider = f"fleet-{node_id}"
        try:
            adapter = d.platform.providers.get(provider, model or None)
            if getattr(adapter, "provider", "") == "mock":
                # The mock calls any tool it is offered, flawlessly. Recording
                # True off it would be a fabricated claim about real hardware.
                return {
                    "node": _dump(d.fleet.get(node_id)),
                    "tool_use": None,
                    "model": model,
                    "error": (
                        f"{provider} resolved to the offline mock adapter, so "
                        "nothing was actually asked of this node"
                    ),
                    "hint": "turn on Routable for this node so it registers as a provider",
                }
            resp = await adapter.complete(
                system="You are a connectivity check. Call the ping tool.",
                messages=[LLMMessage(role="user", content="Call the ping tool.")],
                tools=[_PING_TOOL],
            )
        except Exception as exc:  # noqa: BLE001 — unknown capability, not False
            hint = ""
            node = d.fleet.get(node_id)
            if node is not None and not getattr(node, "routable", False):
                hint = "turn on Routable for this node so it registers as a provider"
            return {
                "node": _dump(node),
                "tool_use": None,
                "model": model,
                "error": _err(exc),
                "hint": hint,
            }

        # A response object with NO tool_calls attribute at all means we learned
        # nothing (an adapter shape we don't understand) — that is UNKNOWN, not
        # "cannot use tools". Persisting a hard False there would permanently
        # bar a capable node from tool work on the strength of a non-answer.
        if not hasattr(resp, "tool_calls"):
            return {
                "node": _dump(d.fleet.get(node_id)),
                "tool_use": None,
                "model": model,
                "error": "the reply carried no tool_calls field — capability unknown",
                "hint": "",
            }
        tool_use = bool(getattr(resp, "tool_calls", None))
        d.fleet.update(node_id, tool_use=tool_use, verified_at=time.time())
        # The completion above proves the endpoint is REACHABLE right now —
        # record it, so availability agrees with what verify just observed.
        # (Previously a green "tools ✓" could still read unavailable until the
        # sampler's next pass — the two signals were decoupled.)
        try:
            d.fleet.set_reachable(node_id, True)
        except Exception:  # noqa: BLE001 — telemetry sync never fails verify
            pass

        # VISION probe: a generated solid-red square + "name the dominant
        # color". Correct answer → vision True; wrong/empty → False (the model
        # answered but didn't SEE it); an ERROR → unknown, nothing recorded —
        # many text-only servers reject image content outright, but a
        # transport fault must not permanently brand a multimodal node blind.
        vision: bool | None = None
        vision_error = ""
        try:
            vresp = await adapter.complete(
                system="You are a vision connectivity check.",
                messages=[
                    LLMMessage(
                        role="user",
                        content=(
                            "Answer with ONE word: the dominant color of this image."
                        ),
                        images=[
                            {
                                "data_b64": _vision_probe_image_b64(),
                                "media_type": "image/jpeg",
                            }
                        ],
                    )
                ],
                tools=[],
            )
            answer = (getattr(vresp, "text", "") or "").strip().lower()
            vision = "red" in answer
        except Exception as exc:  # noqa: BLE001 — unknown capability, not False
            vision_error = _err(exc)
        if vision is not None:
            d.fleet.update(node_id, vision=vision, verified_at=time.time())
        return {
            "node": _dump(d.fleet.get(node_id)),
            "tool_use": tool_use,
            "vision": vision,
            "vision_error": vision_error,
            "model": model or getattr(adapter, "model", ""),
            "error": "",
            "hint": "",
        }

    @app.post("/fleet/probe")
    async def fleet_probe(body: FleetProbeBody) -> dict[str, Any]:
        """Detect + probe an UNSAVED base_url for the add-node form.

        Probe-only: nothing is persisted. Always 200 (mirroring
        ``/providers/endpoint-models``) — an unreachable endpoint returns its
        honest snapshot with the error so the form can show it and still let
        the user save the node.
        """
        base_url = (body.base_url or "").strip()
        if not base_url:
            raise HTTPException(status_code=400, detail="base_url is required")
        try:
            from ...fleet.models import FleetNode
            from ...fleet.probes import detect_kind, probe_node

            kind, reason = await asyncio.to_thread(detect_kind, base_url)
            node = FleetNode(
                id="probe",
                base_url=base_url,
                kind=kind,
                api_key_name=(body.api_key_name or "").strip(),
            )
            snapshot, children = await asyncio.to_thread(probe_node, node)
        except Exception as exc:  # noqa: BLE001 — an odd server can't 500 the form
            return {
                "kind": "unknown",
                "reason": "",
                "snapshot": None,
                "children": [],
                "error": _err(exc),
            }
        return {
            "kind": kind,
            "reason": reason,
            "snapshot": _dump(snapshot),
            "children": [_dump(c) for c in children],
            "error": "",
        }

    # --- attribution + savings --------------------------------------------

    @app.get("/fleet/usage")
    def fleet_usage(days: int = 30) -> dict[str, Any]:
        """Local-vs-cloud token attribution and an ESTIMATED avoided spend.

        Rides the existing usage rollup (no new store): ``by_model`` rows whose
        provider is a fleet node (or the two legacy local slots) are LOCAL and
        cost nothing; everything else is cloud. The saving is what those local
        tokens WOULD have cost on a named baseline model — the response always
        carries ``comparison_provider``/``comparison_model``/``basis`` so the
        UI can never present a bare, unattributed savings number.
        """
        from ...eval.pricing import cost_for
        from ...providers.routing import parse_pm

        try:
            days = max(1, min(int(days), 3650))
        except (TypeError, ValueError):
            days = 30

        cfg = d.platform.config
        baseline = (getattr(cfg, "fleet_savings_baseline", "") or "").strip()
        pm = parse_pm(baseline) if baseline else None
        if not pm or not pm[1]:
            # A provider with no model prices at $0 — that would read as "you
            # saved nothing" rather than "your baseline is misconfigured".
            pm = parse_pm(_DEFAULT_BASELINE)
        comparison_provider, comparison_model = pm

        summary = d.platform.observability.usage_summary(days) or {}
        labels = {getattr(n, "id", ""): getattr(n, "label", "") for n in d.fleet.nodes()}

        by_node: dict[str, dict[str, Any]] = {}
        local_tokens = cloud_tokens = 0
        cloud_cost_usd = est_avoided_usd = 0.0
        for row in summary.get("by_model") or []:
            provider = str(row.get("provider") or "")
            in_tok = int(row.get("input_tokens") or 0)
            out_tok = int(row.get("output_tokens") or 0)
            is_local = provider.startswith("fleet-") or provider in _LOCAL_PROVIDERS
            if not is_local:
                cloud_tokens += in_tok + out_tok
                cloud_cost_usd += float(row.get("cost_usd") or 0.0)
                continue
            local_tokens += in_tok + out_tok
            avoided = cost_for(comparison_provider, comparison_model, in_tok, out_tok)
            est_avoided_usd += avoided
            node_id = provider[len("fleet-"):] if provider.startswith("fleet-") else provider
            entry = by_node.setdefault(
                node_id,
                {
                    "node_id": node_id,
                    "label": labels.get(node_id, ""),
                    "provider": provider,
                    "models": [],
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "runs": 0,
                    "est_avoided_usd": 0.0,
                },
            )
            model = str(row.get("model") or "")
            if model and model not in entry["models"]:
                entry["models"].append(model)
            entry["input_tokens"] += in_tok
            entry["output_tokens"] += out_tok
            entry["runs"] += int(row.get("runs") or 0)
            entry["est_avoided_usd"] += avoided

        # Is the baseline actually PRICED? cost_for returns 0.0 for a model it
        # doesn't know, so an unpriced baseline would render "$0.00 avoided" —
        # indistinguishable from "your local hardware saved you nothing". Probe
        # it once with a fixed nonzero token count; unpriced => report null and
        # say why, never a fabricated zero.
        baseline_priced = (
            cost_for(comparison_provider, comparison_model, 1_000_000, 1_000_000) > 0
        )
        return {
            "days": days,
            "by_node": sorted(
                by_node.values(), key=lambda e: e["est_avoided_usd"], reverse=True
            ),
            "local_tokens": local_tokens,
            "cloud_tokens": cloud_tokens,
            "cloud_cost_usd": round(cloud_cost_usd, 6),
            "est_avoided_usd": round(est_avoided_usd, 6) if baseline_priced else None,
            "baseline_priced": baseline_priced,
            "comparison_provider": comparison_provider,
            "comparison_model": comparison_model,
            "basis": (
                "estimate: what the local tokens would have cost on "
                f"{comparison_provider}:{comparison_model} at list price"
            )
            if baseline_priced
            else (
                f"no list price on file for {comparison_provider}:{comparison_model} — "
                "set fleet_savings_baseline to a priced model to see an estimate"
            ),
        }

    # --- code routing ------------------------------------------------------

    @app.get("/fleet/code-route")
    def fleet_code_route() -> dict[str, Any]:
        return _code_route_view()

    @app.put("/fleet/code-route")
    def fleet_set_code_route(body: CodeRouteBody) -> dict[str, Any]:
        """Point coding work at a local node. Validated BEFORE it's persisted:
        a target that doesn't parse, or names a node that isn't routable, is
        rejected rather than silently saved and quietly ignored at runtime."""
        from ...providers.routing import parse_pm

        cfg = d.platform.config
        if body.target is not None:
            target = body.target.strip()
            if target:
                pm = parse_pm(target)
                if not pm or not pm[0] or not pm[1]:
                    raise HTTPException(
                        status_code=400,
                        detail="target must look like provider:model",
                    )
                if _node_for_provider(pm[0]) is None:
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            f"{pm[0]} isn't a routable fleet node — turn on "
                            "Routable for the node you want coding work to use"
                        ),
                    )
            cfg.fleet_code_target = target
        if body.enabled is not None:
            if body.enabled and not (cfg.fleet_code_target or "").strip():
                raise HTTPException(
                    status_code=400,
                    detail="set a target model before turning code routing on",
                )
            cfg.fleet_code_route_enabled = bool(body.enabled)
        if body.task_classes is not None:
            cfg.fleet_code_task_classes = body.task_classes.strip()
        d._persist_config(
            [
                "fleet_code_route_enabled",
                "fleet_code_target",
                "fleet_code_task_classes",
            ]
        )
        return _code_route_view()
