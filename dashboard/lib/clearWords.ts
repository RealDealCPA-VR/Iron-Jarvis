/**
 * The words of the "Clear" presses on Agent runs and the run board
 * (v1.504.0, north-star plan 4.2, D7).
 *
 * `POST /sessions/clear` used to delete each run's record for good and the
 * folder holding the files it wrote, while the button said "Remove every
 * completed session from the board". It now MOVES each run to the app's
 * trash: the files it wrote, and its record (kept there 30 days). These
 * sentences say exactly that, and where the trash is.
 */

/** How long a cleared run's record is kept in the trash (the daemon's
 *  `maintenance.CLEARED_RECORD_DAYS`). */
export const CLEARED_RECORD_DAYS = 30;

const WHAT: Record<string, string> = {
  completed: "completed",
  failed: "failed",
  stopped: "stopped",
  finished: "finished, failed or stopped",
};

/** The hover text of a Clear press, before it is pressed. */
export function clearTitle(kind: keyof typeof WHAT | string): string {
  const what = WHAT[kind] ?? kind;
  return (
    `Moves every ${what} run to the app's trash: the files it wrote, and its record ` +
    `(kept there ${CLEARED_RECORD_DAYS} days). Running and waiting runs stay. ` +
    `Your own folders are never moved.`
  );
}

/** What the page says after a Clear press, from the daemon's own answer. */
export function clearedWords(cleared: number, trash?: string, kind = ""): string {
  const what = kind ? `${WHAT[kind] ?? kind} ` : "";
  if (!cleared) return `There were no ${what}runs to move.`;
  const runs = `${cleared} ${what}run${cleared === 1 ? "" : "s"}`;
  const where = trash ? ` (${trash})` : "";
  return (
    `Moved ${runs} to the app's trash${where}. ` +
    `${cleared === 1 ? "Its" : "Their"} files stay there until you delete them; ` +
    `${cleared === 1 ? "its record is" : "the records are"} kept for ${CLEARED_RECORD_DAYS} days.`
  );
}
