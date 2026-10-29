"use client";

/**
 * v1.405.0 — ONE SCROLLING REVIEW of a coding session's changes (the Changes
 * pane, both views: "This session" and "Branch").
 *
 * The user's problem (docs/IJ-HARNESS-WALKTHROUGH.md): seeing a change took a
 * press per file plus a dialog to close; Claude Code takes one press. Here
 * every changed file's diff is drawn INLINE in one scroll — no press to see a
 * file, no dialog:
 *
 *  - a top line: "3 files changed +42 −7 · 1 of 3 reviewed", then Collapse
 *    reviewed, Expand all and Wide view (the same review in a large dialog,
 *    side by side on a wide window);
 *  - each file is a section whose HEADER stays in sight while that file
 *    scrolls by: what happened, the path, its counts, the honesty words
 *    (changed since, undone, binary — never behind a press), Open in VS Code /
 *    Cursor, Undo ("This session" only, through the caller's own `undoFor` /
 *    `onUndo`, the receipt's undo — never a second undo path) and a Reviewed
 *    box;
 *  - each hunk says where it is and offers "Open at line N" (N = the line
 *    of its first CHANGE in the new file — v1.405.0 review: the hunk header's
 *    start is up to three lines of context ABOVE the change; a deleted file
 *    opens nothing).
 *
 * There is no Keep: a change is already on disk ("Changes are already saved;
 * Undo takes one back.").
 *
 * REVIEWED marks are remembered per session (lib/changesReview), keyed by the
 * path AND a hash of that file's change (status, counts, the cut flag, a
 * binary file's size and the diff), so a file that changes again is
 * unreviewed by itself. A reviewed file folds (its header stays).
 *
 * MEMOISED (v1.405.0 review): each file is a `memo` component whose props are
 * plain values and callbacks made once (`useCallback`, the caller's handlers
 * read through refs), so marking one file reviewed redraws that file only.
 *
 * BOUNDED: at most REVIEW_ROWS_PER_FILE rows a file ("Show all N lines" for
 * the rest, up to DiffView's own cap), and from LAZY_FROM_FILES files on a
 * file's rows are drawn only once it comes near the screen (an
 * IntersectionObserver on the scroll box; where there is none — jsdom, an old
 * browser — everything is drawn).
 *
 * The rows are DiffView's own (`parseUnified`, `UnifiedRows`, `SplitRows`):
 * one parser, one way of drawing a line.
 */

import { memo, useCallback, useEffect, useId, useMemo, useRef, useState, type RefObject } from "react";
import { ChevronDown, ChevronRight, Loader2, Maximize2, Undo2, X } from "lucide-react";
import { Modal } from "@/components/Modal";
import {
  Counts,
  MAX_DIFF_ROWS,
  SplitRows,
  UnifiedRows,
  hunkStart,
  pairRows,
  parseUnified,
  statusWord,
  type DiffRow,
} from "@/components/chat/DiffView";
import type { ChangeUndoState, FileChange } from "@/components/chat/ChangedFiles";
import { OpenInEditorButton } from "@/components/harness/OpenInEditor";
import {
  LAZY_FROM_FILES,
  REVIEW_HINT,
  REVIEW_ROWS_PER_FILE,
  markOf,
  readMarks,
  reviewSummary,
  withMark,
  writeMarks,
} from "@/lib/changesReview";

const ACTION =
  "inline-flex h-7 items-center gap-1 rounded-md px-1.5 text-[12px] text-zinc-500 transition-colors hover:bg-white/[0.06] hover:text-zinc-200 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/50 disabled:cursor-not-allowed disabled:opacity-40 motion-reduce:transition-none";

/** Each hunk's FIRST CHANGED line in the new file, keyed by its header text:
 *  an added row's own new number, or — for a removal — the new line it sits
 *  before (the next new number). A hunk with no change keeps its header's
 *  start. Read from `parseUnified`'s rows (one parser). */
export function hunkChangeLines(rows: readonly DiffRow[]): Map<string, number> {
  const out = new Map<string, number>();
  let header: string | null = null;
  let next = 1;
  for (const r of rows) {
    if (r.kind === "hunk") {
      header = r.text;
      const h = hunkStart(r.text);
      next = h && h.newStart >= 1 ? h.newStart : 1;
      continue;
    }
    if (header === null) continue;
    if (!out.has(header) && (r.kind === "added" || r.kind === "removed")) {
      out.set(header, r.kind === "added" && typeof r.newNo === "number" ? Math.max(1, r.newNo) : Math.max(1, next));
    }
    if (typeof r.newNo === "number") next = r.newNo + 1;
  }
  for (const r of rows) {
    if (r.kind !== "hunk" || out.has(r.text)) continue;
    const h = hunkStart(r.text);
    if (h) out.set(r.text, Math.max(1, h.newStart));
  }
  return out;
}

