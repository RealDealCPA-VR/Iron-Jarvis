// THE BELL REMEMBERS WHAT HAPPENED WHILE THE WINDOW WAS CLOSED (v1.351.0, G04).
//
// Bell rows used to come only from the live /events socket, and a first
// connect replays nothing; closing to the TRAY destroys the renderer, so
// "Your objective is done", "Your schedule … could not finish" or "Pack X is
// failing" were gone when the window came back — although the daemon
// persists every event (EventRecord) and GET /audit already reads them.
//
// So on its first mount the bell reads GET /audit for the types it maps,
// newer than the moment it was last OPENED (`ij.bell.lastSeen`, written when
// the dropdown opens), and runs each entry through the same `toActivity` the
// live rows use. No new store, no new route — one read.
//
// Dependency-free on purpose: ~71 test files mock `@/lib/api` wholesale, and
// a helper living there would vanish under them. The GET itself stays in the
// component.

import type { IJEvent } from "./types";

/** When the bell's dropdown was last opened on this machine (ISO, UTC). */
export const BELL_LAST_SEEN_KEY = "ij.bell.lastSeen";

/** With no `lastSeen` yet (the bell has never been opened here), how far back
 *  the first read looks: one day. Enough to tell a first-week user what
 *  finished overnight; never a months-long backlog. */
export const AWAY_FALLBACK_MS = 24 * 60 * 60 * 1000;

/** Entries one read asks for (the daemon caps a page at 500). */
export const AWAY_LIMIT = 50;

/** The stored moment the bell was last opened, or null (never opened, a
 *  storage error, or junk in the key). */
export function readBellLastSeen(): string | null {
  try {
    const raw = window.localStorage.getItem(BELL_LAST_SEEN_KEY);
    if (!raw || Number.isNaN(Date.parse(raw))) return null;
    return raw;
  } catch {
    return null;
  }
}

/** Record that the user looked at the bell now (or at `at`). Never throws. */
export function writeBellLastSeen(at: Date = new Date()): void {
  try {
    window.localStorage.setItem(BELL_LAST_SEEN_KEY, at.toISOString());
  } catch {
    /* storage refused (private mode, quota) — the next mount re-reads */
  }
}

/** The `since` of the replay read: the last time the bell was opened, else
 *  `AWAY_FALLBACK_MS` ago. */
export function awaySince(now: number = Date.now()): string {
  return readBellLastSeen() ?? new Date(now - AWAY_FALLBACK_MS).toISOString();
}

/** The one replay read: every bell type in ONE comma-separated `type` list. */
export function awayPath(
  types: readonly string[],
  since: string,
  limit: number = AWAY_LIMIT,
): string {
  const q = new URLSearchParams({ type: types.join(","), since, limit: String(limit) });
  return `/audit?${q.toString()}`;
}

/** An audit timestamp is NAIVE UTC (SQLite drops the zone); a live event's
 *  carries "+00:00". Read both as UTC — `new Date("…T07:00:00")` would
 *  otherwise be local time and every replayed row would show the wrong clock. */
export function utcStamp(ts: string): string {
  const s = (ts || "").trim();
  if (!s) return s;
  return /(?:[zZ]|[+-]\d{2}:?\d{2})$/.test(s) ? s : `${s}Z`;
}

/** Audit entries → the live event shape `toActivity` reads ({id, ts, type,
 *  session_id, payload}), newest first as the daemon sent them. An entry with
 *  no `type` (an older daemon), a type outside `types`, no id, or a repeated
 *  id is dropped — never guessed. */
export function auditEntriesToEvents(entries: unknown, types: readonly string[]): IJEvent[] {
  if (!Array.isArray(entries)) return [];
  const allowed = new Set(types);
  const seen = new Set<string>();
  const out: IJEvent[] = [];
  for (const raw of entries) {
    if (!raw || typeof raw !== "object") continue;
    const r = raw as Record<string, unknown>;
    const id = typeof r.id === "string" ? r.id : "";
    const type = typeof r.type === "string" ? r.type : "";
    if (!id || !type || !allowed.has(type) || seen.has(id)) continue;
    seen.add(id);
    const payload =
      r.payload && typeof r.payload === "object" && !Array.isArray(r.payload)
        ? (r.payload as Record<string, unknown>)
        : {};
    out.push({
      id,
      type,
      ts: utcStamp(typeof r.ts === "string" ? r.ts : ""),
      session_id: typeof r.session_id === "string" && r.session_id ? r.session_id : null,
      payload,
    });
  }
  return out;
}
