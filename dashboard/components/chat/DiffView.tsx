"use client";

/**
 * DiffView (v1.328.0, calm chat B5) — one file's changes from a chat turn,
 * inside the shared <Modal>.
 *
 * Wide screens read it SIDE BY SIDE (before | after), narrow ones as ONE
 * column. Both layouts are in the DOM and CSS picks one (`md:`), so a window
 * resize never refetches or re-parses anything.
 *
 * Where the lines come from: the daemon's `POST /chat/changes` answers a
 * UNIFIED diff per file (`parseUnified` reads it, keeping each line's old and
 * new numbers from the hunk headers). A caller that holds both texts instead
 * passes `before`/`after`, and those go through the shared `lib/diff.ts`
 * `diffLines` (the same LCS the document preview and the coach use). Either
 * way the result is the shared `DiffLine` vocabulary (same / added / removed)
 * plus a hunk marker.
 *
 * Calm look: hairlines, mono 12px, colour ONLY on the +/- counts and on the
 * changed lines (a faint tint and a coloured marker; the text stays readable
 * zinc). A binary file, a file too large to compare, or a before the journal
 * no longer keeps is SAID, never drawn as an empty diff.
 */

import { Fragment, useMemo, useState, type KeyboardEvent as ReactKeyboardEvent, type ReactNode } from "react";
import { X } from "lucide-react";
import { Modal } from "@/components/Modal";
import { diffLines, textLines, type DiffLine } from "@/lib/diff";
import type { FileChange } from "./ChangedFiles";

/** One drawn row: a diff line with its numbers, or a hunk separator. */
export interface DiffRow {
  kind: DiffLine["kind"] | "hunk";
  text: string;
  /** Line number in the before file (same/removed rows). */
  oldNo?: number;
  /** Line number in the after file (same/added rows). */
  newNo?: number;
}

/** Rows drawn per layout before saying how many more there are. */
export const MAX_DIFF_ROWS = 3000;

const HUNK_RE = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/;

/** v1.405.0: a hunk header's first line numbers — `@@ -10,3 +12,4 @@` reads
 *  `{oldStart: 10, newStart: 12}` (the review pane's "Open at line 12" is the
 *  hunk's first line in the NEW file); null when `text` is not a header. */
export function hunkStart(text: string): { oldStart: number; newStart: number } | null {
  const h = HUNK_RE.exec(text ?? "");
  return h ? { oldStart: Number(h[1]), newStart: Number(h[2]) } : null;
}

/** v1.405.0: something drawn at the end of a hunk's header row (the review
 *  pane's "Open at line N"); absent = the plain header, as before. v1.544.0:
 *  `index` is the hunk's POSITION among the file's hunks (0 = the first): two
 *  blocks of a diff that joins several edits can have the same header text. */
export type HunkAction = (hunkText: string, index: number) => ReactNode;

/** v1.544.0: a press on a line's NUMBER (the review pane's "Comment on line
 *  14"; Shift+press extends to a range). Absent = the plain number, exactly
 *  as before. A row's pressable number is its NEW one when it has one (an
 *  added or unchanged line), else its old one (a removed line): one press a
 *  row, in either layout. */
/** 26-a: what to draw under one line of the diff (null = nothing). */
export type LineNote = (row: DiffRow) => ReactNode;

export interface LinePress {
  /** The number was pressed (`shift`: with Shift held). */
  onPress: (row: DiffRow, shift: boolean) => void;
  /** The press's accessible name ("Comment on line 14"). */
  label: (row: DiffRow) => string;
  title?: string;
  /** This is the line the next Shift+press extends from (drawn pressed). */
  pressed?: (row: DiffRow) => boolean;
}

/** Read a unified diff (as `POST /chat/changes` writes it) into rows. The
 *  `---`/`+++` header pair is skipped; `\ No newline at end of file` is not a
 *  line of either file and is skipped too. */