/** The first CHANGED line of the NEW file (the first hunk's first + or −
 *  row); null for a deleted file or no hunk. */
export function firstNewLine(change: Pick<FileChange, "status" | "diff">): number | null {
  if (change.status === "deleted") return null;
  const rows = parseUnified(change.diff ?? "");
  const lines = hunkChangeLines(rows);
  const first = rows.find((r) => r.kind === "hunk");
  return first ? (lines.get(first.text) ?? null) : null;
}

/** The nearest box around `el` that scrolls up and down (the pane, the
 *  dialog), for the IntersectionObserver's root; null = the window. */
function scrollBoxOf(el: HTMLElement): HTMLElement | null {
  let cur = el.parentElement;
  while (cur) {
    const oy = window.getComputedStyle(cur).overflowY;
    if (oy === "auto" || oy === "scroll") return cur;
    cur = cur.parentElement;
  }
  return null;
}

/** True once `ref` has come near the screen (and stays true). With no
 *  IntersectionObserver, true at once. */
function useNearScreen(ref: RefObject<HTMLElement | null>, enabled: boolean): boolean {
  const [seen, setSeen] = useState(() => !enabled || typeof IntersectionObserver === "undefined");
  useEffect(() => {
    if (seen) return;
    const el = ref.current;
    if (!el || typeof IntersectionObserver === "undefined") {
      setSeen(true);
      return;
    }
    const io = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) {
          setSeen(true);
          io.disconnect();
        }
      },
      { root: scrollBoxOf(el), rootMargin: "600px 0px" },
    );
    io.observe(el);
    return () => io.disconnect();
  }, [ref, seen]);
  return seen;
}

/** One file's rows, capped, with "Open at line N" on each hunk. */
function FileRows({ change, wide, index }: { change: FileChange; wide: boolean; index: number }) {
  const [all, setAll] = useState(false);
  const rows = useMemo(() => parseUnified(change.diff), [change.diff]);
  const changeLines = useMemo(() => hunkChangeLines(rows), [rows]);
  const limit = all ? MAX_DIFF_ROWS : REVIEW_ROWS_PER_FILE;
  const shown = useMemo(() => rows.slice(0, limit), [rows, limit]);
  const split = useMemo(() => (wide ? pairRows(shown) : []), [wide, shown]);
  const hidden = rows.length - shown.length;
  const deleted = change.status === "deleted";
  const hunkAction = (text: string) => {
    const line = deleted ? undefined : changeLines.get(text);
    if (!line) return null;
    return (
      <OpenInEditorButton
        path={change.path}
        line={line}
        words={`Open at line ${line}`}
        icon={false}
        testId={`review-hunk-open-${index}`}
        className="font-sans"
      />
    );
  };

  if (change.binary) {
    return (
      <p data-testid={`review-binary-${index}`} className="py-2 pl-[18px] text-[12px] text-zinc-500">
        This is a binary file, so there are no lines to compare.
      </p>
    );
  }
  if (rows.length === 0) {
    return (
      <p data-testid={`review-empty-${index}`} className="py-2 pl-[18px] text-[12px] text-zinc-500">
        {change.note || (change.undone ? "This change was undone." : "No line changes to show.")}
      </p>
    );
  }
  return (
    <div data-testid={`review-rows-${index}`} className="min-w-0 font-mono text-[12px] leading-relaxed">
      <UnifiedRows rows={shown} always={!wide} hunkAction={hunkAction} />
      {wide ? <SplitRows rows={split} hunkAction={hunkAction} /> : null}
      {hidden > 0 ? (
        all ? (
          <p className="border-t border-white/[0.06] px-3 py-2 font-sans text-[12px] text-zinc-500">
            {`${hidden.toLocaleString()} more lines are not shown here.`}
          </p>
        ) : (
          <button
            type="button"
            data-testid={`review-show-all-${index}`}
            onClick={() => setAll(true)}
            className={`${ACTION} my-1 font-sans`}
          >
            Show all {rows.length.toLocaleString()} lines
          </button>
        )
      ) : change.truncated ? (
        <p className="border-t border-white/[0.06] px-3 py-2 font-sans text-[12px] text-zinc-500">
          This diff was cut short. The counts above cover all of it.
        </p>
      ) : null}
    </div>
  );
}

