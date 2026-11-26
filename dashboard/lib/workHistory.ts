/**
 * Next leaps 13-b (build-out plan R2.10, idea 13 "Ask the record"): the rows
 * under a reply that looked up earlier work, drawn from the `work_history`
 * step's OWN result (the finished step frame's `data.rows`, from the daemon's
 * `workmap.tools.screen_rows`), never from the model's words: the
 * `SourcesRow` rule. A reply written home while no window was open carries
 * the same rows (`durable_turns.reply_line` → the message's `workRows`).
 *
 * Decoders keep only what the row shows, typed; an address outside the app
 * is dropped. The rows are saved with the chat, so they are read as
 * untrusted input every time (live, saved, written home). No imports from
 * lib/api (many tests mock that module whole).
 */

import { insideLink } from "./workRecord";

/** The tool the rows come from. */
export const WORK_HISTORY_TOOL = "work_history";
/** Rows one reply carries, at most (the daemon's `LIMIT_MAX`). */
export const WORK_ROWS_MAX = 20;
/** Files one row carries, at most (the daemon's `SCREEN_FILES_MAX`). */
export const WORK_ROW_FILES_MAX = 8;

/** The quiet label in front of the rows, and the two quiet presses. */
export const WORK_ROWS_LABEL = "Earlier work";
export const WORK_ROW_FILES = "Files";
export const WORK_ROW_DECISIONS = "Decisions";

/** The status words the daemon says (`workmap.tools.status_of`). */
export const WORK_STATUS_WORDS = ["finished", "failed", "stopped", "needs you", "part failed", "still open"] as const;

export interface WorkHistoryRowData {
  id: string;
  kind: string;
  title: string;
  /** ISO time it was last worked on ("" when not known). */
  when: string;
  /** One of WORK_STATUS_WORDS, or "" (a chat says nothing). */
  status: string;
  /** How many Open lines its checked recap has. */
  open: number;
  /** Where pressing the row goes: the chat, run or coding session ("" = nowhere). */
  link: string;
  /** File names, and their full paths for the file panel (same order). */
  files: string[];
  paths: string[];
  filesTotal: number;
  /** Where its decisions are read: the client's page or the Memory page. */
  decisions: string;
  times: number;
}

function str(v: unknown, cap = 400): string {
  return typeof v === "string" ? v.trim().slice(0, cap) : "";
}

function num(v: unknown, fallback = 0): number {
  return typeof v === "number" && Number.isFinite(v) && v >= 0 ? Math.floor(v) : fallback;
}

const CONTROL = /[\u0000-\u001f\u007f]/;

/** A full path the file panel may open: absolute (a drive, a share or a
 *  rooted path), no control characters. "" for anything else. */
function fullPath(v: unknown): string {
  const s = str(v, 1000);
  if (!s || CONTROL.test(s)) return "";
  return /^(?:[A-Za-z]:[\\/]|\\\\|\/)/.test(s) ? s : "";
}

function link(v: unknown): string {
  const s = insideLink(str(v));
  return s && !CONTROL.test(s) ? s : "";
}

/** One row, or null when it is not one (no id or no title). */
export function decodeWorkRow(raw: unknown): WorkHistoryRowData | null {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  const o = raw as Record<string, unknown>;
  const id = str(o.id);
  const title = str(o.title, 200);
  if (!id || !title) return null;
  const status = str(o.status, 40);
  const files: string[] = [];
  const paths: string[] = [];
  const rawPaths = Array.isArray(o.paths) ? o.paths : [];
  for (const p of rawPaths.slice(0, WORK_ROW_FILES_MAX)) {
    const path = fullPath(p);
    if (!path) continue;
    paths.push(path);
    files.push(path.split(/[\\/]/).pop() || path);
  }
  // Names with no path (none carried, or a path left out) still say what it wrote.
  const names = Array.isArray(o.files) ? o.files : [];
  for (const n of names.slice(0, WORK_ROW_FILES_MAX)) {
    const name = str(n, 200);
    if (name && !CONTROL.test(name) && !files.includes(name) && files.length < WORK_ROW_FILES_MAX) {
      files.push(name);
    }
  }
  return {
    id,
    kind: str(o.kind, 20),
    title,
    when: str(o.when, 40),
    status: (WORK_STATUS_WORDS as readonly string[]).includes(status) ? status : "",
    open: num(o.open),
    link: link(o.link),
    files,
    paths,
    filesTotal: Math.max(num(o.files_total ?? o.filesTotal), files.length),
    decisions: link(o.decisions),
    times: Math.max(1, num(o.times, 1)),
  };
}