export function parseUnified(diff: string): DiffRow[] {
  const rows: DiffRow[] = [];
  let oldNo = 0;
  let newNo = 0;
  let inHunk = false;
  for (const line of (diff ?? "").split(/\r?\n/)) {
    const h = HUNK_RE.exec(line);
    if (h) {
      oldNo = Number(h[1]);
      newNo = Number(h[2]);
      inHunk = true;
      rows.push({ kind: "hunk", text: line });
      continue;
    }
    if (!inHunk || line.startsWith("\\")) continue;
    const mark = line.charAt(0);
    const text = line.slice(1);
    if (mark === "+") rows.push({ kind: "added", text, newNo: newNo++ });
    else if (mark === "-") rows.push({ kind: "removed", text, oldNo: oldNo++ });
    else if (mark === " ") {
      rows.push({ kind: "same", text, oldNo: oldNo++, newNo: newNo++ });
    }
  }
  return rows;
}

/** Rows for two whole texts, through the shared `diffLines`. */
export function rowsFromTexts(before: string, after: string): DiffRow[] {
  let oldNo = 1;
  let newNo = 1;
  return diffLines(textLines(before), textLines(after)).map((l) =>
    l.kind === "added"
      ? { kind: l.kind, text: l.text, newNo: newNo++ }
      : l.kind === "removed"
        ? { kind: l.kind, text: l.text, oldNo: oldNo++ }
        : { kind: l.kind, text: l.text, oldNo: oldNo++, newNo: newNo++ },
  );
}

/** One side-by-side row: before on the left, after on the right. */
export interface SplitRow {
  hunk?: string;
  left?: DiffRow;
  right?: DiffRow;
}

/** Pair rows for the side-by-side view: an unchanged line sits on both
 *  sides, and a run of removed lines is matched line for line with the run of
 *  added lines that follows it (the classic split layout). */
export function pairRows(rows: DiffRow[]): SplitRow[] {
  const out: SplitRow[] = [];
  let i = 0;
  while (i < rows.length) {
    const r = rows[i];
    if (r.kind === "hunk") {
      out.push({ hunk: r.text });
      i++;
    } else if (r.kind === "same") {
      out.push({ left: r, right: r });
      i++;
    } else {
      const removed: DiffRow[] = [];
      const added: DiffRow[] = [];
      while (i < rows.length && rows[i].kind === "removed") removed.push(rows[i++]);
      while (i < rows.length && rows[i].kind === "added") added.push(rows[i++]);
      for (let k = 0; k < Math.max(removed.length, added.length); k++) {
        out.push({ left: removed[k], right: added[k] });
      }
    }
  }
  return out;
}

/** The tint + marker for a changed line. Colour lives ONLY here and on the
 *  counts. */
function lineTone(kind: DiffRow["kind"]): string {
  if (kind === "added") return "bg-tone-success/10";
  if (kind === "removed") return "bg-tone-danger/10";
  return "";
}

function Marker({ kind }: { kind: DiffRow["kind"] }) {
  if (kind === "added") return <span className="text-tone-success">+</span>;
  if (kind === "removed") return <span className="text-tone-danger">−</span>;
  return <span> </span>;
}

/** One number's press, bound to its row (see `LinePress`). */
interface NumPress {
  label: string;
  title?: string;
  pressed: boolean;
  onPress: (shift: boolean) => void;
  /** This number is the list's ONE Tab stop (the others are reached with the
   *  arrow keys). */
  tab: boolean;
  onFocus: () => void;
}

/** The list's Tab stop, and how a focused number becomes it. */
interface LineStop {
  row: DiffRow | null;
  set: (row: DiffRow) => void;
}

/** The press for `row`'s number in the `col` gutter: only for the row's own
 *  number (new when it has one, else old), and only when the caller asked. */
function numPress(
  press: LinePress | undefined,
  row: DiffRow,
  col: "old" | "new",
  stop: LineStop,
): NumPress | undefined {
  if (!press || row.kind === "hunk") return undefined;
  const own = typeof row.newNo === "number" ? "new" : "old";
  if (own !== col) return undefined;
  return {
    label: press.label(row),
    title: press.title,
    pressed: press.pressed?.(row) ?? false,
    onPress: (shift) => press.onPress(row, shift),
    tab: stop.row === row,
    onFocus: () => stop.set(row),
  };
}

