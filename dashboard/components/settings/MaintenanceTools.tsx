"use client";

/**
 * Settings → Maintenance: the three things a user needs when something is
 * wrong and they are about to ask for help (v1.229.0, audit Wave 3 — OBS5,
 * D8, CL7). Before this the logs path appeared only inside a crash toast,
 * "what went wrong recently" lived in a file, and restoring a backup meant
 * the `ironjarvis restore` CLI.
 *
 *  - Copy diagnostics: ONE JSON blob — /health (version + instance),
 *    /diagnostics, /diagnostics/reliability, /diagnostics/errors — on the
 *    clipboard. An endpoint that fails is recorded as `{ error }` in the blob
 *    rather than dropped, and a copy that did not happen is never reported as
 *    one (the DraftCard rule): when no clipboard is reachable the JSON is shown
 *    in a box to select by hand, under a sentence that says so.
 *  - Open logs folder: the desktop bridge's `shell.openLogs` (IPC
 *    `shell:openLogs`, sender-checked in main.js). The button renders ONLY when
 *    that bridge exists — a browser tab has no folder to open.
 *  - Restore from backup…: lists GET /maintenance/backups, confirms by NAME and
 *    says a restart follows, then POST /maintenance/restore. The daemon refuses
 *    (409) while sessions or workflow runs are in flight; that sentence is
 *    shown as-is.
 */

import { useCallback, useEffect, useState } from "react";
import { ArchiveRestore, ClipboardCopy, FolderOpen } from "lucide-react";
import { get, post, ApiError } from "@/lib/api";
import { ErrorNote, LoaderInline, SectionLabel } from "@/components/ui";

type OpenLogsResult = { ok: boolean; path: string; error?: string } | null;

type Bridge = {
  clipboardWriteText?: (text: string) => Promise<unknown>;
  shell?: { openLogs?: () => Promise<OpenLogsResult> };
};

function bridge(): Bridge | undefined {
  if (typeof window === "undefined") return undefined;
  return (window as unknown as { ironjarvis?: Bridge }).ironjarvis;
}

export interface BackupRow {
  name: string;
  bytes: number;
  modified_at: string;
}

function errorText(err: unknown): string {
  return err instanceof ApiError ? err.message : err instanceof Error ? err.message : String(err);
}

/** One fetch that never throws: a failed endpoint becomes `{ error }` in the blob. */
async function section<T>(path: string, pick?: (v: T) => unknown): Promise<unknown> {
  try {
    const v = await get<T>(path);
    return pick ? pick(v) : v;
  } catch (err) {
    return { error: errorText(err) };
  }
}

/** The diagnostics bundle as pretty JSON. Exported so the test reads the same text the clipboard gets. */
export async function collectDiagnostics(): Promise<string> {
  const [app, diagnostics, reliability, errors] = await Promise.all([
    section<{ version?: string; instance?: string }>("/health", (h) => ({
      version: h?.version ?? null,
      instance: h?.instance ?? null,
    })),
    section("/diagnostics"),
    section("/diagnostics/reliability"),
    section<{ errors?: unknown[] }>("/diagnostics/errors", (e) => e?.errors ?? e),
  ]);
  return JSON.stringify(
    { collected_at: new Date().toISOString(), app, diagnostics, reliability, recent_errors: errors },
    null,
    2,
  );
}

/** Put `text` on the clipboard; "unavailable" when nothing could take it. */
export async function copyPlain(text: string): Promise<"copied" | "unavailable"> {
  const ij = bridge();
  if (ij?.clipboardWriteText) {
    try {
      await ij.clipboardWriteText(text);
      return "copied";
    } catch {
      /* fall through to the browser path */
    }
  }
  if (typeof navigator !== "undefined" && navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return "copied";
    } catch {
      /* permission-gated or no focus */
    }
  }
  return "unavailable";
}

