/**
 * v1.544.0 — COMMENTS ON CHANGED CODE that ride the next message (Feature 3
 * of docs/BUILD-CODE-T3-PLAN-2026-11-19.md; the daemon's half is
 * `harness/code_comments.py`).
 *
 * In a coding session's Changes view the user presses Comment on a changed
 * block and types what they want. The comment WAITS as a chip above the
 * message box and is sent with the next message as a structured `comments`
 * field: `[{path, side, start, end, quote, words}]`. Both Code views use this
 * (Build › Code and Chat › Code draw the same Changes component).
 *
 * The pure half, and a tiny store:
 *  - the limits (the daemon enforces them; the screen never sends more);
 *  - what a block's comment quotes and which lines it names (`hunkComment`);
 *  - the whitelist for comments the daemon hands back (a sent message's
 *    event, a waiting message, a rewind's answer): `decodeComments`;
 *  - the WAITING comments of one session, remembered in localStorage the way
 *    the Reviewed marks are (lib/changesReview), behind subscribe/get so two
 *    separate React trees share them through `useSyncExternalStore`: the
 *    Changes view adds, the message box shows and sends. The Changes rows
 *    are memoised and get no prop from this store.
 *
 * The store's key is the SAME string the Changes component gets as its
 * `sessionId`: `code:<id>` in Build › Code (`buildCodeCommentsKey`), the
 * session's own id in Chat › Code.
 *
 * localStorage can be missing or full: every read and write is wrapped, and
 * the comments then last this visit only.
 *
 * Import-free (one type).
 */

import type { DiffRow } from "@/components/chat/DiffView";

/** How many comments one message carries (the daemon's MAX_COMMENTS). */
export const MAX_COMMENTS = 20;
/** How much quoted code one comment carries. */
export const QUOTE_CHARS = 4_000;
/** How many characters of the user's own words one comment carries. */
export const WORDS_CHARS = 2_000;
export const PATH_CHARS = 1_000;
/** The most comments one session keeps waiting (a refused message's comments
 *  go back beside the ones added since; a message still takes MAX_COMMENTS). */
export const MAX_WAITING = MAX_COMMENTS * 2;
/** A session's waiting comments are kept under this prefix + the store key. */
export const COMMENTS_KEY_PREFIX = "ij.build.comments.";

// The words (one place; "Note" already means a message sent while a reply runs).
export const COMMENT = "Comment";
export const COMMENT_TITLE = "Say what you want changed here. It goes with your next message.";
export const COMMENT_DIALOG_TITLE = "Comment on these lines";
export const COMMENT_WORDS_LABEL = "What do you want changed?";
export const COMMENT_SAVE = "Save";
export const COMMENT_FOOT = "It waits above the message box and goes with your next message.";
export const COMMENT_CHANGED_SINCE = "changed since";
export const COMMENT_CHANGED_SINCE_TITLE = "This file changed after you wrote the comment, so the lines may have moved.";
export const COMMENT_NEEDS_WORDS = "Say what you want changed first.";
export const COMMENTS_FULL = `A message carries at most ${MAX_COMMENTS} comments. Send some first, then add the rest.`;
export const COMMENT_CHIPS_LABEL = "Comments that go with your next message";
export const COMMENT_LINE_TITLE =
  "Comment on this line. Then hold Shift and press another line to comment on the lines between.";

export type CommentSide = "new" | "old";

/** One comment as the daemon takes it (and hands it back). */
export interface CommentBody {
  /** The file: its absolute path, or its path inside the session's folder. */
  path: string;
  side: CommentSide;
  /** The first and last line it is about (0 = not known). */
  start: number;
  end: number;
  /** The lines as the Changes view drew them, with their + / - / space marks. */
  quote: string;
  /** What the user wants. */
  words: string;
}

