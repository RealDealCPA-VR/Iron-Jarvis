// v1.305.0 — preferences you approve: the pure half (decoders + words).
//
// A correction the user repeats ("shorter please" … "too long again") becomes
// ONE quiet question under the reply: keep it as a standing preference? The
// daemon decides when to ask (learning/corrections.py, no model call) and
// carries `suggestion` on the done frame / POST response — null when it has
// nothing to ask. This module turns that wire value, and the Memory page's
// `GET /memory/preferences` answer, into typed shapes the UI can trust.
//
// Deliberately OUTSIDE lib/api.ts: ~71 test files mock that module wholesale,
// and a decoder living there would vanish under every one of those mocks.
// Nothing here fetches; the components own their own calls.
import { timeAgo } from "./format";

/** One piece of evidence: the user's own words, where and when. */
export interface PrefQuote {
  quote: string;
  /** ISO time the message was sent, "" when the daemon did not say. */
  at: string;
  /** "chat" | "phone" | "build" | "claude-code" | "codex" | "" */
  where: string;
  /** Optional door back to the conversation the words came from. */
  link?: string;
}

/** What the in-chat line is about, as it rides the assistant message.
 *  `state` is the CLIENT's record of the user's answer, persisted with the
 *  thread like `remembered` so a reload renders the outcome, not the ask. */
export interface ChatSuggestion {
  id: string;
  /** The proposed standing preference, in the user's own words. */
  text: string;
  /** How many times the correction was seen (≥ 2 when asked). */
  count: number;
  quotes: PrefQuote[];
  /** ISO time of the earliest occurrence, "" when unknown. */
  since: string;
  /** Absent/"open" = still asking; "kept" / "declined" = answered;
   *  "gone" = the row no longer exists (asked again or forgotten elsewhere)
   *  — the line renders nothing. */
  state?: "open" | "kept" | "declined" | "gone";
}

/** One row of the Memory page's preference list. */
export interface PrefRow {
  id: string;
  text: string;
  status: "confirmed" | "proposed" | "declined";
  /** "said" (the user stated it) | "noticed" (a repeated correction) | "". */
  origin: string;
  /** The lesson's source ("preference", "feedback", …) when the daemon sent it. */
  source: string;
  evidence: PrefQuote[];
  created_at: string;
  decided_at: string;
}

export interface PreferencesView {
  kept: PrefRow[];
  suggested: PrefRow[];
  never: PrefRow[];
  /** The apps the history reader found on this PC ("claude-code", "codex") —
   *  the scan action shows only when this is non-empty. */
  scanSources: string[];
}

const str = (v: unknown): string => (typeof v === "string" ? v : "");

/** A quote is either a bare string or {quote|text, at|when, where, link}. */
export function decodeQuote(raw: unknown): PrefQuote | null {
  if (typeof raw === "string") {
    const q = raw.trim();
    return q ? { quote: q, at: "", where: "" } : null;
  }
  if (!raw || typeof raw !== "object") return null;
  const o = raw as Record<string, unknown>;
  const quote = (str(o.quote) || str(o.text)).trim();
  if (!quote) return null;
  const out: PrefQuote = {
    quote,
    at: str(o.at) || str(o.when),
    where: str(o.where),
  };
  const link = str(o.link);
  // Only an in-app path is a door: a quote is the user's text and its link
  // is daemon data, but nothing here should navigate off the dashboard.
  if (link && link.startsWith("/")) out.link = link;
  return out;
}

function quotesOf(raw: unknown, max: number): PrefQuote[] {
  if (!Array.isArray(raw)) return [];
  const out: PrefQuote[] = [];
  for (const q of raw) {
    const d = decodeQuote(q);
    if (d) out.push(d);
    if (out.length >= max) break;
  }
  return out;
}

/**
 * The done frame's `suggestion` (WHITELIST, like `remembered`): an object
 * with a string id and non-blank text survives; null, absent, or anything
 * malformed is null — the line renders nothing rather than a broken ask.
 */
