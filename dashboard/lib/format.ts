import type { AgentAllowance } from "./types";
import { providerDisplay } from "./onboarding";

export function shortId(id: string | null | undefined): string {
  if (!id) return "—";
  return id.length > 14 ? id.slice(0, 14) + "…" : id;
}

/**
 * The daemon stores naive UTC timestamps (no zone suffix). A zone-less ISO
 * string is parsed as LOCAL time by the browser, so a run that finished hours
 * ago in a UTC-offset timezone reads as "now". Treat a zone-less timestamp as
 * UTC by appending 'Z' before parsing. Fixes relative + clock times everywhere.
 */
export function normalizeIso(iso: string): string {
  // Has a time component (T or space + HH:MM) but no zone (Z or ±HH:MM)?
  if (/[T ]\d{2}:\d{2}/.test(iso) && !/[zZ]|[+-]\d{2}:?\d{2}$/.test(iso)) {
    return iso.replace(" ", "T") + "Z";
  }
  return iso;
}

export function timeAgo(iso: string | null | undefined): string {
  if (!iso) return "—";
  const t = new Date(normalizeIso(iso)).getTime();
  if (Number.isNaN(t)) return iso;
  const s = Math.floor((Date.now() - t) / 1000);
  if (s < 0) return "now";
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

export function clockTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(normalizeIso(iso));
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleTimeString();
}

export function pct(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  // accept either 0..1 or 0..100
  const n = v <= 1 ? v * 100 : v;
  return `${n.toFixed(0)}%`;
}

export function num(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return Number(v).toFixed(digits);
}

/**
 * A dead engine's exit code, in words (v1.191.0). The raw number is kept —
 * it is the searchable fact — but it must never be the WHOLE sentence.
 * Windows reports a signed -1 as unsigned 4294967295, and -1/terminated is
 * what a process reads when something killed it from outside: most commonly
 * the app itself restarting underneath the session (an update's
 * restart-to-apply kills the daemon's ConPTY children), sometimes a crash.
 */
export function describeEngineExit(code: number | null): string {
  if (code === null) return "The engine exited.";
  if (code === 0) return "The engine exited normally (code 0).";
  if (code === 4294967295 || code === -1) {
    return (
      "The engine was terminated (code -1) — usually the app restarted " +
      "underneath it (an update applying, or the daemon restarting), " +
      "occasionally a crash in the engine itself."
    );
  }
  if (code === 3221225786) {
    return "The engine was interrupted (Ctrl+C / console closed, code 0xC000013A).";
  }
  return `The engine exited with an error (code ${code}).`;
}

/* ---- v1.295.0: an employee's monthly allowance, in words ---------------- */

/** "12.4k", "50k", "1.2M" — tokens read at a glance in a 15rem rail. Whole
 *  numbers under a thousand stay as they are. */
export function formatTokens(n: number): string {
  const v = Number.isFinite(n) ? Math.max(0, n) : 0;
  if (v >= 1_000_000) return `${trimZero((v / 1_000_000).toFixed(1))}M`;
  if (v >= 1_000) return `${trimZero((v / 1_000).toFixed(1))}k`;
  return String(Math.round(v));
}

function trimZero(s: string): string {
  return s.endsWith(".0") ? s.slice(0, -2) : s;
}

function usdText(n: number): string {
  // "$10" for a round bound, "$1.20" for a spend — bounds are typed by a
  // person, spends are summed by a machine.
  return Number.isInteger(n) ? `$${n}` : `$${n.toFixed(2)}`;
}

/** The one-line reading: "Spent 12.4k of 50k tokens this month · 24%", with
 *  " · $1.20 of $10" when a dollar bound is also set, or the dollar form alone
 *  when ONLY dollars are bounded. */
export function allowanceSummary(a: AgentAllowance): string {
  const parts: string[] = [];
  const tokenBound = a.tokens > 0;
  const usdBound = a.usd > 0;
  if (tokenBound) {
    parts.push(
      `Spent ${formatTokens(a.spent_tokens)} of ${formatTokens(a.tokens)} tokens this month`,
    );
    if (usdBound) parts.push(`$${a.spent_usd.toFixed(2)} of ${usdText(a.usd)}`);
  } else if (usdBound) {
    parts.push(`Spent $${a.spent_usd.toFixed(2)} of ${usdText(a.usd)} this month`);
  } else {
    parts.push(`Spent ${formatTokens(a.spent_tokens)} tokens this month`);
  }
  if (typeof a.pct === "number" && Number.isFinite(a.pct)) {
    parts.push(`${Math.round(a.pct)}%`);
  }
  return parts.join(" · ");
}

/* ---- v1.316.0: big numbers at a glance, money in one format ------------- */

/** "1.52B", "3.15M", "12.4K" — a count read at a glance (Intl compact, at most
 *  two decimals). Show the EXACT count beside it in a `title` (see
 *  `exactCount`): a ten-digit number read digit by digit is not information,
 *  but the precise figure must stay one hover away. */
export function compactCount(v: number | null | undefined): string {
  const n = typeof v === "number" && Number.isFinite(v) ? v : 0;
  return new Intl.NumberFormat(undefined, {
    notation: "compact",
    maximumFractionDigits: 2,
  }).format(n);
}

/** "1,524,819,623" — the exact count, for the `title` beside a compactCount. */
export function exactCount(v: number | null | undefined): string {
  const n = typeof v === "number" && Number.isFinite(v) ? v : 0;
  return n.toLocaleString();
}

/** A sum of metered money where a fraction of a cent matters ("Cost in this
 *  view"): exactly zero reads "$0.00" like every other page; a small non-zero
 *  cost keeps four decimals so $0.0200 does not round to a misleading $0.02 —
 *  or to "$0.00", which would claim nothing was spent. */
export function usdPrecise(v: number | null | undefined): string {
  const n = typeof v === "number" && Number.isFinite(v) ? v : 0;
  if (n === 0) return "$0.00";
  return `$${n.toFixed(Math.abs(n) < 1 ? 4 : 2)}`;
}

/**
 * The model an estimate was priced against, in words: "Claude Opus 4.8" for
 * claude-opus-4-8 (a dated snapshot suffix is dropped); anything else is its
 * id after the provider's plain name. Never invents a model. Copied from the
 * Fleet page's private helper (app/fleet/page.tsx keeps its own copy for now)
 * so Usage names the SAME baseline in the same words.
 */
export function baselineName(provider: string, model: string): string {
  const claude = /^claude-(opus|sonnet|haiku)-(\d+)(?:-(\d+))?(?:-\d{8})?$/i.exec(model);
  if (claude) {
    const family = claude[1].charAt(0).toUpperCase() + claude[1].slice(1).toLowerCase();
    return `Claude ${family} ${claude[2]}${claude[3] ? `.${claude[3]}` : ""}`;
  }
  const who = providerDisplay(provider);
  return [who, model].filter(Boolean).join(" ");
}

/** A usage row's provider in words: `pi/<x>` and `opencode/<x>` read like the
 *  daemon's own fleet labels ("Pi · local-models", fleet.py); every other id
 *  goes through providerDisplay ("claude-cli" → "Claude Code"). */
export function usageProviderName(provider: string | null | undefined): string {
  const p = (provider || "").trim();
  if (p.startsWith("pi/")) return `Pi · ${p.slice(3)}`;
  if (p.startsWith("opencode/")) return `OpenCode · ${p.slice(9)}`;
  return providerDisplay(p);
}
