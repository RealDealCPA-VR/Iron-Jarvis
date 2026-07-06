"use client";

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import Link from "next/link";
import {
  Sparkles,
  Image as ImageIcon,
  Film,
  Music,
  Upload,
  Download,
  Globe,
  Copy,
  Check,
  X,
  ArrowRight,
  ArrowUp,
  Folder,
  FolderOpen,
  HardDrive,
  Play,
  Star,
} from "lucide-react";
import { API_BASE, ApiError, get, ijToken, post } from "@/lib/api";
import { useApi } from "@/lib/useApi";
import { useEvents } from "@/lib/useEvents";
import { timeAgo } from "@/lib/format";
import type { Drive, FsEntry, FsListing } from "@/lib/types";
import {
  Card,
  Empty,
  ErrorNote,
  SuccessNote,
  LoaderInline,
  OfflineHint,
  Skeleton,
} from "@/components/ui";
import { PageHeader } from "@/components/PageHeader";
import { PageShell, Reveal } from "@/components/motion";

/* ---- API shapes (mirror the daemon's /creative routes) -------------------- */

type MediaKind = "image" | "video" | "audio";

interface CreativeItem {
  name: string;
  version: number;
  media: MediaKind;
  kind: string;
  filename: string;
  size: number;
  session_id: string | null;
  created_at: string;
  url: string; // "/creative/file/<name>"
}

interface UploadResult {
  name: string;
  version: number;
  media: MediaKind;
  size: number;
  url?: string;
  publish_error?: string;
}

/* ---- helpers --------------------------------------------------------------- */

const MAX_UPLOAD_BYTES = 100 * 1024 * 1024; // client-side sanity guard (~100 MB)

/** Media tags can't send the Authorization header — the token rides as ?token=. */
function fileSrc(item: CreativeItem): string {
  const token = ijToken();
  return `${API_BASE}${item.url}${token ? `?token=${encodeURIComponent(token)}` : ""}`;
}

/** Stream any local media file by absolute path (Library view). */
function filePathSrc(absPath: string): string {
  const token = ijToken();
  return `${API_BASE}/creative/file-by-path?path=${encodeURIComponent(absPath)}${
    token ? `&token=${encodeURIComponent(token)}` : ""
  }`;
}