export function decodeSuggestion(raw: unknown): ChatSuggestion | null {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  const o = raw as Record<string, unknown>;
  const id = typeof o.id === "string" || typeof o.id === "number" ? String(o.id) : "";
  const text = str(o.text).trim();
  if (!id || !text) return null;
  const count =
    typeof o.count === "number" && Number.isFinite(o.count) && o.count >= 2
      ? Math.floor(o.count)
      : 2;
  const out: ChatSuggestion = {
    id,
    text,
    count,
    quotes: quotesOf(o.quotes, 2),
    since: str(o.since),
  };
  // A persisted message carries the user's answer back through the same
  // decoder (thread reload), so the state is whitelisted here too.
  if (o.state === "kept" || o.state === "declined" || o.state === "gone") out.state = o.state;
  return out;
}

function decodeRow(raw: unknown, fallback: PrefRow["status"]): PrefRow | null {
  if (!raw || typeof raw !== "object") return null;
  const o = raw as Record<string, unknown>;
  const id = typeof o.id === "string" || typeof o.id === "number" ? String(o.id) : "";
  const text = str(o.text).trim();
  if (!id || !text) return null;
  const s = str(o.status);
  const status: PrefRow["status"] =
    s === "confirmed" || s === "proposed" || s === "declined" ? s : fallback;
  return {
    id,
    text,
    status,
    origin: str(o.origin),
    source: str(o.source),
    evidence: quotesOf(o.evidence ?? o.quotes, 6),
    created_at: str(o.created_at),
    decided_at: str(o.decided_at),
  };
}

function rowsOf(raw: unknown, status: PrefRow["status"]): PrefRow[] | null {
  if (!Array.isArray(raw)) return null;
  const out: PrefRow[] = [];
  for (const r of raw) {
    const d = decodeRow(r, status);
    if (d) out.push(d);
  }
  return out;
}

function sourcesOf(o: Record<string, unknown>): string[] {
  // Accept a plain list, {scan: {sources}} or [{id|name, found|available}].
  const raw =
    o.scan_sources ??
    (o.scan && typeof o.scan === "object" ? (o.scan as Record<string, unknown>).sources : undefined) ??
    o.sources;
  if (!Array.isArray(raw)) return [];
  const out: string[] = [];
  for (const s of raw) {
    let name = "";
    if (typeof s === "string") name = s;
    else if (s && typeof s === "object") {
      const so = s as Record<string, unknown>;
      const found = so.found ?? so.available ?? true;
      if (found === false) continue;
      name = str(so.id) || str(so.name) || str(so.source);
    }
    if ((name === "claude-code" || name === "codex") && !out.includes(name)) out.push(name);
  }
  return out;
}

/**
 * `GET /memory/preferences`, grouped. Returns null for anything that is not
 * that answer — an older daemon (404), a failed fetch handled by the caller,
 * or a mock that hands every path the same object — so the Memory page falls
 * back to exactly what it showed before this release.
 */
export function decodePreferences(raw: unknown): PreferencesView | null {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  const o = raw as Record<string, unknown>;
  const kept = rowsOf(o.confirmed ?? o.kept, "confirmed");
  const suggested = rowsOf(o.suggested ?? o.proposed, "proposed");
  const never = rowsOf(o.never ?? o.never_ask ?? o.declined, "declined");
  if (!kept || !suggested || !never) return null;
  return { kept, suggested, never, scanSources: sourcesOf(o) };
}

/** "twice", "three times", "7 times" — the count said like a person would. */
export function timesWord(n: number): string {
  if (n <= 1) return "once";
  if (n === 2) return "twice";
  if (n === 3) return "three times";
  if (n === 4) return "four times";
  return `${n} times`;
}

/** Where a quote was said, as the user would name it. */
export function whereWord(where: string): string {
  switch (where) {
    case "chat":
      return "chat";
    case "phone":
      return "phone";
    case "build":
      return "Build";
    case "claude-code":
      return "Claude Code";
    case "codex":
      return "Codex";
    default:
      return where;
  }
}

/** "chat, 2d ago" — the parts that are known, never a dash for a gap. */
export function quoteMeta(q: PrefQuote): string {
  const parts: string[] = [];
  const w = whereWord(q.where);
  if (w) parts.push(w);
  if (q.at) {
    const ago = timeAgo(q.at);
    if (ago && ago !== "—") parts.push(ago);
  }
  return parts.join(", ");
}

