"use client";

/**
 * The Capability Envelope surfaces (v1.201.0, Wave A A4).
 *
 * IronCore's lesson, ported: a model's profile is only worth showing if its
 * PROVENANCE is shown next to it. The report card once rendered a green
 * SELECTED on an unprobed profile — that class of lie is banned here, so:
 *  - the source badge renders wherever a profile value renders;
 *  - ladder rows (and the word SELECTED) render ONLY for measured sources
 *    (probed / partial / tuned) — seeded and floor profiles say what they
 *    are instead of dressing up as a scorecard;
 *  - a probe that measured nothing says so ("measure failed — keeping floor
 *    defaults"), and a trusted (frontier) model gets one quiet line, never a
 *    fake scorecard.
 *
 * Wire contract (backend ships in parallel — mocked in tests):
 *   GET  /envelope/{provider}/{model}        -> { profile, trusted }
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
}

export interface EnvelopeData {
  profile?: EnvelopeProfile | null;
  trusted?: boolean;
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

/* -------------------------------------------------------- report section */

/**
 * The envelope section of the Model report card. Renders nothing until the
 * GET answers with a real profile; a trusted model gets one quiet line and
 * never a scorecard; ladder rows appear only on measured provenance.
 */
export function EnvelopeSection({
  provider,
  model,
  surface,
}: {
  provider: string;
  model: string;
  /** Same disambiguation ModelReportLine uses when one provider renders on
   *  two surfaces — duplicate testids would make both unaddressable. */
  surface?: string;
}) {
  const { data, reload } = useEnvelope(provider, model);
  useProbeCompleted(provider, model, reload);

  if (!data) return null;
  const tid = `envelope-${surface ? `${surface}-` : ""}${provider}-${model}`;
  if (data.trusted) {
    return (
      <div data-testid={`${tid}-trusted`} className="mt-0.5 text-[10.5px] text-zinc-600">
        fully capable — no measurement needed
      </div>
    );
  }
  const profile = data.profile;
  if (!profile || !profile.source) return null;

  const badge = sourceBadge(profile.source);
  const measured = MEASURED_SOURCES.has(profile.source);
  const tp = profile.tool_protocols ?? {};
  const selectedRung =
    (measured &&
      LADDER.find((l) => {
        const s = tp[l.rung];
        return typeof s === "number" && s >= l.bar;
      })?.rung) ||
    null;

  const adv = profile.context_window ?? null;
  const honest = profile.honest_context ?? null;
  const gapPct = adv && honest && adv > 0 ? Math.round((honest / adv) * 100) : null;
  const bigGap = adv != null && honest != null && adv > 0 && honest / adv < 0.5;

  const meta: string[] = [];
  if (measured) {
    if (profile.json_adherence != null) meta.push(`JSON adherence ${profile.json_adherence.toFixed(2)}`);
    if (profile.coherence_horizon != null) meta.push(`coherent to ~${profile.coherence_horizon} turns`);
    if (profile.vision != null) meta.push(`vision ${profile.vision ? "yes" : "no"}`);
  }

  return (
    <div
      data-testid={tid}
      className="mt-1 space-y-1 rounded-lg border border-white/[0.06] bg-white/[0.02] px-2.5 py-2"
    >
      <div className="flex flex-wrap items-center gap-1.5 text-[10px]">
        <span className="font-medium uppercase tracking-wide text-zinc-500">envelope</span>
        <span
          data-testid={`${tid}-source`}
          title={badge.title}
          className={`rounded-full border px-1.5 py-0.5 ${badge.tone}`}
        >
          {badge.label}
        </span>
        <span className="font-mono text-zinc-600">{model}</span>
      </div>

      {honest != null && adv != null && honest < adv ? (
        <p className="text-[10.5px] text-zinc-400">
          honest context <span className="font-mono">{fmtInt(honest)}</span> of{" "}
          <span className="font-mono">{fmtInt(adv)}</span> advertised
          {gapPct != null ? ` (${gapPct}%)` : ""}
          {bigGap && (
            <span className="text-rose-300/90"> — big gap; budgets use the honest number</span>
          )}
        </p>
      ) : honest != null || adv != null ? (
        <p className="text-[10.5px] text-zinc-400">
          context <span className="font-mono">{fmtInt((honest ?? adv) as number)}</span> tokens
          {measured ? "" : profile.source === "seeded" ? " (reported)" : " (floor)"}
        </p>
      ) : null}

      {profile.chars_per_token != null && (
        <p className="text-[10.5px] text-zinc-400">
          {profile.chars_per_token} chars/token
          {measured ? "" : " (assumed)"}
        </p>
      )}

      {measured ? (
        <div data-testid={`${tid}-ladder`} className="space-y-1 pt-0.5">
          {LADDER.map((l) => (
            <LadderRow
              key={l.rung}
              label={l.label}
              rung={l.rung}
              score={tp[l.rung]}
              bar={l.bar}
              selected={selectedRung === l.rung}
            />
          ))}
          <div data-rung="text_floor" className="flex items-center gap-2 text-[10.5px]">
            <span className="w-36 shrink-0 truncate text-zinc-400">text floor</span>
            <span
              className={
                selectedRung == null ? "font-semibold text-emerald-300" : "text-zinc-600"
              }
            >
              {selectedRung == null ? "SELECTED — no rung cleared its bar" : "floor (always works)"}
            </span>
          </div>
          {meta.length > 0 && (
            <p className="pt-0.5 text-[10px] text-zinc-500">{meta.join(" · ")}</p>
          )}
        </div>
      ) : profile.source === "seeded" ? (
        <p className="text-[10.5px] text-zinc-500">
          capabilities reported by the endpoint, not verified — Measure runs the real probes
        </p>
      ) : profile.source === "probe_failed" ? (
        <p className="text-[10.5px] text-amber-200/80">
          the probe battery ran and nothing came back usable — keeping floor defaults
        </p>
      ) : (
        <p className="text-[10.5px] text-zinc-500">
          nothing probed yet — Measure runs the capability battery
        </p>
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
