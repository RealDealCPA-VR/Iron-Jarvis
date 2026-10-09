// Per-conversation composer drafts (v1.323.0). Switching chats used to lose
// whatever was half-typed; now each conversation keeps its own draft.
//
// ONE localStorage key, `ij.chat.drafts` = {[convKey]: {text, at}}, capped at
// the 50 most recently written drafts (least-recently-written goes first).
// Every storage touch is in try/catch: private mode, a full quota, blocked
// site data or a corrupt value all degrade to "no draft" — this never throws.
// What key an unsaved chat uses is the caller's business.

export const DRAFTS_KEY = "ij.chat.drafts";
export const MAX_DRAFTS = 50;

interface DraftEntry {
  text: string;
  at: number;
}

type DraftMap = Record<string, DraftEntry>;

function storage(): Storage | null {
  try {
    return typeof window !== "undefined" ? window.localStorage : null;
  } catch {
    return null;
  }
}

/** The stored map, keeping only well-formed entries. Corrupt → {}. */
function load(): DraftMap {
  const ls = storage();
  if (!ls) return {};
  let raw: string | null;
  try {
    raw = ls.getItem(DRAFTS_KEY);
  } catch {
    return {};
  }
  if (!raw) return {};
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return {};
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return {};
  const out: DraftMap = {};
  for (const [k, v] of Object.entries(parsed as Record<string, unknown>)) {
    const e = v as Partial<DraftEntry> | null;
    if (e && typeof e === "object" && typeof e.text === "string" && e.text.trim()) {
      out[k] = { text: e.text, at: typeof e.at === "number" && Number.isFinite(e.at) ? e.at : 0 };
    }
  }
  return out;
}

function save(map: DraftMap): void {
  const ls = storage();
  if (!ls) return;
  try {
    if (Object.keys(map).length === 0) ls.removeItem(DRAFTS_KEY);
    else ls.setItem(DRAFTS_KEY, JSON.stringify(map));
  } catch {
    /* quota / blocked storage — the draft is a convenience, not a record */
  }
}

/** The draft for `key`, or "" when there is none. */
export function readDraft(key: string): string {
  if (!key) return "";
  return load()[key]?.text ?? "";
}

/** Keep `text` as the draft for `key`; empty/whitespace text deletes it.
 *  Past {@link MAX_DRAFTS}, the least recently written drafts are dropped. */
export function writeDraft(key: string, text: string): void {
  if (!key) return;
  const map = load();
  if (typeof text !== "string" || !text.trim()) {
    if (!(key in map)) return;
    delete map[key];
    save(map);
    return;
  }
  // Monotonic: two writes in the same millisecond still order correctly.
  const newest = Object.values(map).reduce((m, e) => Math.max(m, e.at), 0);
  map[key] = { text, at: Math.max(Date.now(), newest + 1) };
  const keys = Object.keys(map);
  if (keys.length > MAX_DRAFTS) {
    keys
      .sort((a, b) => map[a].at - map[b].at)
      .slice(0, keys.length - MAX_DRAFTS)
      .forEach((k) => delete map[k]);
  }
  save(map);
}

/** Forget the draft for `key` (after a send). */
export function clearDraft(key: string): void {
  writeDraft(key, "");
}
