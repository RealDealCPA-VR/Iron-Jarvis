"""Auto model routing routes (§6 — the routing model).

Expose the current routing state (+ the SUGGESTED cheapest routing model and the
derived tiers so the UI can recommend one), and flip Auto on/off. Turning Auto on
sets ``default_provider = "auto"`` (the ON switch the router keys off) and records
the chosen classifier; turning it off pins a concrete default model again.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI

from ..schemas import RoutingDisableBody, RoutingEnableBody
from ...core.config import persist_config_values


def register(app: FastAPI, d) -> None:
    def _view() -> dict[str, Any]:
        from ...providers import routing as R

        cfg = d.platform.config
        connected = R.connected_real_models(d.platform.providers, cfg)
        suggested = R.cheapest(connected)
        tiers = R.parse_tiers_json(cfg.routing_tiers_json or "") or R.derive_tiers(connected)
        return {
            "enabled": cfg.default_provider == "auto",
            "routing_model": cfg.routing_model,
            "connected": connected,
            "suggested": (
                {"provider": suggested[0], "model": suggested[1]} if suggested else None
            ),
            "tiers": {k: {"provider": v[0], "model": v[1]} for k, v in tiers.items()},
        }

    @app.get("/routing")
    def get_routing() -> dict[str, Any]:
        return _view()

    @app.post("/routing/enable")
    def enable_routing(body: RoutingEnableBody) -> dict[str, Any]:
        from ...providers import routing as R

        cfg = d.platform.config
        rm = (body.routing_model or "").strip()
        if not rm:  # default to the suggested cheapest connected model
            suggested = R.cheapest(R.connected_real_models(d.platform.providers, cfg))
            rm = R.format_pm(suggested) if suggested else ""
        cfg.default_provider = "auto"
        cfg.routing_model = rm
        persist_config_values(cfg.home, {"default_provider": "auto", "routing_model": rm})
        return _view()

    @app.get("/routing/quality")
    def get_routing_quality() -> dict[str, Any]:
        """The MODEL REPORT CARD (v1.169.0) — the evidence auto-tier judges on.

        The self-tuning router silently rates each LOCAL model on the average
        completion score of its evaluated sessions
        (``observability.local_quality``) against ``config.local_quality_bar``;
        the user could never see that judgment. This exposes it, read-only:
        one row per local ``(provider, model)`` — the granularity the router
        actually judges at (``_local_oracle`` passes the rung's model) — plus a
        row per task class the recorded runs carry.

        HONESTY RULES:
        * ``avg`` and ``clears`` come from ``observability.local_quality``
          ITSELF — the same function, min_samples gate, and bar the router
          consults — never a second implementation that can drift. ``avg`` is
          reported even below the evidence gate (``min_samples=1``) so the UI
          can say "avg 0.9 but only 2 of 3 sessions"; ``clears`` uses the REAL
          gate.
        * ``samples`` counts Evaluation rows (a session evaluated is one
          sample), matching how ``local_quality`` counts its scores.
        * Cloud providers NEVER appear — the bar judges local models only, and
          listing a cloud model would imply it is being judged too.
        * Connected local models with no recorded runs still get a zero-sample
          row, so a card can honestly say "not enough evidence yet (0 of N)".
          ``local_models`` only ever enumerates the two config slots
          (ollama/custom) — it never yields ``fleet-*`` endpoints or
          ``opencode-cli`` — so those are seeded here from their OWN registries
          (the fleet registry's routable nodes, the OpenCode local-model
          allowlist). Without that, a freshly added, verified fleet node was
          silently ABSENT instead of honestly unproven.
        """
        from sqlalchemy import func, or_
        from sqlmodel import select

        from ...core.db import session_scope
        from ...core.models import AgentRun
        from ...eval.models import Evaluation
        from ...providers.local import LOCAL_PREFIXES, LOCAL_PROVIDERS, local_models

        cfg = d.platform.config
        bar = float(getattr(cfg, "local_quality_bar", 0.75))
        # local_quality clamps to max(1, ...) internally; report the EFFECTIVE
        # gate so the UI's "N of M sessions" matches what actually gates.
        min_samples = max(1, int(getattr(cfg, "local_quality_min_samples", 3)))
        obs = d.platform.observability

        # (provider, model) -> {session_id -> set(task_classes)} over LOCAL
        # runs only, filtered IN SQL (AgentRun is unbounded — the
        # observability discipline).
        pairs: dict[tuple[str, str], dict[str, set[str]]] = {}
        eval_counts: dict[str, int] = {}
        try:
            with session_scope(d.platform.engine) as db:
                local_cond = or_(
                    AgentRun.provider.in_(sorted(LOCAL_PROVIDERS)),
                    *[AgentRun.provider.like(f"{p}%") for p in LOCAL_PREFIXES],
                )
                for sid, provider, model, at in db.exec(
                    select(
                        AgentRun.session_id,
                        AgentRun.provider,
                        AgentRun.model,
                        AgentRun.agent_type,
                    ).where(local_cond)
                ):
                    key = (str(provider or ""), str(model or ""))
                    tc = getattr(at, "value", at) if at is not None else ""
                    pairs.setdefault(key, {}).setdefault(str(sid), set()).add(
                        str(tc)
                    )
                session_ids = sorted({s for m in pairs.values() for s in m})
                if session_ids:
                    for sid, n in db.exec(
                        select(Evaluation.session_id, func.count())
                        .where(Evaluation.session_id.in_(session_ids))
                        .group_by(Evaluation.session_id)
                    ):
                        eval_counts[str(sid)] = int(n)
        except Exception:  # noqa: BLE001 — a report card degrades, never 500s
            pairs = {}
            eval_counts = {}

        try:
            for entry in local_models(d.platform.providers, cfg):
                pairs.setdefault(
                    (str(entry["provider"]), str(entry["model"] or "")), {}
                )
        except Exception:  # noqa: BLE001 — enumeration is best-effort
            pass

        # local_models <- connected_real_models never enumerates fleet-* nodes
        # or opencode-cli (KNOWN_MODELS has neither), so the honest zero-sample
        # state was UNREACHABLE for exactly the endpoints this page manages —
        # the UI rendered silent absence instead of "not enough evidence yet
        # (0 of N)". Seed them from the registries this routes layer already
        # owns; purely additive, the shared enumeration is untouched.
        try:
            from ...fleet.registry import provider_name as _fleet_provider_name

            fleet = getattr(d, "fleet", None)
            if fleet is not None:
                for node in fleet.routable_nodes():
                    name = _fleet_provider_name(node.id)
                    try:
                        if not d.platform.providers.available(name):
                            continue
                    except Exception:  # noqa: BLE001 — a probe fault skips it
                        continue
                    # Mirror the picker's fallback (connections.selectable_models
                    # uses `default_model or "default"`) so the seeded row names
                    # the model a user would actually pick.
                    model = str(getattr(node, "default_model", "") or "") or "default"
                    pairs.setdefault((name, model), {})
        except Exception:  # noqa: BLE001 — enumeration is best-effort
            pass
        try:
            providers = d.platform.providers
            if providers.available("opencode-cli"):
                for mid in providers._opencode_allowed():  # noqa: SLF001
                    if str(mid or ""):
                        pairs.setdefault(("opencode-cli", str(mid)), {})
        except Exception:  # noqa: BLE001 — enumeration is best-effort
            pass

        def _row(
            provider: str, model: str, task_class: str | None, samples: int
        ) -> dict[str, Any]:
            avg = obs.local_quality(
                provider, task_class=task_class, min_samples=1, model=model or None
            )
            # EXACTLY the router's own check (platform._local_oracle): same
            # function, same min_samples gate, same >= comparison.
            gated = obs.local_quality(
                provider,
                task_class=task_class,
                min_samples=min_samples,
                model=model or None,
            )
            return {
                "provider": provider,
                "model": model,
                "task_class": task_class,
                "avg": avg,
                "samples": samples,
                "bar": bar,
                "min_samples": min_samples,
                "clears": bool(gated is not None and gated >= bar),
            }

        rows: list[dict[str, Any]] = []
        for (provider, model), sess_types in sorted(pairs.items()):
            if model == "":
                # ``_row`` passes ``model or None``, and local_quality with
                # model=None judges the provider across ALL its models — so an
                # empty-model row must count samples/classes over that SAME
                # population. Counting only the empty-model sessions produced
                # e.g. clears=True with samples=0, which the UI renders as
                # "not enough evidence yet" — hiding a real verdict behind a
                # row whose avg and samples described different worlds.
                merged: dict[str, set[str]] = {}
                for (p2, _m2), sm in pairs.items():
                    if p2 == provider:
                        for sid, tcs in sm.items():
                            merged.setdefault(sid, set()).update(tcs)
                sess_types = merged
            counts = {sid: eval_counts.get(sid, 0) for sid in sess_types}
            rows.append(_row(provider, model, None, sum(counts.values())))
            classes = sorted(
                {tc for types in sess_types.values() for tc in types if tc}
            )
            for tc in classes:
                samples = sum(
                    n for sid, n in counts.items() if tc in sess_types[sid]
                )
                rows.append(_row(provider, model, tc, samples))
        return {"bar": bar, "min_samples": min_samples, "rows": rows}

    @app.post("/routing/disable")
    def disable_routing(body: RoutingDisableBody) -> dict[str, Any]:
        from ...providers import routing as R

        cfg = d.platform.config
        provider = (body.provider or "").strip()
        model = (body.model or "").strip()
        if not provider:  # revert to the suggested/first connected real model
            connected = R.connected_real_models(d.platform.providers, cfg)
            pick = R.cheapest(connected)
            if pick:
                provider, model = pick[0], pick[1]
            elif connected:
                provider, model = connected[0]["provider"], connected[0]["model"]
        cfg.default_provider = provider or "mock"
        if model:
            cfg.default_model = model
        persist_config_values(
            cfg.home,
            {"default_provider": cfg.default_provider, "default_model": cfg.default_model},
        )
        return _view()