/** One WAITING comment: the body, plus what only this screen needs. */
export interface CodeComment extends CommentBody {
  id: string;
  /** The file's change mark (lib/changesReview `markOf`) when the comment was
   *  written; "" = not known (a comment the daemon handed back). */
  mark: string;
  /** Which Changes view it was written in (the same file has another diff,
   *  and another mark, in another view); "" = not said. */
  view: string;
  /** The file changed again since ("changed since" on its chip). */
  stale: boolean;
}

/** The store key of a Build › Code session: what CodePanes hands the Changes
 *  component as its `sessionId` (the Reviewed marks use the same string). */
export function buildCodeCommentsKey(sessionId: string): string {
  return `code:${sessionId}`;
}

// ── a block's comment ───────────────────────────────────────────────────────

/** The rows of the file's `index`-th hunk (0 = the first), without its header
 *  row. By POSITION, never by the header's text: a diff that joins several
 *  edits of one file can hold two blocks with the same header. */
export function rowsOfHunkAt(rows: readonly DiffRow[], index: number): DiffRow[] {
  let seen = -1;
  let at = -1;
  for (let i = 0; i < rows.length; i += 1) {
    if (rows[i].kind !== "hunk") continue;
    seen += 1;
    if (seen === index) {
      at = i;
      break;
    }
  }
  if (at < 0) return [];
  const out: DiffRow[] = [];
  for (let i = at + 1; i < rows.length && rows[i].kind !== "hunk"; i += 1) out.push(rows[i]);
  return out;
}

/** What a quote holds of the lines it is about. */
export interface QuotedLines {
  quote: string;
  /** How many lines the quote holds, and how many there were. */
  lines: number;
  total: number;
  /** The one line quoted is itself longer than a quote: only its start. */
  partial: boolean;
}

/** The rows' own lines with their + / - / space marks: WHOLE lines, as many
 *  as fit in QUOTE_CHARS (a quote never ends in the middle of a line). A
 *  first line longer than that by itself is cut (`partial`). */
export function quoteLines(rows: readonly DiffRow[]): QuotedLines {
  const all = rows
    .filter((r) => r.kind !== "hunk")
    .map((r) => `${r.kind === "added" ? "+" : r.kind === "removed" ? "-" : " "}${r.text}`);
  const kept: string[] = [];
  let size = 0;
  for (const line of all) {
    const next = size + line.length + (kept.length ? 1 : 0);
    if (next > QUOTE_CHARS) break;
    kept.push(line);
    size = next;
  }
  if (!kept.length && all.length) {
    return { quote: all[0].slice(0, QUOTE_CHARS), lines: 1, total: all.length, partial: true };
  }
  return { quote: kept.join("\n"), lines: kept.length, total: all.length, partial: false };
}

/** The hunk's own lines with their marks, whole lines within QUOTE_CHARS. */
export function quoteOfHunk(rows: readonly DiffRow[]): string {
  return quoteLines(rows).quote;
}

/** What the dialog says under a quote that does not hold every line ("" when
 *  it holds them all). */
export function quoteCutWords(q: Pick<QuotedLines, "lines" | "total" | "partial">): string {
  if (q.partial) return q.total > 1 ? "Only the start of the first line is quoted." : "Only the start of the line is quoted.";
  if (q.lines >= q.total) return "";
  return q.lines === 1 ? "Only the first line is quoted." : `Only the first ${q.lines} lines are quoted.`;
}

/** The first and last line numbers the hunk touches on `side` (0, 0 when it
 *  has none there: a block of removals only has no new line). */
export function rangeOfHunk(rows: readonly DiffRow[], side: CommentSide): { start: number; end: number } {
  let start = 0;
  let end = 0;
  for (const r of rows) {
    const n = side === "old" ? r.oldNo : r.newNo;
    if (typeof n !== "number" || n < 1) continue;
    if (!start || n < start) start = n;
    if (n > end) end = n;
  }
  return { start, end };
}

/** What a comment holds before the user's words, and what the dialog says
 *  when its quote was cut ("" = every line is quoted). */
export interface BlockComment extends Pick<CommentBody, "side" | "start" | "end" | "quote"> {
  cut: string;
}

