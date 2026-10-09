/**
 * The pure half of the markdown table tools (v1.325.0): what a cell sorts as,
 * the asc → desc → original cycle, and rows → CSV.
 *
 * The React half lives in `components/Markdown.tsx` (`MarkdownTable`). It
 * reorders the row ELEMENTS react-markdown built, so a bold cell or a link
 * keeps its formatting after a sort; this module only ever sees the plain text
 * of a cell (`nodeText`), which is all a sort key or a CSV field needs.
 *
 * CSV here is RFC 4180 (fields with a comma, quote or line break are quoted,
 * quotes doubled, CRLF between records) plus the spreadsheet formula guard: a
 * text cell starting with = + - @ (or a tab / carriage return) is prefixed with
 * an apostrophe, so a model-written "=HYPERLINK(...)" opens in Excel as text,
 * never as a live formula. A plain number like "-300" is left alone — it is a
 * number, not a formula, and an apostrophe would turn it into text.
 * `chartToCsv` in `lib/chartSpec.ts` uses the same helpers.
 */

export type SortDir = "asc" | "desc";
export interface SortState {
  col: number;
  dir: SortDir;
}

export type SortKey =
  | { kind: "blank" }
  | { kind: "num"; n: number }
  | { kind: "text"; s: string };

const PLAIN_NUMBER_RX = /^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/;
const CURRENCY_RX = /[$€£¥]/g;

/**
 * What a cell sorts as. Numeric-aware for the shapes a model writes in a
 * table: "$1,234.50" → 1234.5, "12%" → 12, "(300)" → -300 (the accountant's
 * negative), "-$5" → -5, "−7" (a typographic minus) → -7. Anything else is
 * text; an empty cell (or a lone dash/em dash) is blank and always sorts last.
 */
export function sortKey(raw: string): SortKey {
  const text = (raw ?? "").replace(/\s+/g, " ").trim();
  if (!text || text === "-" || text === "—" || text === "–") return { kind: "blank" };
  let t = text.replace(/−/g, "-").replace(/\s/g, "");
  let negative = false;
  const paren = /^\((.*)\)$/.exec(t);
  if (paren) {
    negative = true;
    t = paren[1];
  }
  t = t.replace(CURRENCY_RX, "").replace(/%$/, "");
  // Thousands separators: only commas that sit between digit groups of three.
  if (/^[+-]?\d{1,3}(,\d{3})+(\.\d+)?$/.test(t)) t = t.replace(/,/g, "");
  if (PLAIN_NUMBER_RX.test(t)) {
    const n = Number(t);
    if (Number.isFinite(n)) return { kind: "num", n: negative ? -n : n };
  }
  return { kind: "text", s: text };
}

const COLLATOR =
  typeof Intl !== "undefined"
    ? new Intl.Collator(undefined, { numeric: true, sensitivity: "base" })
    : null;

/** Ascending order of two non-blank keys: numbers before text. */
function compareFilled(a: SortKey, b: SortKey): number {
  if (a.kind === "num" && b.kind === "num") return a.n - b.n;
  if (a.kind === "num") return -1;
  if (b.kind === "num") return 1;
  const sa = (a as { s: string }).s;
  const sb = (b as { s: string }).s;
  return COLLATOR ? COLLATOR.compare(sa, sb) : sa < sb ? -1 : sa > sb ? 1 : 0;
}

/**
 * The row ORDER for a sort: indices into `keys` (one cell text per row).
 * Stable (equal keys keep their original order) and blanks last in BOTH
 * directions — a descending sort that put every empty cell on top would bury
 * the numbers the user asked to see.
 */
export function sortOrder(cells: string[], dir: SortDir): number[] {
  const keys = cells.map(sortKey);
  const idx = cells.map((_, i) => i);
  idx.sort((i, j) => {
    const a = keys[i];
    const b = keys[j];
    const aBlank = a.kind === "blank";
    const bBlank = b.kind === "blank";
    if (aBlank || bBlank) {
      if (aBlank && bBlank) return i - j;
      return aBlank ? 1 : -1;
    }
    const c = compareFilled(a, b);
    if (c !== 0) return dir === "asc" ? c : -c;
    return i - j;
  });
  return idx;
}

/** A header press: asc → desc → original (null); another column starts at asc. */
export function nextSort(current: SortState | null, col: number): SortState | null {
  if (!current || current.col !== col) return { col, dir: "asc" };
  if (current.dir === "asc") return { col, dir: "desc" };
  return null;
}

/** One CSV field: formula-guarded (text only), then RFC 4180 quoted when needed. */
export function csvField(value: string | number): string {
  let s = typeof value === "number" ? (Number.isFinite(value) ? String(value) : "") : String(value ?? "");
  if (typeof value !== "number" && /^[=+\-@\t\r]/.test(s) && !PLAIN_NUMBER_RX.test(s)) {
    s = `'${s}`;
  }
  return /[",\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

/** Rows → CSV text (CRLF between records, no trailing line break). */
export function rowsToCsv(rows: (string | number)[][]): string {
  return rows.map((row) => row.map(csvField).join(",")).join("\r\n");
}
