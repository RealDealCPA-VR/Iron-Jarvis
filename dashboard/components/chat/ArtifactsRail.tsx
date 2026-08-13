"use client";

// The ARTIFACTS RAIL — a persistent home for every file this conversation has
// produced. Today a turn that writes a file (redaction output, generated doc,
// repl artifact) reports absolute paths in `documents`, but once that message
// scrolls away the only record is a sentence — the user reported hunting the
// filesystem for a file the app itself created. This rail lists them all,
// newest first, each row offering preview (wired by the coordinator to the
// existing DocPreview), copy-the-full-path, and open-in-native-app (the same
// POST /documents/open the preview header uses — explicit, user-initiated).
// Two OPTIONAL per-row affordances exist so this rail can REPLACE the
// v1.153.2 "Files in this chat" block without regressing it: `downloadHref`
// (the block's download anchor) and `onDismiss` (the thread-doc ×). Both
// absent → rendering is byte-identical to the rail without them.
//
// Honesty rules carried over from DocPreview/DraftCard: the copy check only
// shows when writeText actually resolved (an absent clipboard is a silent
// no-op, never a throw and never a fake check), and an open failure prints the
// daemon's error instead of pretending the app launched.

import { useEffect, useMemo, useRef, useState } from "react";
import {
  BookmarkPlus,
  Check,
  Copy,
  Download,
  ExternalLink,
  File,
  FileAudio,
  FileCode,
  FileImage,
  FileSpreadsheet,
  FileText,
  FileVideo,
  Files,
  Loader2,
  ScrollText,
  Undo2,
  X,
} from "lucide-react";
import { post, ApiError } from "@/lib/api";

/** One file the conversation produced. `path` is ABSOLUTE — that is the whole
 *  point (v1.153.2 made every writing tool report absolute paths; this rail is
 *  where those reports stop being sentences). */
export interface ArtifactItem {
  path: string;
  /** Index of the LAST chat turn that produced/mentioned this file. */
  turnIndex?: number;
}

export interface ArtifactsRailProps {
  /**
   * DISPLAY order: newest first (what `collectArtifacts` returns). May still
   * carry duplicates if the coordinator concatenates naively — the rail keeps
   * the FIRST occurrence of a path, which in newest-first orientation is the
   * newest turn's mention.
   */
  items: ArtifactItem[];
  /** Coordinator wires this to the existing DocPreview right-rail. */
  onPreview: (path: string) => void;
  /** When provided, the header grows a dismiss X. */
  onClose?: () => void;
  /**
   * When provided, each row grows a download anchor whose href comes from this
   * callback (called with the FULL path — the coordinator builds the
   * `/documents/file?path=…&token=…` URL, keeping API_BASE/token knowledge out
   * of this component). The anchor's `download` attribute is the basename, so
   * the browser saves under the file's own name. Absent → no anchor at all
   * (rendering identical to the pre-extension rail).
   */
  downloadHref?: (path: string) => string;
  /**
   * When provided, each row grows a × that forgets the file on this thread
   * (the coordinator wires it to dismissThreadDoc). Called with the FULL path
   * — the thread's document list is keyed by exact path, so a basename here
   * would silently fail to remove anything. Absent → no button.
   */
  onDismiss?: (path: string) => void;
  /**
   * The caller's retention cap (v1.166.0: threadDocs keeps the newest 30).
   * When the DEDUPED row count reaches it, a quiet footer says the list shows
   * only the latest N — without it a capped list reads as the complete
   * history, which is exactly the silent-truncation lie this repo bans.
   * Absent or 0 → no footer ever (rendering identical to the pre-cap rail).
   */
  cap?: number;
  /**
   * v1.168.0 — "Undo this write" where the user is looking. The caller joins
   * the undo journal (GET /undo rows carrying workspace+path) to this row's
   * absolute path and returns the match, or null/undefined when the row could
   * not be matched — an UNMATCHED file gets NO undo affordance at all (a
   * button whose target the journal cannot name would be a guess). A matched
   * but not-undoable row renders the button DISABLED with the honest reason
   * as its title. Requires `onUndo` too; either absent → no button, rendering
   * identical to the pre-v1.168.0 rail.
   */
  undoFor?: (path: string) => ArtifactUndoState | null | undefined;
  /**
   * Performs the undo (the caller owns the explicit confirm + POST /undo/{id}
   * + list/preview refresh). Called with the journal row's action id and the
   * row's FULL path. A rejection is surfaced on the rail's error line — a
   * failed undo must never look like it happened.
   */
  onUndo?: (actionId: string, path: string) => void | Promise<void>;
  /**
   * v1.168.0 — "Add to project knowledge" per row. The caller posts the file
   * through the EXISTING knowledge path (POST /projects/{id}/knowledge); a
   * rejection lands on the rail's error line, a resolve flashes a check.
   * Absent → no button.
   */
  onPromote?: (path: string) => void | Promise<void>;
  /**
   * When set, the promote button renders DISABLED with this reason as its
   * title ("bind this chat to a project first") — an honest disabled state,
   * never a silent no-op.
   */
  promoteDisabledReason?: string | null;
}

