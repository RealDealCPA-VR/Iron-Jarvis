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
 *
 * COMMENT (v1.544.0, lib/codeComments): with `comments` on, each hunk offers
 * "Comment" beside "Open at line N". The press opens ONE dialog owned HERE
 * (the wide view draws every file two or three times, so a form inside a row
 * would exist several times): the file, the lines, the quoted block and a box
 * for the user's words. Save puts the comment in the session's store; the
 * message box shows it as a chip and sends it with the next message. The rows
 * get one plain value (`comments`) and one callback made once: a streamed
 * word still redraws no file. Enter and Escape in the dialog never leave it.
 */

import {
  memo,
  useCallback,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type RefObject,
} from "react";
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
import {
  COMMENT,
  COMMENTS_FULL,
  COMMENT_DIALOG_TITLE,
  COMMENT_FOOT,
  COMMENT_NEEDS_WORDS,
  COMMENT_SAVE,
  COMMENT_TITLE,
  COMMENT_WORDS_LABEL,
  MAX_COMMENTS,
  WORDS_CHARS,
  addComment,
  commentLines,
  getComments,
  hunkComment,
  noteFileMarks,
  type CommentBody,
} from "@/lib/codeComments";

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

/** A hunk's Comment press: the file's path and the hunk's header. */
type OnComment = (path: string, header: string) => void;

/** One file's rows, capped, with "Open at line N" (and, with `comments`,
 *  "Comment") on each hunk. */
function FileRows({
  change,
  wide,
  index,
  comments,
  onComment,
}: {
  change: FileChange;
  wide: boolean;
  index: number;
  comments: boolean;
  onComment: OnComment;
}) {
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
    if (!line && !comments) return null;
    return (
      <>
        {comments ? (
          // v1.544.0: a deleted file's block can be commented on too.
          <button
            type="button"
            data-testid={`review-hunk-comment-${index}`}
            // Which block (a file has several): the name starts with the
            // word on the press.
            aria-label={line ? `${COMMENT} on the block at line ${line}` : `${COMMENT} on this block`}
            title={COMMENT_TITLE}
            onClick={() => onComment(change.path, text)}
            className={`${ACTION} shrink-0 font-sans`}
          >
            {COMMENT}
          </button>
        ) : null}
        {line ? (
          <OpenInEditorButton
            path={change.path}
            line={line}
            words={`Open at line ${line}`}
            icon={false}
            testId={`review-hunk-open-${index}`}
            className="font-sans"
          />
        ) : null}
      </>
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
  comments,
  onComment,
}: {
  change: FileChange;
  wide: boolean;
  index: number;
  lazy: boolean;
  comments: boolean;
  onComment: OnComment;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const near = useNearScreen(ref, lazy);
  if (near) return <FileRows change={change} wide={wide} index={index} comments={comments} onComment={onComment} />;
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
  /** v1.544.0: each hunk offers "Comment" (a plain value; the press is a
   *  callback made once). */
  comments: boolean;
  onComment: OnComment;
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
  comments,
  onComment,
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
          <LazyRows
            change={change}
            wide={wide}
            index={index}
            lazy={lazy}
            comments={comments}
            onComment={onComment}
          />
        </div>
      )}
    </section>
  );
});

/** What the Comment dialog is about: one changed block of one file. */
interface CommentDraft extends Pick<CommentBody, "path" | "side" | "start" | "end" | "quote"> {
  label: string;
  mark: string;
}

const DIALOG_QUIET =
  "inline-flex h-8 items-center rounded-lg px-3 text-[13px] text-zinc-300 transition-colors hover:bg-white/[0.06] hover:text-zinc-100 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/60 disabled:cursor-not-allowed disabled:opacity-50";
const DIALOG_MAIN =
  "inline-flex h-8 items-center rounded-lg border border-accent/30 bg-accent/[0.08] px-3 text-[13px] font-medium text-accent-soft transition-colors hover:bg-accent/[0.14] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/60 disabled:cursor-not-allowed disabled:opacity-50";

/** v1.544.0: the ONE Comment dialog of a review (never one per row). Its own
 *  keys stay inside it: Enter is a new line in the words (Ctrl+Enter saves),
 *  Escape closes THIS dialog only (never the wide view under it), and neither
 *  reaches the message box or a question card. */