function commentOfRows(own: readonly DiffRow[], side: CommentSide): BlockComment {
  const q = quoteLines(own);
  return { side, ...rangeOfHunk(own, side), quote: q.quote, cut: quoteCutWords(q) };
}

/** What a Comment on one changed block holds before the user's words: the
 *  NEW file's lines (the old ones for a deleted file) and the quote. The
 *  block is the file's `index`-th hunk. Null when the file has no such block. */
export function hunkComment(rows: readonly DiffRow[], index: number, deleted: boolean): BlockComment | null {
  const own = rowsOfHunkAt(rows, index);
  if (!own.length) return null;
  return commentOfRows(own, deleted ? "old" : "new");
}

// ── one line, or a range of lines ───────────────────────────────────────────

/** One line of a file, as a comment names it. */
export interface LineRef {
  side: CommentSide;
  line: number;
  /** The row's own POSITION among the file's rows (-1 = not said): two
   *  blocks of a joined diff can carry the same line numbers. */
  at: number;
}

/** Which line a drawn row IS for a comment: its NEW number when it has one
 *  (an added or unchanged line), else its old one (a removed line). Null for
 *  a hunk's header. `at`: the row's position among the file's rows. */
export function lineOfRow(row: DiffRow, at = -1): LineRef | null {
  if (row.kind === "hunk") return null;
  if (typeof row.newNo === "number") return { side: "new", line: row.newNo, at };
  if (typeof row.oldNo === "number") return { side: "old", line: row.oldNo, at };
  return null;
}

/** The accessible name of a line number's press. */
export function commentLineLabel(at: Pick<LineRef, "side" | "line"> | null): string {
  if (!at) return COMMENT;
  return at.side === "old" ? `${COMMENT} on removed line ${at.line}` : `${COMMENT} on line ${at.line}`;
}

/** A press on a line's number. A plain press is that ONE line. Shift+press is
 *  the range between the line pressed before (`anchor`) and this one, in
 *  either order, when both are on one side; across sides (or with no line
 *  pressed before) it starts again at this line: a range never mixes old and
 *  new lines. Answers the rows it spans (`from`..`to`, positions among the
 *  file's rows) and the line the next Shift+press extends from. */
export function pressLines(
  anchor: LineRef | null,
  pressed: LineRef,
  shift: boolean,
): { side: CommentSide; from: number; to: number; anchor: LineRef } {
  if (shift && anchor && anchor.side === pressed.side) {
    return {
      side: pressed.side,
      from: Math.min(anchor.at, pressed.at),
      to: Math.max(anchor.at, pressed.at),
      anchor,
    };
  }
  return { side: pressed.side, from: pressed.at, to: pressed.at, anchor: pressed };
}

/** What a Comment on a line or a range of lines holds before the user's
 *  words. The rows are taken by POSITION (`from`..`to`, either order), never
 *  by number alone, and of those exactly the ones that ARE lines on `side`:
 *  a new-side range holds its added and unchanged lines (never the removed
 *  ones drawn between them); an old-side range holds its removed lines and
 *  the unchanged ones between them. Null when there is no such line. */
export function linesComment(
  rows: readonly DiffRow[],
  side: CommentSide,
  from: number,
  to: number,
): BlockComment | null {
  const lo = Math.max(0, Math.min(from, to));
  const hi = Math.min(rows.length - 1, Math.max(from, to));
  if (!(lo <= hi)) return null;
  const own = rows.slice(lo, hi + 1).filter((r) => {
    if (r.kind === "hunk") return false;
    return typeof (side === "old" ? r.oldNo : r.newNo) === "number";
  });
  if (!own.length) return null;
  return commentOfRows(own, side);
}

/** The box's words start with "/name": a command of the coding tool's own
 *  ("/compact", "/model opus"). A command takes no comments, so the ones
 *  waiting stay above the box for the next message. A path ("/etc/hosts") is
 *  not one. */
