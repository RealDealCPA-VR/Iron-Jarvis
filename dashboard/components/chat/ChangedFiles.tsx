"use client";

/**
 * ChangedFiles (v1.328.0, calm chat B5) — the one quiet line under a reply
 * whose turn wrote files: "3 files changed +42 −7".
 *
 * Pressing the line lists the files (one grey row each: what happened, the
 * path, its counts). Hovering or focusing a file previews the first changed
 * lines; pressing it opens the whole diff (`DiffView`, side by side on a wide
 * window, one column on a narrow one). A caller that wants to own the dialog
 * passes `onOpen(path)`; without it this component opens `DiffView` itself.
 *
 * THE DATA IS THE DAEMON'S (`POST /chat/changes`, read from the undo journal
 * and the disk): nothing here guesses. `decodeChanges` checks the shape at
 * the JSON boundary, and the honesty cases stay visible WITHOUT a press: a
 * file that changed again after the reply says so in amber on its row, and a
 * write that was undone says "undone".
 *
 * Wired into the chat page in v1.328.0 (W3-2) through `ReplyChanges`, which
 * asks `fetchTurnChangeSet` with the turn's start and end, once per reply and
 * only when the reply is on screen. A row offers Undo only when the caller's
 * `undoFor` matches the file to the chat's own undo journal (the same rule and
 * the same handler as the receipt's file chip), never a second undo path.
 */

import { useId, useMemo, useState } from "react";
import { ChevronDown, ChevronRight, FileDiff, Loader2, Undo2 } from "lucide-react";
import { post } from "@/lib/api";
import { Counts, DiffView, parseUnified, statusWord } from "./DiffView";

/** One file a turn changed, as `POST /chat/changes` answers it. */
export interface FileChange {
  /** Absolute path (the journal's workspace + target). */
  path: string;
  /** Workspace-relative path, forward slashes. */
  rel?: string;
  name?: string;
  status: "created" | "modified" | "deleted";
  /** Line counts over the WHOLE diff; null when not compared (binary, too
   *  large, the before no longer kept). */
  added: number | null;
  removed: number | null;
  /** Unified diff text ("binary file" for a binary file). */
  diff: string;
  truncated?: boolean;
  binary?: boolean;
  /** The file changed again after the turn; null = cannot tell. */
  changed_since?: boolean | null;
  undone?: boolean;
  /** A plain sentence when there is nothing to draw. */
  note?: string;
}

export interface TurnChanges {
  changes: FileChange[];
  files: number;
  added: number;
  removed: number;
  truncated_files: boolean;
}

const STATUSES = new Set(["created", "modified", "deleted"]);

function num(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) && v >= 0 ? Math.floor(v) : null;
}

/** The rows worth showing from a `/chat/changes` answer. Crosses a JSON
 *  boundary, so every field is checked; a row without a path is dropped. */
export function decodeChanges(raw: unknown): FileChange[] {
  const list =
    raw && typeof raw === "object" && Array.isArray((raw as { changes?: unknown }).changes)
      ? (raw as { changes: unknown[] }).changes
      : Array.isArray(raw)
        ? raw
        : [];
  const out: FileChange[] = [];
  for (const r of list) {
    if (!r || typeof r !== "object") continue;
    const o = r as Record<string, unknown>;
    const path = typeof o.path === "string" ? o.path : "";
    if (!path) continue;
    out.push({
      path,
      rel: typeof o.rel === "string" ? o.rel : undefined,
      name: typeof o.name === "string" ? o.name : undefined,
      status: STATUSES.has(o.status as string)
        ? (o.status as FileChange["status"])
        : "modified",
      added: num(o.added),
      removed: num(o.removed),
      diff: typeof o.diff === "string" ? o.diff : "",
      truncated: o.truncated === true,
      binary: o.binary === true,
      changed_since: typeof o.changed_since === "boolean" ? o.changed_since : null,
      undone: o.undone === true,
      note: typeof o.note === "string" ? o.note : "",
    });
  }
  return out;
}

/** One turn's window, as `POST /chat/changes` takes it. */
export interface TurnChangesArgs {
  since: number | string;
  until?: number | string;
  paths?: string[];
}

/** The rows plus whether the daemon listed only the first files. */
export interface TurnChangeSet {
  changes: FileChange[];
  /** `truncated_files`: more files changed than the daemon listed. */
  more: boolean;
}

/** Ask the daemon what the turn between `since` and `until` changed, and
 *  whether it listed every file. `paths` (the turn's reported files) narrows
 *  it. Epoch ms or ISO strings. */
