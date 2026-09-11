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
 *  - Copy backups to another drive (v1.249.0, R-05): the `backup_mirror_dir`
 *    and `backup_mirror_media` settings, saved through PUT /settings (which
 *    refuses a folder that cannot hold the copies and says why), and ONE
 *    plain-words line from GET /maintenance/backups -> `mirror`: off, the drive
 *    is missing, no copy yet, the last copy, or why it failed.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { ArchiveRestore, ClipboardCopy, FolderOpen, HardDrive } from "lucide-react";
import { get, post, put, ApiError } from "@/lib/api";
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

/** GET /maintenance/backups -> `mirror` (v1.249.0, R-05). */
export interface MirrorStatus {
  dir: string;
  media: boolean;
  configured: boolean;
  missing: boolean;
  last: {
    ok?: boolean;
    at?: string;
    error?: string | null;
    archive?: string | null;
    media?: { copied?: number; unchanged?: number; failed?: number; truncated?: boolean } | null;
  } | null;
}

/** The one plain-words line under the backup-copy field. Exported so the test
 *  reads the same sentence the card shows. */
export function mirrorLine(m: MirrorStatus | null | undefined): {
  tone: "ok" | "warn" | "muted";
  text: string;
} {
  if (!m || !m.configured) return { tone: "muted", text: "Off — backups are kept on this PC only." };
  if (m.missing) {
    return {
      tone: "warn",
      text: `The folder ${m.dir} isn’t there right now — is the drive plugged in? Backups still run on this PC.`,
    };
  }
  const last = m.last;
  if (!last) {
    return { tone: "muted", text: "No copy yet — the next backup makes one, or press Back up now." };
  }
  if (!last.ok) {
    return { tone: "warn", text: `The last copy didn’t complete: ${last.error ?? "unknown error"}` };
  }
  let mediaText = "";
  if (last.media) {
    const n = last.media.copied ?? 0;
    mediaText = n > 0 ? ` · ${n} new media file${n === 1 ? "" : "s"} copied` : " · media up to date";
    if (last.media.truncated) mediaText += " (the rest follow with the next backup)";
  }
  const partial = last.error ? ` · ${last.error}` : "";
  return {
    tone: last.error ? "warn" : "ok",
    text: `Last copy ${last.at ? fmtWhen(last.at) : ""}${mediaText}${partial}`,
  };
}

const TONE_CLASS: Record<"ok" | "warn" | "muted", string> = {
  ok: "text-zinc-400",
  warn: "text-amber-300/80",
  muted: "text-zinc-500",
};

/** Settings -> Maintenance: a second copy of every backup, on another drive. */
function BackupMirror({ disabled, refreshKey }: { disabled: boolean; refreshKey?: number }) {
  const [dir, setDir] = useState("");
  const [media, setMedia] = useState(true);
  const [saved, setSaved] = useState<{ dir: string; media: boolean } | null>(null);
  const [status, setStatus] = useState<MirrorStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  // What the user has typed is never overwritten by a load that lands late
  // (measured on the live check: the settings GET resolved AFTER the folder
  // was typed, blanked the box, and Save then stored an empty folder). The
  // baseline still updates, so "Save" stays enabled while the two differ.
  const touched = useRef(false);

  const loadSettings = useCallback(async () => {
    try {
      const r = await get<{ settings?: Record<string, unknown> }>("/settings");
      const s = r?.settings ?? {};
      const d = typeof s.backup_mirror_dir === "string" ? s.backup_mirror_dir : "";
      const m = s.backup_mirror_media !== false;
      if (!touched.current) {
        setDir(d);
        setMedia(m);
      }
      setSaved({ dir: d, media: m });
    } catch {
      /* the field stays empty; Save still works */
    }
  }, []);

  const loadStatus = useCallback(async () => {
    try {
      const r = await get<{ mirror?: MirrorStatus }>("/maintenance/backups");
      setStatus(r?.mirror ?? null);
    } catch {
      setStatus(null);
    }
  }, []);

  useEffect(() => {
    void loadSettings();
  }, [loadSettings]);
  useEffect(() => {
    void loadStatus();
  }, [loadStatus, refreshKey]);

  const trimmed = dir.trim();
  const dirty = !saved || saved.dir !== trimmed || saved.media !== media;

  async function save() {
    setBusy(true);
    setErr(null);
    setNote(null);
    try {
      await put("/settings", { values: { backup_mirror_dir: trimmed, backup_mirror_media: media } });
      setSaved({ dir: trimmed, media });
      setNote(
        trimmed
          ? `Saved — every backup is now also copied to ${trimmed}.`
          : "Saved — backups are kept on this PC only.",
      );
      await loadStatus();
    } catch (e) {
      setErr(errorText(e));
    } finally {
      setBusy(false);
    }
  }

  const line = mirrorLine(status);
  return (
    <div className="border-t hairline pt-4" data-testid="backup-mirror">
      <SectionLabel>Copy backups to another drive</SectionLabel>
      <p className="mt-1 text-[12px] leading-relaxed text-zinc-500">
        After every backup, also save a copy in a folder on another drive — a USB drive or a second
        disk — so one failed disk can’t take your data and its backups with it.
      </p>
      <div className="mt-2.5 flex gap-2">
        <input
          id="backup-mirror-dir"
          aria-label="Backup copy folder"
          value={dir}
          onChange={(e) => {
            touched.current = true;
            setDir(e.target.value);
            setNote(null);
            setErr(null);
          }}
          placeholder="e.g. D:\Iron Jarvis backups"
          disabled={disabled || busy}
          spellCheck={false}
          className="min-w-0 flex-1 rounded-lg border hairline bg-ink-900/60 px-2 py-1.5 font-mono text-xs text-zinc-200"
        />
        <button
          type="button"
          onClick={save}
          disabled={disabled || busy || !dirty}
          className="btn-ghost justify-center px-3 py-1.5 text-xs"
        >
          {busy ? (
            <LoaderInline label="Saving…" />
          ) : (
            <>
              <HardDrive size={14} /> Save
            </>
          )}
        </button>
      </div>
      <label className="mt-2 flex items-center gap-2 text-[12px] text-zinc-400">
        <input
          id="backup-mirror-media"
          type="checkbox"
          checked={media}
          onChange={(e) => {
            touched.current = true;
            setMedia(e.target.checked);
            setNote(null);
          }}
          disabled={disabled || busy}
        />
        Also keep generated images, video and audio there (only new files are copied each time)
      </label>
      <p data-testid="backup-mirror-status" className={`mt-2 text-[11px] ${TONE_CLASS[line.tone]}`}>
        {line.text}
      </p>
      {note && <p className="mt-1 text-[11px] text-zinc-400">{note}</p>}
      {err && <ErrorNote>{err}</ErrorNote>}
      <p className="mt-1.5 text-[11px] leading-relaxed text-zinc-500">
        The copy holds everything a restore needs — your database, settings and saved logins — so
        keep that drive somewhere safe.
      </p>
    </div>
  );
}

export function MaintenanceTools({
  disabled = false,
  onRestartRequested,
  refreshKey,
}: {
  disabled?: boolean;
  /** Called after a successful restore with the note to show while reconnecting. */
  onRestartRequested: (note: string) => void | Promise<void>;
  /** v1.249.0: bumped by the page after "Back up now" — re-reads the copy status. */
  refreshKey?: number;
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
      <BackupMirror disabled={disabled} refreshKey={refreshKey} />

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
