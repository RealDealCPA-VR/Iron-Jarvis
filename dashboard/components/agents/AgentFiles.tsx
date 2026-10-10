"use client";

/**
 * An agent's FOLDER (v1.297.0) — its instructions file, with revisions, and
 * a private notebook.
 *
 * Until now a custom agent's instructions lived only inside the persona
 * prompt under "New & manage", and nothing it learned mid-run survived the
 * session. The daemon now keeps a folder per custom agent
 * (`GET /agents/{name}/files`): an instructions file every edit of which is
 * kept as a revision (PUT …/files/instructions {text, reason?}), and a
 * notebook the agent writes to itself between runs (PUT …/files/notes) —
 * injected into its runs, trimmed to 4,000 characters.
 *
 * This panel is where the folder is READ and EDITED: the path on disk (with
 * an Open button while the daemon has the open route), a read-only preview
 * of the instructions with Edit → textarea + reason → Save, the notebook,
 * and the revision history — View shows a revision in full with a line diff
 * against what is current; Restore (confirmed inline) makes it current again.
 *
 * An OLDER DAEMON has no files route: the GET 404s and the whole panel
 * renders nothing — the agents room looks exactly as it did in v1.296.0.
 */

import { useEffect, useState } from "react";
import { FolderOpen, History, NotebookPen, Pencil } from "lucide-react";
import { ApiError, get, post, put } from "@/lib/api";
import { useApi } from "@/lib/useApi";
import { diffTexts, type DiffLine } from "@/lib/diff";
import type { AgentFilesView, AgentFileRevisionText } from "@/lib/types";
import { ErrorNote, LoaderInline, SuccessNote } from "@/components/ui";
import { timeAgo } from "@/lib/format";
import { GHOST_BTN, QUIET_FIELD, ROW_BTN, SECTION_LABEL, SUB_LABEL, primaryBtn } from "./teamLook";

/** How many lines the read-only preview shows before "Show all". */
export const PREVIEW_LINES = 6;

/** The notebook's size cap, as the daemon trims it. Said in the hint. */
export const NOTES_CAP = 4_000;

function errText(err: unknown): string {
  return err instanceof ApiError ? err.message : String(err);
}

/** "1.2 KB" for a revision row; bytes under 1 KB stay bytes. */
export function bytesLabel(n: number): string {
  if (!Number.isFinite(n) || n < 0) return "0 B";
  if (n < 1024) return `${n} B`;
  return `${(n / 1024).toFixed(n < 10 * 1024 ? 1 : 0)} KB`;
}

/** v1.329.0 (calm chat wave 9, K4): the folder's controls are ghosts (no
 *  border, they fill on hover) and its text blocks sit on a faint fill with
 *  no box edge. */
const BTN_QUIET = `${ROW_BTN} text-zinc-400 hover:text-zinc-100`;
const PRE_CLS =
  "max-h-64 overflow-auto whitespace-pre-wrap rounded-lg bg-white/[0.03] p-3 font-mono text-[12px] leading-relaxed text-zinc-300";

/**
 * The two-colour line diff every "before → after" surface of the agents room
 * draws (the coach reuses it). Same palette as the document preview's
 * compare view: added green, removed red, context muted.
 */
export function DiffBlock({ lines, testId }: { lines: DiffLine[]; testId?: string }) {
  return (
    <div
      data-testid={testId}
      className="max-h-72 overflow-auto rounded-lg bg-white/[0.03] p-2 font-mono text-[12px] leading-relaxed"
    >
      {lines.map((l, i) => (
        <div
          key={i}
          data-kind={l.kind}
          className={`whitespace-pre-wrap px-1 ${
            l.kind === "added"
              ? "bg-tone-success/[0.08] text-tone-success"
              : l.kind === "removed"
                ? "bg-tone-danger/[0.08] text-tone-danger"
                : "text-zinc-500"
          }`}
        >
          {(l.kind === "added" ? "+ " : l.kind === "removed" ? "− " : "  ") + l.text}
        </div>
      ))}
    </div>
  );
}

