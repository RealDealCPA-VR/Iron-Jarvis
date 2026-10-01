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

const BTN =
  "rounded-md border px-1.5 py-0.5 text-[10.5px] font-medium transition-colors disabled:opacity-50";
const BTN_QUIET = `${BTN} border-white/10 text-zinc-400 hover:border-accent/40 hover:text-accent-soft`;
const PRE_CLS =
  "max-h-64 overflow-auto whitespace-pre-wrap rounded-xl border border-white/[0.06] bg-ink-950 p-3 font-mono text-[11px] leading-relaxed text-zinc-300";

/**
 * The two-colour line diff every "before → after" surface of the agents room
 * draws (the coach reuses it). Same palette as the document preview's
 * compare view: added green, removed red, context muted.
 */
export function DiffBlock({ lines, testId }: { lines: DiffLine[]; testId?: string }) {
  return (
    <div
      data-testid={testId}
      className="max-h-72 overflow-auto rounded-xl border border-white/[0.06] bg-ink-950 p-2 font-mono text-[11px] leading-relaxed"
    >
      {lines.map((l, i) => (
        <div
          key={i}
          data-kind={l.kind}
          className={`whitespace-pre-wrap px-1 ${
            l.kind === "added"
              ? "bg-emerald-500/[0.08] text-emerald-300"
              : l.kind === "removed"
                ? "bg-rose-500/[0.08] text-rose-300/90"
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
    <li data-testid={`files-revision-${id}`} className="rounded-lg px-1.5 py-1 hover:bg-white/[0.03]">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[11.5px]">
        <span className="shrink-0 tabular-nums text-zinc-500" title={at}>
          {timeAgo(at)}
        </span>
        <span className="min-w-0 flex-1 truncate text-zinc-300" title={reason}>
          {reason || <span className="text-zinc-600">no reason given</span>}
        </span>
        <span className="shrink-0 text-[10.5px] tabular-nums text-zinc-600">{bytesLabel(bytes)}</span>
        <button type="button" onClick={() => void view()} disabled={viewBusy} className={BTN_QUIET}>
          {viewBusy ? "Loading…" : shown ? "Hide" : "View"}
        </button>
        <button
          type="button"
          data-testid={`files-restore-${id}`}
          onClick={() => void restore()}
          disabled={restoring}
          className={`${BTN} ${
            armed
              ? "border-amber-400/50 bg-amber-400/15 text-amber-200"
              : "border-white/10 text-zinc-400 hover:border-amber-400/40 hover:text-amber-200"
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
          <p className="text-[10.5px] uppercase tracking-wide text-zinc-600">
            Then → now
          </p>
          <DiffBlock lines={diffTexts(shown.text, current)} testId={`files-revision-diff-${id}`} />
        </div>
      )}
      {error && <p className="mt-0.5 text-[11px] text-rose-300">{error}</p>}
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
      setSaveNote("Saved — the old text is kept in History.");
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
    <section data-testid={`files-${name}`} className="space-y-3">
      <div className="flex items-center gap-2">
        <FolderOpen size={13} className="text-accent-soft/80" aria-hidden />
        <h3 className="text-[12px] font-semibold tracking-wide text-zinc-200">Folder</h3>
        <span
          className="ml-auto min-w-0 truncate font-mono text-[10.5px] text-zinc-500"
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
            {openBusy ? "Opening…" : "Open"}
          </button>
        )}
      </div>
      {openError && <ErrorNote>{openError}</ErrorNote>}

      {/* Instructions */}
      <div className="space-y-1.5 rounded-xl border border-white/[0.06] bg-white/[0.02] p-3">
        <div className="flex items-center gap-2">
          <span className="text-[11px] uppercase tracking-[0.1em] text-zinc-400">Instructions</span>
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
              <Pencil size={10} className="mr-1 inline-block" aria-hidden /> Edit
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
              className="field resize-y font-mono text-[11.5px] leading-relaxed"
            />
            <input
              type="text"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              aria-label="Reason (optional)"
              placeholder="why (optional)"
              className="field"
            />
            <div className="flex items-center justify-end gap-2">
              <button
                type="button"
                onClick={() => {
                  setEditing(false);
                  setReason("");
                }}
                disabled={saveBusy}
                className="btn-ghost py-1 text-[11.5px]"
              >
                Cancel
              </button>
              <button
                type="button"
                data-testid={`files-save-${name}`}
                onClick={() => void saveInstructions()}
                disabled={saveBusy || draft === serverInstructions}
                className="btn-accent px-2.5 py-1 text-[11.5px]"
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
                className="text-[11px] text-accent-soft transition-colors hover:text-accent"
              >
                {showAll ? "Show less" : `Show all (${lines.length} lines)`}
              </button>
            )}
          </>
        ) : (
          <p className="text-[11.5px] text-zinc-500">
            No instructions yet — Edit to give {name} its standing orders.
          </p>
        )}
        {saveNote && <SuccessNote>{saveNote}</SuccessNote>}
        {saveError && <ErrorNote>{saveError}</ErrorNote>}
      </div>

      {/* Notebook */}
      <div className="space-y-1.5 rounded-xl border border-white/[0.06] bg-white/[0.02] p-3">
        <div className="flex items-center gap-2">
          <NotebookPen size={12} className="text-zinc-500" aria-hidden />
          <span className="text-[11px] uppercase tracking-[0.1em] text-zinc-400">Notebook</span>
        </div>
        <textarea
          data-testid={`files-notes-${name}`}
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          rows={4}
          aria-label="Notebook"
          placeholder={`${name}'s own notes — it writes here too.`}
          className="field resize-y text-[11.5px] leading-relaxed"
        />
        <div className="flex items-center gap-2">
          <p className="min-w-0 flex-1 text-[10.5px] leading-relaxed text-zinc-600">
            Injected into its runs, trimmed to {NOTES_CAP.toLocaleString()} characters.
            {notes.length > NOTES_CAP && (
              <span className="text-amber-300/90"> {notes.length.toLocaleString()} now.</span>
            )}
          </p>
          <button
            type="button"
            data-testid={`files-notes-save-${name}`}
            onClick={() => void saveNotes()}
            disabled={notesBusy || !notesDirty}
            className="btn-accent px-2.5 py-1 text-[11.5px]"
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
          <div className="flex items-center gap-2">
            <History size={12} className="text-zinc-500" aria-hidden />
            <span className="text-[11px] font-medium uppercase tracking-wide text-zinc-500">
              History · {data.revisions.length}
            </span>
          </div>
          <ul className="space-y-0.5">
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