/** The proposal without its closing period, for quoting inside a sentence. */
export function quotable(text: string): string {
  return text.trim().replace(/[.。]+$/, "");
}

/** The in-chat question, in one sentence (the line styles the quote). */
export function suggestionAsk(s: Pick<ChatSuggestion, "text" | "count">): string {
  return `You've said this ${timesWord(s.count)}: “${quotable(s.text)}” — keep it as a standing preference?`;
}

/** The apps the scan reads, named for the button ("Claude Code and Codex"). */
export function scanAppsWord(sources: string[]): string {
  const names = sources.map(whereWord).filter(Boolean);
  if (names.length <= 1) return names[0] ?? "";
  return `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
}

/** The scan's result line: "Read 12 sessions — 2 suggestions". */
export function scanResultLine(raw: unknown): string | null {
  if (!raw || typeof raw !== "object") return null;
  const o = raw as Record<string, unknown>;
  const num = (...vs: unknown[]): number | null => {
    for (const v of vs) if (typeof v === "number" && Number.isFinite(v) && v >= 0) return Math.floor(v);
    return null;
  };
  const read = num(o.sessions_read, o.sessions, o.read);
  const made = num(o.suggestions, o.suggested, o.minted, o.proposed);
  if (read === null || made === null) return null;
  const line = `Read ${read} session${read === 1 ? "" : "s"} — ${made} suggestion${made === 1 ? "" : "s"}`;
  // The daemon keeps at most three suggestions open; when that cap stopped
  // it, say so — "0 suggestions" alone would read as "nothing found".
  return o.skipped_full === true ? `${line} (three are already waiting — answer those first)` : line;
}

/** The sentence the daemon kept, from a keep / edit answer — the routes
 *  answer `{preference: Pref}`; a bare `{text}` is read too. `fallback` when
 *  the answer says nothing usable (the words the user sent). */
export function keptTextFrom(res: unknown, fallback: string): string {
  if (res && typeof res === "object") {
    const o = res as Record<string, unknown>;
    const pref = o.preference && typeof o.preference === "object" ? (o.preference as Record<string, unknown>) : o;
    const t = pref.text;
    if (typeof t === "string" && t.trim()) return t.trim();
  }
  return fallback;
}

/** The thread-save hazard: a turn that started BEFORE the user answered a
 *  suggestion ends with a `history` that still holds the open ask, and its
 *  save would put the question back on disk. The page keeps every answer
 *  given this session in a map and runs each save through this. */
export function applySettled<M extends { suggestion?: ChatSuggestion }>(
  msgs: M[],
  settled: ReadonlyMap<string, ChatSuggestion>,
): M[] {
  if (settled.size === 0) return msgs;
  let changed = false;
  const out = msgs.map((m) => {
    const s = m.suggestion;
    if (!s) return m;
    const done = settled.get(s.id);
    if (!done || (done.state === s.state && done.text === s.text)) return m;
    changed = true;
    return { ...m, suggestion: done };
  });
  return changed ? out : msgs;
}

/**
 * The question was answered somewhere else (the Memory page, another window):
 * keep/decline on the old line got 409 (no longer a suggestion) or 404 (asked
 * again or forgotten). Given `GET /memory/preferences`, the line's REAL
 * outcome — kept (with the words actually kept), declined, or gone (the row
 * is nowhere). Null when the answer is unreadable or the row is STILL a
 * suggestion: then the refusal was something else and the line says it.
 */
export function settledElsewhere(s: ChatSuggestion, raw: unknown): ChatSuggestion | null {
  const view = decodePreferences(raw);
  if (!view) return null;
  if (view.suggested.some((r) => r.id === s.id)) return null;
  const kept = view.kept.find((r) => r.id === s.id);
  if (kept) return { ...s, state: "kept", text: kept.text };
  if (view.never.some((r) => r.id === s.id)) return { ...s, state: "declined" };
  return { ...s, state: "gone" };
}
