"""v1.424.0 (review of D8): per-model capability without breaking the models
the user picks by name behind a gateway.

1. NOTHING PER-MODEL KNOWN = THE GATEWAY'S OWN ANSWER (the coordinator's
   regression). D8 as first built read a model that is NOT the node's
   default, with no live check of it and nothing stated by its server, as
   "not known" — and the router reads not known as unable. Every alias the
   owner picks by name behind their DGX LiteLLM proxy (anything but glm)
   then became a text-only turn where it used to have tools. Now, when
   nothing speaks for THIS model, the node's own record (the gateway-level
   answer, a check of its default model) is used for routing, exactly as
   before D8 — and the source says so (``"node"``, with ``checked_model``
   naming the model that check asked), so the screen can say "from the
   server's check of glm" instead of claiming the model was checked. A live
   check of the model or its server's own "yes"/"no" still wins.

2. A CHECK REACHES THE ROUTER. ``POST /fleet/nodes/{id}/verify`` wrote the
   result into the registry, but every ``fleet-*`` factory closes over the
   ``FleetNode`` object it was registered with (``register_providers``), so
   the adapter the router builds kept reading the OLD node: a per-model
   check (the Fleet page's new Check press) changed what the page said and
   nothing about where turns went, until a restart. The verify routes now
   re-register the fleet's providers after recording a check.
"""

from __future__ import annotations

import types

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.fleet.adapter import FleetAdapter, model_capability, node_capabilities
from iron_jarvis.fleet.models import FleetNode
from iron_jarvis.fleet.upstreams import parse_model_info


def _table(stated: dict[str, dict]):
    body = {
        "data": [
            {
                "model_name": name,
                "litellm_params": {"model": f"hosted_vllm/{name}", "api_base": "http://10.0.0.5:8000/v1"},
                "model_info": dict(info),
            }
            for name, info in stated.items()
        ]
    }
    return parse_model_info(body).caps


CAPS = _table({
    "says-no": {"supports_function_calling": False, "supports_vision": False},
    "says-yes": {"supports_function_calling": True, "supports_vision": True},
    "quiet": {},
})

GW = FleetNode(id="gw", base_url="http://10.0.0.2:4000/v1", default_model="glm",
               tool_use=True, vision=False)


# --------------------------------------------------------------------------- #
# 1. nothing per-model known: the gateway's own answer, said as such
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("name", ["vision-alias", "quiet"])
def test_a_model_nothing_speaks_for_keeps_the_gateways_answer(name):
    """An alias the owner picks by name (unlisted, or listed with nothing
    stated): routing keeps the node's record, exactly as before D8."""
    assert node_capabilities(GW, name, CAPS) == {"tool_use": True, "vision": False}
    assert FleetAdapter(node=GW, model=name, caps=CAPS).capabilities()["tool_use"] is True
    view = model_capability(GW, name, CAPS)
    assert view["tool_use"] is True and view["tool_use_from"] == "node", view
    assert view["vision"] is False and view["vision_from"] == "node", view
    assert view["checked_model"] == "glm", view


def test_a_stated_no_or_a_live_check_still_wins_over_the_gateways_answer():
    assert node_capabilities(GW, "says-no", CAPS)["tool_use"] is False
    assert model_capability(GW, "says-no", CAPS)["tool_use_from"] == "stated"
    checked = GW.model_copy(update={"model_caps": {"vision-alias": {"tool_use": False}}})
    assert node_capabilities(checked, "vision-alias", CAPS)["tool_use"] is False
    assert model_capability(checked, "vision-alias", CAPS)["tool_use_from"] == "checked"
    # And a server's yes beats a gateway "no".
    assert node_capabilities(GW, "says-yes", CAPS)["vision"] is True


def test_a_never_checked_gateway_is_still_not_known():
    bare = GW.model_copy(update={"tool_use": None, "vision": None})
    view = model_capability(bare, "vision-alias", CAPS)
    assert view["tool_use"] is None and view["tool_use_from"] == "", view
    assert "checked_model" not in view
    assert node_capabilities(bare, "vision-alias", CAPS) == {"tool_use": False, "vision": False}


def test_the_default_model_reads_as_its_own_check():
    view = model_capability(GW, "glm", CAPS)
    assert view["tool_use"] is True and view["tool_use_from"] == "checked"
    assert "checked_model" not in view


# --------------------------------------------------------------------------- #
# 2. a check reaches the router
# --------------------------------------------------------------------------- #


async def _answers(self, *, system, messages, tools, **kw):
    return types.SimpleNamespace(tool_calls=[{"name": "ping"}] if tools else [], text="red")


def test_a_per_model_check_reaches_the_adapter_the_router_builds(tmp_path, monkeypatch):
    app = create_app(str(tmp_path))
    d = app.state.d
    d.fleet.add(FleetNode(id="gw", base_url="http://10.0.0.2:4000/v1", default_model="glm", routable=True))
    d.fleet.register_providers(d.platform.providers)
    monkeypatch.setattr(FleetAdapter, "complete", _answers)
    before = d.platform.providers.get("fleet-gw", "qwen").capabilities()
    assert before["tool_use"] is False, "never checked: not known reads as unable"
    r = TestClient(app).post("/fleet/nodes/gw/verify", json={"model": "qwen"})
    assert r.status_code == 200 and r.json()["tool_use"] is True, r.text
    after = d.platform.providers.get("fleet-gw", "qwen").capabilities()
    assert after["tool_use"] is True and after["vision"] is True, after
