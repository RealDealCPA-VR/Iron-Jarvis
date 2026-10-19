/**
 * A TASK ANOTHER JARVIS SENT, in plain words (v1.357.0, product readiness
 * G61 + G76).
 *
 * The daemon's task door (`src/iron_jarvis/agents/task_door.py`) stamps a
 * task that another computer running Iron Jarvis sent with the origin
 * `remote:<caller>` (`REMOTE_TASK_ORIGIN` + the caller's cleaned name, or
 * `DEFAULT_CALLER` "another computer" when it named none). Every surface that
 * reads an origin says it the same way, keyed on the PREFIX (the caller is a
 * name the user gave, never a word to match):
 *
 * - the origin chip, the Sessions filter, Running now, Time travel
 *   (`OriginChip.originLabel`): "From desk";
 * - the bell's row for a finished one (`NotificationBell` unattended doors)
 *   and an ask's subject (`approvalWords.runSubject`): "A task from desk".
 *
 * Dependency-free on purpose: `lib/approvalWords` and the bell import it, and
 * ~70 tests mock `lib/api` wholesale.
 */

/** The origin kind (the part before the first ":"). */
export const REMOTE_KIND = "remote";

/** The caller's name when the origin names nobody (the daemon's
 *  `task_door.DEFAULT_CALLER`). */
export const REMOTE_DEFAULT_CALLER = "another computer";

/** True for a `remote:<caller>` origin (or the bare kind). */
export function isRemoteTaskOrigin(origin: string | null | undefined): boolean {
  const value = (origin || "").trim();
  return value === REMOTE_KIND || value.startsWith(`${REMOTE_KIND}:`);
}

/** The caller's name in a `remote:<caller>` origin; "" or a bare kind is the
 *  default caller. */
export function remoteCaller(origin: string | null | undefined): string {
  const value = (origin || "").trim();
  const i = value.indexOf(":");
  const name = i >= 0 ? value.slice(i + 1).trim() : "";
  return name || REMOTE_DEFAULT_CALLER;
}

/** The chip's words: "From desk". */
export function remoteOriginLabel(origin: string | null | undefined): string {
  return `From ${remoteCaller(origin)}`;
}

/** The subject of a sentence about one such task: "A task from desk"
 *  (`name` is the origin's suffix, as the bell's door table receives it). */
export function remoteTaskSubject(name: string | null | undefined): string {
  const who = (name || "").trim() || REMOTE_DEFAULT_CALLER;
  return `A task from ${who}`;
}
