"use client";

/**
 * The Capability Envelope surfaces (v1.201.0, restructured v1.204.0 from
 * live user feedback).
 *
 * IronCore's lesson, ported: a model's profile is only worth showing if its
 * PROVENANCE is shown next to it. The report card once rendered a green
 * SELECTED on an unprobed profile — that class of lie is banned here, so:
 *  - the source badge renders wherever a profile value renders;
 *  - ladder rows (and the word SELECTED) render ONLY for measured sources
 *    (probed / partial / tuned) — seeded and floor profiles say what they
 *    are instead of dressing up as a scorecard;
 *  - a probe that measured nothing says so ("measure failed — keeping floor
 *    defaults").
 *
 * v1.204.0 — three findings from the shipped UI, live on the user's install:
 *  1. PLACEMENT: the envelope section inside the connect tiles made the
 *     custom-endpoint card enormous. Measurements now render in their OWN
 *     `MeasuredEndpoints` section below the connect cards, showing ONLY
 *     endpoints whose GET returns a stored profile (source != "default").
 *     Nothing measured -> no section at all, not an empty husk. The Measure
 *     button + provenance chip stay small on the endpoint rows.
 *  2. CONTEXT HONESTY: the card used to print the profile's floor context
 *     (8192/4096) although the app budgets with `effective_window` (pin →
 *     measured → endpoint → default). The context row now shows the
 *     effective window with its source in words, and unmeasured context
 *     fields say "not yet deep-measured" instead of parading floor numbers.
 *  3. FLOORED-RUNG HONESTY: a rung the prober FLOORED (score 0.0 and the
 *     path absent from `measured_fields`) is a refusal, not a measurement —
 *     it renders as "the endpoint refused them" (+ the probe_notes reason),
 *     never a bare 0.00 score bar the user reads as their model scoring
 *     zero. A truly SCORED 0.0 (path present) keeps the bar.
 *
 * Wire contract (backend ships in parallel — mocked in tests):
 *   GET  /envelope/{provider}/{model}
 *     -> { profile, trusted,
 *          effective_window: { value, source: pin|measured|endpoint|default } }
 *     profile carries `measured_fields: string[]` (per-field provenance) and
 *     `probe_notes: {[path]: string}` (why a rung was floored).
 *   POST /envelope/{provider}/{model}/probe  -> { started: true } | 400 | 409
 *   completion: `envelope.probe_completed` on the live event stream.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { get, post, ApiError } from "@/lib/api";
import { useEvents } from "@/lib/useEvents";

/* ----------------------------------------------------------------- wire */

/** One measured (or seeded, or floor-default) capability profile. */
export interface EnvelopeProfile {
  model_id: string;
  provider: string;
  /** Provenance — the six-value vocabulary IronCore paid for in blood:
   *  default / seeded / probed / partial / probe_failed / tuned. */
  source: string;
  probed_at?: string | null;
  context_window?: number | null;
  honest_context?: number | null;
  chars_per_token?: number | null;
  vision?: boolean | null;
  tool_protocols?: Partial<Record<"native" | "strict_json", number>> | null;
  json_adherence?: number | null;
  coherence_horizon?: number | null;
  /** Per-field provenance: paths the probe REALLY measured (e.g.
   *  "tool_protocols.native", "honest_context"). A 0.0 rung whose path is
   *  absent here was FLOORED (the endpoint refused the trials), not scored. */
  measured_fields?: string[] | null;
  /** Why a path was floored, keyed the same way as measured_fields (e.g.
   *  {"tool_protocols.native": "native trials errored: HTTP 400 …"}). */
  probe_notes?: Record<string, string> | null;
}

/** The window the app ACTUALLY budgets with (pin → measured → endpoint →
 *  default) — the profile's own context fields may be unmeasured floors. */
export interface EffectiveWindow {
  value: number | null;
  source: "pin" | "measured" | "endpoint" | "default" | string;
}

export interface EnvelopeData {
  profile?: EnvelopeProfile | null;
  trusted?: boolean;
  effective_window?: EffectiveWindow | null;
}

/**
 * The ONE place the envelope wire shape lives. The parallel backend agent may
 * land on a query param instead of a path segment for model ids — if the
 * coordinator reconciles that way, this line changes alone.
 */
export function envelopeUrl(provider: string, model: string): string {
  return `/envelope/${encodeURIComponent(provider)}/${encodeURIComponent(model)}`;
}

