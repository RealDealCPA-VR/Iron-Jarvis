"""CapabilityProfile: what a model can actually do, measured not assumed.

Ported from the user's own IronCore project (ironcore/envelope/profile.py),
adapted to Iron Jarvis: plain dataclass instead of pydantic (this package is
consulted on hot paths and has no validation-on-assignment needs), no
edit-format ladder (Iron Jarvis edits through structured tools, not diff
negotiation), no text-protocol rung (strict_json via constrained decoding is
the floor here; callers treat ``"none"`` as decompose-heavily), and a NEW
``"trusted"`` provenance for cloud/CLI providers — frontier models see ZERO
loop-bending by construction.

Provenance vocabulary (the ``source`` field — paid for in blood on IronCore,
where a battery whose every probe failed once read as "measured"):

    =============  ==========================================================
    source         meaning
    =============  ==========================================================
    default        the floor — nothing was ever asked about this model
    seeded         endpoint introspection (Ollama /api/show, /v1/models);
                   provisional and OPTIMISTIC by design: the battery refines
                   it, and the loop's own retries absorb an over-claim
    probed         a battery ran and EVERY probe answered
    partial        a battery ran and SOME probes answered — what landed is
                   evidence, the dead probes' reliabilities are floored
    probe_failed   a battery ran and measured NOTHING. Carries NO
                   ``probed_at``, so every measured-predicate answers no
    tuned          measured, then lowered from live outcome evidence
    trusted        a cloud/CLI provider: full capability granted by
                   construction, never probed, zero loop-bending
    =============  ==========================================================

The ladder selection (``select_tool_protocol``) is mechanical, never vibes:
the highest rung whose measured score clears its acceptance bar wins, and the
bars are IronCore's proven thresholds.

Probe generations (Wave C, v1.203.0). The Wave-A reviewer note under C2 in
docs/IRONCORE-INTEGRATION.md is BINDING here: Wave A scored strict_json
trials on the bare prompt (adapters had no ``response_format`` yet), so when
constrained decoding became real the rung's semantics changed UNDER stored
scores — a profile probed before the change would let the ladder select
strict_json on evidence measured against a different mechanism. The
``probe_generation`` field records which semantics scored a profile;
:data:`CURRENT_PROBE_GENERATION` names today's; a stored score from an older
generation is STALE for the rungs whose semantics changed (and ONLY those —
see :data:`_GENERATION_SENSITIVE_RUNGS`).
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field, fields

#: Every legal ``source`` value — the full table above. Kept as data so
#: surfaces (report card, tests) can enumerate the vocabulary instead of
#: re-listing it.
SOURCES: tuple[str, ...] = (
    "default",
    "seeded",
    "probed",
    "partial",
    "probe_failed",
    "tuned",
    "trusted",
)

#: Ladder order: most efficient first. Iron Jarvis has no text-protocol rung —
#: the floor below strict_json is ``"none"`` (decompose heavily, verify
#: everything), returned by ``select_tool_protocol`` when nothing clears a bar.
TOOL_PROTOCOL_LADDER: tuple[str, ...] = ("native", "strict_json")

#: Acceptance bars, ported verbatim from IronCore. Selection = the first rung
#: whose measured score is >= its bar.
TOOL_PROTOCOL_THRESHOLDS: dict[str, float] = {"native": 0.95, "strict_json": 0.90}

#: The sources whose numbers came out of a battery (fully or partly) — the
#: only profiles whose scores may drive a CAP (max_tools). ``tuned`` is
#: measured-then-lowered, so it stays in.
_MEASURED_SOURCES = frozenset({"probed", "partial", "tuned"})

#: The probe-battery generation this build SCORES with. Bump it whenever the
#: mechanics of a trial change under stored scores (the binding Wave-A
#: reviewer note under C2 in docs/IRONCORE-INTEGRATION.md):
#:
#:     1  Wave A (v1.201.0): strict_json trials scored on the bare prompt —
#:        the transport dropped ``response_format`` because adapters had no
#:        such parameter yet.
#:     2  Wave C (v1.203.0): strict_json trials scored WITH constrained
#:        decoding — the probe's ``response_format`` json_schema is forwarded
#:        to the adapter, so the score measures the guided rung the loop will
#:        actually run.
#:
#: The runner stamps every battery with this value; a stored profile whose
#: generation is older is stale for the generation-sensitive rungs.
CURRENT_PROBE_GENERATION = 2

#: Clip for one ``probe_notes`` entry. Long enough for an exception class +
#: the first line of a server's error body ("native errored (HTTPStatusError:
#: 400 ... tools not supported)..."), short enough that a profile file can
#: never balloon on a chatty proxy's HTML error page.
PROBE_NOTE_MAX = 200

#: Rungs whose TRIAL SEMANTICS changed between generations. Only strict_json:
#: generation 2 changed how its trials are issued (``response_format``
#: forwarded -> server-side constrained decoding), while native trials are
#: byte-identical in both generations — so a gen-1 native score remains valid
#: evidence and only the strict_json rung goes stale.
_GENERATION_SENSITIVE_RUNGS = frozenset({"strict_json"})


def sanitize_id(value: str) -> str:
    """A filesystem-safe slug for a provider or model id. Model ids carry
    ``/`` and ``:`` (``qwen3:30b/instruct``) — every run of anything outside
    ``[a-zA-Z0-9._-]`` collapses to one ``_`` so the id can never escape the
    envelopes directory or split into a subpath."""
    return re.sub(r"[^a-zA-Z0-9._-]+", "_", value or "")


@dataclass
class CapabilityProfile:
    """Measured capabilities of one model at one provider.

    All reliability scores are fractions in [0, 1] from repeated trials.
    ``probed_at`` is an ISO-8601 stamp and is the measured-predicate: it is
    NEVER set when ``source == "probe_failed"`` (a stamp alone reads as
    "measured" on every surface, and a battery that landed nothing measured
    nothing).
    """

    model_id: str
    provider: str = ""
    source: str = "default"  # see the module docstring table
    probed_at: str | None = None  # ISO-8601; None = never successfully measured

    # Context
    context_window: int = 8192  # advertised
    honest_context: int = 4096  # conservative floor until measured/seeded
    #: measured chars per prompt token (TOKEN-RATIO probe); 4.0 is the
    #: universal unmeasured default and MUST survive a probe that could not
    #: read server usage — a failed measurement never invents a ratio.
    chars_per_token: float = 4.0
    #: whether the model accepts image inputs. Floor-conservative default
    #: False; seeded from Ollama /api/show capabilities.
    vision: bool = False

    # Reliability scores, [0..1]
    tool_protocols: dict[str, float] = field(default_factory=dict)
    json_adherence: float = 0.0
    coherence_horizon: int = 6  # turns before drift

    #: PER-FIELD provenance (the minimal IC-1215): the dotted paths a battery
    #: actually DELIVERED ("tool_protocols.native", "chars_per_token", ...).
    #: A profile-level "probed" stamp means THE BATTERY RAN, not that every
    #: field carries evidence — the quick battery never measures
    #: honest_context, so a consumer that trusted the stamp once shrank a
    #: 128k model's window to the 4096 floor (the Wave-A ship-blocker this
    #: field exists to kill). Consumers of an INDIVIDUAL value must ask
    #: :meth:`field_measured`, never the stamp. Additive and unknown-tolerant:
    #: profiles written before it existed load as [] (everything unmeasured).
    measured_fields: list[str] = field(default_factory=list)

    #: WHY a floored score is zero (v1.204.0, live finding): dotted
    #: reliability path -> a short honest reason ("native errored
    #: (HTTPStatusError: 400 ...); floored to 0.0, not evidence"). The
    #: v1.203.0 rung isolation floored an errored rung with an honest note in
    #: the ProbeResult — and then nothing persisted it, so the live profiles
    #: showed ``native 0.0`` with no way to see the endpoint had 400'd the
    #: tools param, and the user read it as their models scoring zero.
    #: Written by the runner on fold; a note travels WITH the zero it
    #: explains: carried when keep-last-good carries the value, cleared when
    #: a later battery actually measures the path. Values clipped to
    #: :data:`PROBE_NOTE_MAX`. Additive and unknown-tolerant: profiles
    #: written before it existed load as {}.
    probe_notes: dict[str, str] = field(default_factory=dict)

    #: Which battery SEMANTICS scored this profile — see
    #: :data:`CURRENT_PROBE_GENERATION`. Additive: a stored profile written
    #: before the field existed (every Wave-A measurement) loads as 1, which
    #: is exactly the honest reading — its strict_json score was measured on
    #: the bare prompt. The runner restamps to CURRENT on every battery.
    probe_generation: int = 1

    # ------------------------------------------------------------------ #
    # Ladder selection (mechanical — the engine must never pick another way)
    # ------------------------------------------------------------------ #

    def is_current_generation(self) -> bool:
        """Were this profile's scores measured under TODAY's trial semantics?
        A stored profile whose ``probe_generation`` predates
        :data:`CURRENT_PROBE_GENERATION` is stale for ladder purposes — its
        generation-sensitive scores answered a different question."""
        return self.probe_generation >= CURRENT_PROBE_GENERATION

    def select_tool_protocol(self) -> str:
        """The highest rung whose measured score clears its acceptance bar;
        ``"none"`` when nothing clears — callers treat that as "decompose
        heavily, keep the tool surface minimal".

        Generation staleness (the binding Wave-A reviewer note): a rung whose
        trial semantics changed since this profile was scored is treated as
        UNMEASURED — its stored number answered a different question, and
        selecting on it would route the loop onto a rung nothing verified.
        Only the changed rung goes stale (strict_json — its gen-2 trials run
        under real constrained decoding); a gen-1 NATIVE score stays usable
        because native trials are byte-identical across generations, so a
        legacy profile keeps its native rung and merely loses the strict_json
        fallback until a re-probe re-scores it.
        """
        for rung in TOOL_PROTOCOL_LADDER:
            if rung in _GENERATION_SENSITIVE_RUNGS and not self.is_current_generation():
                continue  # scored under old semantics — unmeasured, not evidence
            if self.tool_protocols.get(rung, 0.0) >= TOOL_PROTOCOL_THRESHOLDS[rung]:
                return rung
        return "none"

    # ------------------------------------------------------------------ #
    # Derived loop-bending helpers
    # ------------------------------------------------------------------ #

    def is_trusted(self) -> bool:
        return self.source == "trusted"

    def is_measured(self) -> bool:
        """Did a battery actually land on this model? ``probe_failed`` never
        stamps ``probed_at``, so the stamp is the honest answer. This is the
        BATTERY-level question — for any single value ask
        :meth:`field_measured` instead."""
        return self.source in _MEASURED_SOURCES and self.probed_at is not None

    def field_measured(self, name: str) -> bool:
        """Does ``name`` carry a battery's evidence? Accepts either a full
        dotted path (``"tool_protocols.native"``) or a root field name
        (``"tool_protocols"``, ``"honest_context"``) — a root answers True
        when any of its sub-paths was delivered. This is the predicate the
        window rung (and every other single-value consumer) must gate on: on
        a quick-battery profile ``is_measured()`` is True while
        ``field_measured("honest_context")`` is honestly False."""
        return any(
            entry == name or entry.startswith(name + ".") for entry in self.measured_fields
        )

    def max_tools(self) -> int | None:
        """How many tools may be armed at once, or ``None`` for "no envelope
        cap" (existing caps like autoselect's stay in charge).

        Trusted and UNMEASURED profiles return ``None`` — the envelope only
        ever narrows on evidence, so default/seeded profiles leave today's
        behavior byte-identical. Measured profiles scale from the native
        tool-form score. The bands are v1 heuristics the outcome tuner may
        lower later; they are documented, not sacred:

            native >= 0.95 -> None (full menu — the model earned it)
            native >= 0.90 -> 6
            native >= 0.75 -> 4
            else           -> 3
        """
        if self.is_trusted() or not self.is_measured():
            return None
        native = self.tool_protocols.get("native", 0.0)
        if native >= 0.95:
            return None
        if native >= 0.90:
            return 6
        if native >= 0.75:
            return 4
        return 3

    def needs_decomposition(self) -> bool:
        """Should the loop run this model through plan/verify decomposition?

        Trusted -> False (frontier sees zero change). Otherwise True whenever
        the model cannot hold the native rung or drifts before six turns —
        both are the failure shapes decomposition exists to absorb. Note the
        conservative floor: an unmeasured, untrusted profile has no rung and
        answers True, which is exactly the behavior a small local model gets
        until a battery says otherwise.
        """
        if self.is_trusted():
            return False
        return self.select_tool_protocol() != "native" or self.coherence_horizon < 6

    def verify_every_step(self) -> bool:
        """Should every decomposed step be verified before the next one runs?

        Trusted -> False. Otherwise True when the MEASURED json_adherence is
        below 0.90 (an unmeasured 0.0 is absence of evidence, not evidence of
        weakness — a seeded profile is judged by its rung, not by a score
        nothing produced) or when the model needs decomposition at all.
        """
        if self.is_trusted():
            return False
        if self.is_measured() and self.json_adherence < 0.90:
            return True
        return self.needs_decomposition()

    # ------------------------------------------------------------------ #
    # Serialization (unknown-field tolerant in BOTH directions)
    # ------------------------------------------------------------------ #

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> CapabilityProfile:
        """Build from a JSON payload, tolerating unknown fields (a profile
        written by a NEWER version must load here) and missing fields (an
        OLDER profile loads with today's defaults). Raises only on payloads
        that are not profile-shaped at all — the store catches that and
        treats the file as corrupt."""
        known = {f.name for f in fields(cls)}
        kwargs = {k: v for k, v in dict(data).items() if k in known}
        profile = cls(**kwargs)
        # Coerce the one nested container: JSON hands back plain dicts, but a
        # corrupt payload can hold anything. Non-numeric scores are dropped
        # rather than trusted — a score that cannot compare against a bar is
        # not a score.
        raw = profile.tool_protocols if isinstance(profile.tool_protocols, dict) else {}
        clean: dict[str, float] = {}
        for key, value in raw.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                clean[str(key)] = float(value)
        profile.tool_protocols = clean
        # measured_fields: pre-existing JSONs (and corrupt shapes) load as []
        # — everything honestly unmeasured, never a guess.
        raw_fields = profile.measured_fields if isinstance(profile.measured_fields, list) else []
        profile.measured_fields = [entry for entry in raw_fields if isinstance(entry, str)]
        # probe_notes: pre-v1.204.0 JSONs (and corrupt shapes) load as {};
        # non-string entries are dropped, never coerced (a note is prose for
        # the user, not data to launder), and values are re-clipped so a
        # hand-edited file cannot smuggle a megabyte into every GET.
        raw_notes = profile.probe_notes if isinstance(profile.probe_notes, dict) else {}
        profile.probe_notes = {
            key: value[:PROBE_NOTE_MAX]
            for key, value in raw_notes.items()
            if isinstance(key, str) and isinstance(value, str) and value
        }
        # probe_generation: pre-Wave-C JSONs load as 1 (dataclass default);
        # a corrupt value coerces to 1 too — the STALE reading, so a garbage
        # generation can never launder an old strict_json score into current.
        gen = profile.probe_generation
        if not isinstance(gen, int) or isinstance(gen, bool) or gen < 1:
            profile.probe_generation = 1
        return profile

    def copy(self) -> CapabilityProfile:
        """A deep-enough copy: every mutable container is the copy's own."""
        return CapabilityProfile.from_dict(self.to_dict())


def trusted_profile(provider: str, model_id: str) -> CapabilityProfile:
    """The envelope a cloud/CLI provider gets BY CONSTRUCTION: never probed,
    full capability, zero loop-bending. Scores are 1.0 so every mechanical
    consumer (ladder, bands) answers "full capability" without special-casing
    — but ``probed_at`` stays None because nothing was measured, and the
    ``source`` says so. ``measured_fields`` stays EMPTY for the same reason:
    trusted is capability-by-construction, not evidence, and a surface that
    asks "was this value measured?" must hear no. NOT a context-window
    authority: the provider manager's pin -> probe -> default chain keeps
    that job. ``probe_generation`` is stamped CURRENT: generation staleness
    exists to invalidate old MEASUREMENTS, and a grant by construction is
    never a measurement — a trusted profile must not lose its strict_json
    rung to a rule about probe semantics it never participated in."""
    return CapabilityProfile(
        model_id=model_id,
        provider=provider,
        source="trusted",
        tool_protocols={"native": 1.0, "strict_json": 1.0},
        json_adherence=1.0,
        coherence_horizon=12,
        probe_generation=CURRENT_PROBE_GENERATION,
    )
