/**
 * What a chat reply changed on disk, asked for ONCE per reply (v1.328.0,
 * calm chat W3-2).
 *
 * A reply whose turn wrote or edited files carries `documents` (the absolute
 * paths the turn reported) and its timing (the stream lane's `timing`, else
 * the user's question time and the reply's settle time). `turnWindow` turns
 * that into the `POST /chat/changes` window, or null for a reply that wrote
 * nothing or has no start to ask from, so such a reply never fetches.
 *
 * The answer is kept in a bounded map keyed by the window (start, end, files),
 * which is the same for a reply live and for the same reply reopened from the
 * saved chat. In-flight asks are joined, so two mounts of one reply make one
 * request. A failed ask is dropped from the map so a later mount (reopening
 * the chat) may ask again; it never retries on its own.
 *
 * Its own module (not the chat page) because tests that mock
 * `@/lib/useChatStream` have fixed export lists, and so it can be tested
 * alone.
 */

import { fetchTurnChangeSet, type TurnChangeSet, type TurnChangesArgs } from "@/components/chat/ChangedFiles";

/** The reply fields this reads (a structural subset of the page's message). */
export interface TurnChangeSource {
  documents?: string[];
  timing?: { startedAt: number; endedAt: number } | null;
  /** The reply's settle time (ISO). */
  at?: string;
}

/** Entries kept. A long session reads far fewer replies than this. */
export const TURN_CHANGES_CACHE_MAX = 200;

function stamp(iso: string | undefined): string | undefined {
  if (!iso) return undefined;
  return Number.isNaN(new Date(iso).getTime()) ? undefined : iso;
}

/** The window to ask about, or null when the reply wrote nothing or there is
 *  no start time to ask from (then nothing is fetched). `prevUserAt` is the
 *  question's send time, used when the reply has no `timing` (the POST lane
 *  and older replies). */
export function turnWindow(m: TurnChangeSource, prevUserAt?: string): TurnChangesArgs | null {
  const paths = Array.from(
    new Set((m.documents ?? []).filter((p): p is string => typeof p === "string" && p.trim() !== "")),
  );
  if (paths.length === 0) return null;
  const t = m.timing;
  if (t && Number.isFinite(t.startedAt) && t.startedAt > 0) {
    return {
      since: t.startedAt,
      ...(Number.isFinite(t.endedAt) && t.endedAt >= t.startedAt ? { until: t.endedAt } : {}),
      paths,
    };
  }
  const since = stamp(prevUserAt);
  if (!since) return null;
  const until = stamp(m.at);
  return { since, ...(until ? { until } : {}), paths };
}

/** The cache key: the same for a reply live and reopened. */
export function turnChangesKey(w: TurnChangesArgs): string {
  return [String(w.since), w.until === undefined ? "" : String(w.until), ...[...(w.paths ?? [])].sort()].join("\n");
}

const done = new Map<string, TurnChangeSet>();
const pending = new Map<string, Promise<TurnChangeSet>>();

function remember(key: string, value: TurnChangeSet) {
  done.delete(key);
  done.set(key, value);
  while (done.size > TURN_CHANGES_CACHE_MAX) {
    const oldest = done.keys().next().value;
    if (oldest === undefined) break;
    done.delete(oldest);
  }
}

/** The answer already held for this window, if any (no request). */
export function peekTurnChanges(w: TurnChangesArgs): TurnChangeSet | undefined {
  return done.get(turnChangesKey(w));
}

/** The turn's changes: from the map, or ONE request shared by every caller. */
export function loadTurnChanges(w: TurnChangesArgs): Promise<TurnChangeSet> {
  const key = turnChangesKey(w);
  const held = done.get(key);
  if (held) return Promise.resolve(held);
  const inFlight = pending.get(key);
  if (inFlight) return inFlight;
  const p = fetchTurnChangeSet(w)
    .then((set) => {
      remember(key, set);
      return set;
    })
    .finally(() => {
      pending.delete(key);
    });
  pending.set(key, p);
  return p;
}

/** Drop what is held for this window (after an Undo, so it is read again). */
export function forgetTurnChanges(w: TurnChangesArgs): void {
  done.delete(turnChangesKey(w));
}

/** Tests only: start from an empty map. */
export function resetTurnChanges(): void {
  done.clear();
  pending.clear();
}
