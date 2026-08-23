"use client";

/**
 * GoalsStrip (v1.208.0) — the "forgotten goal" killer on the Overview.
 *
 * A goal the user cannot see is a trust withdrawal even when nothing goes
 * wrong, so the Overview carries one compact row: a pill per goal that still
 * needs attention (active, tripped, failed), each linking to the Autonomy
 * page where the full contract and controls live. Tripped/failed goals wear
 * the warn tone and SORT FIRST — bad news leads.
 *
 * When there is nothing live to show the strip renders literally NOTHING —
 * no husk, no "no goals yet" filler on the busiest page in the app. Paused,
 * satisfied and stopped goals are also omitted here on purpose: they are at
 * rest by the user's own hand or by success, not forgotten.
 *
 * This file is also the one home of the v1.208.0 goal wire contract
 * (GET /goals, POST /goals/{id}/run|pause|resume|stop) and its wording
 * helpers; the Autonomy page's Goals section imports them from here so the
 * two surfaces can never drift on what "$0.41 of $2.00" means.
 */

import { useEffect, useRef } from "react";
import Link from "next/link";
import { useApi, type ApiState } from "@/lib/useApi";
import { useEvents } from "@/lib/useEvents";
import { timeAgo } from "@/lib/format";

/* -------------------------------------------------------------------------- */
/*  The goal contract (v1.208.0)                                              */
/* -------------------------------------------------------------------------- */

export type GoalState =
  | "active"
  | "paused"
  | "satisfied"
  | "failed"
  | "stopped"
  | "tripped";

/**
 * CROSS-SUITE RULE: these keys are the BACKEND's, verbatim — `goal_view`
 * serves `budget`/`spent` straight from the record, and the routes suite
 * (tests/test_goals_routes_v1208.py, `TOKENS_BUDGET = {"max_tokens": …}`,
 * goals/models.py `BUDGET_BOUNDS`) pins them. The dashboard suite and the
 * routes suite must pin THE SAME contract; inventing a key here renders
 * "no budget set" beside real spend on every budgeted goal.
 */
export interface GoalBudget {
  unlimited?: boolean;
  max_dollars?: number | null;
  max_tokens?: number | null;
  max_wallclock_s?: number | null;
}

/** Accumulated counters (`spent_json`); `iterations` counts but never gates. */
export interface GoalSpent {
  tokens?: number;
  dollars?: number;
  wallclock_s?: number;
  iterations?: number;
}

/** Per-tool ask receipts (v1.209.0 `ask_stats`): what the goal asked for and
 *  how the user answered. Receipts, not policy — the SERVER computes offers. */
export interface GoalAskStats {
  asked?: number;
  approved?: number;
  denied?: number;
  timed_out?: number;
}

export interface GoalVerifier {
  kind?: string;
  checks?: unknown[];
  /** Only on "judged" verifiers: the judge's own sentence on satisfaction. */
  judged_note?: string | null;
}

export interface GoalRecord {
  id: string;
  name: string;
  contract_text: string;
  state: GoalState;
  schedule?: string | null;
  budget?: GoalBudget | null;
  spent?: GoalSpent | null;
  last_run_at?: string | null;
  project_id?: string | null;
  verifier?: GoalVerifier | null;
  /** The breaker reason when state === "tripped" (shown VERBATIM). */
  trip_reason?: string | null;
  breaker?: { reason?: string | null } | null;
  /** v1.209.0: per-tool ask receipts, keyed by tool name. DETAIL ROUTE ONLY:
   *  the list route deliberately omits this (routes/goals.py `_payload` —
   *  the counts back the offer, the offer is what lists render); surfaces
   *  needing the table GET /goals/{id} lazily. */
  ask_stats?: Record<string, GoalAskStats> | null;
  /** v1.209.0: SERVER-computed grant offers (≥3 asks, all approved, never
   *  deny-floor tools, never already-granted). The UI renders ONLY these —
   *  it must never derive an offer from ask_stats on its own. */
  grant_offers?: string[] | null;
}

/* -------------------------------------------------------------------------- */
/*  Wording helpers (shared with the Autonomy page's Goals section)           */
/* -------------------------------------------------------------------------- */

export function goalDollars(v: number | null | undefined): string {
  const n = typeof v === "number" && Number.isFinite(v) ? v : 0;
  return `$${n.toFixed(2)}`;
}

/** Wallclock seconds in hours: 7560 → "2.1h", 14400 → "4h". */
export function goalHours(seconds: number | null | undefined): string {
  const s = typeof seconds === "number" && Number.isFinite(seconds) ? seconds : 0;
  const h = s / 3600;
  const rounded = h >= 10 ? Math.round(h) : Math.round(h * 10) / 10;
  return `${rounded}h`;
}

