/**
 * A FIGURE THAT LINKS TO WHERE IT WAS READ (v1.549.0, idea 30-a).
 *
 * After a chat turn that read a file, Iron Jarvis links each figure of the
 * reply to its own record of the read: "#source/<id>[,<id>…]" (several ids =
 * the same value in several places, offered as a chooser). A figure found in
 * nothing the turn was given is "#source/none": a faint dotted line whose
 * press says there is no record. The model never writes these: the daemon
 * removes any it did (documents/sources.py).
 *
 * Pressing a figure asks GET /sources/<ids> for the place and opens the file
 * beside the chat at that page, line or cell (DocPreview's `at`). The chat
 * page answers the open request (OPEN_PLACE_EVENT); anywhere else the place
 * is said in words beside the figure.
 *
 * The daemon's twin is documents/sources.py (SOURCE_LINK_PREFIX, NO_RECORD,
 * NO_RECORD_WORDS), pinned equal by tests/test_leap30a_link_reply.py.
 */

export const SOURCE_LINK_PREFIX = "#source/";
export const NO_RECORD = "none";
export const NO_RECORD_WORDS = "No record of where this came from: Jarvis worked it out or wrote it itself.";
export const HOW_WORKED_OUT = "How this was worked out";
export const SHOW_PLACE_TITLE = "Show where this came from";
export const PLACE_FAILED = "Could not show where this came from.";
export const FILE_CHANGED = "The file has changed since it was read.";
export const FILE_GONE = "The file is no longer there.";
/** The window event the chat page answers (preventDefault = opened). */
export const OPEN_PLACE_EVENT = "ij:open-place";

const ID_RX = /^src_[0-9a-f]{8,32}$/;
const MAX_IDS = 12;

export type SourceHref = { none: true } | { none: false; ids: string[] };

/** Where in a file: the page and line of a PDF, a Word paragraph or table
 *  cell, a line (and column, for a CSV) of a text file, a sheet cell. */
export type SourceAt = {
  page?: number;
  line?: number;
  col?: number;
  paragraph?: number;
  table?: number;
  row?: number;
  sheet?: string;
  cell?: string;
  /** The figure as the file writes it, and the line it sat on. */
  shown?: string;
  context?: string;
  /** The record's id: the page picture is asked for by it, so no line of
   *  the file rides a web address. */
  id?: string;
  /** The file has changed since it was read: the preview says so and lights
   *  no cell or line by its position (what is there now may be another
   *  figure). */
  changed?: boolean;
};

export interface SourceMark {
  id: string;
  path: string;
  name: string;
  kind: string;
  locator: Record<string, number | string>;
  shown: string;
  key: string;
  context: string;
  present: boolean;
  changed: boolean | null;
}

/** The figure link a reply carries, or null when the link is an ordinary one. */
export function parseSourceHref(href: string | undefined | null): SourceHref | null {
  if (!href || !href.startsWith(SOURCE_LINK_PREFIX)) return null;
  const rest = href.slice(SOURCE_LINK_PREFIX.length);
  if (rest === NO_RECORD) return { none: true };
  const ids = rest.split(",").map((s) => s.trim());
  if (!ids.length || ids.length > MAX_IDS || !ids.every((id) => ID_RX.test(id))) return null;
  return { none: false, ids };
}

/** A mark's place, for the preview beside the chat. */
export function atOf(mark: SourceMark): SourceAt {
  const at: SourceAt = { shown: mark.shown, context: mark.context, id: mark.id };
  const loc = mark.locator || {};
  for (const k of ["page", "line", "col", "paragraph", "table", "row"] as const) {
    const v = Number(loc[k]);
    if (Number.isFinite(v) && v > 0) at[k] = v;
  }
  if (typeof loc.sheet === "string") at.sheet = loc.sheet;
  if (typeof loc.cell === "string") at.cell = loc.cell;
  if (mark.changed === true) at.changed = true;
  return at;
}

/** The place in plain words: "page 7, line 3", "cell C7 on Summary",
 *  "row 3, column 2" (a CSV). */
export function placeWords(at: SourceAt): string {
  if (at.cell) return at.sheet ? `cell ${at.cell} on ${at.sheet}` : `cell ${at.cell}`;
  if (at.table) {
    const parts = [`table ${at.table}`];
    if (at.row) parts.push(`row ${at.row}`);
    if (at.col) parts.push(`column ${at.col}`);
    return parts.join(", ");
  }
  if (at.paragraph) return `paragraph ${at.paragraph}`;
  const parts: string[] = [];
  if (at.page) parts.push(`page ${at.page}`);
  if (at.line) parts.push(`line ${at.line}`);
  if (at.row) parts.push(`row ${at.row}`);
  if (at.col) parts.push(`column ${at.col}`);
  return parts.join(", ") || "the file";
}

