/**
 * v1.453.0: archiving a Build › Code session, and the short Markdown summary
 * the daemon writes when it does (`hosted/archive_summary`): what project,
 * which tool and account, the folder's state before, what was done, and the
 * state after — from the session's own log and git, never a model.
 *
 * Import-free on purpose (the Code view and its tests read it).
 */

export const ARCHIVE_ROW = "Archive";
export const ARCHIVE_ROW_TITLE =
  "Archive this session: Iron Jarvis writes a short summary of it (the folder before and after, and what was done) and keeps it with the session.";
export const ARCHIVE_RUNNING_TITLE = "A reply is running: stop it or let it finish first.";
export const ARCHIVE_FAILED = "The session was not archived.";
export const SUMMARY_HEADING = "Archive summary";
export const SUMMARY_HEADING_TITLE = "The summary Iron Jarvis wrote when this session was archived, from its own log and git.";
export const SUMMARY_READING = "Reading the summary…";
export const SUMMARY_UNREADABLE = "The summary could not be read.";
export const SUMMARY_COPY = "Copy";
export const SUMMARY_COPIED = "Copied.";
export const SUMMARY_OPEN = "Open file";
export const SUMMARY_SHOW = "Show the archive summary";
export const BRING_BACK = "Bring back";
export const ARCHIVED_NOTE = "This session is archived. Bring it back to send it a message.";

/** Where the summary is kept (from a session view or an archive answer). */
export interface ArchiveSummaryFacts {
  path: string;
  at: string;
}

export interface ArchiveSummary extends ArchiveSummaryFacts {
  text: string;
}

function obj(raw: unknown): Record<string, unknown> | null {
  return raw && typeof raw === "object" && !Array.isArray(raw) ? (raw as Record<string, unknown>) : null;
}

/** `archive_summary` of a session view or an archive answer, or null. */
export function archiveSummaryFacts(raw: unknown): ArchiveSummaryFacts | null {
  const r = obj(obj(raw)?.archive_summary);
  if (!r) return null;
  const path = typeof r.path === "string" ? r.path : "";
  if (!path) return null;
  return { path, at: typeof r.at === "string" ? r.at : "" };
}

/** GET …/summary: `{text, path, at}`, or null. */
export function decodeArchiveSummary(raw: unknown): ArchiveSummary | null {
  const r = obj(raw);
  if (!r || typeof r.text !== "string" || typeof r.path !== "string") return null;
  return { text: r.text, path: r.path, at: typeof r.at === "string" ? r.at : "" };
}

/** The archive answer's own words when the summary could not be written
 *  (the session WAS archived), else "". */
export function archiveSummaryError(raw: unknown): string {
  const v = obj(raw)?.archive_summary_error;
  return typeof v === "string" ? v : "";
}
