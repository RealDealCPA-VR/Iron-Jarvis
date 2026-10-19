"""Connector Marketplace routes (CX-01).

The one-tap gallery: list every connector with its live status, connect one
(collecting its fields), test it, or disconnect it. All logic lives in
:mod:`iron_jarvis.connectors.service`; these handlers are thin.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException

from ..schemas import ConnectorConnectBody


def register(app: FastAPI, d) -> None:
    @app.get("/connectors")
    def list_connectors_route() -> dict[str, Any]:
        from ...connectors import list_connectors
        from ...connectors.catalog import CATEGORY_ORDER
        from ...connectors.service import USER_CATEGORY

        return {
            "connectors": list_connectors(d.platform),
            # Dynamic categories (the user's own packs + memory sources) ride
            # at the end so the gallery groups + filters them properly.
            # v1.353.0 integrate (G42): the user's own packs are "Added by
            # you" (service.USER_CATEGORY), the category their cards carry --
            # the list said "Custom", an always-empty chip.
            "categories": [*CATEGORY_ORDER, USER_CATEGORY, "Memory"],
        }

    @app.post("/connectors/{connector_id}/connect")
    def connect_route(connector_id: str, body: ConnectorConnectBody) -> dict[str, Any]:
        from ...connectors import connect, get_connector

        if get_connector(connector_id) is None:
            raise HTTPException(status_code=404, detail="no such connector")
        try:
            return connect(d.platform, connector_id, body.values)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except Exception as exc:  # noqa: BLE001 — surface a connect failure honestly
            raise HTTPException(status_code=422, detail=f"{type(exc).__name__}: {exc}")

    @app.post("/connectors/{connector_id}/test")
    def test_route(connector_id: str) -> dict[str, Any]:
        from ...connectors import test

        # The service also resolves DYNAMIC entries (user MCP servers, memory
        # sources) the curated catalog doesn't know — KeyError = truly unknown.
        try:
            return test(d.platform, connector_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="no such connector")

    @app.delete("/connectors/{connector_id}")
    def disconnect_route(connector_id: str) -> dict[str, Any]:
        from ...connectors import disconnect

        try:
            return disconnect(d.platform, connector_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="no such connector")
