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

import { useMemo } from "react";
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

function Num({ n }: { n?: number }) {
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

function UnifiedRows({ rows }: { rows: DiffRow[] }) {
  return (
    <div data-testid="diff-unified" className="md:hidden">
      {rows.map((r, i) =>
        r.kind === "hunk" ? (
          <div
            key={i}
            data-kind="hunk"
            className="border-y border-white/[0.06] px-3 py-1 text-zinc-600"
          >
            {r.text}
          </div>
        ) : (
          <div key={i} data-kind={r.kind} className={`flex ${lineTone(r.kind)}`}>
            <Num n={r.oldNo} />
            <Num n={r.newNo} />
            <span className="w-4 shrink-0 select-none">
              <Marker kind={r.kind} />
            </span>
            <span className="min-w-0 whitespace-pre-wrap break-all pr-3 text-zinc-300">
              {r.text}
            </span>
          </div>
        ),
      )}
    </div>
  );
}

function SplitCell({ row, side }: { row?: DiffRow; side: "left" | "right" }) {
  if (!row) return <div className="min-w-0 bg-white/[0.02]" data-side={side} />;
  const n = side === "left" ? row.oldNo : row.newNo;
  return (
    <div data-side={side} data-kind={row.kind} className={`flex min-w-0 ${lineTone(row.kind)}`}>
      <Num n={n} />
      <span className="w-4 shrink-0 select-none">
        <Marker kind={row.kind} />
      </span>
      <span className="min-w-0 whitespace-pre-wrap break-all pr-3 text-zinc-300">
        {row.text}
      </span>
    </div>
  );
}

function SplitRows({ rows }: { rows: SplitRow[] }) {
  return (
    <div data-testid="diff-split" className="hidden md:block">
      {rows.map((r, i) =>
        r.hunk !== undefined ? (
          <div
            key={i}
            data-kind="hunk"
            className="border-y border-white/[0.06] px-3 py-1 text-zinc-600"
          >
            {r.hunk}
          </div>
        ) : (
          <div
            key={i}
            data-testid="diff-split-row"
            className="grid grid-cols-2 divide-x divide-white/[0.06]"
          >
            <SplitCell row={r.left} side="left" />
            <SplitCell row={r.right} side="right" />
          </div>
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
