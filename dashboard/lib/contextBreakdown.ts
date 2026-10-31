/**
 * WHAT FILLS A CODING SESSION'S CONVERSATION (v1.415.0) — the Session tab's
 * Context drop-down, in BOTH coding-session views.
 *
 * The numbers are never made up here. Chat › Code reads the reply line's
 * `context.breakdown` (the daemon counted the very strings the turn sent —
 * `context/breakdown.harness_breakdown`); Build › Code reads
 * `GET /build/app/sessions/{id}/context` (Claude Code's own `/context`, Pi's
 * own stats, or the total the tool said after its last reply). A tool that
 * says only a total has NO parts, and the drop-down says so in its words.
 *
 * Pure decoders and words; the one shared component is
 * components/harness/ContextBreakdown.
 */

import { formatTokens } from "@/lib/format";

export type PartKind = "used" | "buffer" | "free";

export interface ContextPart {
  key: string;
  label: string;
  tokens: number;
  kind: PartKind;
}

export interface ContextExtra {
  key: string;
  label: string;
  tokens: number;
}

export interface ContextListItem {
  name: string;
  detail: string;
  path: string;
  tokens: number | null;
}

export interface ContextList {
  key: string;
  label: string;
  count: number;
  items: ContextListItem[];
}

export interface AutoCompact {
  enabled: boolean;
  /** The size at which earlier messages are summarized (tokens). */
  at: number | null;
  /** How far away that is. */
  left: number | null;
  percent: number | null;
}

export interface ContextReading {
  source: "app" | "tool";
  used: number;
  window: number;
  percent: number;
  parts: ContextPart[];
  extra: ContextExtra[];
  lists: ContextList[];
  autoCompact: AutoCompact | null;
  /** When it was read (a kept reading), else null. */
  at: string | null;
}

const rec = (v: unknown): Record<string, unknown> | null =>
  v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : null;
const num = (v: unknown): number | null =>
  typeof v === "number" && Number.isFinite(v) && v >= 0 ? Math.round(v) : null;
const str = (v: unknown, n = 200): string => (typeof v === "string" ? v.replace(/\s+/g, " ").trim().slice(0, n) : "");

/** The daemon's breakdown (`context/breakdown` shape), or null when it holds
 *  no window. */
export function decodeBreakdown(raw: unknown): ContextReading | null {
  const r = rec(raw);
  const window = num(r?.window);
  const used = num(r?.used);
  if (!r || !window || used === null) return null;
  const pct = num(r.percent);
  const parts: ContextPart[] = [];
  for (const p of Array.isArray(r.parts) ? r.parts : []) {
    const o = rec(p);
    const tokens = num(o?.tokens);
    const label = str(o?.label, 80);
    if (!o || tokens === null || !label) continue;
    const kind: PartKind = o.kind === "buffer" || o.kind === "free" ? o.kind : "used";
    parts.push({ key: str(o.key, 60), label, tokens, kind });
  }
  const extra: ContextExtra[] = [];
  for (const p of Array.isArray(r.extra) ? r.extra : []) {
    const o = rec(p);
    const tokens = num(o?.tokens);
    const label = str(o?.label, 80);
    if (o && tokens !== null && label) extra.push({ key: str(o.key, 60), label, tokens });
  }
  const lists: ContextList[] = [];
  for (const l of Array.isArray(r.lists) ? r.lists : []) {
    const o = rec(l);
    const count = num(o?.count);
    const label = str(o?.label, 80);
    if (!o || !count || !label) continue;
    const items: ContextListItem[] = [];
    for (const it of Array.isArray(o.items) ? o.items : []) {
      const x = rec(it);
      const name = str(x?.name);
      if (!x || !name) continue;
      items.push({ name, detail: str(x.detail, 80), path: typeof x.path === "string" ? x.path : "", tokens: num(x.tokens) });
    }
    lists.push({ key: str(o.key, 60), label, count, items });
  }
  const ac = rec(r.auto_compact);
  const autoCompact: AutoCompact | null = ac
    ? ac.enabled === false
      ? { enabled: false, at: null, left: null, percent: null }
      : num(ac.at)
        ? { enabled: true, at: num(ac.at), left: num(ac.left), percent: num(ac.percent) }
        : null
    : null;
  return {
    source: r.source === "tool" ? "tool" : "app",
    used,
    window,
    percent: pct !== null ? Math.min(100, pct) : Math.min(100, Math.round((used / window) * 100)),
    parts,
    extra,
    lists,
    autoCompact,
    at: typeof r.at === "string" && r.at ? r.at : null,
  };
}

/** A reading with no parts (an older reply, a tool that says only a total). */
export function totalReading(window: number, used: number | null, percent: number, at: string | null = null): ContextReading {
  return {
    source: "app",
    used: used ?? Math.round((percent / 100) * window),
    window,
    percent,
    parts: [],
    extra: [],
    lists: [],
    autoCompact: null,
    at,
  };
}

/** The newest reply line's reading in a Chat › Code session: its
 *  `context.breakdown` when the daemon wrote one, else the plain total the
 *  line always carried. The SAME line `latestContext` picks. */