/** Compact token counts for the strip: 1200 → "1.2k", 1000000 → "1M". */
export function goalTokensCompact(v: number | null | undefined): string {
  const n = typeof v === "number" && Number.isFinite(v) ? v : 0;
  const scale = (x: number) => (x >= 10 ? Math.round(x) : Math.round(x * 10) / 10);
  if (n >= 1e6) return `${scale(n / 1e6)}M`;
  if (n >= 1e3) return `${scale(n / 1e3)}k`;
  return `${n}`;
}

/**
 * The verifier kind in words — the honesty scale matters: a deterministic
 * check, an adversarial one, a model's judgement, and "you decide" are four
 * different amounts of certainty and must never read alike.
 */
export function verifierWords(v?: GoalVerifier | null): string | null {
  const kind = v?.kind;
  if (!kind) return null;
  switch (kind) {
    case "checks":
      return "verified by checks";
    case "adversarial":
      // The server attaches judged_note exactly when the judge was the ONLY
      // gate (goal_view: kind "judged", or "adversarial" with zero checks,
      // on a satisfied goal). An adversarial run with no deterministic
      // checks must not read as if the ledger anchored anything.
      return v?.judged_note
        ? "adversarially verified — judge-only (no deterministic checks)"
        : "adversarially verified";
    case "judged":
      return "model-judged — no deterministic checks";
    case "manual":
      return "manual — you decide";
    default:
      // An unknown kind is shown raw, never guessed into a nicer sentence.
      return `verified by ${kind}`;
  }
}

/** The breaker reason of a tripped goal, or null. Never paraphrased. */
export function tripReason(g: GoalRecord): string | null {
  const r = g.trip_reason ?? g.breaker?.reason ?? null;
  return typeof r === "string" && r.trim() ? r : null;
}

/**
 * Spent vs budget, rendered honestly: a dollar cap reads "$0.41 of $2.00",
 * an unlimited budget SAYS it is unlimited and whose choice that was, and a
 * goal with no budget at all does not pretend to have one.
 */
export function spentVsBudget(g: GoalRecord): string {
  const sp = g.spent ?? {};
  const b = g.budget ?? null;
  if (b?.unlimited) {
    return `${goalDollars(sp.dollars)} spent · unlimited — by your choice`;
  }
  // Every SET bound renders as its own fraction (the store allows any mix of
  // the three; an absent bound gates nothing and is not shown).
  const parts: string[] = [];
  if (b && b.max_dollars != null) {
    parts.push(`${goalDollars(sp.dollars)} of ${goalDollars(b.max_dollars)}`);
  }
  if (b && b.max_tokens != null) {
    parts.push(`${(sp.tokens ?? 0).toLocaleString()} of ${b.max_tokens.toLocaleString()} tokens`);
  }
  if (b && b.max_wallclock_s != null) {
    parts.push(`${goalHours(sp.wallclock_s)} of ${goalHours(b.max_wallclock_s)}`);
  }
  if (parts.length) return parts.join(" · ");
  // The store refuses budgetless creation, so this is only ever a degenerate
  // or hand-edited row — say what we know, claim nothing.
  return `${goalDollars(sp.dollars)} spent — no budget set`;
}

/** The strip's mini-fraction: "$0.41/$2.00", or "$0.41/∞" for unlimited.
 *  One bound only (a pill is not a ledger): dollars, else tokens, else hours. */
export function spentMini(g: GoalRecord): string {
  const sp = g.spent ?? {};
  const b = g.budget ?? null;
  if (b?.unlimited) return `${goalDollars(sp.dollars)}/∞`;
  if (b && b.max_dollars != null) {
    return `${goalDollars(sp.dollars)}/${goalDollars(b.max_dollars)}`;
  }
  if (b && b.max_tokens != null) {
    return `${goalTokensCompact(sp.tokens)}/${goalTokensCompact(b.max_tokens)} tok`;
  }
  if (b && b.max_wallclock_s != null) {
    return `${goalHours(sp.wallclock_s)}/${goalHours(b.max_wallclock_s)}`;
  }
  return `${goalDollars(sp.dollars)} spent`;
}

const DAY_NAMES = [
  "Sundays",
  "Mondays",
  "Tuesdays",
  "Wednesdays",
  "Thursdays",
  "Fridays",
  "Saturdays",
];

/**
 * A schedule in words. Handles the common 5-field cron shapes; anything it
 * cannot honestly translate is shown raw (quoted) rather than guessed at,
 * and a schedule the backend already wrote in words passes through as-is.
 */