/* ------------------------------------------------------------- revisions --- */

function RevisionRow({
  name,
  id,
  at,
  reason,
  bytes,
  current,
  onRestored,
}: {
  name: string;
  id: string;
  at: string;
  reason: string;
  bytes: number;
  current: string;
  onRestored: () => void;
}) {
  const [shown, setShown] = useState<AgentFileRevisionText | null>(null);
  const [viewBusy, setViewBusy] = useState(false);
  const [armed, setArmed] = useState(false);
  const [restoring, setRestoring] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const base = `/agents/${encodeURIComponent(name)}/files/revisions/${encodeURIComponent(id)}`;

  // The arm times out like ConfirmButton's: a stray click never restores.
  useEffect(() => {
    if (!armed) return;
    const t = setTimeout(() => setArmed(false), 3000);
    return () => clearTimeout(t);
  }, [armed]);

  async function view() {
    if (shown) {
      setShown(null);
      return;
    }
    setViewBusy(true);
    setError(null);
    try {
      setShown(await get<AgentFileRevisionText>(base));
    } catch (err) {
      setError(errText(err));
    } finally {
      setViewBusy(false);
    }
  }

  async function restore() {
    if (!armed) {
      setArmed(true);
      return;
    }
    setRestoring(true);
    setError(null);
    try {
      await post(`${base}/restore`);
      setArmed(false);
      setShown(null);
      onRestored();
    } catch (err) {
      setError(errText(err));
    } finally {
      setRestoring(false);
    }
  }

  return (
    <li data-testid={`files-revision-${id}`} className="px-1 py-1.5">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[12px]">
        <span className="shrink-0 tabular-nums text-zinc-500" title={at}>
          {timeAgo(at)}
        </span>
        <span className="min-w-0 flex-1 truncate text-zinc-300" title={reason}>
          {reason || <span className="text-zinc-500">no reason given</span>}
        </span>
        <span className="shrink-0 text-[11px] tabular-nums text-zinc-500">{bytesLabel(bytes)}</span>
        <button type="button" onClick={() => void view()} disabled={viewBusy} className={BTN_QUIET}>
          {viewBusy ? "Loading…" : shown ? "Hide" : "View"}
        </button>
        <button
          type="button"
          data-testid={`files-restore-${id}`}
          onClick={() => void restore()}
          disabled={restoring}
          className={`${ROW_BTN} ${
            armed ? "bg-tone-warn/15 text-tone-warn" : "text-zinc-400 hover:text-tone-warn"
          }`}
        >
          {restoring ? "Restoring…" : armed ? "Restore this one?" : "Restore"}
        </button>
      </div>
      {shown && (
        <div className="mt-1.5 space-y-1.5">
          <pre data-testid={`files-revision-text-${id}`} className={PRE_CLS}>
            {shown.text}
          </pre>
          <p className={SUB_LABEL}>Then → now</p>
          <DiffBlock lines={diffTexts(shown.text, current)} testId={`files-revision-diff-${id}`} />
        </div>
      )}
      {error && <p className="mt-0.5 text-[12px] text-tone-danger">{error}</p>}
    </li>
  );
}

/* ----------------------------------------------------------------- panel --- */

/**
 * `<AgentFiles name="analyst" />` — `name` is the BARE slug of a custom
 * agent, exactly as the daemon keys the folder.
 */
