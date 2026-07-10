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