export function scheduleWords(schedule?: string | null): string {
  const s = (schedule ?? "").trim();
  if (!s) return "runs when asked";
  const parts = s.split(/\s+/);
  if (parts.length !== 5) return s; // already words ("every 30 minutes")
  const [min, hour, dom, mon, dow] = parts;
  const pad = (v: string) => v.padStart(2, "0");
  if (min === "*" && hour === "*") return "every minute";
  const minStep = min.match(/^\*\/(\d+)$/);
  if (minStep && hour === "*") return `every ${minStep[1]} minutes`;
  const hourStep = hour.match(/^\*\/(\d+)$/);
  if (/^\d+$/.test(min) && hourStep) return `every ${hourStep[1]} hours`;
  if (/^\d+$/.test(min) && hour === "*") return `hourly at :${pad(min)}`;
  if (/^\d+$/.test(min) && /^\d+$/.test(hour) && dom === "*" && mon === "*") {
    const at = `${pad(hour)}:${pad(min)}`;
    if (dow === "*") return `daily at ${at}`;
    const d = Number(dow);
    if (Number.isInteger(d) && d >= 0 && d <= 7) return `${DAY_NAMES[d % 7]} at ${at}`;
  }
  return `on cron "${s}"`;
}

/* -------------------------------------------------------------------------- */
/*  Live fetch: GET /goals, refetched when a goal.* event lands               */
/* -------------------------------------------------------------------------- */

/**
 * GET /goals kept fresh by the live stream: any goal.* frame
 * (iteration_started / iteration_completed / satisfied / tripped / …) marks
 * the list stale and reloads it. The seen-boundary is an event id so a
 * re-render never re-processes old frames into refetch loops (the RoundTable
 * idiom).
 */
export function useLiveGoals(): ApiState<{ goals: GoalRecord[] }> {
  const goals = useApi<{ goals: GoalRecord[] }>("/goals");
  const { events } = useEvents(40);
  const seenRef = useRef<string | null>(null);
  const reload = goals.reload;
  useEffect(() => {
    const newest = events[0];
    if (!newest?.id) return;
    const boundary = seenRef.current;
    seenRef.current = newest.id;
    let stale = false;
    for (const e of events) {
      if (e.id === boundary) break; // frames already processed
      if (typeof e.type === "string" && e.type.startsWith("goal.")) stale = true;
    }
    if (stale) reload();
  }, [events, reload]);
  return goals;
}

/* -------------------------------------------------------------------------- */
/*  The strip                                                                 */
/* -------------------------------------------------------------------------- */

/** Sort rank: bad news leads. Stable sort keeps server order within groups. */
function rank(g: GoalRecord): number {
  return g.state === "tripped" || g.state === "failed" ? 0 : 1;
}

export function GoalsStrip() {
  const goals = useLiveGoals();
  const list = goals.data?.goals ?? [];
  // Only what still needs eyes: active work, and anything broken. While the
  // list is loading or unreachable we also show nothing — absence makes no
  // claim, unlike an empty-state sentence rendered off a guess.
  const shown = [...list]
    .filter((g) => g.state === "active" || g.state === "tripped" || g.state === "failed")
    .sort((a, b) => rank(a) - rank(b));
  if (shown.length === 0) return null;

  return (
    <div data-testid="goals-strip" className="flex flex-wrap items-center gap-2">
      <span className="text-[10px] font-medium uppercase tracking-[0.14em] text-zinc-500">
        Goals
      </span>
      {shown.map((g) => {
        const warn = g.state === "tripped" || g.state === "failed";
        const pill = warn
          ? "border-rose-500/30 bg-rose-500/[0.07] text-rose-200 hover:bg-rose-500/[0.12]"
          : "border-accent/25 bg-accent/[0.06] text-zinc-200 hover:bg-accent/[0.1]";
        const dot = warn ? "bg-rose-400" : "bg-accent";
        return (
          <Link
            key={g.id}
            href="/autonomy"
            data-goal-pill
            data-state={g.state}
            data-tone={warn ? "warn" : "ok"}
            title={`${g.name} — ${g.state} · ${spentVsBudget(g)} · open Autonomy`}
            className={`inline-flex items-center gap-2 rounded-full border px-3 py-1 text-xs transition-colors ${pill}`}
          >
            <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${dot}`} />
            <span className="max-w-[11rem] truncate font-medium">{g.name}</span>
            <span className="tabular-nums text-[10px] opacity-80">{spentMini(g)}</span>
            <span className="text-[10px] opacity-60">
              {g.last_run_at ? timeAgo(g.last_run_at) : "never ran"}
            </span>
          </Link>
        );
      })}
    </div>
  );
}