function formatSize(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  if (bytes >= 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${bytes} B`;
}

function mediaIcon(media: MediaKind, size = 13): ReactNode {
  if (media === "image") return <ImageIcon size={size} />;
  if (media === "video") return <Film size={size} />;
  return <Music size={size} />;
}

function readAsBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = String(reader.result ?? "");
      const comma = result.indexOf(",");
      resolve(comma >= 0 ? result.slice(comma + 1) : result);
    };
    reader.onerror = () => reject(new Error(`Could not read ${file.name}`));
    reader.readAsDataURL(file);
  });
}

/* ---- Library helpers (local-folder browsing) -------------------------------- */

/** Extension → media kind. Mirrors the daemon's /creative/file-by-path allowlist. */
const EXT_KINDS: Record<string, MediaKind> = {
  png: "image",
  jpg: "image",
  jpeg: "image",
  webp: "image",
  gif: "image",
  bmp: "image",
  svg: "image",
  mp4: "video",
  webm: "video",
  mov: "video",
  m4v: "video",
  avi: "video",
  mkv: "video",
  mp3: "audio",
  wav: "audio",
  ogg: "audio",
  m4a: "audio",
  flac: "audio",
  aac: "audio",
  opus: "audio",
};

function mediaKindOf(name: string): MediaKind | null {
  const dot = name.lastIndexOf(".");
  if (dot < 0) return null;
  return EXT_KINDS[name.slice(dot + 1).toLowerCase()] ?? null;
}

/** Last path segment ("D:\Videos\Trips" → "Trips", "D:\" → "D:"). */
function folderLabel(p: string): string {
  const segs = p.replace(/[\\/]+$/, "").split(/[\\/]/).filter(Boolean);
  return segs.length ? segs[segs.length - 1] : p;
}

type View = "creations" | "library";
const VIEW_KEY = "ironjarvis.creative.view";
const LASTDIR_KEY = "ironjarvis.creative.lastdir";
const PINS_KEY = "ironjarvis.creative.pins";
/** Hard cap on tiles rendered per folder (a 10k-file folder must not melt the DOM). */
const LIB_RENDER_CAP = 200;

interface PinnedFolder {
  path: string;
  label: string;
}

/** A media file inside the currently open library folder. */
interface LibraryFile {
  path: string;
  name: string;
  kind: MediaKind;
  size: number | null;
}

function parsePins(raw: string | null): PinnedFolder[] {
  if (!raw) return [];
  try {
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(
      (p): p is PinnedFolder =>
        !!p &&
        typeof p === "object" &&
        typeof (p as { path?: unknown }).path === "string" &&
        typeof (p as { label?: unknown }).label === "string",
    );
  } catch {
    return [];
  }
}

/* ---- Public URL box --------------------------------------------------------- */

function PublicUrlBox({ url, autoCopy = false }: { url: string; autoCopy?: boolean }) {
  const [copied, setCopied] = useState(false);
  const copy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(url);
      setCopied(true);
    } catch {
      /* clipboard unavailable — the button stays available */
    }
  }, [url]);
  useEffect(() => {
    if (autoCopy) void copy();
  }, [autoCopy, copy]);
  return (
    <div className="space-y-1.5">
      <div className="flex items-center gap-2 rounded-xl border border-white/[0.08] bg-ink-950 px-3 py-2">
        <code className="min-w-0 flex-1 truncate font-mono text-xs text-zinc-300">{url}</code>
        <button
          type="button"
          onClick={copy}
          aria-label="Copy URL"
          title="Copy URL"
          className="shrink-0 rounded-lg border border-transparent p-1 text-zinc-400 transition-colors hover:border-white/10 hover:bg-white/[0.04] hover:text-zinc-200"
        >
          {copied ? <Check size={13} className="text-emerald-400" /> : <Copy size={13} />}
        </button>
      </div>
      <p className="text-[11px] text-zinc-500">
        {copied ? "Copied to clipboard ✓ — " : ""}clean, permanent, public — paste it into any
        generation param.
      </p>
    </div>
  );
}

/* ---- Grid tiles --------------------------------------------------------------- */

function MediaTile({ item, onOpen }: { item: CreativeItem; onOpen: () => void }) {
  const src = fileSrc(item);
  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onOpen}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onOpen();
        }
      }}
      className="card-surface group cursor-pointer overflow-hidden text-left transition-all duration-300 hover:-translate-y-0.5 hover:shadow-card-hover focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/50"
    >
      <div className="relative aspect-video w-full overflow-hidden bg-ink-950">
        {item.media === "image" ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={src}
            alt={item.filename}
            loading="lazy"
            className="h-full w-full object-cover transition-transform duration-300 group-hover:scale-[1.03]"
          />
        ) : item.media === "video" ? (
          <video
            src={src}
            muted
            playsInline
            preload="metadata"
            className="h-full w-full object-cover"
            onMouseEnter={(e) => {
              e.currentTarget.play().catch(() => {});
            }}
            onMouseLeave={(e) => {
              e.currentTarget.pause();
              e.currentTarget.currentTime = 0;
            }}
          />
        ) : (
          <div className="flex h-full w-full flex-col items-center justify-center gap-3 px-4">
            <Music size={22} className="text-accent-soft/70" />
            <audio
              src={src}
              controls
              preload="metadata"
              className="h-8 w-full"
              onClick={(e) => e.stopPropagation()}
              onKeyDown={(e) => e.stopPropagation()}
            />
          </div>
        )}
        <span className="absolute right-2 top-2 inline-flex items-center gap-1 rounded-full border border-white/10 bg-black/50 px-2 py-0.5 text-[10px] font-medium capitalize text-zinc-300 backdrop-blur">
          {mediaIcon(item.media, 10)} {item.media}
        </span>
      </div>
      <div className="flex items-center justify-between gap-2 px-3 py-2.5">
        <span className="min-w-0 truncate text-xs text-zinc-300" title={item.filename}>
          {item.filename}
        </span>
        <span className="shrink-0 text-[11px] text-zinc-500">{timeAgo(item.created_at)}</span>
      </div>
    </div>
  );
}

/**
 * Library tile — same card language as MediaTile, but disk-friendly: videos and
 * audio render NO media element at all (a big folder of videos must not hammer
 * the drive); they load only when the lightbox opens. Images stay lazy <img>.
 */
function LibraryTile({ file, onOpen }: { file: LibraryFile; onOpen: () => void }) {
  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onOpen}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onOpen();
        }
      }}
      className="card-surface group cursor-pointer overflow-hidden text-left transition-all duration-300 hover:-translate-y-0.5 hover:shadow-card-hover focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/50"
    >
      <div className="relative aspect-video w-full overflow-hidden bg-ink-950">
        {file.kind === "image" ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={filePathSrc(file.path)}
            alt={file.name}
            loading="lazy"
            className="h-full w-full object-cover transition-transform duration-300 group-hover:scale-[1.03]"
          />
        ) : file.kind === "video" ? (
          <div className="flex h-full w-full items-center justify-center">
            <span
              aria-hidden="true"
              className="grid h-12 w-12 place-items-center rounded-full border border-white/15 bg-black/50 text-zinc-200 backdrop-blur transition-colors group-hover:border-accent/40 group-hover:text-accent-soft"
            >
              <Play size={20} className="ml-0.5" />
            </span>
          </div>
        ) : (
          <div className="flex h-full w-full flex-col items-center justify-center gap-2 px-4">
            <Music size={22} className="text-accent-soft/70" />
            <span className="text-[11px] text-zinc-500">click to play</span>
          </div>
        )}
        <span className="absolute right-2 top-2 inline-flex items-center gap-1 rounded-full border border-white/10 bg-black/50 px-2 py-0.5 text-[10px] font-medium capitalize text-zinc-300 backdrop-blur">
          {mediaIcon(file.kind, 10)} {file.kind}
        </span>
      </div>
      <div className="flex items-center justify-between gap-2 px-3 py-2.5">
        <span className="min-w-0 truncate text-xs text-zinc-300" title={file.name}>
          {file.name}
        </span>
        {file.size !== null && (
          <span className="shrink-0 text-[11px] text-zinc-500">{formatSize(file.size)}</span>
        )}
      </div>
    </div>
  );
}

/* ---- Lightbox ----------------------------------------------------------------- */

/**
 * Shared lightbox shell for BOTH views. Creations publish by artifact name,
 * Library items by absolute path — same endpoint, same 424 handling.
 */
function MediaLightbox({
  media,
  src,
  title,
  downloadName,
  publishBody,
  meta,
  onClose,
}: {
  media: MediaKind;
  src: string;
  title: string;
  downloadName: string;
  publishBody: Record<string, string>;
  meta: ReactNode;
  onClose: () => void;
}) {
  const [pubBusy, setPubBusy] = useState(false);
  const [pubUrl, setPubUrl] = useState<string | null>(null);
  const [pubErr, setPubErr] = useState<{ detail: string; notConnected: boolean } | null>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const publish = async () => {
    setPubBusy(true);
    setPubErr(null);
    try {
      const res = await post<{ url: string }>("/creative/publish", publishBody);
      setPubUrl(res.url);
    } catch (e) {
      const err = e instanceof ApiError ? e : new ApiError(String(e), 0);
      setPubErr({ detail: err.message, notConnected: err.status === 424 });
    } finally {
      setPubBusy(false);
    }
  };

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={title}
      onClick={onClose}
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4 backdrop-blur-sm"
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className="card-surface flex max-h-[90vh] w-full max-w-3xl flex-col overflow-hidden"
      >
        <header className="flex items-center justify-between gap-3 border-b hairline px-5 py-3.5">
          <h2 className="flex min-w-0 items-center gap-2 text-[13px] font-semibold tracking-wide text-zinc-200">
            <span className="shrink-0 text-accent-soft/80">{mediaIcon(media, 15)}</span>
            <span className="truncate">{title}</span>
          </h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="shrink-0 rounded-lg border border-transparent p-1.5 text-zinc-400 transition-colors hover:border-white/10 hover:bg-white/[0.04] hover:text-zinc-200"
          >
            <X size={16} />
          </button>
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto">
          <div className="flex items-center justify-center bg-ink-950">
            {media === "image" ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img
                src={src}
                alt={title}
                className="max-h-[55vh] w-auto max-w-full object-contain"
              />
            ) : media === "video" ? (
              <video src={src} controls autoPlay playsInline className="max-h-[55vh] w-full" />
            ) : (
              <div className="w-full px-6 py-10">
                <audio src={src} controls autoPlay className="w-full" />
              </div>
            )}
          </div>

          <div className="space-y-4 p-5">
            <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 text-xs text-zinc-500">
              {meta}
            </div>

            <div className="flex flex-wrap items-center gap-2">
              <button
                type="button"
                onClick={publish}
                disabled={pubBusy}
                className="inline-flex items-center gap-1.5 rounded-xl border border-accent/30 bg-accent/[0.08] px-3 py-1.5 text-xs font-medium text-accent-soft transition-colors hover:bg-accent/[0.14] disabled:opacity-50"
              >
                {pubBusy ? (
                  <LoaderInline label="Publishing…" />
                ) : (
                  <>
                    <Globe size={13} /> Get public URL
                  </>
                )}
              </button>
              <a
                href={src}
                download={downloadName}
                className="inline-flex items-center gap-1.5 rounded-xl border border-white/10 px-3 py-1.5 text-xs font-medium text-zinc-300 transition-colors hover:border-white/20 hover:bg-white/[0.04]"
              >
                <Download size={13} /> Download
              </a>
            </div>

            {pubUrl && <PublicUrlBox url={pubUrl} autoCopy />}
            {pubErr && (
              <ErrorNote>
                {pubErr.detail}
                {pubErr.notConnected && (
                  <>
                    {" "}
                    <Link
                      href="/connections"
                      className="font-medium text-accent-soft underline underline-offset-2 hover:text-accent"
                    >
                      Connect Pixio →
                    </Link>
                  </>
                )}
              </ErrorNote>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

/* ---- Small shared bits ----------------------------------------------------------- */

function SkeletonGrid() {
  return (
    <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 xl:grid-cols-4">
      {Array.from({ length: 8 }).map((_, i) => (
        <div key={i} className="card-surface overflow-hidden">
          <Skeleton className="aspect-video w-full" />
          <div className="px-3 py-2.5">
            <Skeleton className="h-3.5 w-2/3" />
          </div>
        </div>
      ))}
    </div>
  );
}

/** Quick-access pinned-folder chips (library home + above the folder grid). */
function PinChips({
  pins,
  onGo,
  onUnpin,
}: {
  pins: PinnedFolder[];
  onGo: (path: string) => void;
  onUnpin: (path: string) => void;
}) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      <Star size={13} className="shrink-0 fill-current text-accent-soft/70" aria-hidden="true" />
      {pins.map((p) => (
        <span
          key={p.path}
          className="inline-flex max-w-[16rem] items-center overflow-hidden rounded-full border border-white/10 bg-white/[0.02]"
        >
          <button
            type="button"
            onClick={() => onGo(p.path)}
            title={p.path}
            className="min-w-0 truncate px-3 py-1.5 text-xs font-medium text-zinc-300 transition-colors hover:text-accent-soft"
          >
            {p.label}
          </button>
          <button
            type="button"
            onClick={() => onUnpin(p.path)}
            aria-label={`Unpin ${p.label}`}
            title="Unpin"
            className="shrink-0 py-1.5 pl-0.5 pr-2 text-zinc-600 transition-colors hover:text-zinc-300"
          >
            <X size={11} />
          </button>
        </span>
      ))}
    </div>
  );
}

/* ---- Page ---------------------------------------------------------------------- */

type Filter = "all" | MediaKind;

const FILTERS: { key: Filter; label: string }[] = [
  { key: "all", label: "All" },
  { key: "image", label: "Images" },
  { key: "video", label: "Video" },
  { key: "audio", label: "Audio" },
];

const VIEWS: { id: View; label: string; icon: ReactNode }[] = [
  { id: "creations", label: "Creations", icon: <Sparkles size={14} /> },
  { id: "library", label: "Library", icon: <FolderOpen size={14} /> },
];

export default function CreativePage() {
  const { data, error, loading, reload } = useApi<{ items: CreativeItem[]; count: number }>(
    "/creative/items?limit=200",
  );
  const [filter, setFilter] = useState<Filter>("all");
  const [selected, setSelected] = useState<CreativeItem | null>(null);
  const closeLightbox = useCallback(() => setSelected(null), []);

  // View switcher — SSR-safe: default Creations, hydrate from localStorage in an
  // effect (this page is statically prerendered, so no lazy-initializer reads).
  const [view, setView] = useState<View>("creations");
  const [libDir, setLibDir] = useState<string | null>(null);
  const [pins, setPins] = useState<PinnedFolder[]>([]);
  useEffect(() => {
    try {
      const v = window.localStorage.getItem(VIEW_KEY);
      if (v === "creations" || v === "library") setView(v);
      const last = window.localStorage.getItem(LASTDIR_KEY);
      if (last) setLibDir(last);
      setPins(parsePins(window.localStorage.getItem(PINS_KEY)));
    } catch {
      /* localStorage unavailable — defaults stand */
    }
  }, []);

  const switchView = (next: View) => {
    setView(next);
    try {
      window.localStorage.setItem(VIEW_KEY, next);
    } catch {
      /* ignore */
    }
  };

  const savePins = (next: PinnedFolder[]) => {
    setPins(next);
    try {
      window.localStorage.setItem(PINS_KEY, JSON.stringify(next));
    } catch {
      /* ignore */
    }
  };
  const isPinned = (path: string) => pins.some((p) => p.path === path);
  const togglePin = (path: string, label: string) => {
    savePins(
      isPinned(path) ? pins.filter((p) => p.path !== path) : [...pins, { path, label }],
    );
  };
  const unpin = (path: string) => savePins(pins.filter((p) => p.path !== path));

  // Library: drives (home screen only) + one-level folder listing.
  const {
    data: drivesData,
    error: drivesError,
    loading: drivesLoading,
  } = useApi<{ drives: Drive[] }>(view === "library" && libDir === null ? "/fs/drives" : null);
  const drives = drivesData?.drives ?? [];

  const [listing, setListing] = useState<FsListing | null>(null);
  const [libLoading, setLibLoading] = useState(false);
  const [libError, setLibError] = useState<ApiError | null>(null);
  const [libSelected, setLibSelected] = useState<LibraryFile | null>(null);

  useEffect(() => {
    if (view !== "library" || libDir === null) return;
    let cancelled = false;
    setLibLoading(true);
    setLibError(null);
    setListing(null);
    get<FsListing>(`/fs/list?path=${encodeURIComponent(libDir)}`)
      .then((d) => {
        if (cancelled) return;
        setListing(d);
        try {
          window.localStorage.setItem(LASTDIR_KEY, d.path);
        } catch {
          /* ignore */
        }
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        setLibError(e instanceof ApiError ? e : new ApiError(String(e), 0));
      })
      .finally(() => {
        if (!cancelled) setLibLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [view, libDir]);

  // Live: refetch the moment the daemon emits artifact.generated (dedupe by event id).
  const { events } = useEvents(50);
  const lastGeneratedId = events.find((e) => e.type === "artifact.generated")?.id;
  const [flash, setFlash] = useState(false);
  useEffect(() => {
    if (!lastGeneratedId) return;
    reload();
    setFlash(true);
    const t = setTimeout(() => setFlash(false), 4000);
    return () => clearTimeout(t);
  }, [lastGeneratedId, reload]);

  // Upload affordance.
  const fileRef = useRef<HTMLInputElement | null>(null);
  const [alsoPublish, setAlsoPublish] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [uploadOk, setUploadOk] = useState<string | null>(null);
  const [uploadErr, setUploadErr] = useState<string | null>(null);
  const [uploadUrl, setUploadUrl] = useState<string | null>(null);

  const onFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = ""; // allow re-picking the same file
    if (!file) return;
    setUploadOk(null);
    setUploadErr(null);
    setUploadUrl(null);
    if (file.size > MAX_UPLOAD_BYTES) {
      setUploadErr(
        `"${file.name}" is ${formatSize(file.size)} — uploads are capped around 100 MB. Try a smaller file.`,
      );
      return;
    }
    setUploading(true);
    try {
      const content_b64 = await readAsBase64(file);
      const res = await post<UploadResult>("/creative/upload", {
        filename: file.name,
        content_b64,
        ...(alsoPublish ? { publish: true } : {}),
      });
      setUploadOk(`Uploaded ${file.name} (${formatSize(res.size)}).`);
      if (res.url) setUploadUrl(res.url);
      if (res.publish_error) setUploadErr(`Upload saved, but publishing failed: ${res.publish_error}`);
      reload();
    } catch (err) {
      const ae = err instanceof ApiError ? err : new ApiError(String(err), 0);
      setUploadErr(ae.status === 0 ? "Daemon offline — could not upload." : ae.message);
    } finally {
      setUploading(false);
    }
  };

  const items = data?.items ?? [];

  // Library derived data (current folder only).
  const folders = useMemo(() => (listing?.entries ?? []).filter((e) => e.is_dir), [listing]);
  const mediaFiles = useMemo<LibraryFile[]>(() => {
    const out: LibraryFile[] = [];
    for (const e of listing?.entries ?? []) {
      if (e.is_dir) continue;
      const kind = mediaKindOf(e.name);
      if (kind) out.push({ path: e.path, name: e.name, kind, size: e.size });
    }
    return out;
  }, [listing]);

  const counts = useMemo(() => {
    const c: Record<Filter, number> = { all: 0, image: 0, video: 0, audio: 0 };
    if (view === "creations") {
      c.all = items.length;
      for (const it of items) c[it.media] = (c[it.media] ?? 0) + 1;
    } else {
      c.all = mediaFiles.length;
      for (const f of mediaFiles) c[f.kind] = (c[f.kind] ?? 0) + 1;
    }
    return c;
  }, [view, items, mediaFiles]);

  const visible = filter === "all" ? items : items.filter((i) => i.media === filter);
  const libVisible = filter === "all" ? mediaFiles : mediaFiles.filter((f) => f.kind === filter);
  const libShown = libVisible.slice(0, LIB_RENDER_CAP);

  const offline = error !== null && error.status === 0;
  const drivesOffline = drivesError !== null && drivesError.status === 0;
  const libOffline = libError !== null && libError.status === 0;

  const curPath = listing?.path ?? libDir;
  const curPinned = curPath !== null && isPinned(curPath);

  const filterRow = (
    <div className="flex flex-wrap items-center gap-2">
      {FILTERS.map((f) => (
        <button
          key={f.key}
          type="button"
          onClick={() => setFilter(f.key)}
          className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-xs font-medium transition-colors ${
            filter === f.key
              ? "border-accent/30 bg-accent/[0.08] text-accent-soft"
              : "border-white/10 text-zinc-400 hover:border-white/20 hover:bg-white/[0.04] hover:text-zinc-200"
          }`}
        >
          {f.label}
          <span className="font-mono text-[10px] opacity-70">{counts[f.key]}</span>
        </button>
      ))}
      {view === "creations" && flash && (
        <span className="inline-flex animate-pulse items-center gap-1.5 rounded-full border border-accent/30 bg-accent/[0.1] px-3 py-1.5 text-xs font-medium text-accent-soft">
          <Sparkles size={12} /> new creation ✨
        </span>
      )}
    </div>
  );

  return (
    <PageShell>
      <Reveal>
        <PageHeader
          title="Creative"
          subtitle={
            view === "library"
              ? "Browse your own media folders — every image, video, and track on this machine. Pin the folders you use most."
              : "Everything Iron Jarvis has made — generations land here automatically. Ask for media in Chat (arm the pixio tools with the + menu) or in an agent session."
          }
          actions={
            <div className="flex flex-wrap items-center gap-3">
              <label className="flex cursor-pointer select-none items-center gap-1.5 text-xs text-zinc-400">
                <input
                  type="checkbox"
                  checked={alsoPublish}
                  onChange={(e) => setAlsoPublish(e.target.checked)}
                  className="h-3.5 w-3.5 accent-cyan-400"
                />
                also get a public URL
              </label>
              <button
                type="button"
                onClick={() => fileRef.current?.click()}
                disabled={uploading}
                className="inline-flex items-center gap-1.5 rounded-xl border border-accent/30 bg-accent/[0.08] px-3 py-1.5 text-xs font-medium text-accent-soft transition-colors hover:bg-accent/[0.14] disabled:opacity-50"
              >
                {uploading ? (
                  <LoaderInline label="Uploading…" />
                ) : (
                  <>
                    <Upload size={13} /> Upload media
                  </>
                )}
              </button>
              <input
                ref={fileRef}
                type="file"
                accept="image/*,video/*,audio/*"
                className="hidden"
                onChange={onFile}
              />
            </div>
          }
        />
      </Reveal>

      <Reveal>
        <div
          role="tablist"
          aria-label="Creative view"
          className="inline-flex items-center gap-1 rounded-2xl border border-white/[0.07] bg-white/[0.02] p-1"
        >
          {VIEWS.map((v) => {
            const selectedTab = v.id === view;
            return (
              <button
                key={v.id}
                type="button"
                role="tab"
                aria-selected={selectedTab}
                onClick={() => switchView(v.id)}
                className={`inline-flex items-center gap-1.5 rounded-xl px-3 py-1.5 text-[13px] font-medium transition-colors ${
                  selectedTab
                    ? "border border-accent/30 bg-accent/[0.12] text-accent-soft"
                    : "border border-transparent text-zinc-400 hover:bg-white/[0.05] hover:text-zinc-200"
                }`}
              >
                {v.icon}
                {v.label}
              </button>
            );
          })}
        </div>
      </Reveal>

      {(uploadOk || uploadErr || uploadUrl) && (
        <Reveal className="space-y-2">
          {uploadOk && <SuccessNote>{uploadOk}</SuccessNote>}
          {uploadErr && <ErrorNote>{uploadErr}</ErrorNote>}
          {uploadUrl && <PublicUrlBox url={uploadUrl} />}
        </Reveal>
      )}

      {view === "creations" ? (
        /* ---- Creations (everything exactly as before) --------------------- */
        <>
          {offline && (
            <Reveal>
              <OfflineHint />
            </Reveal>
          )}
          {error && !offline && (
            <Reveal>
              <ErrorNote>Couldn’t load creations: {error.message}</ErrorNote>
            </Reveal>
          )}

          <Reveal>{filterRow}</Reveal>

          <Reveal>
            {loading && !data ? (
              <SkeletonGrid />
            ) : items.length === 0 ? (
              <Card>
                <Empty
                  icon={<Sparkles size={22} />}
                  action={{ label: "Open Chat", href: "/chat" }}
                >
                  Nothing here yet — ask Iron Jarvis to make something, or upload media to use in
                  generations.
                </Empty>
              </Card>
            ) : visible.length === 0 ? (
              <Card>
                <Empty icon={mediaIcon(filter === "all" ? "image" : filter, 22)}>
                  No {filter} creations yet — try another filter.
                </Empty>
              </Card>
            ) : (
              <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 xl:grid-cols-4">
                {visible.map((item) => (
                  <MediaTile
                    key={`${item.name}:${item.version}`}
                    item={item}
                    onOpen={() => setSelected(item)}
                  />
                ))}
              </div>
            )}
          </Reveal>
        </>
      ) : libDir === null ? (
        /* ---- Library home: pinned folders + drives ------------------------ */
        <>
          {drivesOffline && (
            <Reveal>
              <OfflineHint />
            </Reveal>
          )}
          {drivesError && !drivesOffline && (
            <Reveal>
              <ErrorNote>Couldn’t list drives: {drivesError.message}</ErrorNote>
            </Reveal>
          )}

          {pins.length > 0 && (
            <Reveal>
              <div className="space-y-2">
                <p className="text-[11px] font-medium uppercase tracking-wider text-zinc-500">
                  Pinned folders
                </p>
                <PinChips pins={pins} onGo={setLibDir} onUnpin={unpin} />
              </div>
            </Reveal>
          )}

          <Reveal>
            <div className="space-y-2">
              <p className="text-[11px] font-medium uppercase tracking-wider text-zinc-500">
                Drives
              </p>
              {drivesLoading && drives.length === 0 ? (
                <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-4">
                  {Array.from({ length: 3 }).map((_, i) => (
                    <Skeleton key={i} className="h-14 w-full rounded-2xl" />
                  ))}
                </div>
              ) : drives.length === 0 && !drivesError ? (
                <Card>
                  <Empty icon={<HardDrive size={22} />}>No drives found.</Empty>
                </Card>
              ) : (
                <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-4">
                  {drives.map((d) => (
                    <button
                      key={d.path}
                      type="button"
                      onClick={() => setLibDir(d.path)}
                      title={d.path}
                      className="card-surface flex items-center gap-2.5 px-4 py-3.5 text-left transition-all duration-300 hover:-translate-y-0.5 hover:shadow-card-hover"
                    >
                      <HardDrive size={16} className="shrink-0 text-accent-soft/80" />
                      <span className="min-w-0 truncate text-sm text-zinc-200">{d.label}</span>
                    </button>
                  ))}
                </div>
              )}
            </div>
          </Reveal>
        </>
      ) : (
        /* ---- Library folder view ------------------------------------------ */
        <>
          <Reveal>
            <div className="card-surface flex flex-wrap items-center gap-2 px-4 py-3">
              <button
                type="button"
                onClick={() => setLibDir(null)}
                title="Back to drives"
                className="inline-flex items-center gap-1.5 rounded-lg border border-white/10 px-2.5 py-1.5 text-xs font-medium text-zinc-300 transition-colors hover:border-white/20 hover:bg-white/[0.04]"
              >
                <HardDrive size={13} /> Drives
              </button>
              <button
                type="button"
                onClick={() => {
                  const parent = listing?.parent ?? null;
                  if (parent) setLibDir(parent);
                }}
                disabled={!listing?.parent}
                title="Up one folder"
                aria-label="Up one folder"
                className="inline-flex items-center gap-1.5 rounded-lg border border-white/10 px-2.5 py-1.5 text-xs font-medium text-zinc-300 transition-colors hover:border-white/20 hover:bg-white/[0.04] disabled:cursor-not-allowed disabled:opacity-40"
              >
                <ArrowUp size={13} /> Up
              </button>
              <code
                className="min-w-0 flex-1 truncate font-mono text-xs text-zinc-300"
                title={curPath ?? undefined}
              >
                {curPath}
              </code>
              {curPath !== null && (
                <button
                  type="button"
                  onClick={() => togglePin(curPath, folderLabel(curPath))}
                  title={curPinned ? "Unpin this folder" : "Pin this folder"}
                  aria-label={curPinned ? "Unpin this folder" : "Pin this folder"}
                  aria-pressed={curPinned}
                  className={`shrink-0 rounded-lg border border-transparent p-1.5 transition-colors hover:border-white/10 hover:bg-white/[0.04] ${
                    curPinned ? "text-accent-soft" : "text-zinc-400 hover:text-zinc-200"
                  }`}
                >
                  <Star size={14} className={curPinned ? "fill-current" : undefined} />
                </button>
              )}
            </div>
          </Reveal>

          {libOffline && (
            <Reveal>
              <OfflineHint />
            </Reveal>
          )}
          {libError && !libOffline && (
            <Reveal>
              <ErrorNote>Couldn’t open this folder: {libError.message}</ErrorNote>
            </Reveal>
          )}

          {pins.length > 0 && (
            <Reveal>
              <PinChips pins={pins} onGo={setLibDir} onUnpin={unpin} />
            </Reveal>
          )}

          {folders.length > 0 && (
            <Reveal>
              <div className="flex flex-wrap gap-2">
                {folders.map((f: FsEntry) => (
                  <button
                    key={f.path}
                    type="button"
                    onClick={() => setLibDir(f.path)}
                    title={f.path}
                    className="inline-flex max-w-[16rem] items-center gap-1.5 rounded-xl border border-white/10 bg-white/[0.02] px-3 py-1.5 text-xs text-zinc-300 transition-colors hover:border-accent/30 hover:bg-accent/[0.06] hover:text-accent-soft"
                  >
                    <Folder size={13} className="shrink-0 text-accent-soft/70" />
                    <span className="truncate">{f.name}</span>
                  </button>
                ))}
              </div>
            </Reveal>
          )}

          <Reveal>{filterRow}</Reveal>

          <Reveal>
            {libLoading ? (
              <SkeletonGrid />
            ) : !listing ? null : mediaFiles.length === 0 ? (
              <Card>
                <Empty icon={<FolderOpen size={22} />}>No media in this folder.</Empty>
              </Card>
            ) : libVisible.length === 0 ? (
              <Card>
                <Empty icon={mediaIcon(filter === "all" ? "image" : filter, 22)}>
                  No {filter} files in this folder — try another filter.
                </Empty>
              </Card>
            ) : (
              <div className="space-y-3">
                {libVisible.length > LIB_RENDER_CAP && (
                  <p className="text-[11px] text-zinc-500">
                    Showing the first {LIB_RENDER_CAP} of {libVisible.length} media files in this
                    folder.
                  </p>
                )}
                <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 xl:grid-cols-4">
                  {libShown.map((f) => (
                    <LibraryTile key={f.path} file={f} onOpen={() => setLibSelected(f)} />
                  ))}
                </div>
              </div>
            )}
          </Reveal>
        </>
      )}

      {selected && (
        <MediaLightbox
          key={`${selected.name}:${selected.version}`}
          media={selected.media}
          src={fileSrc(selected)}
          title={selected.filename}
          downloadName={selected.filename}
          publishBody={{ name: selected.name }}
          meta={
            <>
              <span className="font-mono">{formatSize(selected.size)}</span>
              <span>{timeAgo(selected.created_at)}</span>
              <span className="font-mono">v{selected.version}</span>
              {selected.session_id && (
                <Link
                  href={`/sessions/${selected.session_id}`}
                  className="inline-flex items-center gap-1 text-accent-soft transition-colors hover:text-accent"
                >
                  from session <ArrowRight size={12} />
                </Link>
              )}
            </>
          }
          onClose={closeLightbox}
        />
      )}

      {libSelected && (
        <MediaLightbox
          key={libSelected.path}
          media={libSelected.kind}
          src={filePathSrc(libSelected.path)}
          title={libSelected.name}
          downloadName={libSelected.name}
          publishBody={{ path: libSelected.path }}
          meta={
            <>
              {libSelected.size !== null && (
                <span className="font-mono">{formatSize(libSelected.size)}</span>
              )}
              <span
                className="min-w-0 max-w-full truncate font-mono"
                title={libSelected.path}
              >
                {libSelected.path}
              </span>
            </>
          }
          onClose={() => setLibSelected(null)}
        />
      )}
    </PageShell>
  );
}
