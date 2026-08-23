# IronCore → Iron Jarvis: the Capability Envelope integration

*Written 2026-08-22 at v1.200.0, from a direct study of Projects/IronCore
(branch phase-12/local-model-polish, 2,739 tests) and its project memory.
Goal: Iron Jarvis's loop measures the model it was given and bends itself to
fit — a small local model gets a decomposed, verified, narrow-tool loop; a
frontier model sees ZERO change. Extremely user-friendly: no new pages, no
new decisions; the envelope is invisible machinery surfaced only where the
app already reports on models.*

## What IronCore proved (the portable assets)

IronCore is the user's own Codex-style CLI for open-source models. Its thesis
— the **Capability Envelope** — shipped and was hardened across 12 phases:

1. **CapabilityProfile** (envelope/profile.py): per-model measured record —
   `context_window` / `honest_context`, `chars_per_token` (measured, 4.0
   unmeasured default), `tool_protocols` + `json_adherence` scores,
   `coherence_horizon`, `vision`, and a *provenance* field `source` whose
   vocabulary was paid for in blood: `default` (floor) / `seeded`
   (introspected, provisional) / `probed` (every probe answered) / `partial`
   (some failed; the rest is evidence) / `probe_failed` (battery ran,
   measured NOTHING — carries no probed_at, so every measured-predicate says
   no) / `tuned` (measured, then lowered from live evidence). The IC-1210
   lesson: a battery where every probe failed must NEVER read as "measured".
2. **Downgrade ladders with acceptance bars**: tool calls `native (≥0.95) →
   strict_json (≥0.90) → text floor`; selection is mechanical, never vibes.
3. **Instant-on seeding**: ~1s introspection (Ollama /api/show → real window,
   capabilities) makes a usable provisional profile; the deep battery runs in
   the background and hot-swaps; a failed re-probe never clobbers the last
   good measurement.
4. **Guided decoding** (core/guided.py): the strict_json rung is real
   server-side constrained decoding (`response_format` json_schema pinning
   `{tool: enum, args: object}` + a `done` pseudo-tool because constrained
   output can never stop otherwise; `extra_body` as the vLLM
   guided_json / llama.cpp GBNF escape hatch; the JSON scaffold is
   protocol, not prose — suppressed from the transcript).
5. **Outcome tuning**: downgrade-ONLY adjustments from a per-model outcome
   ledger, with hysteresis; a new probe generation resets evidence.
6. **Narrated adaptation**: a `LadderChanged` event — the single most
   differentiating behavior must not be silent.

## What Iron Jarvis already has (the sockets)

- `providers/manager._context_window`: pin → probe → default (window only).
- `tools/autoselect`: auto-arm with a cap — built exactly because a local
  model picks `shell` over `read_file` from a wide menu.