/** v1.544.0: the line numbers of one list are ONE Tab stop (a file draws up
 *  to hundreds of them): ArrowUp and ArrowDown move between them, Home and
 *  End go to the first and the last. On the list's own box. */
function lineKeys(e: ReactKeyboardEvent<HTMLElement>) {
  const from = (e.target as HTMLElement | null)?.closest?.("button[data-line-press]");
  if (!from) return;
  const all = Array.from(e.currentTarget.querySelectorAll<HTMLButtonElement>("button[data-line-press]"));
  const i = all.indexOf(from as HTMLButtonElement);
  let to = -1;
  if (e.key === "ArrowDown") to = Math.min(all.length - 1, i + 1);
  else if (e.key === "ArrowUp") to = Math.max(0, i - 1);
  else if (e.key === "Home") to = 0;
  else if (e.key === "End") to = all.length - 1;
  if (to < 0 || i < 0) return;
  e.preventDefault();
  all[to].focus(); // its focus makes it the Tab stop
}

function Num({ n, press }: { n?: number; press?: NumPress }) {
  if (press && n !== undefined) {
    // v1.544.0: a real button. Enter and Space press it (Shift held extends
    // a range); the keys are read here so Shift is never lost on the way.
    return (
      <button
        type="button"
        data-line-press=""
        tabIndex={press.tab ? 0 : -1}
        aria-label={press.label}
        aria-pressed={press.pressed}
        title={press.title}
        onFocus={press.onFocus}
        onClick={(e) => press.onPress(e.shiftKey)}
        onKeyDown={(e) => {
          if (e.key !== "Enter" && e.key !== " ") return;
          e.preventDefault();
          press.onPress(e.shiftKey);
        }}
        onKeyUp={(e) => {
          if (e.key === " ") e.preventDefault(); // (a browser's own click for Space)
        }}
        className={`w-10 shrink-0 cursor-pointer select-none rounded-sm pr-2 text-right tabular-nums transition-colors hover:bg-white/[0.06] hover:text-zinc-200 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/50 motion-reduce:transition-none ${
          press.pressed ? "bg-accent/[0.08] text-accent-soft" : "text-zinc-600"
        }`}
      >
        {n}
      </button>
    );
  }
  return (
    <span className="w-10 shrink-0 select-none pr-2 text-right tabular-nums text-zinc-600">
      {n ?? ""}
    </span>
  );
}

/** The +a −r pair, coloured. Absent counts (binary, not compared) draw nothing. */
export function Counts({
  added,
  removed,
  className = "",
}: {
  added: number | null | undefined;
  removed: number | null | undefined;
  className?: string;
}) {
  if (added == null && removed == null) return null;
  return (
    <span className={`shrink-0 tabular-nums ${className}`}>
      <span className="text-tone-success">+{added ?? 0}</span>{" "}
      <span className="text-tone-danger">−{removed ?? 0}</span>
    </span>
  );
}

/** "new file" / "edited" / "deleted" — the plain word for a change's status. */
export function statusWord(status: FileChange["status"]): string {
  if (status === "created") return "new file";
  if (status === "deleted") return "deleted";
  return "edited";
}

/** A hunk's separator row: its header, and the caller's action (v1.405.0). */
function HunkRow({ text, action, index }: { text: string; action?: HunkAction; index: number }) {
  const extra = action ? action(text, index) : null;
  if (!extra) {
    return (
      <div data-kind="hunk" className="border-y border-white/[0.06] px-3 py-1 text-zinc-600">
        {text}
      </div>
    );
  }
  return (
    <div
      data-kind="hunk"
      className="flex min-w-0 items-center gap-2 border-y border-white/[0.06] px-3 py-1 text-zinc-600"
    >
      <span className="min-w-0 flex-1 truncate">{text}</span>
      {extra}
    </div>
  );
}