// --- How a figure is compared (twin of core/numbers.number_key) -------------
// "1,234.50" and "1234.5" are the same number; a scale is part of it ("25k"
// is not "25"). Pinned equal to the daemon by a shared list of examples
// (__tests__/fixtures/figure-keys-v1549.json).
const NUMBER_RX =
  /(?:(?<![\w.,])\.(\d+)|(\d+(?:[.,]\d+)*))(?:(k|mm|m|bn|b)\b|[\s-]?(mil|mill|grand|thousands?|millions?|billions?|trillions?|hundreds?|lakhs?|crores?)\b)?/i;
const THOUSANDS_RX = /^\d{1,3}(?:,\d{3})+(?:\.\d+)?$/;
const PLAIN_RX = /^\d+(?:\.\d+)?$/;
const SCALES: Record<string, string> = {
  k: "k", thousand: "k", grand: "k", m: "m", mm: "m", mil: "m", mill: "m",
  million: "m", b: "b", bn: "b", billion: "b", trillion: "t", hundred: "h",
  lakh: "lakh", crore: "crore",
};

/** One number as it is compared (core/numbers.number_key). */
export function numberKey(digits: string, scale = ""): string {
  let d = digits;
  if (THOUSANDS_RX.test(d)) d = d.replace(/,/g, "");
  if (PLAIN_RX.test(d) && d.includes(".")) d = d.replace(/0+$/, "").replace(/\.+$/, "");
  const word = scale.toLowerCase();
  const tail = SCALES[word] || SCALES[word.replace(/s+$/, "")] || "";
  return tail ? `${d}~${tail}` : d;
}

/** The key of the first number written in `text` ("$48,210.00" → "48210"),
 *  or "" when it holds none: what a figure link's words are compared by. */
export function figureKey(text: string): string {
  const m = NUMBER_RX.exec(text || "");
  if (!m) return "";
  const digits = m[1] !== undefined ? `0.${m[1]}` : m[2];
  return numberKey(digits, m[3] || m[4] || "");
}

/** The places a figure link may open: only a record of the SAME value as the
 *  figure's words. A link the daemon made always matches; anything else
 *  (a link a model managed to write) opens nothing. */
export function marksForFigure(words: string, marks: SourceMark[]): SourceMark[] {
  const key = figureKey(words);
  return key ? marks.filter((m) => m.key === key) : [];
}

/** "Read from page 7, line 3 of statement.pdf." */
export function readFromWords(at: SourceAt, name: string): string {
  return `Read from ${placeWords(at)} of ${name}.`;
}

/** The context line split around the figure, for lighting it. */
export function litParts(context: string, shown: string): [string, string, string] {
  const text = context || "";
  const i = shown ? text.indexOf(shown) : -1;
  if (i < 0) return [text, "", ""];
  return [text.slice(0, i), shown, text.slice(i + shown.length)];
}

/** "C7" → {col: 3, row: 7} (1-based), or null. */
export function cellPosition(cell: string | undefined): { col: number; row: number } | null {
  const m = /^([A-Za-z]{1,3})(\d{1,7})$/.exec((cell || "").replace(/\$/g, ""));
  if (!m) return null;
  let col = 0;
  for (const ch of m[1].toUpperCase()) col = col * 26 + (ch.charCodeAt(0) - 64);
  return { col, row: Number(m[2]) };
}

/** Ask the chat page to open a file at a place. True when it did. */
export function openPlace(path: string, at: SourceAt): boolean {
  if (typeof window === "undefined") return false;
  const ev = new CustomEvent(OPEN_PLACE_EVENT, { detail: { path, at }, cancelable: true });
  window.dispatchEvent(ev);
  return ev.defaultPrevented;
}

// A figure link: its words (brackets may nest one level), then the address,
// bare or in <…>. Twin of documents/sources.py (strip_model_links).
const LINK_RX =
  /!?\[((?:[^[\]\n\\]|\\.|\[(?:[^[\]\n\\]|\\.)*\])*)\]\(\s*<?#source\/[^)\n]*\)/g;

/** A reply's words without its figure links (Copy message keeps the words). */
export function withoutPlaceLinks(text: string): string {
  return text && text.includes(SOURCE_LINK_PREFIX) ? text.replace(LINK_RX, "$1") : text;
}

/** A stable key for a place (the preview remounts when it changes). */
export function placeKey(at: SourceAt | null | undefined): string {
  if (!at) return "";
  return [at.page, at.line, at.col, at.paragraph, at.table, at.row, at.sheet, at.cell]
    .map((v) => (v === undefined ? "" : String(v)))
    .join("|");
}
