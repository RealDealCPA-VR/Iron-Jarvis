"use client";

// Live "Files" panel for the Build page. Given the folder a terminal is working
// in, it polls `GET /fs/files` every few seconds and lists every file under that
// folder NEWEST FIRST — so files a CLI just created surface at the top. Click a
// row to preview it in a lightbox: media streams from `/creative/file-by-path`,
// text/code/documents are extracted via `/documents/read`.

import { useEffect, useRef, useState } from "react";
import { useVisibleInterval } from "@/lib/useVisibleInterval";
import {
  Copy,
  Check,
  ExternalLink,
  FileAudio,
  FileText,
  FileVideo,
  File as FileIcon,
  FolderOpen,
  Image as ImageIcon,
  RefreshCw,
  SquareTerminal,
  X,
} from "lucide-react";
import { API_BASE, ApiError, get, ijToken } from "@/lib/api";
import { etagOf, isNotModified } from "@/lib/etag";
import { Empty, ErrorNote, OfflineHint, Spinner } from "@/components/ui";
import { Modal } from "@/components/Modal";

/** One file from `GET /fs/files` — mtime is a UNIX epoch SECONDS float. */
interface FileRow {
  name: string;
  path: string;
  rel: string;
  size: number;
  mtime: number;
}

interface FilesResponse {
  root: string;
  files: FileRow[];
  count: number;
  /** More files were found than the list holds (it is the newest of them). */
  truncated: boolean;
  /** v1.311.0: the WALK stopped early (entry budget or deadline); absent on
   *  an older daemon. `scanned` = how many entries it examined. */
  scan_truncated?: boolean;
  scanned?: number;
}

type Kind = "image" | "video" | "audio" | "text" | "other";

const IMAGE_EXT = new Set(["png", "jpg", "jpeg", "webp", "gif", "bmp", "svg"]);
const VIDEO_EXT = new Set(["mp4", "webm", "mov", "m4v", "mkv"]);
const AUDIO_EXT = new Set(["mp3", "wav", "ogg", "m4a", "flac", "aac", "opus"]);
// Text / code / documents readable via /documents/read (extracts pdf/docx/…).
const TEXT_EXT = new Set([
  "txt", "md", "markdown", "py", "js", "mjs", "cjs", "ts", "tsx", "jsx", "json",
  "csv", "tsv", "html", "htm", "css", "scss", "sass", "less", "yaml", "yml",
  "toml", "log", "sh", "bash", "zsh", "ps1", "bat", "xml", "sql", "ini", "cfg",
  "conf", "env", "rs", "go", "java", "kt", "c", "h", "cpp", "hpp", "cc", "rb",
  "php", "swift", "r", "lua", "vue", "svelte", "dockerfile", "makefile", "gitignore",
  "pdf", "docx", "xlsx", "pptx",
]);

/** File extension (lowercased), or "" when there is none. */
function extOf(name: string): string {
  const i = name.lastIndexOf(".");
  return i >= 0 ? name.slice(i + 1).toLowerCase() : "";
}

function kindOf(name: string): Kind {
  const e = extOf(name);
  if (IMAGE_EXT.has(e)) return "image";
  if (VIDEO_EXT.has(e)) return "video";
  if (AUDIO_EXT.has(e)) return "audio";
  if (TEXT_EXT.has(e)) return "text";
  // A dotless config-ish file (Dockerfile, Makefile) reads as text.
  if (e === "" && TEXT_EXT.has(name.toLowerCase())) return "text";
  return "other";
}

function KindIcon({ kind, size = 14 }: { kind: Kind; size?: number }) {
  // v1.329.0: the tone tokens, which every theme re-inks (both looks).
  if (kind === "image") return <ImageIcon size={size} className="text-tone-violet/80" />;
  if (kind === "video") return <FileVideo size={size} className="text-tone-info/80" />;
  if (kind === "audio") return <FileAudio size={size} className="text-tone-success/80" />;
  if (kind === "text") return <FileText size={size} className="text-accent-soft/80" />;
  return <FileIcon size={size} className="text-zinc-500" />;
}

/** Media tags can't send the Authorization header — the token rides as ?token=. */
function fileSrc(abs: string): string {
  const t = ijToken();
  return `${API_BASE}/creative/file-by-path?path=${encodeURIComponent(abs)}${
    t ? `&token=${encodeURIComponent(t)}` : ""
  }`;
}

/** Last path segment (Windows- or POSIX-separated), for a short header label. */
function baseName(p: string): string {
  const parts = p.replace(/[\\/]+$/, "").split(/[\\/]/);
  return parts[parts.length - 1] || p;
}