/** The journal row matched to a rail item — what `undoFor` returns. */
export interface ArtifactUndoState {
  actionId: string;
  /** False → button renders disabled with `reason` as the title. */
  undoable: boolean;
  /** The honest reason a matched row cannot be undone ("already undone"…). */
  reason?: string;
  /** Journal kind — picks the confirm wording (restore vs remove-created). */
  kind?: string;
}

/** One row of GET /undo as the chat page consumes it (v1.168.0 fields). */
export interface UndoRowLike {
  action_id: string;
  kind?: string;
  undoable?: boolean;
  /** Workspace-relative target from the journal envelope; null = pathless. */
  path?: string | null;
  /** The v1.166.3 capture-time stamp; null for pre-stamp rows. */
  workspace?: string | null;
}

/**
 * JOIN identity for "is this rail item the file that journal row wrote?" —
 * separators unified to "/", duplicate separators collapsed (a UNC lead
 * "//server" survives), trailing separators stripped. Beyond that the EXACT
 * string, case-sensitively — same policy as `cleanPath` (see its doc): both
 * sides of the join come from the daemon's own resolved reporting, so their
 * casing agrees, and casefolding would wrongly merge distinct posix paths.
 */
export function normalizeFsPath(p: string): string {
  const unified = p.trim().replace(/\\/g, "/");
  const lead = unified.startsWith("//") ? "/" : "";
  const collapsed = lead + unified.replace(/\/{2,}/g, "/");
  const stripped = collapsed.replace(/\/+$/, "");
  return stripped || "/";
}

/**
 * Absolute-path → journal-row map. Rows arrive NEWEST FIRST (GET /undo order)
 * and the first row per path wins — the newest write is the one "Undo this
 * write" means. Rows without BOTH `workspace` and `path` are skipped: they
 * cannot be joined to a file, so no affordance is ever offered for them.
 */
export function joinUndoByPath(
  rows: UndoRowLike[] | null | undefined,
): Map<string, UndoRowLike> {
  const map = new Map<string, UndoRowLike>();
  for (const r of rows ?? []) {
    if (!r || typeof r.action_id !== "string" || !r.action_id) continue;
    if (typeof r.path !== "string" || !r.path.trim()) continue;
    if (typeof r.workspace !== "string" || !r.workspace.trim()) continue;
    const abs = normalizeFsPath(`${r.workspace}/${r.path}`);
    if (!map.has(abs)) map.set(abs, r);
  }
  return map;
}

/** The slice of an /events frame this module needs — structurally matches
 *  `IJEvent` (lib/types) without importing the whole shape. */
export interface RevertEventLike {
  id: string;
  type: string;
  payload?: Record<string, unknown> | null;
}

/**
 * Action ids reverted in frames NEWER than `boundaryId` (v1.168.0 fix). An
 * undo performed on another surface (Timeline page, a second window) publishes
 * `action.reverted`; without reacting to it the rail keeps offering a live
 * "Undo this write" whose POST can only 409. Events arrive NEWEST FIRST
 * (useEvents prepends); the scan stops at the boundary id so a re-render never
 * re-processes old frames, and a null boundary (first frame batch) scans them
 * all. Frames without a string `action_id` are skipped — no id, no marking.
 */