export function latestReading(
  messages: readonly { role: string; context?: unknown; at?: string }[],
  pick: { window: number; used: number | null; percent: number } | null,
  at: string | null,
): ContextReading | null {
  if (!pick) return null;
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    const m = messages[i];
    if (m.role !== "assistant") continue;
    const ctx = rec(m.context);
    if (!ctx || !num(ctx.window)) continue;
    const b = decodeBreakdown(ctx.breakdown);
    if (b) return { ...b, percent: pick.percent, at };
    break;
  }
  return totalReading(pick.window, pick.used, pick.percent, at);
}

/** `GET /build/app/sessions/{id}/context`. */
export interface CodeContext {
  reading: ContextReading | null;
  /** A reply is running and the tool is asked again when it ends. */
  waiting: boolean;
  note: string;
  waitingNote: string;
  /** "message": the tool's own `/compact`, sent as a message ("" = none). */
  compactHow: "message" | "";
  compactText: string;
  compactWhy: string;
}

export function decodeCodeContext(raw: unknown): CodeContext | null {
  const r = rec(raw);
  if (!r) return null;
  const c = rec(r.compact);
  return {
    reading: decodeBreakdown(r.reading),
    waiting: r.waiting === true,
    note: str(r.note, 300),
    waitingNote: str(r.waiting_note, 300),
    compactHow: c?.how === "message" ? "message" : "",
    compactText: str(c?.text, 40),
    compactWhy: str(c?.why, 300),
  };
}

/** "722.5k / 1M". */
export function usedOfWindow(r: ContextReading): string {
  return `${formatTokens(r.used)} / ${formatTokens(r.window)}`;
}

/** A part's share of the window, one decimal ("33.8%"; "<0.1%" for a sliver). */
export function partPercent(tokens: number, window: number): string {
  if (window <= 0) return "";
  const p = (tokens / window) * 100;
  if (p > 0 && p < 0.05) return "<0.1%";
  return `${p.toFixed(1)}%`;
}

/** "240k until earlier messages are summarized" — or null when there is no
 *  automatic summary to name. */
export function untilSummaryWords(r: ContextReading): string | null {
  const ac = r.autoCompact;
  if (!ac) return null;
  if (!ac.enabled) return "The automatic summary is turned off.";
  if (ac.left === null) return null;
  if (ac.left <= 0) return "Earlier messages are summarized with the next message.";
  return `${formatTokens(ac.left)} until earlier messages are summarized`;
}

/** The colour of a part (tone tokens; the label and percent beside it are
 *  the second cue — never the colour alone). */
export function swatchClass(key: string, kind: PartKind): string {
  if (kind === "free") return "bg-white/[0.08]";
  if (kind === "buffer") return "bg-zinc-600";
  switch (key) {
    case "messages":
      return "bg-tone-violet";
    case "rules":
    case "memory_files":
      return "bg-tone-warn";
    case "memory":
      return "bg-tone-success";
    case "project":
      return "bg-tone-info";
    case "skills":
      return "bg-tone-success/60";
    case "attachments":
      return "bg-tone-info/60";
    case "summary":
      return "bg-tone-violet/50";
    case "mcp_tools":
    case "extension_tools":
      return "bg-accent";
    case "mcp_instructions":
      return "bg-accent/60";
    case "custom_agents":
      return "bg-tone-warn/60";
    case "system_tools":
    case "tools":
      return "bg-zinc-500";
    default:
      return "bg-zinc-400";
  }
}

// ── The automatic summary's mark (Chat › Code: a real setting) ─────────────

/** The setting that holds it (settings/schema `context_compaction`). */
export const COMPACTION_SETTING = "context_compaction";

/** The choices: "auto" leaves the app's own mark (92 %). Every choice is
 *  above the 70 % offer mark, so the offer still comes first. */
export const AUTO_COMPACT_CHOICES: readonly { value: string; label: string; at: number | null }[] = [
  { value: "auto", label: "Auto", at: null },
  { value: "85", label: "85%", at: 0.85 },
  { value: "80", label: "80%", at: 0.8 },
  { value: "75", label: "75%", at: 0.75 },
];

export const AUTO_COMPACT_HELP =
  "Lower keeps more room free: earlier messages are summarized sooner. Applies to every chat and coding session.";

/** The choice the stored setting reads as ("auto" when it names no mark). */
export function autoCompactChoice(setting: unknown): string {
  const r = rec(setting);
  const at = typeof r?.auto_at === "number" ? r.auto_at : null;
  if (at === null) return "auto";
  const hit = AUTO_COMPACT_CHOICES.find((c) => c.at !== null && Math.abs(c.at - at) < 0.001);
  return hit ? hit.value : `${Math.round(at * 100)}`;
}

/** The setting to save for a choice: the stored value with only its mark
 *  changed (the offer mark and the on/off switch are kept). */
export function settingFor(setting: unknown, choice: string): Record<string, unknown> {
  const out: Record<string, unknown> = { ...(rec(setting) ?? {}) };
  const hit = AUTO_COMPACT_CHOICES.find((c) => c.value === choice);
  if (!hit || hit.at === null) delete out.auto_at;
  else out.auto_at = hit.at;
  return out;
}