/** v1.329.0: the files list's error as ONE plain line that names the folder.
 *  The daemon's own words often do already ("no such directory: <path>"); when
 *  they do not, the folder is added so the line always says WHICH folder. */
export function filesErrorLine(error: string, folder: string): string {
  if (!folder || error.toLowerCase().includes(folder.toLowerCase())) return error;
  return `${error} (${folder})`;
}

function fmtSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

/** Relative time from an epoch-SECONDS mtime, e.g. "12s ago", "3m ago". */
function relTime(mtimeSec: number): string {
  const diff = Date.now() / 1000 - mtimeSec;
  if (diff < 5) return "just now";
  if (diff < 60) return `${Math.floor(diff)}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

const MAX_ROWS = 400; // bound the DOM even when the folder is huge

/* -------------------------------------------------------------------------- */
/*  Preview lightbox                                                           */
/* -------------------------------------------------------------------------- */

function FilePreview({ file, onClose }: { file: FileRow; onClose: () => void }) {
  const kind = kindOf(file.name);
  const [text, setText] = useState<string | null>(null);
  const [textLoading, setTextLoading] = useState(false);
  const [textErr, setTextErr] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  // Esc closes — through <Modal> since v1.315.0, which also moves focus into
  // the preview, keeps Tab inside it and gives focus back to the file row.

  // For text/code/documents, pull the extracted text (capped at 20k server-side).
  useEffect(() => {
    if (kind !== "text") return;
    let cancelled = false;
    setTextLoading(true);
    setText(null);
    setTextErr(null);
    get<{ path: string; text: string }>(`/documents/read?path=${encodeURIComponent(file.path)}`)
      .then((d) => {
        if (!cancelled) setText(d.text);
      })
      .catch((e) => {
        if (!cancelled) setTextErr(e instanceof ApiError ? e.message : String(e));
      })
      .finally(() => {
        if (!cancelled) setTextLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [file.path, kind]);

  function copyPath() {
    try {
      void navigator.clipboard?.writeText(file.path);
      setCopied(true);
      setTimeout(() => setCopied(false), 1400);
    } catch {
      /* clipboard blocked — no-op */
    }
  }

  // v1.315.0 (UX wave 3): the shared <Modal> — the same backdrop + Escape
  // dismissal and layer (80, above the chat's own overlays) as before, plus
  // focus in / Tab trap / focus back, which this hand-rolled overlay lacked.
  return (
    <Modal z={80} label={file.name} onClose={onClose} className="w-full max-w-3xl">
        <header className="flex items-center justify-between gap-3 border-b hairline px-5 py-3.5">
          <div className="flex min-w-0 items-center gap-2">
            <span className="shrink-0">
              <KindIcon kind={kind} size={15} />
            </span>
            <div className="min-w-0">
              <div className="truncate text-[13px] font-semibold tracking-wide text-zinc-100">
                {file.name}
              </div>
              <div className="truncate font-mono text-[11px] text-zinc-500" title={file.path}>
                {fmtSize(file.size)} · {file.path}
              </div>
            </div>
          </div>
          <div className="flex shrink-0 items-center gap-1">
            <button
              type="button"
              onClick={copyPath}
              aria-label="Copy full path"
              title="Copy full path"
              className="rounded-lg border border-transparent p-1.5 text-zinc-400 transition-colors hover:border-white/10 hover:bg-white/[0.04] hover:text-zinc-200"
            >
              {copied ? <Check size={15} className="text-tone-success" /> : <Copy size={15} />}
            </button>
            <button
              type="button"
              onClick={onClose}
              aria-label="Close"
              className="rounded-lg border border-transparent p-1.5 text-zinc-400 transition-colors hover:border-white/10 hover:bg-white/[0.04] hover:text-zinc-200"
            >
              <X size={16} />
            </button>
          </div>
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto">
          {kind === "image" ? (
            <div className="flex items-center justify-center bg-ink-950 p-2">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src={fileSrc(file.path)}
                alt={file.name}
                className="max-h-[70vh] w-auto max-w-full object-contain"
              />
            </div>
          ) : kind === "video" ? (
            <div className="flex items-center justify-center bg-ink-950 p-2">
              <video
                src={fileSrc(file.path)}
                controls
                className="max-h-[70vh] max-w-full"
              />
            </div>
          ) : kind === "audio" ? (
            <div className="p-6">
              <audio src={fileSrc(file.path)} controls className="w-full" />
            </div>
          ) : kind === "text" ? (
            textLoading ? (
              <div className="px-5">
                <Spinner label="Reading file…" />
              </div>
            ) : textErr ? (
              <div className="p-5">
                <ErrorNote>Couldn&apos;t read this file: {textErr}</ErrorNote>
              </div>
            ) : (
              <pre className="max-h-[70vh] overflow-auto whitespace-pre-wrap break-words bg-ink-950 p-4 font-mono text-[12px] leading-relaxed text-zinc-300">
                {text && text.length > 0 ? text : "(empty file)"}
              </pre>
            )
          ) : (
            <div className="p-6">
              <div className="rounded-xl border border-white/[0.06] bg-ink-900/40 p-5 text-center">
                <div className="mx-auto mb-2 grid h-10 w-10 place-items-center rounded-full bg-white/[0.04]">
                  <FileIcon size={18} className="text-zinc-500" />
                </div>
                <div className="text-[13px] font-medium text-zinc-200">{file.name}</div>
                <div className="mt-0.5 text-[12px] text-zinc-500">{fmtSize(file.size)}</div>
                <div className="mt-1 break-all font-mono text-[11px] text-zinc-600">
                  {file.path}
                </div>
                <p className="mt-3 text-[12px] text-zinc-500">
                  No inline preview for this file type.
                </p>
                <a
                  href={fileSrc(file.path)}
                  target="_blank"
                  rel="noreferrer"
                  className="mt-3 inline-flex items-center gap-1.5 rounded-lg border border-accent/30 bg-accent/[0.08] px-3 py-1.5 text-[12px] font-medium text-accent-soft transition-colors hover:bg-accent/[0.14]"
                >
                  <ExternalLink size={13} /> Open
                </a>
              </div>
            </div>
          )}
        </div>
    </Modal>
  );
}

/* -------------------------------------------------------------------------- */
/*  Files panel                                                               */
/* -------------------------------------------------------------------------- */

export function FilesPanel({
  folder,
  onOpenTerminal,
  onPreview,
  variant = "card",
  noFolderText,
}: {
  /** v1.329.0: "calm" draws the list without its card, in the normal font.
   *  The chat drawer AND Build pass it (wave 6: one look for the same list);
   *  "card" stays the default for any other caller. */
  variant?: "card" | "calm";
  /** Calm only: the line shown while no folder is known. The host says what
   *  picks one (Build: a focused terminal or the Folders tab). */
  noFolderText?: string;
  folder: string | null;
  onOpenTerminal?: (path: string) => void;
  /** When set (the chat rail), clicking a file opens the HOST's document
   *  preview panel instead of the built-in inline preview. */
  onPreview?: (path: string) => void;
}) {
  const [root, setRoot] = useState<string | null>(null);
  const [files, setFiles] = useState<FileRow[]>([]);
  const [count, setCount] = useState(0);
  const [truncated, setTruncated] = useState(false);
  const [scanCut, setScanCut] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [offline, setOffline] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<FileRow | null>(null);
  const tickRef = useRef<() => void>(() => {});
  // The last answer this panel SHOWS — its ETag is what the next poll sends.
  const lastRef = useRef<FilesResponse | null>(null);

  // Poll `/fs/files` every 4s (and immediately on folder change). A small
  // in-flight guard keeps a slow response from stacking overlapping requests.
  //
  // v1.311.0: the poll is CONDITIONAL. It sends the ETag the shown answer
  // came with (`If-None-Match`, the `/sessions` mechanism: `lib/api.ts`'s
  // `ifNoneMatch` + `lib/etag.ts`), and a 304 sets NO state — an unchanged
  // folder costs no ~130 KB body and no re-render of up to 400 rows. A
  // failed poll forgets the tag, so recovery always re-downloads in full.
  useEffect(() => {
    setError(null);
    setOffline(false);
    lastRef.current = null;
    if (!folder) {
      setLoading(false);
      setFiles([]);
      setRoot(null);
      setCount(0);
      setTruncated(false);
      setScanCut(null);
      return;
    }
    setLoading(true);
    setFiles([]);
    let cancelled = false;
    let inFlight = false;

    const tick = async () => {
      if (inFlight || cancelled) return;
      inFlight = true;
      let unchanged = false;
      try {
        const url = `/fs/files?path=${encodeURIComponent(folder)}&depth=4&limit=600`;
        const tag = etagOf(lastRef.current);
        // One argument when there is no tag: a mocked `get` keeps seeing the
        // exact call it always saw.
        const data = tag
          ? await get<FilesResponse>(url, { ifNoneMatch: tag })
          : await get<FilesResponse>(url);
        if (cancelled) return;
        if (isNotModified(data)) {
          unchanged = true; // what is on screen is still the answer
          return;
        }
        lastRef.current = data;
        setRoot(data.root);
        setFiles(data.files);
        setCount(data.count);
        setTruncated(data.truncated);
        setScanCut(data.scan_truncated ? (data.scanned ?? 0) : null);
        setOffline(false);
        setError(null);
      } catch (e) {
        if (cancelled) return;
        lastRef.current = null;
        if (e instanceof ApiError && e.status === 0) setOffline(true);
        else setError(e instanceof ApiError ? e.message : String(e));
      } finally {
        inFlight = false;
        if (!cancelled && !unchanged) setLoading(false);
      }
    };

    tickRef.current = () => void tick();
    void tick();
    // v1.250.0 (S-09): the 4 s repeat runs through useVisibleInterval below —
    // a minimised window lists nothing and catches up once on return.
    return () => {
      cancelled = true;
      tickRef.current = () => {};
    };
  }, [folder]);
  useVisibleInterval(() => tickRef.current(), 4000, Boolean(folder));

  const shown = files.slice(0, MAX_ROWS);

  if (variant === "calm") {
    const ghostIcon =
      "grid h-7 w-7 place-items-center rounded-lg text-zinc-500 transition-colors hover:bg-white/[0.06] hover:text-zinc-100 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/50 disabled:opacity-40";
    return (
      <div data-testid="files-panel" data-variant="calm" className="flex h-full min-h-0 flex-col">
        <header className="flex shrink-0 items-center gap-2 pb-2">
          <div className="min-w-0">
            <h2 className="truncate text-[13px] font-medium text-zinc-200">
              {folder ? baseName(root ?? folder) : "Files"}
            </h2>
            {folder && (
              <div className="truncate text-[12px] text-zinc-500" title={root ?? folder}>
                {root ?? folder}
              </div>
            )}
          </div>
          <div className="ml-auto flex shrink-0 items-center gap-0.5">
            {folder && onOpenTerminal && (
              <button
                type="button"
                onClick={() => onOpenTerminal(folder)}
                title="Open a new terminal in this folder"
                aria-label="Open a terminal here"
                className={ghostIcon}
              >
                <SquareTerminal size={14} />
              </button>
            )}
            <button
              type="button"
              onClick={() => tickRef.current()}
              disabled={!folder}
              title="Refresh"
              aria-label="Refresh files"
              className={ghostIcon}
            >
              <RefreshCw size={13} className={loading ? "animate-spin" : ""} />
            </button>
          </div>
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto border-t hairline">
          {!folder ? (
            <p className="py-3 text-[12px] text-zinc-500">
              {noFolderText ?? "Pick a folder to see its files here."}
            </p>
          ) : offline ? (
            <div className="py-3">
              <OfflineHint detail="The files list needs Iron Jarvis running." />
            </div>
          ) : error ? (
            <p
              role="alert"
              data-testid="files-panel-error"
              className="break-words py-3 text-[12px] text-tone-danger"
            >
              {/* The folder ASKED for: `root` may still hold the last folder's. */}
              {filesErrorLine(error, folder)}
            </p>
          ) : loading ? (
            <div className="py-1">
              <Spinner label="Loading files…" />
            </div>
          ) : files.length === 0 ? (
            <p className="py-3 text-[12px] text-zinc-500">
              No files yet. New files show up here as they are made.
            </p>
          ) : (
            <ul className="-mx-2 py-1">
              {shown.map((f) => {
                const kind = kindOf(f.name);
                const fresh = Date.now() / 1000 - f.mtime < 30;
                return (
                  <li key={f.path}>
                    <button
                      type="button"
                      onClick={() => (onPreview ? onPreview(f.path) : setSelected(f))}
                      title={onPreview ? `Preview ${f.rel}` : f.path}
                      className={`flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/50 ${
                        fresh ? "bg-accent/[0.06] hover:bg-accent/[0.1]" : "hover:bg-white/[0.05]"
                      }`}
                    >
                      <span className="shrink-0">
                        <KindIcon kind={kind} />
                      </span>
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-[13px] text-zinc-200">{f.rel}</span>
                        <span className="mt-0.5 flex items-center gap-1.5 text-[11px] tabular-nums text-zinc-500">
                          <span>{fmtSize(f.size)}</span>
                          <span aria-hidden>·</span>
                          <span className={fresh ? "text-accent-soft" : ""}>{relTime(f.mtime)}</span>
                        </span>
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </div>

        {folder && !loading && !offline && !error && files.length > 0 && (
          <footer className="shrink-0 border-t hairline py-2 text-[11px] tabular-nums text-zinc-500">
            {files.length > MAX_ROWS ? `Showing ${MAX_ROWS} of ` : ""}
            {count} file{count === 1 ? "" : "s"}
            {truncated ? ". The list stops at 600, newest first." : ""}
            {scanCut !== null
              ? ` This folder is too big to read in full, so these are the newest of the first ${scanCut.toLocaleString()} entries.`
              : ""}
          </footer>
        )}

        {selected && <FilePreview file={selected} onClose={() => setSelected(null)} />}
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col overflow-hidden rounded-2xl border border-white/[0.06] bg-ink-850/80 shadow-card backdrop-blur-sm">
      <header className="flex shrink-0 items-center gap-2 border-b border-white/[0.06] px-4 py-3">
        <FolderOpen size={15} className="shrink-0 text-accent-soft/80" />
        <div className="min-w-0">
          <h2 className="truncate text-[13px] font-semibold tracking-wide text-zinc-200">
            {folder ? baseName(root ?? folder) : "Files"}
          </h2>
          {folder && (
            <div className="truncate font-mono text-[10px] text-zinc-500" title={root ?? folder}>
              {root ?? folder}
            </div>
          )}
        </div>
        <div className="ml-auto flex shrink-0 items-center gap-1">
          {folder && onOpenTerminal && (
            <button
              type="button"
              onClick={() => onOpenTerminal(folder)}
              title="Open a new terminal in this folder"
              aria-label="Open a terminal here"
              className="grid h-6 w-6 place-items-center rounded-md text-zinc-500 transition-colors hover:bg-white/[0.06] hover:text-accent-soft"
            >
              <SquareTerminal size={14} />
            </button>
          )}
          <button
            type="button"
            onClick={() => tickRef.current()}
            disabled={!folder}
            title="Refresh"
            aria-label="Refresh files"
            className="grid h-6 w-6 place-items-center rounded-md text-zinc-500 transition-colors hover:bg-white/[0.06] hover:text-zinc-200 disabled:opacity-40"
          >
            <RefreshCw size={13} className={loading ? "animate-spin" : ""} />
          </button>
        </div>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto">
        {!folder ? (
          <Empty icon={<FolderOpen size={22} />}>
            Focus a terminal to see the files in its folder, or pick a folder in the Folders tab.
          </Empty>
        ) : offline ? (
          <div className="p-3">
            <OfflineHint detail="The files panel needs the daemon running." />
          </div>
        ) : error ? (
          <p
            role="alert"
            data-testid="files-panel-error"
            className="break-words px-4 py-3 text-[12px] text-tone-danger"
          >
            {filesErrorLine(error, folder)}
          </p>
        ) : loading ? (
          <div className="px-4">
            <Spinner label="Loading files…" />
          </div>
        ) : files.length === 0 ? (
          <Empty icon={<FileIcon size={22} />}>
            No files yet — they&apos;ll appear here as they&apos;re created.
          </Empty>
        ) : (
          <ul className="p-1.5">
            {shown.map((f) => {
              const kind = kindOf(f.name);
              const fresh = Date.now() / 1000 - f.mtime < 30;
              return (
                <li key={f.path}>
                  <button
                    type="button"
                    onClick={() =>
                      onPreview ? onPreview(f.path) : setSelected(f)
                    }
                    title={onPreview ? `Preview ${f.rel}` : f.path}
                    className={`flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left transition-colors ${
                      fresh
                        ? "bg-accent/[0.06] ring-1 ring-inset ring-accent/20 hover:bg-accent/[0.1]"
                        : "hover:bg-white/[0.05]"
                    }`}
                  >
                    <span className="shrink-0">
                      <KindIcon kind={kind} />
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate font-mono text-[12px] text-zinc-200">
                        {f.rel}
                      </span>
                      <span className="mt-0.5 flex items-center gap-1.5 text-[11px] text-zinc-500">
                        <span>{fmtSize(f.size)}</span>
                        <span className="text-zinc-700">·</span>
                        <span className={fresh ? "text-accent-soft" : ""}>{relTime(f.mtime)}</span>
                      </span>
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </div>

      {folder && !loading && !offline && !error && files.length > 0 && (
        <footer className="shrink-0 border-t border-white/[0.06] px-4 py-2 text-[11px] text-zinc-500">
          {files.length > MAX_ROWS ? `Showing ${MAX_ROWS} of ` : ""}
          {count} file{count === 1 ? "" : "s"}
          {truncated ? " (capped at 600 — newest shown)" : ""}
          {scanCut !== null
            ? ` — newest of the first ${scanCut.toLocaleString()} entries scanned; this folder is too big to read in full`
            : ""}
        </footer>
      )}

      {selected && <FilePreview file={selected} onClose={() => setSelected(null)} />}
    </div>
  );
}