export function revertedActionIds(
  events: RevertEventLike[],
  boundaryId: string | null,
): string[] {
  const out: string[] = [];
  for (const e of events) {
    if (e.id === boundaryId) break; // frames already processed
    if (e.type !== "action.reverted") continue;
    const aid = e.payload?.action_id;
    if (typeof aid === "string" && aid) out.push(aid);
  }
  return out;
}

/**
 * The explicit-confirm wording (window.confirm — the app's convention, see
 * DocPreview's overwrite prompt) shared by every undo call site. It says what
 * will actually happen: a `file_delete`/`files_delete` journal row means the
 * chat CREATED the file, so its undo REMOVES it — wording that promised a
 * "restore" there would confirm the user into a deletion.
 */
export function confirmUndoPrompt(kind: string | undefined, base: string): string {
  return kind === "file_delete" || kind === "files_delete"
    ? `Undo this write? ${base} was created by the chat and will be removed.`
    : `Undo this write? ${base} will be restored to its content from before the write.`;
}

/**
 * DEDUPE IDENTITY. Trim whitespace and strip trailing path separators —
 * `C:\x\a.pdf` and `C:\x\a.pdf\` are one file, and because basename/parentDir
 * already ignore trailing separators the two would otherwise render as two
 * IDENTICAL-looking rows. A path that is nothing but separators (root "/")
 * survives untouched. Beyond that, identity is the EXACT string —
 * case-SENSITIVE even though NTFS is not: the daemon's writing tools report
 * each path with one consistent casing, the v1.153.2 inline block and
 * `threadDocs` (preview/dismiss keys) both compare exact strings, and
 * casefolding here would wrongly merge genuinely distinct posix paths. So a
 * case-variant duplicate stays two rows, by policy.
 */
function cleanPath(raw: unknown): string {
  if (typeof raw !== "string") return "";
  const trimmed = raw.trim();
  const stripped = trimmed.replace(/[\\/]+$/, "");
  return stripped || trimmed;
}

/**
 * Flatten per-message `documents` arrays (chat order: index 0 = oldest turn)
 * into the rail's deduped, NEWEST-FIRST list. LAST occurrence wins: a path
 * produced in turn 2 and again in turn 7 appears once, at the turn-7 position,
 * with `turnIndex` = 7 — the newest turn's mention is the one shown. Turns
 * without documents pass `undefined` and are skipped; turn indices are the
 * original array positions, never compacted. Dedupe identity is `cleanPath`
 * (exact string after trailing-separator strip — see its doc for the case
 * policy). Cost is linear: one Map pass over every entry, delete+set is how a
 * re-seen key moves to the newest insertion position.
 */
export function collectArtifacts(
  perTurnDocs: (string[] | undefined)[],
): ArtifactItem[] {
  // Map insertion order tracks the order of LAST occurrence: a re-seen path is
  // deleted and re-inserted, so reversing at the end yields newest-first.
  const last = new Map<string, number>();
  perTurnDocs.forEach((docs, turnIndex) => {
    if (!docs) return;
    for (const raw of docs) {
      const path = cleanPath(raw);
      if (!path) continue;
      if (last.has(path)) last.delete(path);
      last.set(path, turnIndex);
    }
  });
  return Array.from(last, ([path, turnIndex]) => ({ path, turnIndex })).reverse();
}

/** Final path segment — the row label. Handles / and \ (daemon paths are
 *  Windows on the user's install, posix in tests and on other setups). */
export function basename(path: string): string {
  const parts = path.split(/[\\/]/).filter(Boolean);
  return parts[parts.length - 1] ?? path;
}

/** Everything above the basename — the dim location hint. "" when the path has
 *  no separators (nothing useful to show). */
export function parentDir(path: string): string {
  const trimmed = path.replace(/[\\/]+$/, "");
  const i = Math.max(trimmed.lastIndexOf("/"), trimmed.lastIndexOf("\\"));
  if (i < 0) return "";
  return trimmed.slice(0, i) || trimmed.slice(0, i + 1); // keep root "/"
}

export type FileKind =
  | "doc"
  | "sheet"
  | "image"
  | "text"
  | "code"
  | "video"
  | "audio"
  | "file";

