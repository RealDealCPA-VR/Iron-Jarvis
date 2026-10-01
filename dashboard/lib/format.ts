import type { AgentAllowance } from "./types";

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
