"""A REAL fleet registry + sampler holding a probe's reading (v1.421.0, D3).

The context-window ladder's "endpoint" rung reads the window a model's own
server reports off the sampler's last :class:`NodeSnapshot`
(``fleet/windows.probed_window``). Before v1.421.0 the rung walked
``fleet.nodes()[].models[].name`` — a shape no real FleetNode has — and the
tests that pinned it handed it fakes in that dead shape, so they stayed green
while the rung never answered. This builds the real objects instead: a
:class:`FleetRegistry` with the Ollama settings slot set, and a
:class:`FleetSampler` holding a snapshot of it whose ``models`` are real
:class:`ModelEntry` rows (Ollama's ``/api/ps`` reports a loaded model's
``context_length``).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from iron_jarvis.fleet.models import ModelEntry, NodeSnapshot
from iron_jarvis.fleet.registry import FleetRegistry
from iron_jarvis.fleet.sampler import FleetSampler

OLLAMA_URL = "http://ollama-box:11434"


def probed_fleet(model: str = "qwen3:30b", context_length: int = 32_768) -> Any:
    """``.registry`` and ``.sampler`` — the Ollama slot, its last probe
    listing *model* loaded with *context_length* tokens."""
    cfg = SimpleNamespace(
        home=None, fleet_nodes=[], ollama_base_url=OLLAMA_URL, ollama_model="",
        custom_base_url="", custom_model="",
    )
    registry = FleetRegistry(cfg, persist=lambda *a, **k: None)
    sampler = FleetSampler(registry, probe=lambda node, **kw: (_snap(node, model, context_length), []))
    node = registry.get("ollama")
    sampler.record(node, _snap(node, model, context_length))
    return SimpleNamespace(registry=registry, sampler=sampler)


def _snap(node: Any, model: str, context_length: int) -> NodeSnapshot:
    return NodeSnapshot(
        node=node, status="online", evidence="direct",
        models=[ModelEntry(id=model, loaded=True, context_length=context_length)],
    )