export function isTypedCommand(text: string): boolean {
  return /^\s*\/[^\s/]+(\s|$)/.test(text);
}

// ── words ───────────────────────────────────────────────────────────────────

function baseName(path: string): string {
  return path.split(/[\\/]/).filter(Boolean).pop() || path;
}

/** "12–18" / "12" / "" (no line known). */
export function commentLines(c: Pick<CommentBody, "start" | "end">): string {
  if (!c.start) return "";
  return c.end && c.end !== c.start ? `${c.start}–${c.end}` : String(c.start);
}

/** "app.py 12–18" — the file and the lines. */
export function commentWhere(c: Pick<CommentBody, "path" | "start" | "end">): string {
  const lines = commentLines(c);
  return lines ? `${baseName(c.path)} ${lines}` : baseName(c.path);
}

/** A chip's short text: "app.py 12–18: Use math.fsum here…". */
export function commentChipWords(c: Pick<CommentBody, "path" | "start" | "end" | "words">, max = 60): string {
  const words = c.words.replace(/\s+/g, " ").trim();
  const cut = words.length > max ? `${words.slice(0, max - 1).trimEnd()}…` : words;
  return cut ? `${commentWhere(c)}: ${cut}` : commentWhere(c);
}

/** A chip's whole text (its title): the path, the lines, then the words. */
export function commentTitle(c: Pick<CommentBody, "path" | "start" | "end" | "words" | "side">): string {
  const lines = commentLines(c);
  const where = lines ? `${c.path}, ${lines.includes("–") ? "lines" : "line"} ${lines}` : c.path;
  return `${where}${c.side === "old" ? " (as it was before the change)" : ""}\n${c.words}`;
}

/** "1 comment" / "3 comments". */
export function commentCountWords(n: number): string {
  return n === 1 ? "1 comment" : `${n} comments`;
}

// ── the wire ────────────────────────────────────────────────────────────────

function line(v: unknown): number {
  const n = typeof v === "number" ? Math.floor(v) : typeof v === "string" && /^\d+$/.test(v) ? Number(v) : 0;
  return Number.isFinite(n) && n > 0 && n < 10_000_000 ? n : 0;
}

function bodyOf(r: Record<string, unknown>): CommentBody | null {
  if (typeof r.path !== "string" || !r.path.trim() || typeof r.words !== "string") return null;
  const start = line(r.start);
  const end = line(r.end);
  return {
    path: r.path.slice(0, PATH_CHARS),
    side: r.side === "old" ? "old" : "new",
    start,
    end: end || start,
    quote: typeof r.quote === "string" ? r.quote.slice(0, QUOTE_CHARS) : "",
    words: r.words.slice(0, WORDS_CHARS),
  };
}

/** The daemon's `comments` (on a sent message's event, a waiting message, a
 *  rewind's answer), by whitelist: [] for anything else. */
export function decodeComments(raw: unknown): CommentBody[] {
  if (!Array.isArray(raw)) return [];
  const out: CommentBody[] = [];
  for (const c of raw.slice(0, MAX_COMMENTS)) {
    const b = c && typeof c === "object" && !Array.isArray(c) ? bodyOf(c as Record<string, unknown>) : null;
    if (b) out.push(b);
  }
  return out;
}

/** What a message sends for its comments: the six fields, at most
 *  MAX_COMMENTS, never one with no words (the daemon refuses the message). */
export function commentsBody(list: readonly CommentBody[]): CommentBody[] {
  return list
    .filter((c) => c.words.trim() !== "")
    .slice(0, MAX_COMMENTS)
    .map((c) => ({
      path: c.path,
      side: c.side === "old" ? "old" : "new",
      start: c.start,
      end: c.end,
      quote: c.quote.slice(0, QUOTE_CHARS),
      words: c.words.slice(0, WORDS_CHARS),
    }));
}

// ── the store ───────────────────────────────────────────────────────────────