function fmtBytes(n: number): string {
  if (n >= 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1)} MB`;
  if (n >= 1024) return `${Math.round(n / 1024)} KB`;
  return `${n} B`;
}

function fmtWhen(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

export function MaintenanceTools({
  disabled = false,
  onRestartRequested,
}: {
  disabled?: boolean;
  /** Called after a successful restore with the note to show while reconnecting. */
  onRestartRequested: (note: string) => void | Promise<void>;
}) {
  // --- Copy diagnostics ----------------------------------------------------
  const [copyState, setCopyState] = useState<"idle" | "busy" | "copied" | "unavailable">("idle");
  const [fallbackText, setFallbackText] = useState<string | null>(null);

  const copyDiagnostics = useCallback(async () => {
    setCopyState("busy");
    setFallbackText(null);
    const text = await collectDiagnostics();
    const outcome = await copyPlain(text);
    if (outcome === "copied") {
      setCopyState("copied");
      window.setTimeout(() => setCopyState("idle"), 2500);
    } else {
      setFallbackText(text);
      setCopyState("unavailable");
    }
  }, []);

  // --- Open logs folder (desktop only) ---------------------------------------
  const [canOpenLogs, setCanOpenLogs] = useState(false);
  const [logsNote, setLogsNote] = useState<{ ok: boolean; text: string } | null>(null);
  useEffect(() => {
    setCanOpenLogs(typeof bridge()?.shell?.openLogs === "function");
  }, []);

  const openLogs = useCallback(async () => {
    setLogsNote(null);
    const fn = bridge()?.shell?.openLogs;
    if (!fn) return;
    try {
      const r = await fn();
      if (!r) setLogsNote({ ok: false, text: "The desktop shell refused the request." });
      else if (r.ok) setLogsNote({ ok: true, text: `Opened ${r.path}` });
      else setLogsNote({ ok: false, text: `Couldn’t open ${r.path}: ${r.error ?? "unknown error"}` });
    } catch (err) {
      setLogsNote({ ok: false, text: `Couldn’t open the logs folder: ${errorText(err)}` });
    }
  }, []);

  // --- Restore from backup ---------------------------------------------------
  const [backups, setBackups] = useState<BackupRow[] | null>(null);
  const [backupsErr, setBackupsErr] = useState<string | null>(null);
  const [picked, setPicked] = useState<string>("");
  const [confirming, setConfirming] = useState(false);
  const [restoreBusy, setRestoreBusy] = useState(false);
  const [restoreErr, setRestoreErr] = useState<string | null>(null);

  const loadBackups = useCallback(async () => {
    setBackupsErr(null);
    try {
      const r = await get<{ backups: BackupRow[] }>("/maintenance/backups");
      const rows = Array.isArray(r?.backups) ? r.backups : [];
      setBackups(rows);
      setPicked((cur) => (cur && rows.some((b) => b.name === cur) ? cur : (rows[0]?.name ?? "")));
    } catch (err) {
      setBackups([]);
      setBackupsErr(errorText(err));
    }
  }, []);

  useEffect(() => {
    void loadBackups();
  }, [loadBackups]);

  const pickedRow = backups?.find((b) => b.name === picked) ?? null;

  async function confirmRestore() {
    if (!pickedRow) return;
    setRestoreBusy(true);
    setRestoreErr(null);
    try {
      const r = await post<{ ok: boolean; restored_from: string; files: number }>(
        "/maintenance/restore",
        { name: pickedRow.name },
      );
      setConfirming(false);
      await onRestartRequested(
        `Restored ${r.files} file(s) from ${r.restored_from} — Iron Jarvis is restarting on that snapshot.`,
      );
    } catch (err) {
      setRestoreErr(errorText(err));
    } finally {
      setRestoreBusy(false);
    }
  }

  return (
    <>
      <div className="border-t hairline pt-4" data-testid="copy-diagnostics">
        <SectionLabel>Copy diagnostics</SectionLabel>
        <p className="mt-1 text-[12px] leading-relaxed text-zinc-500">
          Version, health, background loops, disk, provider failures and the last 50 warnings and
          errors, as one JSON blob to paste into a report.
        </p>
        <div className="mt-2.5 flex gap-2">
          <button
            type="button"
            onClick={copyDiagnostics}
            disabled={disabled || copyState === "busy"}
            className="btn-ghost flex-1 justify-center py-1.5 text-xs"
          >
            {copyState === "busy" ? (
              <LoaderInline label="Collecting…" />
            ) : (
              <>
                <ClipboardCopy size={14} />{" "}
                {copyState === "copied" ? "Copied" : "Copy diagnostics"}
              </>
            )}
          </button>
          {canOpenLogs && (
            <button
              type="button"
              onClick={openLogs}
              disabled={disabled}
              className="btn-ghost flex-1 justify-center py-1.5 text-xs"
              title="Opens the folder holding daemon.log, dashboard.log and desktop.log"
            >
              <FolderOpen size={14} /> Open logs folder
            </button>
          )}
        </div>
        {copyState === "unavailable" && fallbackText !== null && (
          <div className="mt-2">
            <p className="text-[11px] text-amber-300/80">
              Clipboard unavailable here — select and copy the text below.
            </p>
            <textarea
              readOnly
              value={fallbackText}
              aria-label="Diagnostics JSON"
              className="mt-1.5 h-32 w-full rounded-lg border hairline bg-ink-900/60 p-2 font-mono text-[10px] text-zinc-300"
            />
          </div>
        )}
        {logsNote &&
          (logsNote.ok ? (
            <p className="mt-2 text-[11px] text-zinc-400">{logsNote.text}</p>
          ) : (
            <p className="mt-2 text-[11px] text-amber-300/80">{logsNote.text}</p>
          ))}
      </div>

      <div className="border-t hairline pt-4" data-testid="restore-backup">
        <SectionLabel>Restore from backup</SectionLabel>
        <p className="mt-1 text-[12px] leading-relaxed text-zinc-500">
          Replace the database and settings with an earlier snapshot. Iron Jarvis restarts right
          after; anything running is refused first.
        </p>
        {backupsErr && <p className="mt-2 text-[11px] text-amber-300/80">Couldn’t list backups: {backupsErr}</p>}
        {backups && backups.length === 0 && !backupsErr && (
          <p className="mt-2 text-[11px] text-zinc-500">No backups yet — “Back up now” writes the first one.</p>
        )}
        {backups && backups.length > 0 && (
          <div className="mt-2.5 space-y-2">
            <select
              aria-label="Backup to restore"
              value={picked}
              onChange={(e) => {
                setPicked(e.target.value);
                setConfirming(false);
                setRestoreErr(null);
              }}
              disabled={disabled || restoreBusy}
              className="w-full rounded-lg border hairline bg-ink-900/60 px-2 py-1.5 text-xs text-zinc-200"
            >
              {backups.map((b) => (
                <option key={b.name} value={b.name}>
                  {fmtWhen(b.modified_at)} · {fmtBytes(b.bytes)} · {b.name}
                </option>
              ))}
            </select>
            {!confirming ? (
              <button
                type="button"
                onClick={() => {
                  setRestoreErr(null);
                  setConfirming(true);
                }}
                disabled={disabled || restoreBusy || !pickedRow}
                className="btn-ghost w-full justify-center border-amber-500/30 py-1.5 text-xs text-amber-200 hover:border-amber-500/50 hover:text-amber-100"
              >
                <ArchiveRestore size={14} /> Restore from backup…
              </button>
            ) : (
              <div
                role="dialog"
                aria-label="Confirm restore"
                className="rounded-lg border border-amber-500/30 bg-amber-500/5 p-2.5 text-[12px] text-zinc-300"
              >
                <p>
                  Restore <span className="font-mono text-amber-200">{pickedRow?.name}</span> (from{" "}
                  {pickedRow ? fmtWhen(pickedRow.modified_at) : ""})? Your current database and
                  settings will be replaced by that snapshot, and{" "}
                  <strong>Iron Jarvis restarts</strong> right after.
                </p>
                <div className="mt-2 flex gap-2">
                  <button
                    type="button"
                    onClick={confirmRestore}
                    disabled={restoreBusy}
                    className="btn-accent flex-1 justify-center py-1 text-xs"
                  >
                    {restoreBusy ? <LoaderInline label="Restoring…" /> : "Restore and restart"}
                  </button>
                  <button
                    type="button"
                    onClick={() => setConfirming(false)}
                    disabled={restoreBusy}
                    className="btn-ghost flex-1 justify-center py-1 text-xs"
                  >
                    Cancel
                  </button>
                </div>
              </div>
            )}
          </div>
        )}
        {restoreErr && <ErrorNote>{restoreErr}</ErrorNote>}
      </div>
    </>
  );
}