- `agents/decompose.py`: a real plan/verify/assemble engine, today gated on
  the blanket `decompose_local_tasks` flag and still unused by the
  supervisor (the repo's oldest open item).
- `plan.*` events that already reach chat's stream but render as generic
  "Working…".
- The Model report card (v1.169) — per-model outcome track record.
- Fleet telemetry (tokens/sec, VRAM, context) for local endpoints.
- The identity/honesty machinery every adaptation must plug into
  (TurnReceipt, events, never-silently-degrade).

## Design principles (locked)

- **Frontier sees zero change.** Cloud/CLI providers get a `trusted`
  envelope by construction — no probes, no ladders, no behavior delta.
  Probing exists for LOCAL/custom endpoints, plus an explicit button.
- **No new pages.** Surfaces touched: the Model report card gains an
  envelope section; Connections' local-endpoint rows gain "Measure";
  chat's progress line learns step narration; TurnReceipt gains a quiet
  "adapted" note. Nothing else moves.
- **Honest-mock rule**: no real endpoint ⇒ no probing; the offline suite
  runs every probe against fake transports (the IronCore test pattern).
- **Adaptation is narrated, never silent** (`envelope.adapted` event), and
  provenance is displayed wherever a profile value is displayed.
- **Absent ≠ delete, failed ≠ measured**: atomic profile writes,
  never-raising loads, `probe_failed` never stamps `probed_at`, a failed
  re-probe keeps the last good measurement.
- Every wave ships as a release (suite-gated), built by file-disjoint doers
  + an adversarial reviewer, load-bearing lines mutation-checked.

## Deliberately NOT ported (recorded so nobody re-files these as gaps)

- **Edit-format ladders** (unified_diff→search_replace→whole_file): Iron
  Jarvis edits through structured tools, not diff negotiation with the
  model. No socket, no need.
- **Per-model sampling defaults**: the router owns parameters; revisit only
  with evidence.
- **IRONCALL text floor**: strict_json via constrained decoding is the
  floor here; a text-protocol rung returns only if a real endpoint needs it.
- **Fleet role-splitting** (7B doer + 70B judge): deferred to a
  suggest-don't-act v2 — silent model substitution collides with the
  routing-honesty rules; a *suggested* judge is the right first shape.
- **Deep CTX-HONESTY/RETENTION battery**: expensive; ships later as an
  opt-in "deep measure". The quick battery (tool-form, json-strict,
  token-ratio) is seconds and covers the loop-bending decisions.
  **Consequence enforced since the Wave-A review (per-field provenance):**
  a profile-level "probed" stamp means the battery RAN, not that every
  field carries evidence — `measured_fields` records exactly what was
  delivered, and the window rung speaks ONLY when `honest_context` is in
  it. Until the deep battery ships, no quick-battery profile can alter a
  context window (the reviewer's executed repro: one Measure click was
  about to stamp a seeded 4096 guess as a "measured" window on a 128k
  model).
- **opencode-cli envelope treatment**: its models are local, but the CLI
  owns its own harness; Wave A renders no envelope surfaces for it
  (backend treats every `*-cli` as trusted). Revisit only with evidence.
- **C4 live tuning wiring (deferred from Wave C, 2026-08-23)**: the
  OutcomeLedger + downgrade-only tuner shipped fully tested, and the two
  production recorders collect per-step outcomes — but `apply_tuning` is
  DELIBERATELY UNCONSULTED live. The Wave-C reviewer proved the only
  production evidence stream is step-GOAL verdicts (judge fails on hard
  tasks, "unverified" passes, budget stops), and the ported constants were
  calibrated for tool-PROTOCOL adherence: wiring them together makes a
  difficulty-driven downgrade spiral (hard goals → lowered rung → tighter
  loop → harder goals). Wiring condition: a parse/validate evidence feed
  (the guided rung is the natural source once it sees live traffic) or
  constants re-argued for goal evidence. The plan's original "report card
  shows it" claim is therefore NOT met this wave — by this recorded
  decision, not by omission.
- **Compaction pressure gauges deliberately not ratio-threaded (Wave C,
  2026-08-23)**: `_measure` in chat_turn.py and the raw-demand sums in
  runtime.py still estimate at the module default while both PLANNERS use
  the measured ratio — so for a dense measured model the fill percent can
  under-report pressure relative to the plan (compaction fires late, never
  early; the planners themselves stay correct). Accepted for now; thread
  the gauges when compaction behavior on measured models gets its own
  wave.
- **strict_json in the adapted disclosure (deferred from Wave C)**: the
  guided rung engages inside the router, which has no reach into the
  runtime's `adaptations` list, and the runtime cannot honestly predict
  the wrap. The rung IS disclosed as `reason="prompted-tools"` (the quiet
  class it upgrades). The clean seam, when wanted: an additive
  `RouteResult` field the runtime folds into its list — `wordChange`
  already passes unknown tokens through, so the rendering side needs one
  vocabulary entry.

## THE TODO — every task, by wave

### Wave A (v1.201.0) — the envelope exists
- [x] A1 NEW `src/iron_jarvis/envelope/` package: `profile.py`
  (CapabilityProfile port: window/honest_context/chars_per_token/vision/
  tool_protocols/json_adherence/coherence_horizon; `source` with the full
  six-value provenance vocabulary + docstring table; ladder selection with
  IronCore's acceptance bars; derived helpers `max_tools()`,
  `needs_decomposition()`, `verify_every_step()`), `store.py` (atomic
  write, never-raise load, `<home>/envelopes/<provider>__<model>.json`,
  unknown-field tolerance), `seed.py` (Ollama /api/show + openai-compat
  introspection → `seeded`), `probes.py` + `runner.py` (quick battery:
  TOOL-FORM native/strict_json trials with mechanical scoring, JSON-STRICT,
  TOKEN-RATIO from server usage; bounded time; off-loop; honest source
  stamping incl. partial/probe_failed; failed re-probe keeps last good).
- [x] A2 NEW `daemon/routes/envelope.py`: GET profile, POST probe
  (background, `envelope.probe_started/completed` events), provider+model
  addressed; local/custom endpoints only; registration by coordinator.
- [x] A3 `providers/manager.py`: `_context_window` consults the profile's
  measured window between pin and probe; `trusted` envelope for cloud/CLI
  providers by construction.
- [x] A4 UI (minimal): Model report card gains the envelope section
  (source, window, ladder scores vs bars, chars/token); Connections
  local-endpoint rows gain "Measure" (progress via events, honest failure);
  no new routes.
- [x] A5 Tests: fake-transport probe batteries; provenance pins
  (probe_failed never probed_at; partial vs probed; re-probe-keeps-last-
  good); store atomicity/never-raise; frontier-unchanged pins.

### Wave B (v1.202.0) — the loop bends
- [x] B1 Envelope-driven arming: `tools/autoselect`'s cap and the agent
  lane's `arm_for_task` consult `profile.max_tools()`; trusted/unmeasured
  ⇒ today's behavior byte-identical (pinned).
- [x] B2 `agents/runtime.should_decompose` consults
  `profile.needs_decomposition()` (config flag becomes an override, not the
  only gate); the SUPERVISOR lane finally routes through `decompose.py`'s
  plan/verify engine for low-envelope models — closing the repo's oldest
  open item.
- [x] B3 Chat narration: `stepLabel` renders `plan.created/step_started/
  step_completed` as "step k of n: <goal>" with verified marks — the
  Codex-feel seam, one switch statement away.
- [x] B4 `envelope.adapted` event published when the loop bends (decomposed
  / tool-cap / strict_json rung), + a quiet TurnReceipt note. Narrated,
  never alarming, absent for trusted models.
- [x] B5 Tests incl. mutation pins on every consult site + frontier
  zero-change pins.

### Wave C (v1.203.0) — the rungs get real
- [x] C1 Adapters (openai-compat family: custom/ollama): additive
  `response_format` (json_schema) + `tool_choice` forcing + `extra_body`
  escape hatch; capability-probed at seed time; cloud adapters untouched.
- [x] C2 The strict_json rung: when native tool-form < bar but strict_json
  ≥ bar, the tool loop switches to guided decoding (port of
  `core/guided.py`: schema pinning, `done` pseudo-tool, exclusive
  3-way parse, scaffold suppressed from the transcript).
  **Wave-A reviewer note (binding):** Wave A scored strict_json trials on
  the bare prompt (adapters had no `response_format` yet). When C1 makes
  the rung real, the rung's semantics change under stored scores — C2 must
  bump the probe generation / force a re-probe for profiles whose
  strict_json score predates constrained decoding, or the ladder selects on
  stale evidence.
- [x] C3 Per-step verify/retry ladder: `verify_every_step` envelopes get
  one retry per failed step with the error fed back and the instruction
  narrowed, then an honest failure — never a silent skip.
- [x] C4 Outcome tuning (SHIPPED AS MACHINERY + RECORDERS; live overlay deferred by the dated decision recorded below): downgrade-only with hysteresis, fed from
  ToolInvocation outcomes per provider+model; `tuned` provenance; a new
  probe generation resets evidence; report card shows it.
- [x] C5 `context/budget` token estimator uses measured `chars_per_token`
  for the answering model (4.0 default preserved for everything
  unmeasured — byte-identical today-behavior pinned).
- [x] C6 Tests: guided parse battery (ported cases), retry-ladder pins,
  tuning hysteresis pins, estimator regression pins.

### Definition of "integrated and flawless"
Every box above checked; every wave shipped as a suite-gated release;
adversarial reviewer verdict clean (or all confirmed defects fixed and
mutation-pinned) per wave; the offline suite green throughout; frontier
paths proven byte-identical by pinned tests; the live install's report card
shows a measured envelope for at least one local model and `trusted` for
cloud/CLI providers.