/* ----------------------------------------------------------- provenance */

/** Sources that are real evidence — everything else must not read as such. */
const MEASURED_SOURCES = new Set(["probed", "partial", "tuned"]);

const TONE_OK = "border-emerald-400/25 bg-emerald-400/[0.08] text-emerald-300/90";
const TONE_NEUTRAL = "border-white/10 bg-white/[0.04] text-zinc-400";
const TONE_WARN = "border-amber-400/25 bg-amber-400/[0.08] text-amber-200/90";

interface SourceBadge {
  label: string;
  tone: string;
  title: string;
}

/** Badge text/tone per provenance: measured=ok, seeded=neutral, floor/failed=warn. */
export function sourceBadge(source: string): SourceBadge {
  switch (source) {
    case "probed":
      return {
        label: "measured",
        tone: TONE_OK,
        title: "Every probe answered — these numbers were taken against this model, not read off a spec sheet.",
      };
    case "tuned":
      return {
        label: "measured · tuned down",
        tone: TONE_OK,
        title: "Measured, then lowered from live outcome evidence — never raised without a re-probe.",
      };
    case "partial":
      return {
        label: "partly measured",
        tone: TONE_OK,
        title: "Some probes failed; what is shown is the evidence from the ones that answered.",
      };
    case "seeded":
      return {
        label: "seeded",
        tone: TONE_NEUTRAL,
        title: "Reported by the endpoint's own introspection (~1s) — provisional until a probe verifies it.",
      };
    case "probe_failed":
      return {
        label: "measure failed — keeping floor defaults",
        tone: TONE_WARN,
        title: "The probe battery ran and nothing came back usable — these stay conservative floor defaults, not evidence.",
      };
    default:
      return {
        label: "floor defaults",
        tone: TONE_WARN,
        title: "Nothing probed yet — conservative defaults until a probe runs.",
      };
  }
}

/* -------------------------------------------------------------- fetching */

/** GET the envelope; reload() refetches (probe completion calls it). */
function useEnvelope(provider: string, model: string) {
  const [data, setData] = useState<EnvelopeData | null>(null);
  const [gen, setGen] = useState(0);
  const reload = useCallback(() => setGen((g) => g + 1), []);
  useEffect(() => {
    if (!provider || !model) return;
    let cancelled = false;
    get<EnvelopeData>(envelopeUrl(provider, model))
      .then((d) => {
        if (!cancelled) setData(d ?? null);
      })
      .catch(() => {
        // Best-effort surface: no envelope backend (or a daemon hiccup)
        // renders NOTHING rather than a made-up profile.
        if (!cancelled) setData(null);
      });
    return () => {
      cancelled = true;
    };
  }, [provider, model, gen]);
  return { data, reload };
}

/**
 * Fire `onCompleted` when a NEW `envelope.probe_completed` for this
 * (provider, model) lands on the live stream. A payload missing `model`
 * still counts for its provider — an extra refetch is harmless; a missed
 * one leaves stale numbers on screen.
 */
function useProbeCompleted(provider: string, model: string, onCompleted: () => void) {
  const { events } = useEvents(60);
  const seenRef = useRef<string | null>(null);
  const latest = events.find(
    (e) =>
      e.type === "envelope.probe_completed" &&
      e.payload?.provider === provider &&
      (e.payload?.model == null || e.payload.model === model),
  );
  useEffect(() => {
    if (!latest || seenRef.current === latest.id) return;
    seenRef.current = latest.id;
    onCompleted();
  }, [latest, onCompleted]);
}

/* ------------------------------------------------------------ the ladder */

/** Tool-call ladder with IronCore's acceptance bars. strict_json is the real
 *  floor here (server-constrained decoding); the text row exists so the card
 *  always names where the loop lands. */
const LADDER: { rung: "native" | "strict_json"; bar: number; label: string }[] = [
  { rung: "native", bar: 0.95, label: "native tool calls" },
  { rung: "strict_json", bar: 0.9, label: "strict JSON (constrained)" },
];

function fmtInt(n: number): string {
  return n.toLocaleString("en-US");
}