export function AgentFiles({ name }: { name: string }) {
  const path = `/agents/${encodeURIComponent(name)}/files`;
  const { data, error, reload } = useApi<AgentFilesView>(path);

  // Instructions: preview / edit.
  const [showAll, setShowAll] = useState(false);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [reason, setReason] = useState("");
  const [saveBusy, setSaveBusy] = useState(false);
  const [saveNote, setSaveNote] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);

  // Notebook.
  const [notes, setNotes] = useState("");
  const [notesBusy, setNotesBusy] = useState(false);
  const [notesNote, setNotesNote] = useState<string | null>(null);
  const [notesError, setNotesError] = useState<string | null>(null);

  // Open: offered until the daemon says it has no such route.
  const [openSupported, setOpenSupported] = useState(true);
  const [openBusy, setOpenBusy] = useState(false);
  const [openError, setOpenError] = useState<string | null>(null);

  // Follow the server's text when it changes under us (a restore, the agent
  // writing its notebook) — but never over an edit in progress.
  const serverInstructions = data?.instructions ?? "";
  const serverNotes = data?.notes ?? "";
  useEffect(() => {
    if (!editing) setDraft(serverInstructions);
  }, [serverInstructions, editing]);
  useEffect(() => {
    setNotes(serverNotes);
  }, [serverNotes]);

  // An older daemon (404) — or no answer yet — draws nothing at all.
  if (error && error.status === 404) return null;
  if (!data) return null;

  const lines = serverInstructions.split(/\r?\n/);
  const clipped = !showAll && lines.length > PREVIEW_LINES;
  const preview = clipped ? lines.slice(0, PREVIEW_LINES).join("\n") : serverInstructions;

  async function saveInstructions() {
    if (saveBusy) return;
    setSaveBusy(true);
    setSaveError(null);
    setSaveNote(null);
    try {
      const body: { text: string; reason?: string } = { text: draft };
      if (reason.trim()) body.reason = reason.trim();
      await put<AgentFilesView>(`${path}/instructions`, body);
      setEditing(false);
      setReason("");
      setSaveNote("Saved. The old text is kept in History.");
      reload();
    } catch (err) {
      setSaveError(errText(err));
    } finally {
      setSaveBusy(false);
    }
  }

  async function saveNotes() {
    if (notesBusy) return;
    setNotesBusy(true);
    setNotesError(null);
    setNotesNote(null);
    try {
      await put<AgentFilesView>(`${path}/notes`, { text: notes });
      setNotesNote("Notebook saved.");
      reload();
    } catch (err) {
      setNotesError(errText(err));
    } finally {
      setNotesBusy(false);
    }
  }

  async function openFolder() {
    if (openBusy) return;
    setOpenBusy(true);
    setOpenError(null);
    try {
      await post(`${path}/open`);
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) setOpenSupported(false);
      else setOpenError(errText(err));
    } finally {
      setOpenBusy(false);
    }
  }

  const notesDirty = notes !== serverNotes;

  return (
    // v1.329.0: a plain section under a hairline with a quiet label; its
    // parts (Instructions, Notebook, History) are sub-labels, not boxes.
    <section data-testid={`files-${name}`} className="space-y-4 border-t hairline pt-4">
      <div className="flex items-center gap-2 px-1">
        <h3 className={SECTION_LABEL}>Folder</h3>
        <span
          className="ml-auto min-w-0 truncate font-mono text-[11px] text-zinc-500"
          title={data.folder}
        >
          {data.folder}
        </span>
        {openSupported && (
          <button
            type="button"
            onClick={() => void openFolder()}
            disabled={openBusy}
            title="Open the folder on this computer"
            className={BTN_QUIET}
          >
            <FolderOpen size={12} aria-hidden /> {openBusy ? "Opening…" : "Open"}
          </button>
        )}
      </div>
      {openError && <ErrorNote>{openError}</ErrorNote>}

      {/* Instructions */}
      <div className="space-y-1.5">
        <div className="flex items-center gap-2 px-1">
          <span className={SUB_LABEL}>Instructions</span>
          <span className="ml-auto" />
          {!editing && (
            <button
              type="button"
              data-testid={`files-edit-${name}`}
              onClick={() => {
                setDraft(serverInstructions);
                setSaveNote(null);
                setSaveError(null);
                setEditing(true);
              }}
              className={BTN_QUIET}
            >
              <Pencil size={11} aria-hidden /> Edit
            </button>
          )}
        </div>
        {editing ? (
          <div className="space-y-2">
            <textarea
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              rows={10}
              aria-label="Instructions"
              className={`${QUIET_FIELD} resize-y font-mono leading-relaxed`}
            />
            <input
              type="text"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              aria-label="Reason (optional)"
              placeholder="Why (optional)"
              className={QUIET_FIELD}
            />
            <div className="flex items-center justify-end gap-2">
              <button
                type="button"
                onClick={() => {
                  setEditing(false);
                  setReason("");
                }}
                disabled={saveBusy}
                className={GHOST_BTN}
              >
                Cancel
              </button>
              <button
                type="button"
                data-testid={`files-save-${name}`}
                onClick={() => void saveInstructions()}
                disabled={saveBusy || draft === serverInstructions}
                className={primaryBtn(!saveBusy && draft !== serverInstructions)}
              >
                {saveBusy ? <LoaderInline label="Saving…" /> : "Save"}
              </button>
            </div>
          </div>
        ) : serverInstructions.trim() ? (
          <>
            <pre data-testid={`files-preview-${name}`} className={PRE_CLS}>
              {preview}
              {clipped ? "\n…" : ""}
            </pre>
            {lines.length > PREVIEW_LINES && (
              <button
                type="button"
                onClick={() => setShowAll((v) => !v)}
                className="px-1 text-[12px] text-accent-soft transition-colors hover:text-accent"
              >
                {showAll ? "Show less" : `Show all (${lines.length} lines)`}
              </button>
            )}
          </>
        ) : (
          <p className="px-1 text-[12px] text-zinc-500">
            No instructions yet. Press Edit to give {name} its standing orders.
          </p>
        )}
        {saveNote && <SuccessNote>{saveNote}</SuccessNote>}
        {saveError && <ErrorNote>{saveError}</ErrorNote>}
      </div>

      {/* Notebook */}
      <div className="space-y-1.5">
        <div className="flex items-center gap-2 px-1">
          <NotebookPen size={12} className="text-zinc-500" aria-hidden />
          <span className={SUB_LABEL}>Notebook</span>
        </div>
        <textarea
          data-testid={`files-notes-${name}`}
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          rows={4}
          aria-label="Notebook"
          placeholder={`${name}'s own notes. It writes here too.`}
          className={`${QUIET_FIELD} resize-y leading-relaxed`}
        />
        <div className="flex items-center gap-2">
          <p className="min-w-0 flex-1 px-1 text-[11px] leading-relaxed text-zinc-500">
            Injected into its runs, trimmed to {NOTES_CAP.toLocaleString()} characters.
            {notes.length > NOTES_CAP && (
              <span className="text-tone-warn"> {notes.length.toLocaleString()} now.</span>
            )}
          </p>
          <button
            type="button"
            data-testid={`files-notes-save-${name}`}
            onClick={() => void saveNotes()}
            disabled={notesBusy || !notesDirty}
            className={primaryBtn(!notesBusy && notesDirty)}
          >
            {notesBusy ? <LoaderInline label="Saving…" /> : "Save"}
          </button>
        </div>
        {notesNote && <SuccessNote>{notesNote}</SuccessNote>}
        {notesError && <ErrorNote>{notesError}</ErrorNote>}
      </div>

      {/* History */}
      {(data.revisions ?? []).length > 0 && (
        <div className="space-y-1">
          <div className="flex items-center gap-2 px-1">
            <History size={12} className="text-zinc-500" aria-hidden />
            <span className={SUB_LABEL}>History · {data.revisions.length}</span>
          </div>
          <ul className="divide-y divide-white/[0.06] border-t hairline">
            {data.revisions.map((r) => (
              <RevisionRow
                key={r.id}
                name={name}
                id={r.id}
                at={r.at}
                reason={r.reason}
                bytes={r.bytes}
                current={serverInstructions}
                onRestored={reload}
              />
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}

export default AgentFiles;