/** One column (before and after interleaved). Hidden from `md` unless
 *  `always` (v1.405.0: the review pane is narrow on any window). */
export function UnifiedRows({
  rows,
  always = false,
  hunkAction,
  linePress,
  lineNote,
}: {
  rows: DiffRow[];
  always?: boolean;
  hunkAction?: HunkAction;
  /** v1.544.0: a press on a line's number; absent = plain numbers. */
  linePress?: LinePress;
  /** 26-a: what sits under a line (a review comment from GitHub); absent or
   *  null = the line alone, exactly as before. */
  lineNote?: LineNote;
}) {
  // The ONE Tab stop among the line numbers: the one focused last, else the
  // first line (only with `linePress`; nothing is drawn for it otherwise).
  const [focused, setFocused] = useState<DiffRow | null>(null);
  const stop: LineStop = {
    row: !linePress ? null : focused && rows.includes(focused) ? focused : (rows.find((r) => r.kind !== "hunk") ?? null),
    set: setFocused,
  };
  let hunks = -1;
  return (
    <div
      data-testid="diff-unified"
      className={always ? "" : "md:hidden"}
      onKeyDown={linePress ? lineKeys : undefined}
    >
      {rows.map((r, i) =>
        r.kind === "hunk" ? (
          <HunkRow key={i} text={r.text} action={hunkAction} index={(hunks += 1)} />
        ) : (
          <Fragment key={i}>
            <div data-kind={r.kind} className={`flex ${lineTone(r.kind)}`}>
              <Num n={r.oldNo} press={numPress(linePress, r, "old", stop)} />
              <Num n={r.newNo} press={numPress(linePress, r, "new", stop)} />
              <span className="w-4 shrink-0 select-none">
                <Marker kind={r.kind} />
              </span>
              <span className="min-w-0 whitespace-pre-wrap break-all pr-3 text-zinc-300">
                {r.text}
              </span>
            </div>
            {lineNote ? lineNote(r) : null}
          </Fragment>
        ),
      )}
    </div>
  );
}

function SplitCell({
  row,
  side,
  linePress,
  stop,
}: {
  row?: DiffRow;
  side: "left" | "right";
  linePress?: LinePress;
  stop: LineStop;
}) {
  if (!row) return <div className="min-w-0 bg-white/[0.02]" data-side={side} />;
  const n = side === "left" ? row.oldNo : row.newNo;
  return (
    <div data-side={side} data-kind={row.kind} className={`flex min-w-0 ${lineTone(row.kind)}`}>
      <Num n={n} press={numPress(linePress, row, side === "left" ? "old" : "new", stop)} />
      <span className="w-4 shrink-0 select-none">
        <Marker kind={row.kind} />
      </span>
      <span className="min-w-0 whitespace-pre-wrap break-all pr-3 text-zinc-300">
        {row.text}
      </span>
    </div>
  );
}

/** Side by side (before | after). Shown from `md` (v1.405.0: `hunkAction`
 *  as in `UnifiedRows`; v1.544.0: `linePress` too). */
export function SplitRows({
  rows,
  hunkAction,
  linePress,
  lineNote,
}: {
  rows: SplitRow[];
  hunkAction?: HunkAction;
  linePress?: LinePress;
  /** 26-a: as in `UnifiedRows` — drawn under the pair, across both sides. */
  lineNote?: LineNote;
}) {
  // One Tab stop here too (see `UnifiedRows`). A row's own number is its new
  // one when it has one (the right cell), else its old one (the left cell).
  const [focused, setFocused] = useState<DiffRow | null>(null);
  const lines = linePress ? rows.flatMap((r) => (r.hunk !== undefined ? [] : [r.left, r.right])) : [];
  const stop: LineStop = {
    row: !linePress
      ? null
      : focused && lines.includes(focused)
        ? focused
        : (lines.find((r): r is DiffRow => r !== undefined) ?? null),
    set: setFocused,
  };
  let hunks = -1;
  return (
    <div data-testid="diff-split" className="hidden md:block" onKeyDown={linePress ? lineKeys : undefined}>
      {rows.map((r, i) =>
        r.hunk !== undefined ? (
          <HunkRow key={i} text={r.hunk} action={hunkAction} index={(hunks += 1)} />
        ) : (
          <Fragment key={i}>
            <div data-testid="diff-split-row" className="grid grid-cols-2 divide-x divide-white/[0.06]">
              <SplitCell row={r.left} side="left" linePress={linePress} stop={stop} />
              <SplitCell row={r.right} side="right" linePress={linePress} stop={stop} />
            </div>
            {lineNote && r.left ? lineNote(r.left) : null}
            {lineNote && r.right && r.right !== r.left ? lineNote(r.right) : null}
          </Fragment>
        ),
      )}
    </div>
  );
}