export async function fetchTurnChangeSet(args: TurnChangesArgs): Promise<TurnChangeSet> {
  const iso = (t: number | string) => (typeof t === "number" ? new Date(t).toISOString() : t);
  const body: Record<string, unknown> = { since: iso(args.since) };
  if (args.until !== undefined) body.until = iso(args.until);
  if (args.paths && args.paths.length) body.paths = args.paths;
  const raw = await post<unknown>("/chat/changes", body);
  const more =
    !!raw &&
    typeof raw === "object" &&
    (raw as { truncated_files?: unknown }).truncated_files === true;
  return { changes: decodeChanges(raw), more };
}

/** Ask the daemon what the turn between `since` and `until` changed. `paths`
 *  (the turn's reported files) narrows it. Epoch ms or ISO strings. */
export async function fetchTurnChanges(args: TurnChangesArgs): Promise<FileChange[]> {
  return (await fetchTurnChangeSet(args)).changes;
}

/** What the caller's undo journal knows about one file (the receipt's own
 *  shape): the action to undo, or why it cannot be undone. */
export interface ChangeUndoState {
  actionId: string;
  undoable: boolean;
  reason?: string;
}

/** "1 file changed" / "3 files changed". */
export function filesWord(n: number): string {
  return `${n} ${n === 1 ? "file" : "files"} changed`;
}

/** Totals over the rows, counting only what was compared. `null` when no row
 *  had counts (all binary, say) so the line draws no numbers. */
export function totals(changes: FileChange[]): { added: number; removed: number } | null {
  let any = false;
  let added = 0;
  let removed = 0;
  for (const c of changes) {
    if (c.added != null || c.removed != null) any = true;
    added += c.added ?? 0;
    removed += c.removed ?? 0;
  }
  return any ? { added, removed } : null;
}

/** The first changed lines of a diff, for the hover preview. */
export function previewLines(change: FileChange, max = 8): { kind: "added" | "removed"; text: string }[] {
  if (change.binary) return [];
  const out: { kind: "added" | "removed"; text: string }[] = [];
  for (const r of parseUnified(change.diff)) {
    if (r.kind !== "added" && r.kind !== "removed") continue;
    out.push({ kind: r.kind, text: r.text });
    if (out.length >= max) break;
  }
  return out;
}

function Preview({ change, id }: { change: FileChange; id: string }) {
  const lines = previewLines(change);
  return (
    <div
      id={id}
      role="tooltip"
      data-testid="changed-preview"
      className="pointer-events-none absolute left-0 top-full z-20 mt-1 w-[min(32rem,calc(100vw-5rem))] overflow-hidden rounded-xl border border-white/[0.08] bg-ink-900 py-1 font-mono text-[12px] leading-relaxed shadow-card-hover"
    >
      {change.binary ? (
        <p className="px-3 py-1 font-sans text-zinc-500">binary file</p>
      ) : lines.length === 0 ? (
        <p className="px-3 py-1 font-sans text-zinc-500">
          {change.note || "No line changes to show."}
        </p>
      ) : (
        lines.map((l, i) => (
          <div
            key={i}
            data-kind={l.kind}
            className={`truncate px-3 ${l.kind === "added" ? "bg-tone-success/10" : "bg-tone-danger/10"}`}
          >
            <span className={l.kind === "added" ? "text-tone-success" : "text-tone-danger"}>
              {l.kind === "added" ? "+" : "−"}
            </span>{" "}
            <span className="text-zinc-300">{l.text}</span>
          </div>
        ))
      )}
    </div>
  );
}