/** One rung row: score vs its bar, word-first verdict, tiny bar visual. */
function LadderRow({
  label,
  rung,
  score,
  bar,
  selected,
}: {
  label: string;
  rung: string;
  score: number | undefined;
  bar: number;
  selected: boolean;
}) {
  const verdict = selected
    ? "SELECTED"
    : score == null
      ? "not measured"
      : score >= bar
        ? "ok, fallback"
        : `below bar (${(bar - score).toFixed(2)} short)`;
  const verdictTone = selected
    ? "font-semibold text-emerald-300"
    : score != null && score < bar
      ? "text-rose-300/90"
      : "text-zinc-500";
  return (
    <div data-rung={rung} className="flex items-center gap-2 text-[10.5px]">
      <span className="w-36 shrink-0 truncate text-zinc-400">{label}</span>
      <span className="w-24 shrink-0 font-mono text-zinc-300">
        {score != null ? score.toFixed(2) : "—"}{" "}
        <span className="text-zinc-600">(needs {bar.toFixed(2)})</span>
      </span>
      <span className="relative h-1 min-w-[3rem] flex-1 overflow-hidden rounded-full bg-white/[0.08]">
        {score != null && (
          <span
            className={`absolute inset-y-0 left-0 rounded-full ${
              score >= bar ? "bg-emerald-400/70" : "bg-rose-400/70"
            }`}
            style={{ width: `${Math.min(100, Math.max(0, score * 100))}%` }}
          />
        )}
        {/* the acceptance bar the score must clear */}
        <span
          className="absolute inset-y-0 w-px bg-zinc-300/70"
          style={{ left: `${bar * 100}%` }}
        />
      </span>
      <span className={`shrink-0 ${verdictTone}`}>{verdict}</span>
    </div>
  );
}

/* ---------------------------------------------- measured-endpoints section */

/** One (provider, model) the page considers measurable — the same list the
 *  endpoint rows offer Measure for. */
export interface MeasuredEntry {
  provider: string;
  model: string;
  /** Human name of the endpoint the model runs on (node label / provider). */
  label: string;
}

/** A GET answer worth a card: a stored, non-trusted profile. Floor defaults
 *  (source "default") are the ABSENCE of measurement and render nothing. */
function isStoredProfile(
  d: EnvelopeData | undefined,
): d is EnvelopeData & { profile: EnvelopeProfile } {
  return Boolean(
    d && d.trusted !== true && d.profile?.source && d.profile.source !== "default",
  );
}

/** The effective window's source, in words the user can act on. */
function effectiveWindowWords(source: string): string {
  switch (source) {
    case "pin":
      return "pinned by you";
    case "measured":
      return "measured";
    case "endpoint":
      return "from the endpoint";
    default:
      return "conservative default";
  }
}

/**
 * The measurements section — its OWN card BELOW the connect cards, one
 * compact row per stored non-trusted profile. Renders NOTHING (no husk)
 * when no endpoint has a stored profile. Refetches every profile when an
 * `envelope.probe_completed` lands on the live stream, so a Measure pressed
 * on an endpoint row surfaces its result here.
 */