function CommentDialog({
  draft,
  full,
  onSave,
  onClose,
}: {
  draft: CommentDraft;
  /** The message already carries as many comments as it can. */
  full: boolean;
  /** Answers why it was not saved (null = saved). */
  onSave: (words: string) => string | null;
  onClose: () => void;
}) {
  const [words, setWords] = useState("");
  const [problem, setProblem] = useState<string | null>(full ? COMMENTS_FULL : null);
  const lines = commentLines(draft);
  const ready = words.trim() !== "" && !full;
  function save() {
    if (full) return;
    if (!words.trim()) {
      setProblem(COMMENT_NEEDS_WORDS);
      return;
    }
    const why = onSave(words);
    if (why) setProblem(why);
  }
  function onKeyDown(e: ReactKeyboardEvent<HTMLDivElement>) {
    if (e.nativeEvent.isComposing || e.keyCode === 229) return;
    if (e.key === "Escape") {
      // Stopped HERE (React's and the browser's): the wide view's own dialog
      // listens for Escape on the document, and must stay open.
      e.stopPropagation();
      e.preventDefault();
      onClose();
      return;
    }
    if (e.key === "Enter") {
      e.stopPropagation();
      if ((e.ctrlKey || e.metaKey) && (e.target as HTMLElement).tagName === "TEXTAREA") {
        e.preventDefault();
        save();
      }
    }
  }
  return (
    <Modal label={COMMENT_DIALOG_TITLE} onClose={onClose} className="w-full max-w-xl" testId="review-comment-dialog">
      <div className="flex min-h-0 flex-col gap-3 px-5 pb-4 pt-5" onKeyDown={onKeyDown}>
        <h2 className="text-[15px] font-medium text-zinc-100">{COMMENT_DIALOG_TITLE}</h2>
        <p data-testid="review-comment-where" className="min-w-0 break-all text-[12px] text-zinc-400">
          <span className="font-mono text-zinc-300">{draft.label}</span>
          {lines ? <span>{`, ${lines.includes("–") ? "lines" : "line"} ${lines}`}</span> : null}
          {draft.side === "old" ? <span> (as it was before the change)</span> : null}
        </p>
        <pre
          data-testid="review-comment-quote"
          tabIndex={0}
          aria-label="The lines you are commenting on"
          className="max-h-[32vh] min-h-0 overflow-auto whitespace-pre rounded-lg bg-white/[0.04] px-3 py-2 font-mono text-[12px] leading-relaxed text-zinc-300 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/50"
        >
          {draft.quote}
        </pre>
        <label className="flex flex-col gap-1 text-[12px] text-zinc-400">
          {COMMENT_WORDS_LABEL}
          <textarea
            data-testid="review-comment-words"
            // The dialog opens on the words (Modal keeps a control that takes
            // focus itself).
            autoFocus
            rows={3}
            maxLength={WORDS_CHARS}
            value={words}
            onChange={(e) => {
              setWords(e.target.value);
              if (!full) setProblem(null);
            }}
            className="min-h-[4.5rem] resize-y rounded-lg bg-white/[0.04] px-3 py-2 text-[13px] text-zinc-100 placeholder:text-zinc-600 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/60"
          />
        </label>
        {problem ? (
          <p role="alert" data-testid="review-comment-problem" className="text-[12px] text-tone-danger">
            {problem}
          </p>
        ) : (
          <p className="text-[12px] text-zinc-500">{COMMENT_FOOT}</p>
        )}
        <div className="flex flex-wrap items-center justify-end gap-2 pt-1">
          <button type="button" className={DIALOG_QUIET} onClick={onClose} data-testid="review-comment-cancel">
            Cancel
          </button>
          <button
            type="button"
            className={DIALOG_MAIN}
            onClick={save}
            disabled={!ready}
            title="Save this comment (Ctrl+Enter)"
            data-testid="review-comment-save"
          >
            {COMMENT_SAVE}
          </button>
        </div>
      </div>
    </Modal>
  );
}

export function ChangesReview({
  changes,
  sessionId,
  more = false,
  hint = false,
  undoFor,
  onUndo,
  onUndone,
  comments = false,
  commentView = "",
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
  /** v1.544.0: offer "Comment" on each hunk (only when Iron Jarvis says it
   *  takes comments with a message; an older one would drop them). The
   *  comments wait in lib/codeComments under `sessionId`. */
  comments?: boolean;
  /** Which view of the changes this is ("session", "branch", "turn"): the
   *  same file has another diff in another view, so "changed since" is
   *  judged within one view. */
  commentView?: string;
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

  // v1.544.0: the ONE Comment dialog. The press is made once (the files it
  // reads come through a ref), so no file redraws for it.
  const [draft, setDraft] = useState<CommentDraft | null>(null);
  const keyedRef = useRef(keyed);
  keyedRef.current = keyed;
  const openComment = useCallback((path: string, header: string) => {
    const hit = keyedRef.current.find((k) => k.c.path === path);
    if (!hit) return;
    const body = hunkComment(parseUnified(hit.c.diff ?? ""), header, hit.c.status === "deleted");
    if (!body) return;
    setDraft({ ...body, path, label: hit.c.rel || hit.c.name || hit.c.path, mark: hit.mark });
  }, []);
  const saveComment = (words: string): string | null => {
    if (!draft) return null;
    const res = addComment(sessionId, {
      path: draft.path,
      side: draft.side,
      start: draft.start,
      end: draft.end,
      quote: draft.quote,
      words,
      mark: draft.mark,
      view: commentView,
    });
    if (!res.ok) return res.why;
    setDraft(null);
    return null;
  };
  // A file that changed again after a comment was written on it: its chip
  // says "changed since" (the store writes only when something changed).
  useEffect(() => {
    if (!comments) return;
    noteFileMarks(sessionId, commentView, new Map(keyed.map((k) => [k.c.path, k.mark])));
  }, [comments, sessionId, commentView, keyed]);
  // No comments any more (another session's review, an older Iron Jarvis):
  // an open dialog goes with them.
  useEffect(() => setDraft(null), [sessionId, comments]);

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
            comments={comments}
            onComment={openComment}
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
      {/* After the wide view, so it opens over it. */}
      {comments && draft ? (
        <CommentDialog
          key={`${draft.path}:${draft.start}:${draft.end}`}
          draft={draft}
          full={getComments(sessionId).length >= MAX_COMMENTS}
          onSave={saveComment}
          onClose={() => setDraft(null)}
        />
      ) : null}
    </div>
  );
}

export default ChangesReview;
