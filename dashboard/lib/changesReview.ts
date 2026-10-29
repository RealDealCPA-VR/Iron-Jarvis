/**
 * v1.405.0 — the coding session's ONE SCROLLING REVIEW (components/harness/
 * ChangesReview): every changed file's diff inline, a Reviewed mark per file.
 *
 * The pure half:
 *  - `diffHash`: a short hash of one file's change (its status and diff text);
 *  - the REVIEWED marks, remembered per session in localStorage as
 *    `path + hash` pairs — so a file whose diff changes again (the session
 *    edited it once more, an Undo) is unreviewed by itself, while the
 *    session's and the branch's views of one file (two different diffs) keep
 *    their own marks;
 *  - `reviewSummary`: "3 files changed +42 −7 · 1 of 3 reviewed".
 *
 * localStorage can be missing or full: every read and write is wrapped, and
 * the pane works (with marks for this visit only) without it.
 */

import type { FileChange } from "@/components/chat/ChangedFiles";

/** The marks of one session are kept under this prefix + the session id. */
export const REVIEW_KEY_PREFIX = "ij.build.reviewed.";
/** At most this many marks per session (the oldest go first). */
export const MAX_MARKS = 1000;
/** Diff rows drawn per file before "Show all N lines". */
export const REVIEW_ROWS_PER_FILE = 400;
/** From this many files on, a file's rows are drawn only near the screen. */
export const LAZY_FROM_FILES = 8;

/** FNV-1a (32 bit) over the change's status and diff text, as 8 hex digits. */
export function diffHash(change: Pick<FileChange, "status" | "diff">): string {
  const text = `${change.status}\n${change.diff ?? ""}`;
  let h = 0x811c9dc5;
  for (let i = 0; i < text.length; i++) {
    h ^= text.charCodeAt(i);
    h = Math.imul(h, 0x01000193) >>> 0;
  }
  return h.toString(16).padStart(8, "0");
}

/** The mark of one file in its current state. */
export function markOf(change: Pick<FileChange, "path" | "status" | "diff">): string {
  return `${change.path}\u0000${diffHash(change)}`;
}

function storageKey(sessionId: string): string {
  return `${REVIEW_KEY_PREFIX}${sessionId}`;
}

/** The session's marks (oldest first); [] when none or unreadable. */
export function readMarks(sessionId: string): string[] {
  try {
    const raw = window.localStorage.getItem(storageKey(sessionId));
    if (!raw) return [];
    const v = JSON.parse(raw);
    return Array.isArray(v) ? v.filter((x): x is string => typeof x === "string").slice(-MAX_MARKS) : [];
  } catch {
    return [];
  }
}

/** Save the session's marks (the newest MAX_MARKS). */
export function writeMarks(sessionId: string, marks: readonly string[]): void {
  try {
    window.localStorage.setItem(storageKey(sessionId), JSON.stringify(marks.slice(-MAX_MARKS)));
  } catch {
    /* the marks last this visit */
  }
}

/** `marks` with `mark` set on or off (a set mark moves to the newest end). */
export function withMark(marks: readonly string[], mark: string, on: boolean): string[] {
  const rest = marks.filter((m) => m !== mark);
  return on ? [...rest, mark] : rest;
}

/** "1 file changed" / "3 files changed". */
function filesChanged(n: number): string {
  return `${n} ${n === 1 ? "file" : "files"} changed`;
}

/** The review's top line, in words: "3 files changed +42 −7 · 1 of 3 reviewed"
 *  (no counts when no file was compared — binary files only). */
export function reviewSummary(changes: readonly FileChange[], reviewed: number): string {
  let any = false;
  let added = 0;
  let removed = 0;
  for (const c of changes) {
    if (c.added != null || c.removed != null) any = true;
    added += c.added ?? 0;
    removed += c.removed ?? 0;
  }
  const counts = any ? ` +${added} −${removed}` : "";
  return `${filesChanged(changes.length)}${counts} · ${reviewed} of ${changes.length} reviewed`;
}

/** The one sentence the "This session" view says first: there is no Keep. */
export const REVIEW_HINT = "Changes are already saved; Undo takes one back.";