const DOC_EXT = new Set(["pdf", "doc", "docx", "ppt", "pptx", "rtf", "odt"]);
const SHEET_EXT = new Set(["xls", "xlsx", "xlsm", "csv", "tsv", "ods"]);
const IMAGE_EXT = new Set([
  "png", "jpg", "jpeg", "webp", "gif", "svg", "bmp", "ico", "tiff", "tif",
  "heic", "avif",
]);
const TEXT_EXT = new Set(["md", "txt", "log"]);
const CODE_EXT = new Set([
  "py", "js", "ts", "tsx", "jsx", "json", "html", "css", "sh", "ps1",
  "bat", "yaml", "yml", "toml", "sql", "ipynb", "xml",
]);
// The pixio tools write generated media into the workspace — those artifacts
// land in this rail too and deserve better than the generic page icon.
const VIDEO_EXT = new Set(["mp4", "mov", "webm", "mkv", "avi"]);
const AUDIO_EXT = new Set(["mp3", "wav", "m4a", "flac", "ogg", "aac"]);

/** Icon bucket for a path, by extension (case-insensitive). A dotfile like
 *  `.env` has no extension for this purpose — it falls through to "file". */
export function fileKind(path: string): FileKind {
  const base = basename(path);
  const dot = base.lastIndexOf(".");
  const ext = dot > 0 ? base.slice(dot + 1).toLowerCase() : "";
  if (DOC_EXT.has(ext)) return "doc";
  if (SHEET_EXT.has(ext)) return "sheet";
  if (IMAGE_EXT.has(ext)) return "image";
  if (TEXT_EXT.has(ext)) return "text";
  if (CODE_EXT.has(ext)) return "code";
  if (VIDEO_EXT.has(ext)) return "video";
  if (AUDIO_EXT.has(ext)) return "audio";
  return "file";
}

const KIND_ICON: Record<FileKind, typeof File> = {
  doc: FileText,
  sheet: FileSpreadsheet,
  image: FileImage,
  text: ScrollText,
  code: FileCode,
  video: FileVideo,
  audio: FileAudio,
  file: File,
};

