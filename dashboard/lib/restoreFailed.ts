/**
 * v1.418.1 — terminals from before a restart that could not be reopened.
 *
 * THE USER BUG: Build › Terminal said "Reopening 3 terminals…" for more than
 * four hours; the three shells never started and nothing ever gave up. The
 * daemon now gives up on a restore after a minute (`RESTORE_GIVE_UP_S`) and
 * `GET /terminals` says so as `restore_failed: {count, ids, reason}` — absent
 * when nothing failed. The saved terminals are KEPT, so the next start tries
 * them again; a shell that starts late still opens and the note goes away.
 *
 * Import-free on purpose (the page and the rail both read it).
 */

export type RestoreFailed = {
  count: number;
  ids: string[];
  /** Plain words from the daemon, e.g. "their shell didn't start". */
  reason: string;
};

/** The daemon's `restore_failed`, or null when absent or malformed. */
export function decodeRestoreFailed(raw: unknown): RestoreFailed | null {
  if (!raw || typeof raw !== "object") return null;
  const r = raw as Record<string, unknown>;
  const ids = Array.isArray(r.ids)
    ? r.ids.filter((id): id is string => typeof id === "string" && id.length > 0)
    : [];
  const count =
    typeof r.count === "number" && Number.isFinite(r.count) && r.count > 0
      ? Math.floor(r.count)
      : ids.length;
  if (count <= 0) return null;
  const reason =
    typeof r.reason === "string" && r.reason.trim() && r.reason.length <= 160
      ? r.reason.trim().replace(/\.$/, "")
      : count === 1
        ? "its shell didn't start"
        : "their shell didn't start";
  return { count, ids, reason };
}

/** "3 terminals from before the restart couldn't be reopened: their shell
 *  didn't start. Open a new terminal, or restart Iron Jarvis to try again." */
export function restoreFailedWords(note: RestoreFailed): string {
  const n = note.count;
  return (
    `${n} ${n === 1 ? "terminal" : "terminals"} from before the restart couldn't be reopened: ` +
    `${note.reason}. Open a new terminal, or restart Iron Jarvis to try again.`
  );
}