/** The rows of a message or a frame, decoded, one per piece, at most
 *  WORK_ROWS_MAX. [] for anything that is not a list of rows. */
export function decodeWorkRows(raw: unknown): WorkHistoryRowData[] {
  if (!Array.isArray(raw)) return [];
  const out: WorkHistoryRowData[] = [];
  const seen = new Set<string>();
  for (const r of raw) {
    if (out.length >= WORK_ROWS_MAX) break;
    const row = decodeWorkRow(r);
    if (!row || seen.has(row.id)) continue;
    seen.add(row.id);
    out.push(row);
  }
  return out;
}

/** The rows a turn's finished `work_history` steps carried (newest call
 *  last), merged once per piece. Only steps that ran (`ok !== false`). */
export function extractWorkRows(
  cards: readonly { name: string; status: string; ok?: boolean; workRows?: WorkHistoryRowData[] }[],
): WorkHistoryRowData[] {
  const out: WorkHistoryRowData[] = [];
  const seen = new Set<string>();
  for (const c of cards) {
    if (c.name !== WORK_HISTORY_TOOL || c.status !== "done" || c.ok === false) continue;
    for (const row of c.workRows ?? []) {
      if (out.length >= WORK_ROWS_MAX || seen.has(row.id)) continue;
      seen.add(row.id);
      out.push(row);
    }
  }
  return out;
}

/** "Mar 12, 2026" in this PC's zone (the daemon says the owner's day the
 *  same way); "" when not known. */
export function dayWords(iso: string): string {
  if (!iso) return "";
  const t = Date.parse(iso);
  if (!Number.isFinite(t)) return "";
  try {
    return new Date(t).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
  } catch {
    return "";
  }
}

/* -------------------------------------------- the Memory page's ?work=<id> */

/** `GET /work-record/piece?id=` → `{id, folder, name}`: where one piece of
 *  work is, so the Memory page opens its folder (the address names the
 *  piece by its id, never a folder). */
export const WORK_PIECE_PATH = "/work-record/piece";
export const PIECE_GONE_WORDS = "That piece of work is not on record any more.";
export const PIECE_UNREAD_WORDS = "That piece of work could not be found just now. Try again in a moment.";

export function workPiecePath(id: string): string {
  return `${WORK_PIECE_PATH}?id=${encodeURIComponent(id)}`;
}

/** The `work` the page's address asks for ("" for none). */
export function focusedWorkId(search: string): string {
  try {
    const id = new URLSearchParams(search).get("work") ?? "";
    return CONTROL.test(id) ? "" : id.trim().slice(0, 400);
  } catch {
    return "";
  }
}

/** The folder of `GET /work-record/piece`'s answer, or "" when it is not one. */
export function decodePiece(raw: unknown): string {
  if (!raw || typeof raw !== "object") return "";
  const folder = str((raw as Record<string, unknown>).folder, 1000);
  return folder && !CONTROL.test(folder) ? folder : "";
}

/** Two spellings of one folder compare equal (slashes, a trailing slash, case). */
export function sameFolder(a: string, b: string): boolean {
  const norm = (s: string) => s.replace(/\\/g, "/").replace(/\/+$/, "").toLowerCase();
  return Boolean(a) && Boolean(b) && norm(a) === norm(b);
}

/** The kind of work, in the record's words (lib/workRecord kindWords, lower case). */
export function rowKindWords(kind: string): string {
  if (kind === "chat") return "chat";
  if (kind === "run") return "agent run";
  if (kind === "code" || kind === "tool_code") return "coding session";
  if (kind === "outside") return "chat in another tool";
  return "work";
}
