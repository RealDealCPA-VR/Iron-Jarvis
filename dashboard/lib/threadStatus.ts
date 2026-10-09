// Chat list status (v1.327.0, calm chat B2): is a saved chat RUNNING, WAITING
// on the user, or UNREAD, and how long ago did it last change?
//
// RUNNING and WAITING come from the daemon: every `GET /chat/threads` row
// carries `running` (a turn is in flight for that chat) and `waiting` (that
// turn is parked on an approval card or an app's question). An older daemon
// sends neither, and both read as false.
//
// UNREAD is this browser's own memory: a chat whose `updated_at` is newer than
// the last time it was opened here. The stamps live under ONE localStorage key
// (`ij.chat.lastViewed` = {v, since, seen: {[threadId]: ms}}), capped at the
// MAX_VIEWED most recent. `since` is written the first time the store is read,
// so on a fresh browser nothing already saved reads as unread — only what
// changes after that. Every storage touch is in try/catch: private mode, a
// full quota, blocked site data or a corrupt value all degrade to "nothing is
// unread", and nothing here throws.
//
// WIRING NOTES (for the page that mounts ThreadGroups):
// - call `markViewed(id, thread.updated_at)` when a chat is opened AND when it
//   is left (your own messages move `updated_at` while you are in it);
// - pass the open chat as `activeId` to `threadStatuses`, so the chat on screen
//   never reads unread.

import { normalizeIso } from "./format";

export type ThreadStatus = "running" | "waiting" | "unread" | "idle";

/** The fields of a `GET /chat/threads` row this module reads. */
export interface ThreadSummary {
  id: string;
  title?: string | null;
  project_id?: string | null;
  updated_at?: string | null;
  running?: boolean;
  waiting?: boolean;
}

export interface ViewedStore {
  /** When this browser started keeping stamps (ms). Never-opened chats are
   *  compared against it. */
  since: number;
  /** thread id -> when it was last opened here (ms). */
  seen: Record<string, number>;
}

export const LAST_VIEWED_KEY = "ij.chat.lastViewed";
export const MAX_VIEWED = 300;

function storage(): Storage | null {
  try {
    return typeof window !== "undefined" ? window.localStorage : null;
  } catch {
    return null;
  }
}

function finiteNumber(v: unknown): v is number {
  return typeof v === "number" && Number.isFinite(v);
}

/** The daemon's timestamp in ms (its naive UTC strings are read as UTC), or
 *  null when missing or unreadable. */
export function threadTime(iso: string | null | undefined): number | null {
  if (!iso || typeof iso !== "string") return null;
  const t = new Date(normalizeIso(iso)).getTime();
  return Number.isNaN(t) ? null : t;
}

function parseStore(raw: string | null): ViewedStore | null {
  if (!raw) return null;
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return null;
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return null;
  const p = parsed as { since?: unknown; seen?: unknown };
  if (!finiteNumber(p.since)) return null;
  const seen: Record<string, number> = {};
  if (p.seen && typeof p.seen === "object" && !Array.isArray(p.seen)) {
    for (const [k, v] of Object.entries(p.seen as Record<string, unknown>)) {
      if (k && finiteNumber(v)) seen[k] = v;
    }
  }
  return { since: p.since, seen };
}

function writeStore(store: ViewedStore): void {
  const ls = storage();
  if (!ls) return;
  try {
    ls.setItem(LAST_VIEWED_KEY, JSON.stringify({ v: 1, since: store.since, seen: store.seen }));
  } catch {
    /* full quota / blocked: the stamps are a convenience */
  }
}

/** The stamps this browser keeps. The first read on a fresh browser starts the
 *  clock (`since` = now) and saves it; storage that cannot be read answers a
 *  store dated now, so nothing reads unread. */
export function readLastViewed(now: number = Date.now()): ViewedStore {
  const ls = storage();
  if (!ls) return { since: now, seen: {} };
  let raw: string | null = null;
  try {
    raw = ls.getItem(LAST_VIEWED_KEY);
  } catch {
    return { since: now, seen: {} };
  }
  const store = parseStore(raw);
  if (store) return store;
  const fresh: ViewedStore = { since: now, seen: {} };
  writeStore(fresh);
  return fresh;
}

/** Record that chat `id` was opened. The stamp is the later of now and the
 *  chat's own `updatedAt`, so a save that landed just before cannot make the
 *  chat on screen read unread. Keeps the MAX_VIEWED most recent stamps. */
export function markViewed(
  id: string,
  updatedAt?: string | null,
  now: number = Date.now(),
): void {
  if (!id) return;
  const store = readLastViewed(now);
  const changed = threadTime(updatedAt) ?? 0;
  store.seen[id] = Math.max(now, changed, store.seen[id] ?? 0);
  const ids = Object.keys(store.seen);
  if (ids.length > MAX_VIEWED) {
    ids
      .sort((a, b) => store.seen[b] - store.seen[a])
      .slice(MAX_VIEWED)
      .forEach((old) => delete store.seen[old]);
  }
  writeStore(store);
}

/** True when the chat changed after it was last opened here (or, never
 *  opened, after this browser started keeping stamps). */
export function isUnread(thread: ThreadSummary, store: ViewedStore): boolean {
  const changed = threadTime(thread.updated_at);
  if (changed == null) return false;
  const seen = store.seen[thread.id] ?? store.since;
  return changed > seen;
}

/** ONE status per chat, most urgent first: waiting on you, running, unread,
 *  idle. The chat on screen (`activeId`) is never unread. */
export function threadStatus(
  thread: ThreadSummary,
  store: ViewedStore,
  activeId?: string | null,
): ThreadStatus {
  if (thread.waiting === true) return "waiting";
  if (thread.running === true) return "running";
  if (thread.id !== activeId && isUnread(thread, store)) return "unread";
  return "idle";
}

/** `threadStatus` for a whole list, keyed by id (the ThreadGroups prop). */
export function threadStatuses(
  threads: ThreadSummary[],
  store: ViewedStore = readLastViewed(),
  activeId?: string | null,
): Record<string, ThreadStatus> {
  const out: Record<string, ThreadStatus> = {};
  for (const t of threads) out[t.id] = threadStatus(t, store, activeId);
  return out;
}

/** A short age for the list: "now" under a minute (and for a clock that runs
 *  ahead), then "12m", "3h", "2d". "" when the time cannot be read. */
export function formatAge(iso: string | null | undefined, now: number = Date.now()): string {
  const t = threadTime(iso);
  if (t == null) return "";
  const s = Math.floor((now - t) / 1000);
  if (s < 60) return "now";
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h`;
  return `${Math.floor(h / 24)}d`;
}

/** Words for a status, for screen readers and tooltips ("" for idle). */
export function statusWords(status: ThreadStatus): string {
  if (status === "running") return "Working on it";
  if (status === "waiting") return "Waiting on you";
  if (status === "unread") return "New since you last looked";
  return "";
}
