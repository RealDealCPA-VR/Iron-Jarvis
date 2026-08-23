"""The Capability Envelope: measure the model you were given, then bend the
loop to fit it — ported from the user's own IronCore project (its proven
thesis) and adapted to Iron Jarvis.

Package map:
    profile.py — CapabilityProfile, the provenance vocabulary (incl. the
                 Iron-Jarvis-only ``"trusted"`` for cloud/CLI providers),
                 mechanical ladder selection + loop-bending helpers.
    store.py   — atomic persistence under ``<home>/envelopes/``, never-raising
                 loads, and the keep-last-good merge (a failed re-probe never
                 destroys a measurement).
    seed.py    — instant-on introspection (Ollama /api/show, /v1/models).
    probes.py  — the quick battery (TOOL-FORM, JSON-STRICT, TOKEN-RATIO),
                 mechanical scoring against an injected transport.
    runner.py  — orchestration + honest source stamping (probed / partial /
                 probe_failed — the last one never carries ``probed_at``).
"""

from iron_jarvis.envelope.profile import (
    SOURCES,
    TOOL_PROTOCOL_LADDER,
    TOOL_PROTOCOL_THRESHOLDS,
    CapabilityProfile,
    trusted_profile,
)
from iron_jarvis.envelope.runner import run_quick_battery
from iron_jarvis.envelope.seed import seed_profile

__all__ = [
    "SOURCES",
    "TOOL_PROTOCOL_LADDER",
    "TOOL_PROTOCOL_THRESHOLDS",
    "CapabilityProfile",
    "run_quick_battery",
    "seed_profile",
    "trusted_profile",
]
