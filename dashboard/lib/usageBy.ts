/**
 * What everything cost, by folder, client, automation and agent (next leaps
 * 46-a): the Usage page's **By** switch.
 *
 * The wire, from `src/iron_jarvis/daemon/routes/system.py`:
 *   GET /usage/by?group=folder|client|automation|agent&days=N
 * Each row keeps three figures apart and never adds them up: pay per use
 * (money, `measured` when every paid figure was the vendor's own, else
 * estimated from the price table), your plan (TOKENS, never money) and free
 * (your own models). An automation row also counts how many times it asked.
 *
 * Decoded defensively: a folder's, a rule's or a client's name is text,
 * never markup. Pure: words and decoding only (the card is
 * components/usage/UsageBy.tsx).
 */

export const USAGE_BY_GROUPS = ["folder", "client", "automation", "agent"] as const;
export type UsageByGroup = (typeof USAGE_BY_GROUPS)[number];

/** The switch's words, in order. */
export const GROUP_WORDS: Record<UsageByGroup, string> = {
  folder: "Folder",
  client: "Client",
  automation: "Automation",
  agent: "Agent",
};

export interface PayFigure {
  usd: number;
  tokens: number;
  measured: boolean;
  price_unknown_tokens: number;
}

export interface PlanFigure {
  tokens: number;
  /** "about N% of a 5-hour window": only once the account's windows were
   *  read before and after the work. Null = not known (tokens only). */
  window_share: number | null;
  window: string;
}

export interface UsageByRow {
  key: string;
  label: string;
  kind: string;
  folder: string;
  pay_per_use: PayFigure;
  plan: PlanFigure;
  free: { tokens: number };
  asks: number;
  runs: number;
}

export interface UsageByView {
  group: UsageByGroup;
  days: number;
  rows: UsageByRow[];
  rows_total: number;
  rest: (Omit<UsageByRow, "key" | "label" | "kind" | "folder"> & { count: number }) | null;
  totals: Omit<UsageByRow, "key" | "label" | "kind" | "folder">;
  truncated: boolean;
  error: string;
}

const num = (v: unknown): number => (typeof v === "number" && Number.isFinite(v) && v > 0 ? v : 0);
const text = (v: unknown): string => (typeof v === "string" ? v : "");

function pay(v: unknown): PayFigure {
  const o = (v && typeof v === "object" ? v : {}) as Record<string, unknown>;
  return {
    usd: num(o.usd),
    tokens: num(o.tokens),
    measured: o.measured === true,
    price_unknown_tokens: num(o.price_unknown_tokens),
  };
}

function plan(v: unknown): PlanFigure {
  const o = (v && typeof v === "object" ? v : {}) as Record<string, unknown>;
  const share = o.window_share;
  return {
    tokens: num(o.tokens),
    window_share: typeof share === "number" && Number.isFinite(share) && share >= 0 ? share : null,
    window: text(o.window),
  };
}

function figures(v: Record<string, unknown>) {
  const free = (v.free && typeof v.free === "object" ? v.free : {}) as Record<string, unknown>;
  return {
    pay_per_use: pay(v.pay_per_use),
    plan: plan(v.plan),
    free: { tokens: num(free.tokens) },
    asks: num(v.asks),
    runs: num(v.runs),
  };
}

export function isGroup(v: unknown): v is UsageByGroup {
  return typeof v === "string" && (USAGE_BY_GROUPS as readonly string[]).includes(v);
}

/** The view, or null for an answer that is not one. */
export function decodeUsageBy(raw: unknown): UsageByView | null {
  if (!raw || typeof raw !== "object") return null;
  const o = raw as Record<string, unknown>;
  if (!Array.isArray(o.rows)) return null;
  const rows: UsageByRow[] = [];
  for (const r of o.rows) {
    if (!r || typeof r !== "object") continue;
    const x = r as Record<string, unknown>;
    const label = text(x.label).trim();
    if (!label) continue;
    rows.push({ key: text(x.key), label, kind: text(x.kind), folder: text(x.folder), ...figures(x) });
  }
  const rest = o.rest && typeof o.rest === "object" ? (o.rest as Record<string, unknown>) : null;
  return {
    group: isGroup(o.group) ? o.group : "folder",
    days: num(o.days) || 30,
    rows,
    rows_total: num(o.rows_total) || rows.length,
    rest: rest ? { count: num(rest.count), ...figures(rest) } : null,
    totals: figures((o.totals && typeof o.totals === "object" ? o.totals : {}) as Record<string, unknown>),
    truncated: o.truncated === true,
    error: text(o.error),
  };
}

/* -------------------------------------------------------------------------- */
/*  Words                                                                      */
/* -------------------------------------------------------------------------- */

/** "$4.10"; "under $0.01" for a figure that rounds to nothing. */
export function moneyWords(usd: number): string {
  if (usd > 0 && usd < 0.005) return "under $0.01";
  return usd.toLocaleString(undefined, {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

/** "measured" (the vendor's own figure) or "estimated" (the price table). */
export function markWords(p: PayFigure): "measured" | "estimated" {
  return p.measured ? "measured" : "estimated";
}

export function tokenWords(n: number): string {
  return `${n.toLocaleString()} token${n === 1 ? "" : "s"}`;
}

/** "Asked you once" / "Asked you twice" / "Asked you 5 times"; "" for none. */
export function askedWords(n: number): string {
  if (!(n > 0)) return "";
  if (n === 1) return "Asked you once";
  if (n === 2) return "Asked you twice";
  return `Asked you ${n} times`;
}

/** The plan figure: tokens, never money; the window share once known. */
export function planWords(p: PlanFigure): string {
  if (!(p.tokens > 0)) return "";
  const share =
    p.window_share !== null ? `, about ${Math.round(p.window_share)}% of a ${p.window || "5-hour"} window` : "";
  return `${tokenWords(p.tokens)}${share}`;
}

/** A folder row's eye-name: its last part ("C:\\Work\\Price list" -> "Price list"). */
export function folderName(path: string): string {
  const parts = path.replace(/[\\/]+$/, "").split(/[\\/]/);
  return parts[parts.length - 1] || path;
}

export const UNKNOWN_PRICE = "price not known";
export const PLAN_NOTE = "Work on your plan is counted in tokens, never as money.";
export const RECORD_NOTE = "From Iron Jarvis's own record of each model call.";
export const TRUNCATED_NOTE = "Counted from the newest 200,000 model calls in this window.";
export const EMPTY_WORDS = "Nothing used in this window.";