const NONE: readonly CodeComment[] = Object.freeze([]) as readonly CodeComment[];
/** What `getComments` answers per key: the SAME list until it changes (a
 *  `useSyncExternalStore` snapshot must be). */
const cache = new Map<string, readonly CodeComment[]>();
const listeners = new Map<string, Set<() => void>>();
let seq = 0;
let watching = false;

function storageKey(key: string): string {
  return `${COMMENTS_KEY_PREFIX}${key}`;
}

function newId(): string {
  seq += 1;
  return `c${Date.now().toString(36)}${seq.toString(36)}${Math.floor(Math.random() * 1e6).toString(36)}`;
}

function readStored(key: string): readonly CodeComment[] {
  try {
    const raw = window.localStorage.getItem(storageKey(key));
    if (!raw) return NONE;
    const v = JSON.parse(raw);
    if (!Array.isArray(v)) return NONE;
    const out: CodeComment[] = [];
    for (const c of v.slice(0, MAX_WAITING)) {
      if (!c || typeof c !== "object" || Array.isArray(c)) continue;
      const r = c as Record<string, unknown>;
      const b = bodyOf(r);
      if (!b || !b.words.trim()) continue;
      const id = typeof r.id === "string" && r.id ? r.id.slice(0, 80) : newId();
      if (out.some((x) => x.id === id)) continue;
      out.push({
        ...b,
        id,
        mark: typeof r.mark === "string" ? r.mark.slice(0, PATH_CHARS + 40) : "",
        view: typeof r.view === "string" ? r.view.slice(0, 40) : "",
        stale: r.stale === true,
      });
    }
    return out.length ? out : NONE;
  } catch {
    return NONE;
  }
}

function persist(key: string, list: readonly CodeComment[]): void {
  try {
    if (list.length) window.localStorage.setItem(storageKey(key), JSON.stringify(list));
    else window.localStorage.removeItem(storageKey(key));
  } catch {
    /* the comments last this visit */
  }
}

function tell(key: string): void {
  for (const fn of Array.from(listeners.get(key) ?? [])) fn();
}

function put(key: string, next: readonly CodeComment[]): void {
  const list = next.length ? next : NONE;
  cache.set(key, list);
  persist(key, list);
  tell(key);
}

/** Another window changed a session's comments: read them again. */
function onStorage(e: StorageEvent): void {
  if (e.key === null) {
    const keys = Array.from(cache.keys());
    cache.clear();
    for (const k of keys) tell(k);
    return;
  }
  if (!e.key.startsWith(COMMENTS_KEY_PREFIX)) return;
  const key = e.key.slice(COMMENTS_KEY_PREFIX.length);
  cache.delete(key);
  tell(key);
}

/** The session's waiting comments, oldest first. The same list until it
 *  changes; an empty key has none. */
export function getComments(key: string): readonly CodeComment[] {
  if (!key) return NONE;
  let list = cache.get(key);
  if (!list) {
    list = readStored(key);
    cache.set(key, list);
  }
  return list;
}

/** Be told when the session's waiting comments change. Returns the undo. */
export function subscribeComments(key: string, fn: () => void): () => void {
  if (!key) return () => {};
  if (!watching && typeof window !== "undefined") {
    watching = true;
    try {
      window.addEventListener("storage", onStorage);
    } catch {
      /* this window's own comments still work */
    }
  }
  let set = listeners.get(key);
  if (!set) {
    set = new Set();
    listeners.set(key, set);
  }
  set.add(fn);
  return () => {
    const cur = listeners.get(key);
    if (!cur) return;
    cur.delete(fn);
    if (!cur.size) listeners.delete(key);
  };
}

function sameComment(a: CommentBody, b: CommentBody): boolean {
  return a.path === b.path && a.side === b.side && a.start === b.start && a.end === b.end && a.words === b.words;
}

export type AddedComment = { ok: true; comment: CodeComment } | { ok: false; why: string };

/** Add a comment to the session's waiting ones. Refused, in words, when it
 *  has no words or the message is full; the quote and the words are cut to
 *  what one comment carries. */