export function ArtifactsRail({
  items,
  onPreview,
  onClose,
  downloadHref,
  onDismiss,
  cap,
  undoFor,
  onUndo,
  onPromote,
  promoteDisabledReason,
}: ArtifactsRailProps) {
  // Defensive dedupe (first occurrence wins — items arrive newest-first) so a
  // coordinator that concatenates per-turn lists still shows each file once.
  // Same identity as collectArtifacts: cleanPath (trailing-separator strip,
  // exact string otherwise — see cleanPath's doc for the case policy).
  const rows = useMemo(() => {
    const seen = new Set<string>();
    const out: ArtifactItem[] = [];
    for (const it of items) {
      const path = cleanPath(it?.path);
      if (!path || seen.has(path)) continue;
      seen.add(path);
      out.push({ ...it, path });
    }
    return out;
  }, [items]);

  const [copiedPath, setCopiedPath] = useState<string | null>(null);
  const [openingPath, setOpeningPath] = useState<string | null>(null);
  const [undoingPath, setUndoingPath] = useState<string | null>(null);
  const [promotingPath, setPromotingPath] = useState<string | null>(null);
  const [promotedPath, setPromotedPath] = useState<string | null>(null);
  const promotedTimer = useRef<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  // The check-flash timer must not outlive the rail (thread switch, last doc
  // dismissed) — it would fire setPromotedPath on an unmounted component.
  // Same cleanup pattern as PromoteKnowledgeButton in chat/page.tsx.
  useEffect(
    () => () => {
      if (promotedTimer.current !== null)
        window.clearTimeout(promotedTimer.current);
    },
    [],
  );

  // No artifacts → no chrome. An empty "Files" box in the chat column would be
  // noise on every fresh thread.
  if (rows.length === 0) return null;

  async function copyPath(path: string) {
    // Clipboard can be absent (plain-http origin, older webview) or refused.
    // Guarded both ways: no throw, and no check unless the write RESOLVED —
    // a check over a copy that never happened is a lie about the one thing
    // this button does.
    try {
      const clip = typeof navigator !== "undefined" ? navigator.clipboard : undefined;
      if (!clip?.writeText) return;
      await clip.writeText(path);
      setCopiedPath(path);
      window.setTimeout(
        () => setCopiedPath((p) => (p === path ? null : p)),
        1400,
      );
    } catch {
      // Swallowed on purpose — see above.
    }
  }

  async function openNative(path: string) {
    if (openingPath) return;
    setOpeningPath(path);
    setError(null);
    try {
      await post("/documents/open", { path });
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setOpeningPath(null);
    }
  }

  /** Run the caller's undo (which owns the confirm + POST); a rejection lands
   *  on the error line — a failed undo must never look like it happened. */
  async function runUndo(actionId: string, path: string) {
    if (!onUndo || undoingPath) return;
    setUndoingPath(path);
    setError(null);
    try {
      await onUndo(actionId, path);
    } catch (e) {
      setError(
        e instanceof ApiError || e instanceof Error ? e.message : String(e),
      );
    } finally {
      setUndoingPath(null);
    }
  }

  /** Add the file to the bound project's knowledge; the check only flashes
   *  when the caller's promise RESOLVED — same honesty rule as the copy. */
  async function runPromote(path: string) {
    if (!onPromote || promotingPath) return;
    setPromotingPath(path);
    setError(null);
    try {
      await onPromote(path);
      setPromotedPath(path);
      if (promotedTimer.current !== null)
        window.clearTimeout(promotedTimer.current);
      promotedTimer.current = window.setTimeout(
        () => setPromotedPath((p) => (p === path ? null : p)),
        1600,
      );
    } catch (e) {
      setError(
        e instanceof ApiError || e instanceof Error ? e.message : String(e),
      );
    } finally {
      setPromotingPath(null);
    }
  }

  return (
    <div className="flex min-h-0 flex-col rounded-xl border border-white/[0.06] bg-white/[0.02]">
      <div className="flex shrink-0 items-center gap-1.5 border-b border-white/[0.05] px-2.5 py-2">
        <Files size={12} className="shrink-0 text-accent-soft/80" aria-hidden />
        <span className="text-[11px] font-medium uppercase tracking-[0.1em] text-zinc-400">
          Files
        </span>
        <span className="text-[11px] text-zinc-600">
          {rows.length} file{rows.length === 1 ? "" : "s"}
        </span>
        {onClose && (
          <button
            type="button"
            onClick={onClose}
            aria-label="Close files panel"
            title="Close"
            className="ml-auto grid h-5 w-5 shrink-0 place-items-center rounded-md text-zinc-500 transition-colors hover:bg-white/[0.06] hover:text-zinc-200"
          >
            <X size={13} />
          </button>
        )}
      </div>

      <ul className="min-h-0 flex-1 overflow-y-auto p-1">
        {rows.map((it) => {
          const base = basename(it.path);
          const dir = parentDir(it.path);
          const Icon = KIND_ICON[fileKind(it.path)];
          // Undo renders ONLY for a row the caller matched to a journal entry
          // by path — an unmatched file gets no affordance, never a guess.
          const undoState =
            undoFor && onUndo ? (undoFor(it.path) ?? null) : null;
          return (
            <li
              key={it.path}
              className="group flex min-w-0 items-center gap-0.5 rounded-lg px-0.5 transition-colors hover:bg-white/[0.03]"
            >
              <button
                type="button"
                onClick={() => onPreview(it.path)}
                title={it.path}
                className="flex min-w-0 flex-1 items-center gap-2 rounded-md px-1 py-1 text-left"
              >
                <Icon
                  size={13}
                  className="shrink-0 text-zinc-500 transition-colors group-hover:text-accent-soft/80"
                  aria-hidden
                />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-[12px] text-zinc-300">
                    {base}
                  </span>
                  {dir && (
                    <span className="block truncate text-[10.5px] text-zinc-600">
                      {dir}
                    </span>
                  )}
                </span>
              </button>
              <button
                type="button"
                onClick={() => void copyPath(it.path)}
                aria-label={`Copy path to ${base}`}
                title={`Copy the full path\n${it.path}`}
                className="grid h-6 w-6 shrink-0 place-items-center rounded-md text-zinc-500 transition-colors hover:bg-white/[0.06] hover:text-accent-soft"
              >
                {copiedPath === it.path ? (
                  <Check
                    size={12}
                    data-testid="copied-check"
                    className="text-emerald-400"
                  />
                ) : (
                  <Copy size={12} />
                )}
              </button>
              <button
                type="button"
                onClick={() => void openNative(it.path)}
                disabled={openingPath !== null}
                aria-label={`Open ${base}`}
                title={`Open ${base} in its app`}
                className="grid h-6 w-6 shrink-0 place-items-center rounded-md text-zinc-500 transition-colors hover:bg-white/[0.06] hover:text-accent-soft disabled:opacity-40"
              >
                {openingPath === it.path ? (
                  <Loader2 size={12} className="animate-spin" />
                ) : (
                  <ExternalLink size={12} />
                )}
              </button>
              {undoState && (
                // "Undo this write" WHERE THE FILE IS LISTED (v1.168.0). A
                // matched-but-not-undoable row stays visible, disabled, with
                // the honest reason as its title — greying beats vanishing,
                // which would read as "this was never undoable".
                <button
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation();
                    void runUndo(undoState.actionId, it.path);
                  }}
                  disabled={!undoState.undoable || undoingPath !== null}
                  aria-label={`Undo the write to ${base}`}
                  title={
                    undoState.undoable
                      ? `Undo this write — revert ${base}`
                      : `Can't undo: ${undoState.reason ?? "not undoable"}`
                  }
                  className="grid h-6 w-6 shrink-0 place-items-center rounded-md text-zinc-500 transition-colors hover:bg-white/[0.06] hover:text-amber-300 disabled:opacity-40"
                >
                  {undoingPath === it.path ? (
                    <Loader2 size={12} className="animate-spin" />
                  ) : (
                    <Undo2 size={12} />
                  )}
                </button>
              )}
              {onPromote && (
                // "Add to project knowledge" (v1.168.0) — disabled with the
                // honest reason when no project is bound, never a silent no-op.
                <button
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation();
                    void runPromote(it.path);
                  }}
                  disabled={!!promoteDisabledReason || promotingPath !== null}
                  aria-label={`Add ${base} to project knowledge`}
                  title={
                    promoteDisabledReason ?? `Add ${base} to project knowledge`
                  }
                  className="grid h-6 w-6 shrink-0 place-items-center rounded-md text-zinc-500 transition-colors hover:bg-white/[0.06] hover:text-accent-soft disabled:opacity-40"
                >
                  {promotingPath === it.path ? (
                    <Loader2 size={12} className="animate-spin" />
                  ) : promotedPath === it.path ? (
                    <Check
                      size={12}
                      data-testid="promoted-check"
                      className="text-emerald-400"
                    />
                  ) : (
                    <BookmarkPlus size={12} />
                  )}
                </button>
              )}
              {downloadHref && (
                // Sized like the v1.153.2 inline block's download anchor
                // (h-5 w-5, icon 12) — this rail replaces that block and the
                // affordance must not shrink or move under the user's hand.
                // stopPropagation: the preview button is a SIBLING today, but
                // a coordinator wrapping the row in a click target must never
                // turn "save this file" into "also open the preview".
                <a
                  href={downloadHref(it.path)}
                  download={base}
                  onClick={(e) => e.stopPropagation()}
                  aria-label={`Download ${base}`}
                  title={`Download ${base}`}
                  className="grid h-5 w-5 shrink-0 place-items-center rounded-md text-zinc-500 transition-colors hover:bg-white/[0.06] hover:text-accent-soft"
                >
                  <Download size={12} />
                </a>
              )}
              {onDismiss && (
                <button
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation();
                    onDismiss(it.path);
                  }}
                  aria-label={`Remove ${base} from this chat`}
                  title="Remove — forget this file on this thread"
                  className="grid h-6 w-6 shrink-0 place-items-center rounded-md text-zinc-500 transition-colors hover:bg-white/[0.06] hover:text-rose-300"
                >
                  <X size={12} />
                </button>
              )}
            </li>
          );
        })}
      </ul>

      {cap !== undefined && cap > 0 && rows.length >= cap && (
        <p className="shrink-0 px-2.5 pb-2 text-[10.5px] text-zinc-600">
          Showing the latest {cap} files — older ones rolled off this list.
        </p>
      )}

      {error && (
        <p className="shrink-0 px-2.5 pb-2 text-[11px] text-rose-300/90">{error}</p>
      )}
    </div>
  );
}