function Row({
  change,
  index,
  onOpen,
  undo,
  undoing,
  onUndo,
}: {
  change: FileChange;
  index: number;
  onOpen: (path: string) => void;
  /** The journal's match for this file; null = no Undo offered (never a guess). */
  undo: ChangeUndoState | null;
  /** Some row's undo is running (one at a time). */
  undoing: string | null;
  onUndo: (actionId: string, path: string) => void;
}) {
  const [peek, setPeek] = useState(false);
  const tipId = useId();
  const label = change.rel || change.name || change.path;
  const base = change.name || label.split("/").pop() || label;
  return (
    <li
      data-testid={`changed-file-${index}`}
      className="relative flex min-w-0 items-center gap-2"
      onMouseEnter={() => setPeek(true)}
      onMouseLeave={() => setPeek(false)}
    >
      <span className="w-16 shrink-0 text-zinc-600">{statusWord(change.status)}</span>
      <button
        type="button"
        onClick={() => onOpen(change.path)}
        onFocus={() => setPeek(true)}
        onBlur={() => setPeek(false)}
        aria-describedby={peek ? tipId : undefined}
        title={change.path}
        className="min-w-0 truncate rounded text-left text-zinc-400 underline decoration-dotted underline-offset-[3px] transition-colors hover:text-zinc-200"
      >
        {label}
      </button>
      <Counts added={change.added} removed={change.removed} />
      {change.binary && <span className="shrink-0 text-zinc-600">binary</span>}
      {change.changed_since && (
        <span data-testid={`changed-since-${index}`} className="shrink-0 text-tone-warn">
          changed since
        </span>
      )}
      {change.undone && <span className="shrink-0 text-zinc-600">undone</span>}
      {undo && !change.undone && (
        <button
          type="button"
          data-testid={`changed-undo-${index}`}
          onClick={() => onUndo(undo.actionId, change.path)}
          disabled={!undo.undoable || undoing !== null}
          aria-label={`Undo the change to ${base}`}
          title={undo.undoable ? `Undo this change to ${base}` : `Can't undo: ${undo.reason ?? "not undoable"}`}
          className="inline-flex shrink-0 items-center gap-1 rounded px-1 text-zinc-500 transition-colors hover:bg-white/[0.06] hover:text-zinc-200 disabled:opacity-40"
        >
          {undoing === change.path ? (
            <Loader2 size={12} className="animate-spin" />
          ) : (
            <Undo2 size={12} />
          )}
          Undo
        </button>
      )}
      {peek && <Preview change={change} id={tipId} />}
    </li>
  );
}

export function ChangedFiles({
  changes,
  onOpen,
  more = false,
  undoFor,
  onUndo,
  onUndone,
}: {
  changes: FileChange[];
  /** Open a file's diff. Omitted: this component opens `DiffView` itself. */
  onOpen?: (path: string) => void;
  /** The daemon listed only the first files (`truncated_files`). */
  more?: boolean;
  /** The caller's undo journal match for a file (the receipt's `undoFor`).
   *  Undo renders only for a matched file, and only with `onUndo` too. */
  undoFor?: (path: string) => ChangeUndoState | null | undefined;
  /** Performs the undo (the caller owns the confirm + POST). A rejection is
   *  shown under the list: a failed undo must never look like it happened. */
  onUndo?: (actionId: string, path: string) => void | Promise<void>;
  /** Called after `onUndo` settled without an error, so the caller can read
   *  the turn's changes again. */
  onUndone?: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [viewing, setViewing] = useState<string | null>(null);
  const [undoing, setUndoing] = useState<string | null>(null);
  const [undoErr, setUndoErr] = useState<string | null>(null);
  const listId = useId();
  const sum = useMemo(() => totals(changes), [changes]);
  if (!changes.length) return null;

  const openFile = onOpen ?? ((path: string) => setViewing(path));
  const shown = viewing ? changes.find((c) => c.path === viewing) : undefined;
  const warn = changes.some((c) => c.changed_since);

  async function runUndo(actionId: string, path: string) {
    if (!onUndo || undoing) return;
    setUndoing(path);
    setUndoErr(null);
    try {
      await onUndo(actionId, path);
      onUndone?.();
    } catch (e) {
      setUndoErr(e instanceof Error ? e.message : String(e));
    } finally {
      setUndoing(null);
    }
  }
  const undoOf = (path: string): ChangeUndoState | null =>
    undoFor && onUndo ? (undoFor(path) ?? null) : null;

  return (
    <div data-testid="changed-files" className="text-[13px] text-zinc-500">
      <button
        type="button"
        data-testid="changed-files-line"
        aria-expanded={open}
        aria-controls={listId}
        onClick={() => setOpen((v) => !v)}
        className="-mx-1.5 inline-flex max-w-full items-center gap-2 rounded-lg px-1.5 py-0.5 transition-colors hover:bg-white/[0.06] hover:text-zinc-300"
      >
        <FileDiff size={14} className="shrink-0" />
        <span>{filesWord(changes.length)}</span>
        {sum && <Counts added={sum.added} removed={sum.removed} />}
        {warn && !open && <span className="text-tone-warn">· changed since</span>}
        {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
      </button>
      {open && (
        <ul id={listId} className="mt-1 grid gap-1 pl-[22px]">
          {changes.map((c, i) => (
            <Row
              key={c.path}
              change={c}
              index={i}
              onOpen={openFile}
              undo={undoOf(c.path)}
              undoing={undoing}
              onUndo={(id, p) => void runUndo(id, p)}
            />
          ))}
          {more && (
            <li className="text-zinc-600">Only the first {changes.length} files are listed.</li>
          )}
          {undoErr && (
            <li data-testid="changed-undo-error" className="text-tone-danger">
              {undoErr}
            </li>
          )}
        </ul>
      )}
      {shown && <DiffView change={shown} onClose={() => setViewing(null)} />}
    </div>
  );
}

export default ChangedFiles;