export function addComment(
  key: string,
  draft: CommentBody & { mark?: string; view?: string },
): AddedComment {
  const words = draft.words.trim().slice(0, WORDS_CHARS);
  if (!key || !draft.path.trim()) return { ok: false, why: COMMENT_NEEDS_WORDS };
  if (!words) return { ok: false, why: COMMENT_NEEDS_WORDS };
  const cur = getComments(key);
  if (cur.length >= MAX_COMMENTS) return { ok: false, why: COMMENTS_FULL };
  const start = line(draft.start);
  const end = line(draft.end);
  const comment: CodeComment = {
    id: newId(),
    path: draft.path.slice(0, PATH_CHARS),
    side: draft.side === "old" ? "old" : "new",
    start,
    end: end || start,
    quote: draft.quote.slice(0, QUOTE_CHARS),
    words,
    mark: draft.mark ?? "",
    view: draft.view ?? "",
    stale: false,
  };
  put(key, [...cur, comment]);
  return { ok: true, comment };
}

/** Take one waiting comment away (its chip's ×). */
export function removeComment(key: string, id: string): void {
  const cur = getComments(key);
  const next = cur.filter((c) => c.id !== id);
  if (next.length !== cur.length) put(key, next);
}

/** Forget every waiting comment of the session. */
export function clearComments(key: string): void {
  if (getComments(key).length) put(key, NONE);
}

/** The comments the next message carries, TAKEN out of the store (the box
 *  was cleared): the oldest MAX_COMMENTS; any beyond that stay waiting. */
export function takeComments(key: string): CodeComment[] {
  const cur = getComments(key);
  if (!cur.length) return [];
  const taken = cur.slice(0, MAX_COMMENTS);
  put(key, cur.slice(MAX_COMMENTS));
  return taken;
}

/** Put comments BACK (a refused message's own, a waiting message taken back,
 *  a rewind's): before the ones written since, each once. A comment the
 *  daemon handed back has no id or mark: it gets an id here. */
export function restoreComments(key: string, back: readonly (CodeComment | CommentBody)[]): void {
  if (!key || !back.length) return;
  const cur = getComments(key);
  const first: CodeComment[] = [];
  for (const b of back) {
    if (!b.words.trim()) continue;
    const own = b as Partial<CodeComment>;
    const id = typeof own.id === "string" && own.id ? own.id : "";
    if (id && (cur.some((c) => c.id === id) || first.some((c) => c.id === id))) continue;
    if (cur.some((c) => sameComment(c, b)) || first.some((c) => sameComment(c, b))) continue;
    first.push({
      path: b.path,
      side: b.side === "old" ? "old" : "new",
      start: b.start,
      end: b.end,
      quote: b.quote,
      words: b.words,
      id: id || newId(),
      mark: typeof own.mark === "string" ? own.mark : "",
      view: typeof own.view === "string" ? own.view : "",
      stale: own.stale === true,
    });
  }
  if (first.length) put(key, [...first, ...cur].slice(0, MAX_WAITING));
}

/** The Changes view read its files again: a waiting comment written in THIS
 *  view whose file now has another change mark says "changed since" (and
 *  stops saying it when the file is back as it was). A file the view no
 *  longer lists, and a comment from another view, are left as they are.
 *  Writes only when something changed. */
export function noteFileMarks(key: string, view: string, marks: ReadonlyMap<string, string>): void {
  const cur = getComments(key);
  if (!cur.length) return;
  let changed = false;
  const next = cur.map((c) => {
    if (!c.mark || c.view !== view) return c;
    const now = marks.get(c.path);
    if (now === undefined) return c;
    const stale = now !== c.mark;
    if (stale === c.stale) return c;
    changed = true;
    return { ...c, stale };
  });
  if (changed) put(key, next);
}

/** Tests: forget what this window read (as a reload does). */
export function __resetCommentStore(): void {
  cache.clear();
}