/** The body only (no dialog): what the modal shows, also usable inline. */
export function DiffBody({
  change,
  before,
  after,
}: {
  change: FileChange;
  before?: string;
  after?: string;
}) {
  const rows = useMemo(
    () =>
      before !== undefined || after !== undefined
        ? rowsFromTexts(before ?? "", after ?? "")
        : parseUnified(change.diff),
    [change.diff, before, after],
  );
  const shown = useMemo(() => rows.slice(0, MAX_DIFF_ROWS), [rows]);
  const hidden = rows.length - shown.length;
  const split = useMemo(() => pairRows(shown), [shown]);

  if (change.binary) {
    return (
      <p data-testid="diff-binary" className="px-4 py-6 text-[13px] text-zinc-500">
        This is a binary file, so there are no lines to compare.
      </p>
    );
  }
  if (rows.length === 0) {
    return (
      <p data-testid="diff-empty" className="px-4 py-6 text-[13px] text-zinc-500">
        {change.note || "No line changes to show."}
      </p>
    );
  }
  return (
    <div className="font-mono text-[12px] leading-relaxed">
      <UnifiedRows rows={shown} />
      <SplitRows rows={split} />
      {(hidden > 0 || change.truncated) && (
        <p
          data-testid="diff-more"
          className="border-t border-white/[0.06] px-3 py-2 font-sans text-[12px] text-zinc-500"
        >
          {hidden > 0
            ? `${hidden.toLocaleString()} more lines are not shown here.`
            : "This diff was cut short. The counts above cover all of it."}
        </p>
      )}
    </div>
  );
}

export function DiffView({
  change,
  onClose,
  before,
  after,
}: {
  change: FileChange;
  onClose: () => void;
  /** Optional whole texts; when given they are diffed here with `diffLines`. */
  before?: string;
  after?: string;
}) {
  const name = change.name || change.rel || change.path;
  return (
    <Modal
      label={`Changes to ${name}`}
      onClose={onClose}
      className="w-full max-w-6xl"
      testId="diff-view"
    >
      <div className="flex items-center gap-3 border-b border-white/[0.06] px-4 py-3">
        <div className="min-w-0 flex-1">
          <div className="truncate text-[14px] text-zinc-100" title={change.path}>
            {name}
          </div>
          <div className="mt-0.5 flex flex-wrap items-center gap-x-2 text-[12px] text-zinc-500">
            {change.rel && change.rel !== name && (
              <span className="min-w-0 truncate font-mono">{change.rel}</span>
            )}
            <span>{statusWord(change.status)}</span>
            <Counts added={change.added} removed={change.removed} />
            {change.changed_since && (
              <span data-testid="diff-changed-since" className="text-tone-warn">
                changed again since this reply
              </span>
            )}
            {change.undone && <span>undone</span>}
          </div>
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close"
          className="shrink-0 rounded-lg p-1.5 text-zinc-500 transition-colors hover:bg-white/[0.06] hover:text-zinc-200"
        >
          <X size={16} />
        </button>
      </div>
      <div className="min-h-0 flex-1 overflow-auto py-1">
        <DiffBody change={change} before={before} after={after} />
      </div>
    </Modal>
  );
}

export default DiffView;