export function MeasuredEndpoints({ entries }: { entries: MeasuredEntry[] }) {
  const [results, setResults] = useState<Record<string, EnvelopeData>>({});
  const [gen, setGen] = useState(0);
  const reload = useCallback(() => setGen((g) => g + 1), []);

  // ANY probe completion refetches the lot — one extra GET per entry is
  // cheap, and a missed one leaves a stale (or missing) card on screen.
  const { events } = useEvents(60);
  const latest = events.find((e) => e.type === "envelope.probe_completed");
  const seenRef = useRef<string | null>(null);
  useEffect(() => {
    if (!latest || seenRef.current === latest.id) return;
    seenRef.current = latest.id;
    reload();
  }, [latest, reload]);

  // entries is rebuilt every parent render — key the effect on its CONTENT.
  const entriesKey = entries.map((e) => `${e.provider}\u0000${e.model}`).join("|");
  useEffect(() => {
    if (entries.length === 0) {
      setResults({});
      return;
    }
    let cancelled = false;
    void Promise.all(
      entries.map(async (e) => {
        try {
          const d = await get<EnvelopeData>(envelopeUrl(e.provider, e.model));
          return [`${e.provider}\u0000${e.model}`, d ?? null] as const;
        } catch {
          return null; // best-effort — an unreachable daemon shows nothing
        }
      }),
    ).then((pairs) => {
      if (cancelled) return;
      const next: Record<string, EnvelopeData> = {};
      for (const p of pairs) {
        if (p && p[1]) next[p[0]] = p[1];
      }
      setResults(next);
    });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [entriesKey, gen]);

  const shown = entries.filter((e) =>
    isStoredProfile(results[`${e.provider}\u0000${e.model}`]),
  );
  if (shown.length === 0) return null;

  return (
    <section data-testid="measured-endpoints" className="card-surface p-5">
      <div className="mb-3">
        <h2 className="text-sm font-semibold text-zinc-100">Measured endpoints</h2>
        <p className="mt-0.5 text-[11px] leading-relaxed text-zinc-500">
          What each local model was measured to do. An endpoint appears here once a
          capability profile exists — Measure lives on the endpoint rows above.
        </p>
      </div>
      <div className="space-y-2">
        {shown.map((e) => (
          <MeasuredProfileCard
            key={`${e.provider}\u0000${e.model}`}
            entry={e}
            data={results[`${e.provider}\u0000${e.model}`] as EnvelopeData & { profile: EnvelopeProfile }}
          />
        ))}
      </div>
    </section>
  );
}

/**
 * One measured profile: plain-language verdict first, the effective window
 * (what the app REALLY budgets with) with its source in words, and the full
 * scores behind an expand. Floored rungs say "refused", never 0.00.
 */
function MeasuredProfileCard({
  entry,
  data,
}: {
  entry: MeasuredEntry;
  data: EnvelopeData & { profile: EnvelopeProfile };
}) {
  const [open, setOpen] = useState(false);
  const { provider, model, label } = entry;
  const tid = `measured-${provider}-${model}`;
  const profile = data.profile;
  const badge = sourceBadge(profile.source);
  const measured = MEASURED_SOURCES.has(profile.source);
  const tp = profile.tool_protocols ?? {};
  const notes = profile.probe_notes ?? {};

  // Per-field provenance. Profiles written before measured_fields existed
  // carry none — treat every field of a measured profile as measured then
  // (the old behaviour), rather than calling real scores floored.
  const hasFieldProvenance = Array.isArray(profile.measured_fields);
  const mf = new Set(profile.measured_fields ?? []);
  const fieldMeasured = (path: string): boolean =>
    hasFieldProvenance ? mf.has(path) : measured;

  const rungs = LADDER.map((l) => {
    const raw = tp[l.rung];
    const score = typeof raw === "number" ? raw : undefined;
    // A 0.0 whose path the probe never scored is a REFUSAL (the endpoint
    // errored on those trials), not a measurement of the model.
    const refused = score === 0 && !fieldMeasured(`tool_protocols.${l.rung}`);
    return { ...l, score, refused };
  });
  const selectedRung = measured
    ? (rungs.find((r) => r.score != null && !r.refused && r.score >= r.bar)?.rung ?? null)
    : null;

  // The one-line verdict, plain language FIRST — the user read "native 0.00"
  // as their model scoring zero; the card now leads with what it means.
  const verdict = !measured
    ? profile.source === "probe_failed"
      ? "measure failed — keeping floor defaults"
      : "reported by the endpoint — not verified yet"
    : selectedRung === "native"
      ? "fully usable — native tool calls"
      : selectedRung === "strict_json"
        ? "fully usable — tool calls run as guided JSON"
        : "limited — runs step-by-step with verification";
  const verdictTone = !measured
    ? profile.source === "probe_failed"
      ? "text-amber-200/90"
      : "text-zinc-400"
    : selectedRung != null
      ? "text-emerald-300/90"
      : "text-amber-200/90";

  // Context honesty: the app budgets with effective_window, so THAT is the
  // number shown. The profile's own floor context (8192/4096) never renders
  // as if it were the operating window.
  const ew = data.effective_window ?? null;
  const ctxMeasured = fieldMeasured("context_window") || fieldMeasured("honest_context");
  const adv = profile.context_window ?? null;
  const honest = profile.honest_context ?? null;
  const gapPct = adv && honest && adv > 0 ? Math.round((honest / adv) * 100) : null;
  const bigGap = adv != null && honest != null && adv > 0 && honest / adv < 0.5;

  const meta: string[] = [];
  if (measured) {
    if (profile.json_adherence != null && fieldMeasured("json_adherence"))
      meta.push(`JSON adherence ${profile.json_adherence.toFixed(2)}`);
    if (profile.coherence_horizon != null && fieldMeasured("coherence_horizon"))
      meta.push(`coherent to ~${profile.coherence_horizon} turns`);
    if (profile.vision != null && fieldMeasured("vision"))
      meta.push(`vision ${profile.vision ? "yes" : "no"}`);
  }

  return (
    <div
      data-testid={tid}
      className="rounded-xl border border-white/[0.06] bg-white/[0.02] px-3 py-2.5"
    >
      <div className="flex flex-wrap items-center gap-2">
        <span className="max-w-[10rem] truncate text-[12px] font-medium text-zinc-200" title={label}>
          {label}
        </span>
        <span className="min-w-0 truncate font-mono text-[10.5px] text-zinc-500" title={model}>
          {model}
        </span>
        <span
          data-testid={`${tid}-source`}
          title={badge.title}
          className={`shrink-0 rounded-full border px-1.5 py-0.5 text-[10px] ${badge.tone}`}
        >
          {badge.label}
        </span>
        <button
          type="button"
          data-testid={`${tid}-expand`}
          aria-expanded={open}
          onClick={() => setOpen((v) => !v)}
          className="ml-auto shrink-0 rounded-full border border-white/10 px-1.5 py-0.5 text-[10px] text-zinc-400 transition-colors hover:border-accent/30 hover:text-accent-soft"
        >
          {open ? "hide details" : "details"}
        </button>
      </div>

      <p data-testid={`${tid}-verdict`} className={`mt-1 text-[11px] font-medium ${verdictTone}`}>
        {verdict}
      </p>

      {ew && ew.value != null && (
        <p className="mt-0.5 text-[10.5px] text-zinc-400">
          context window: <span className="font-mono">{fmtInt(ew.value)}</span>{" "}
          ({effectiveWindowWords(ew.source)})
        </p>
      )}
      {!ctxMeasured && (
        <p className="mt-0.5 text-[10.5px] text-zinc-500">
          context not yet deep-measured; the app uses{" "}
          {ew?.source === "pin" ? "your pinned value" : "the endpoint's value"}
        </p>
      )}

      {open && (
        <div data-testid={`${tid}-detail`} className="mt-2 space-y-1 border-t border-white/[0.06] pt-2">
          {measured ? (
            <>
              <div data-testid={`${tid}-ladder`} className="space-y-1">
                {rungs.map((r) =>
                  r.refused ? (
                    <div key={r.rung} data-rung={r.rung} className="text-[10.5px]">
                      <span className="text-zinc-400">{r.label}: </span>
                      <span className="text-amber-200/90">the endpoint refused them</span>
                      {notes[`tool_protocols.${r.rung}`] && (
                        <p className="mt-0.5 text-[10px] leading-relaxed text-zinc-500">
                          {notes[`tool_protocols.${r.rung}`]}
                        </p>
                      )}
                    </div>
                  ) : (
                    <LadderRow
                      key={r.rung}
                      label={r.label}
                      rung={r.rung}
                      score={r.score}
                      bar={r.bar}
                      selected={selectedRung === r.rung}
                    />
                  ),
                )}
                <div data-rung="text_floor" className="flex items-center gap-2 text-[10.5px]">
                  <span className="w-36 shrink-0 truncate text-zinc-400">text floor</span>
                  <span
                    className={
                      selectedRung == null ? "font-semibold text-emerald-300" : "text-zinc-600"
                    }
                  >
                    {selectedRung == null
                      ? "SELECTED — no rung cleared its bar"
                      : "floor (always works)"}
                  </span>
                </div>
              </div>
              {ctxMeasured && honest != null && adv != null && honest < adv && (
                <p className="text-[10.5px] text-zinc-400">
                  honest context <span className="font-mono">{fmtInt(honest)}</span> of{" "}
                  <span className="font-mono">{fmtInt(adv)}</span> advertised
                  {gapPct != null ? ` (${gapPct}%)` : ""}
                  {bigGap && (
                    <span className="text-rose-300/90">
                      {" "}
                      — big gap; budgets use the honest number
                    </span>
                  )}
                </p>
              )}
              {profile.chars_per_token != null && fieldMeasured("chars_per_token") && (
                <p className="text-[10.5px] text-zinc-400">
                  {profile.chars_per_token} chars/token
                </p>
              )}
              {meta.length > 0 && (
                <p className="pt-0.5 text-[10px] text-zinc-500">{meta.join(" · ")}</p>
              )}
            </>
          ) : profile.source === "probe_failed" ? (
            <p className="text-[10.5px] text-amber-200/80">
              the probe battery ran and nothing came back usable — keeping floor defaults
            </p>
          ) : (
            <p className="text-[10.5px] text-zinc-500">
              capabilities reported by the endpoint, not verified — Measure runs the real probes
            </p>
          )}
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------- endpoint row controls */

/**
 * The row-level affordance for local endpoints: a provenance chip once a
 * profile exists, and the Measure button. POST kicks the background battery;
 * completion arrives as `envelope.probe_completed` (which also refetches the
 * chip); 400/409 details render verbatim — never paraphrased into success.
 */
export function EnvelopeRowControls({
  provider,
  model,
  pollMs = 10_000,
  timeoutMs = 180_000,
}: {
  provider: string;
  model: string;
  /** Test seams only — the defaults ARE the product cadence. */
  pollMs?: number;
  timeoutMs?: number;
}) {
  const { data, reload } = useEnvelope(provider, model);
  const [measuring, setMeasuring] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // The profile's provenance the moment Measure was pressed — the poll below
  // compares against it to notice a completed probe without the WS event.
  const baselineRef = useRef<{ source: string | null; probedAt: string | null } | null>(null);
  const onCompleted = useCallback(() => {
    setMeasuring(false);
    setError(null);
    reload();
  }, [reload]);
  useProbeCompleted(provider, model, onCompleted);

  // SAFETY NET: the live `envelope.probe_completed` event cannot be the ONLY
  // exit from the measuring state — the /events WebSocket replays no backlog,
  // so a completion that fires during a reconnect gap is gone forever and
  // would wedge the button until a page reload. While measuring, ALSO poll
  // the GET every 10s (a changed source/probed_at means the probe wrote its
  // result), and after 3 minutes give up loudly with an honest note instead
  // of spinning forever.
  useEffect(() => {
    if (!measuring) return;
    const iv = setInterval(async () => {
      try {
        const d = await get<EnvelopeData>(envelopeUrl(provider, model));
        const p = d?.profile;
        if (!p) return;
        const base = baselineRef.current;
        if (p.source !== base?.source || (p.probed_at ?? null) !== base?.probedAt) {
          setMeasuring(false);
          setError(null);
          reload();
        }
      } catch {
        /* daemon hiccup — keep polling until the cap */
      }
    }, pollMs);
    const to = setTimeout(() => {
      setMeasuring(false);
      setError("measurement finished or timed out — refresh shows the latest");
      reload();
    }, timeoutMs);
    return () => {
      clearInterval(iv);
      clearTimeout(to);
    };
  }, [measuring, provider, model, reload, pollMs, timeoutMs]);

  if (!model) return null;

  async function measure() {
    setError(null);
    baselineRef.current = {
      source: data?.profile?.source ?? null,
      probedAt: data?.profile?.probed_at ?? null,
    };
    setMeasuring(true);
    try {
      await post(`${envelopeUrl(provider, model)}/probe`);
      // Started — stay in the measuring state until the completion event.
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        // A probe IS already running — say so, and keep the measuring state
        // honest (the completion event will clear it).
        setError(err.message);
      } else {
        setMeasuring(false);
        setError(err instanceof ApiError ? err.message : String(err));
      }
    }
  }

  const profile = data?.profile;
  const trusted = data?.trusted === true;
  const badge = profile?.source ? sourceBadge(profile.source) : null;

  if (trusted) return null; // frontier rows get no chip, no button, no delta

  return (
    <>
      {badge && (
        <span
          data-testid={`envelope-chip-${provider}-${model}`}
          title={badge.title}
          className={`shrink-0 rounded-full border px-1.5 py-0.5 text-[10px] ${badge.tone}`}
        >
          {badge.label}
        </span>
      )}
      <button
        type="button"
        onClick={() => void measure()}
        disabled={measuring}
        data-testid={`measure-${provider}-${model}`}
        title="Measure this model's real capability envelope (tool-call ladder, honest context, chars/token) — runs in the background; the card updates when it finishes"
        className="shrink-0 rounded-full border border-white/10 px-1.5 py-0.5 text-[10px] text-zinc-400 transition-colors hover:border-accent/30 hover:text-accent-soft disabled:opacity-50"
      >
        {measuring ? "Measuring…" : "Measure"}
      </button>
      {error && (
        <span
          data-testid={`measure-error-${provider}-${model}`}
          className="w-full basis-full text-[10px] leading-relaxed text-amber-300/90"
        >
          {error}
        </span>
      )}
    </>
  );
}