/** The rows, drawn once the file comes near the screen (`lazy`). */
function LazyRows({
  change,
  wide,
  index,
  lazy,
}: {
  change: FileChange;
  wide: boolean;
  index: number;
  lazy: boolean;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const near = useNearScreen(ref, lazy);
  if (near) return <FileRows change={change} wide={wide} index={index} />;
  const lines = Math.min((change.diff ?? "").split("\n").length, REVIEW_ROWS_PER_FILE);
  return (
    <div
      ref={ref}
      data-testid={`review-pending-${index}`}
      aria-hidden
      style={{ height: `${Math.max(1, lines) * 20 + 8}px` }}
    />
  );
}

interface FileProps {
  change: FileChange;
  /** The file's Reviewed mark (path + hash): what the callbacks are told. */
  mark: string;
  index: number;
  wide: boolean;
  lazy: boolean;
  reviewed: boolean;
  folded: boolean;
  headBg: string;
  /** The undo journal's match, as plain values (memo compares them). */
  undoActionId: string | null;
  undoable: boolean;
  undoReason: string | null;
  /** This file's undo is running / another file's is. */
  undoBusy: boolean;
  undoLocked: boolean;
  onFold: (mark: string, folded: boolean) => void;
  onReviewed: (mark: string, on: boolean) => void;
  onUndo: (actionId: string, path: string) => void;
}

const ReviewFile = memo(function ReviewFile({
  change,
  mark,
  index,
  wide,
  lazy,
  reviewed,
  folded,
  headBg,
  undoActionId,
  undoable,
  undoReason,
  undoBusy,
  undoLocked,
  onFold,
  onReviewed,
  onUndo,
}: FileProps) {
  const bodyId = useId();
  const label = change.rel || change.name || change.path;
  const base = change.name || label.split(/[\\/]/).pop() || label;
  const first = useMemo(() => firstNewLine(change), [change]);
  return (
    <section
      data-testid={`review-file-${index}`}
      data-path={change.path}
      data-reviewed={reviewed ? "true" : "false"}
      aria-label={`${statusWord(change.status)}: ${label}`}
      className="min-w-0"
    >
      <div
        data-testid={`review-head-${index}`}
        className={`sticky top-0 z-[1] flex min-w-0 flex-wrap items-center gap-x-2 gap-y-0.5 border-b border-white/[0.06] py-1 text-[12px] text-zinc-500 ${headBg}`}
      >
        <button
          type="button"
          data-testid={`review-fold-${index}`}
          aria-expanded={!folded}
          aria-controls={folded ? undefined : bodyId}
          onClick={() => onFold(mark, folded)}
          title={change.path}
          className="flex min-w-0 grow basis-[14rem] items-center gap-1.5 rounded-sm text-left transition-colors hover:text-zinc-300 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/50 motion-reduce:transition-none"
        >
          {folded ? (
            <ChevronRight size={12} aria-hidden className="shrink-0" />
          ) : (
            <ChevronDown size={12} aria-hidden className="shrink-0" />
          )}
          <span className="shrink-0 text-zinc-600">{statusWord(change.status)}</span>
          <span data-testid={`review-path-${index}`} className="min-w-0 truncate font-mono text-zinc-300">
            {label}
          </span>
        </button>
        <Counts added={change.added} removed={change.removed} />
        {change.binary ? <span className="shrink-0 text-zinc-600">binary</span> : null}
        {change.changed_since ? (
          <span data-testid={`review-changed-since-${index}`} className="shrink-0 text-tone-warn">
            changed since
          </span>
        ) : null}
        {change.undone ? (
          <span data-testid={`review-undone-${index}`} className="shrink-0 text-zinc-600">
            undone
          </span>
        ) : null}
        <span className="ml-auto flex shrink-0 flex-wrap items-center gap-1">
          {change.status !== "deleted" ? (
            <OpenInEditorButton path={change.path} line={first} testId={`review-open-${index}`} />
          ) : null}
          {undoActionId && !change.undone ? (
            <button
              type="button"
              data-testid={`review-undo-${index}`}
              onClick={() => onUndo(undoActionId, change.path)}
              disabled={!undoable || undoLocked}
              aria-label={`Undo the change to ${base}`}
              title={undoable ? `Undo this change to ${base}` : `Can't undo: ${undoReason ?? "not undoable"}`}
              className={ACTION}
            >
              {undoBusy ? (
                <Loader2 size={12} aria-hidden className="animate-spin motion-reduce:animate-none" />
              ) : (
                <Undo2 size={12} aria-hidden />
              )}
              Undo
            </button>
          ) : null}
          <label className="inline-flex h-7 cursor-pointer items-center gap-1 rounded-md px-1.5 text-zinc-500 transition-colors hover:text-zinc-200 motion-reduce:transition-none">
            <input
              type="checkbox"
              data-testid={`review-mark-${index}`}
              checked={reviewed}
              onChange={(e) => onReviewed(mark, e.target.checked)}
              aria-label={`Reviewed: ${label}`}
              className="h-3.5 w-3.5 accent-accent"
            />
            Reviewed
          </label>
        </span>
      </div>
      {folded ? null : (
        <div id={bodyId} className="min-w-0 pb-2">
          <LazyRows change={change} wide={wide} index={index} lazy={lazy} />
        </div>
      )}
    </section>
  );
});

export function ChangesReview({
  changes,
  sessionId,
  more = false,
  hint = false,
  undoFor,
  onUndo,
  onUndone,
}: {
  changes: FileChange[];
  /** Whose review marks (the session's thread id). */
  sessionId: string;
  /** The daemon listed only the first files (`truncated_files`). */
  more?: boolean;
  /** Say that changes are already saved and Undo takes one back ("This
   *  session" — the view with Undo). */
  hint?: boolean;
  /** The caller's undo journal match for a file (the receipt's `undoFor`).
   *  Undo renders only for a matched file, and only with `onUndo` too. */
  undoFor?: (path: string) => ChangeUndoState | null | undefined;
  /** Performs the undo (the caller owns the confirm + POST). */
  onUndo?: (actionId: string, path: string) => void | Promise<void>;
  /** Called after `onUndo` settled without an error (read the changes again). */
  onUndone?: () => void;
}) {
  const [marks, setMarks] = useState<string[]>(() => readMarks(sessionId));
  useEffect(() => setMarks(readMarks(sessionId)), [sessionId]);
  const markSet = useMemo(() => new Set(marks), [marks]);
  // Fold state by the file's CURRENT mark (path + diff hash): a file that
  // changed again opens again by itself. Absent = folded when reviewed.
  const [fold, setFold] = useState<Record<string, boolean>>({});
  const [undoing, setUndoing] = useState<string | null>(null);
  const [undoErr, setUndoErr] = useState<string | null>(null);
  const [wide, setWide] = useState(false);

  const keyed = useMemo(() => changes.map((c) => ({ c, mark: markOf(c) })), [changes]);
  const reviewedCount = keyed.filter((k) => markSet.has(k.mark)).length;
  const isFolded = (mark: string) => fold[mark] ?? markSet.has(mark);

  // The callbacks every file gets, made ONCE per session (memo(ReviewFile)
  // keeps a file that did not change from drawing again).
  const setReviewed = useCallback(
    (mark: string, on: boolean) => {
      setMarks((prev) => {
        const next = withMark(prev, mark, on);
        writeMarks(sessionId, next);
        return next;
      });
      // A file marked reviewed folds; one unmarked opens again.
      setFold((prev) => ({ ...prev, [mark]: on }));
    },
    [sessionId],
  );
  const toggleFold = useCallback((mark: string, folded: boolean) => {
    setFold((prev) => ({ ...prev, [mark]: !folded }));
  }, []);
  function collapseReviewed() {
    setFold((prev) => {
      const next = { ...prev };
      for (const k of keyed) if (markSet.has(k.mark)) next[k.mark] = true;
      return next;
    });
  }
  function expandAll() {
    setFold(Object.fromEntries(keyed.map((k) => [k.mark, false])));
  }
  // The caller's handlers through refs: a new function from the caller each
  // render must not redraw every file.
  const onUndoRef = useRef(onUndo);
  const onUndoneRef = useRef(onUndone);
  onUndoRef.current = onUndo;
  onUndoneRef.current = onUndone;
  const undoingRef = useRef<string | null>(null);
  const runUndo = useCallback((actionId: string, path: string) => {
    const handler = onUndoRef.current;
    if (!handler || undoingRef.current) return;
    undoingRef.current = path;
    setUndoing(path);
    setUndoErr(null);
    void (async () => {
      try {
        await handler(actionId, path);
        onUndoneRef.current?.();
      } catch (e) {
        setUndoErr(e instanceof Error ? e.message : String(e));
      } finally {
        undoingRef.current = null;
        setUndoing(null);
      }
    })();
  }, []);
  const undoOf = (path: string): ChangeUndoState | null =>
    undoFor && onUndo ? (undoFor(path) ?? null) : null;

  if (!changes.length) return null;
  const lazy = changes.length >= LAZY_FROM_FILES;
  const sum = changes.some((c) => c.added != null || c.removed != null);
  const totals = changes.reduce(
    (t, c) => ({ added: t.added + (c.added ?? 0), removed: t.removed + (c.removed ?? 0) }),
    { added: 0, removed: 0 },
  );

  const summary = (pre: string) => (
    <p
      data-testid={`${pre}-summary`}
      title={reviewSummary(changes, reviewedCount)}
      className="min-w-0 text-[12px] text-zinc-500"
    >
      <span>{`${changes.length} ${changes.length === 1 ? "file" : "files"} changed`}</span>
      {sum ? (
        <>
          {" "}
          <Counts added={totals.added} removed={totals.removed} />
        </>
      ) : null}
      <span data-testid={`${pre}-count`}>{` · ${reviewedCount} of ${changes.length} reviewed`}</span>
    </p>
  );

  const files = (inWide: boolean) => (
    <div data-testid={inWide ? "review-wide-files" : "review-files"} className="grid min-w-0 gap-1">
      {keyed.map(({ c, mark }, i) => {
        const undo = undoOf(c.path);
        return (
          <ReviewFile
            key={c.path}
            change={c}
            mark={mark}
            index={i}
            wide={inWide}
            lazy={lazy}
            reviewed={markSet.has(mark)}
            folded={isFolded(mark)}
            headBg={inWide ? "bg-ink-850" : "bg-ink-950"}
            undoActionId={undo?.actionId ?? null}
            undoable={undo?.undoable === true}
            undoReason={undo?.reason ?? null}
            undoBusy={undoing === c.path}
            undoLocked={undoing !== null}
            onFold={toggleFold}
            onReviewed={setReviewed}
            onUndo={runUndo}
          />
        );
      })}
      {more ? <p className="text-[12px] text-zinc-600">Only the first {changes.length} files are listed.</p> : null}
    </div>
  );

  const actions = (pre: string) => (
    <div className="flex flex-wrap items-center gap-1">
      <button
        type="button"
        data-testid={`${pre}-collapse-reviewed`}
        onClick={collapseReviewed}
        disabled={reviewedCount === 0}
        className={ACTION}
      >
        Collapse reviewed
      </button>
      <button type="button" data-testid={`${pre}-expand-all`} onClick={expandAll} className={ACTION}>
        Expand all
      </button>
    </div>
  );

  return (
    <div data-testid="changes-review" className="min-w-0 space-y-1.5">
      {hint ? (
        <p data-testid="review-hint" className="text-[12px] text-zinc-500">
          {REVIEW_HINT}
        </p>
      ) : null}
      {summary("review")}
      <div className="flex min-w-0 flex-wrap items-center gap-1">
        {actions("review")}
        <button
          type="button"
          data-testid="review-wide-open"
          onClick={() => setWide(true)}
          title="The same review in a large window, side by side"
          className={ACTION}
        >
          <Maximize2 size={12} aria-hidden />
          Wide view
        </button>
      </div>
      {undoErr ? (
        <p role="alert" data-testid="review-undo-error" className="text-[12px] text-tone-danger">
          {undoErr}
        </p>
      ) : null}
      {files(false)}
      {wide ? (
        <Modal label="Review the changes" onClose={() => setWide(false)} className="w-full max-w-6xl" testId="review-wide">
          <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1 border-b border-white/[0.06] px-4 py-3">
            <h2 className="text-[14px] text-zinc-100">Review the changes</h2>
            <div className="min-w-0 grow basis-[16rem]">{summary("review-wide")}</div>
            {actions("review-wide")}
            <button
              type="button"
              onClick={() => setWide(false)}
              aria-label="Close"
              className="shrink-0 rounded-lg p-1.5 text-zinc-500 transition-colors hover:bg-white/[0.06] hover:text-zinc-200"
            >
              <X size={16} />
            </button>
          </div>
          {undoErr ? (
            <p role="alert" data-testid="review-wide-undo-error" className="px-4 pt-2 text-[12px] text-tone-danger">
              {undoErr}
            </p>
          ) : null}
          <div data-testid="review-wide-body" className="min-h-0 flex-1 overflow-auto px-4 py-2">
            {files(true)}
          </div>
        </Modal>
      ) : null}
    </div>
  );
}

export default ChangesReview;
