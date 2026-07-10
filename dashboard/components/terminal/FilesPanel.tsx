"use client";

// Live "Files" panel for the Build page. Given the folder a terminal is working
// in, it polls `GET /fs/files` every few seconds and lists every file under that
// folder NEWEST FIRST — so files a CLI just created surface at the top. Click a
// row to preview it in a lightbox: media streams from `/creative/file-by-path`,
// text/code/documents are extracted via `/documents/read`.

import { useEffect, useRef, useState } from "react";
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
import { Empty, ErrorNote, OfflineHint, Spinner } from "@/components/ui";

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
  truncated: boolean;
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
  if (kind === "image") return <ImageIcon size={size} className="text-violet-300/80" />;
  if (kind === "video") return <FileVideo size={size} className="text-sky-300/80" />;
  if (kind === "audio") return <FileAudio size={size} className="text-emerald-300/80" />;
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

  // Esc closes.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

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

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={file.name}
      onClick={onClose}
      className="fixed inset-0 z-[80] flex items-center justify-center bg-black/70 p-4 backdrop-blur-sm"
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className="card-surface flex max-h-[90vh] w-full max-w-3xl flex-col overflow-hidden focus:outline-none"
      >
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
              {copied ? <Check size={15} className="text-emerald-400" /> : <Copy size={15} />}
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
                <ErrorNote>Couldn&apos;t read this file — {textErr}</ErrorNote>
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
      </div>
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/*  Files panel                                                               */
/* -------------------------------------------------------------------------- */

export function FilesPanel({
  folder,
  onOpenTerminal,
}: {
  folder: string | null;
  onOpenTerminal?: (path: string) => void;
}) {
  const [root, setRoot] = useState<string | null>(null);
  const [files, setFiles] = useState<FileRow[]>([]);
  const [count, setCount] = useState(0);
  const [truncated, setTruncated] = useState(false);
  const [loading, setLoading] = useState(false);
  const [offline, setOffline] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<FileRow | null>(null);
  const tickRef = useRef<() => void>(() => {});

  // Poll `/fs/files` every 4s (and immediately on folder change). A small
  // in-flight guard keeps a slow response from stacking overlapping requests.
  useEffect(() => {
    setError(null);
    setOffline(false);
    if (!folder) {
      setLoading(false);
      setFiles([]);
      setRoot(null);
      setCount(0);
      setTruncated(false);
      return;
    }
    setLoading(true);
    setFiles([]);
    let cancelled = false;
    let inFlight = false;

    const tick = async () => {
      if (inFlight || cancelled) return;
      inFlight = true;
      try {
        const data = await get<FilesResponse>(
          `/fs/files?path=${encodeURIComponent(folder)}&depth=4&limit=600`,
        );
        if (cancelled) return;
        setRoot(data.root);
        setFiles(data.files);
        setCount(data.count);
        setTruncated(data.truncated);
        setOffline(false);
        setError(null);
      } catch (e) {
        if (cancelled) return;
        if (e instanceof ApiError && e.status === 0) setOffline(true);
        else setError(e instanceof ApiError ? e.message : String(e));
      } finally {
        inFlight = false;
        if (!cancelled) setLoading(false);
      }
    };

    tickRef.current = () => void tick();
    void tick();
    const timer = setInterval(tick, 4000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [folder]);

  const shown = files.slice(0, MAX_ROWS);

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
          <div className="p-3">
            <ErrorNote>{error}</ErrorNote>
          </div>
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
                    onClick={() => setSelected(f)}
                    title={f.path}
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
                      <span className="mt-0.5 flex items-center gap-1.5 text-[10.5px] text-zinc-500">
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
        <footer className="shrink-0 border-t border-white/[0.06] px-4 py-2 text-[10.5px] text-zinc-500">
          {files.length > MAX_ROWS ? `Showing ${MAX_ROWS} of ` : ""}
          {count} file{count === 1 ? "" : "s"}
          {truncated ? " (capped at 600 — newest shown)" : ""}
        </footer>
      )}

      {selected && <FilePreview file={selected} onClose={() => setSelected(null)} />}
    </div>
  );
}
