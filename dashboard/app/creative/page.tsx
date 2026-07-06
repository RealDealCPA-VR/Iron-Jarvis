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
  ExternalLink,
  Folder,
  FolderOpen,
  FolderPlus,
  HardDrive,
  Link2,
  Mail,
  MessageCircle,
  Play,
  Rocket,
  Send,
  Share2,
  Square,
  Star,
  Terminal,
  Wand2,
} from "lucide-react";
import { API_BASE, ApiError, del, get, ijToken, post } from "@/lib/api";
import { useApi } from "@/lib/useApi";
import { useEvents } from "@/lib/useEvents";
import { timeAgo } from "@/lib/format";
import type { AiCli, Drive, FsEntry, FsListing, Skill } from "@/lib/types";
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

type View = "creations" | "library" | "create";
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

/* ---- Share row ---------------------------------------------------------------- */

const SHARE_TEXT = "Made with Iron Jarvis";

/** Letter-glyph chip for brands lucide no longer ships icons for (X, Facebook, LinkedIn). */
function BrandGlyph({ glyph }: { glyph: string }) {
  return (
    <span
      aria-hidden="true"
      className="grid h-4 w-4 shrink-0 place-items-center rounded-[5px] bg-white/10 text-[9px] font-bold leading-none text-zinc-200"
    >
      {glyph}
    </span>
  );
}

/**
 * Social share buttons for a PUBLISHED (public) url. YouTube has no URL-prefill
 * upload, so that button opens YouTube Studio and triggers the Download action.
 */
function ShareRow({
  url,
  isVideo,
  onYouTubeDownload,
}: {
  url: string;
  isVideo: boolean;
  onYouTubeDownload: () => void;
}) {
  const [copied, setCopied] = useState(false);
  const [ytHint, setYtHint] = useState(false);

  const openShare = (target: string) => {
    window.open(target, "_blank", "noopener,noreferrer");
  };
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(url);
      setCopied(true);
    } catch {
      /* clipboard unavailable — never claim "Copied" without the copy */
    }
  };

  const enc = encodeURIComponent;
  const btnClass =
    "inline-flex items-center gap-1.5 rounded-xl border border-white/10 px-2.5 py-1.5 text-[11px] font-medium text-zinc-300 transition-colors hover:border-white/20 hover:bg-white/[0.04] hover:text-zinc-100";

  return (
    <div className="space-y-1.5">
      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={() => openShare(`https://twitter.com/intent/tweet?url=${enc(url)}&text=${enc(SHARE_TEXT)}`)}
          title="Share on X (Twitter)"
          className={btnClass}
        >
          <BrandGlyph glyph="X" /> X (Twitter)
        </button>
        <button
          type="button"
          onClick={() => openShare(`https://www.facebook.com/sharer/sharer.php?u=${enc(url)}`)}
          title="Share on Facebook"
          className={btnClass}
        >
          <BrandGlyph glyph="f" /> Facebook
        </button>
        <button
          type="button"
          onClick={() => openShare(`https://www.linkedin.com/sharing/share-offsite/?url=${enc(url)}`)}
          title="Share on LinkedIn"
          className={btnClass}
        >
          <BrandGlyph glyph="in" /> LinkedIn
        </button>
        <button
          type="button"
          onClick={() => openShare(`https://wa.me/?text=${enc(url)}`)}
          title="Share on WhatsApp"
          className={btnClass}
        >
          <MessageCircle size={13} /> WhatsApp
        </button>
        <button
          type="button"
          onClick={() => openShare(`https://t.me/share/url?url=${enc(url)}`)}
          title="Share on Telegram"
          className={btnClass}
        >
          <Send size={13} /> Telegram
        </button>
        <button
          type="button"
          onClick={() => openShare(`mailto:?subject=${enc("Sharing a creation")}&body=${enc(url)}`)}
          title="Share by email"
          className={btnClass}
        >
          <Mail size={13} /> Email
        </button>
        <button
          type="button"
          onClick={() => void copy()}
          title="Copy the public link"
          className={btnClass}
        >
          {copied ? (
            <>
              <Check size={13} className="text-emerald-400" /> Copied ✓
            </>
          ) : (
            <>
              <Link2 size={13} /> Copy link
            </>
          )}
        </button>
        {isVideo && (
          <button
            type="button"
            onClick={() => {
              openShare("https://studio.youtube.com/channel/upload");
              onYouTubeDownload();
              setYtHint(true);
            }}
            title="YouTube can't take a URL — this opens the upload page and downloads the file"
            className={btnClass}
          >
            <Play size={13} /> YouTube
          </button>
        )}
      </div>
      {ytHint && (
        <p className="text-[11px] text-zinc-500">
          YouTube needs the file itself — download it, then drop it into the upload page.
        </p>
      )}
      <p className="text-[11px] text-zinc-500">The link is public — anyone with it can view.</p>
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
  const [shareOpen, setShareOpen] = useState(false);
  const downloadRef = useRef<HTMLAnchorElement | null>(null);

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

  /** Sharing needs the PUBLIC url — publish on first click, same 424 handling. */
  const share = async () => {
    setShareOpen(true);
    if (!pubUrl && !pubBusy) await publish();
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
              <button
                type="button"
                onClick={() => void share()}
                disabled={pubBusy}
                className="inline-flex items-center gap-1.5 rounded-xl border border-white/10 px-3 py-1.5 text-xs font-medium text-zinc-300 transition-colors hover:border-white/20 hover:bg-white/[0.04] disabled:opacity-50"
              >
                {pubBusy && shareOpen ? (
                  <LoaderInline label="Preparing…" />
                ) : (
                  <>
                    <Share2 size={13} /> Share
                  </>
                )}
              </button>
              <a
                ref={downloadRef}
                href={src}
                download={downloadName}
                className="inline-flex items-center gap-1.5 rounded-xl border border-white/10 px-3 py-1.5 text-xs font-medium text-zinc-300 transition-colors hover:border-white/20 hover:bg-white/[0.04]"
              >
                <Download size={13} /> Download
              </a>
            </div>

            {pubUrl && <PublicUrlBox url={pubUrl} autoCopy />}
            {shareOpen && pubUrl && (
              <ShareRow
                url={pubUrl}
                isVideo={media === "video"}
                onYouTubeDownload={() => downloadRef.current?.click()}
              />
            )}
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

/* ---- Create (studio) --------------------------------------------------------- */

const STUDIO_KEY = "ironjarvis.creative.studio";
/** Snapshot cap — enough to diff any sane destination folder without bloating storage. */
const STUDIO_BASELINE_CAP = 1000;

/** A live studio session, persisted so a page unmount doesn't lose the terminal. */
interface StudioSession {
  terminal_id: string;
  dest: string;
  cli_label: string;
  command: string;
  sent_first: boolean;
  /** Media filenames already in the destination when the session started. */
  baseline: string[];
  /** Briefs sent so far (the user side of the chat). */
  messages: string[];
  started_at: number;
}

/** Everything the studio remembers between visits (one localStorage key). */
interface StudioStore {
  cli?: string;
  skill?: string;
  dir?: string;
  autopilot?: boolean;
  session?: StudioSession;
}

function readStudioStore(): StudioStore {
  if (typeof window === "undefined") return {};
  try {
    const raw = window.localStorage.getItem(STUDIO_KEY);
    if (!raw) return {};
    const parsed: unknown = JSON.parse(raw);
    return parsed && typeof parsed === "object" ? (parsed as StudioStore) : {};
  } catch {
    return {};
  }
}

function writeStudioStore(patch: Partial<StudioStore>): void {
  try {
    window.localStorage.setItem(STUDIO_KEY, JSON.stringify({ ...readStudioStore(), ...patch }));
  } catch {
    /* ignore */
  }
}

function patchStoredSession(patch: Partial<StudioSession>): void {
  const cur = readStudioStore().session;
  if (cur) writeStudioStore({ session: { ...cur, ...patch } });
}

/** Windows-aware join: "\" when the base uses backslashes, "/" otherwise. */
function joinPath(base: string, name: string): string {
  const sep = base.includes("\\") ? "\\" : "/";
  return base.replace(/[\\/]+$/, "") + sep + name;
}

/** Media skills only: pixio/seedance families, or media words in the description. */
function isMediaSkill(s: Skill): boolean {
  return (
    /^(pixio|seedance)/i.test(s.name) ||
    /\b(image|video|audio|music|media|story)\b/i.test(s.description ?? "")
  );
}

interface StudioTail {
  tail: string;
  alive: boolean;
  exit_code: number | null;
  /** Human-readable CLI mode line (e.g. "auto-accept edits on") — null when unknown. */
  mode: string | null;
  /** True once the daemon has verified an auto/full-auto mode is engaged. */
  automode: boolean;
}

interface StudioStartResult {
  terminal_id: string;
  command: string;
  cwd: string;
  autopilot: boolean;
  cli: string;
  /** How autopilot engages: Shift+Tab after boot (claude), a CLI flag (codex), or null. */
  automode_method?: "shift-tab" | "flag" | null;
}

/** In-memory shape of the running session (camelCase mirror of StudioSession). */
interface LiveSession {
  terminalId: string;
  dest: string;
  cliLabel: string;
  command: string;
  /** Epoch ms — drives the "auto mode: engaging…" grace window on the badge. */
  startedAt: number;
}

/**
 * The CREATE view: pick an engine (AI CLI) + skill + destination folder, launch
 * a background terminal (it appears on Build), brief it chat-style, and watch
 * new media land in the folder — the honest completion signal.
 */
function StudioView({
  pins,
  onUnpin,
}: {
  pins: PinnedFolder[];
  onUnpin: (path: string) => void;
}) {
  // Setup defaults from the last visit (this component only mounts client-side).
  const [initialStore] = useState<StudioStore>(readStudioStore);
  const [cli, setCli] = useState<string>(initialStore.cli ?? "");
  const [skill, setSkill] = useState<string>(initialStore.skill ?? "");
  const [autopilot, setAutopilot] = useState<boolean>(initialStore.autopilot ?? true);
  const [pickDir, setPickDir] = useState<string | null>(initialStore.dir ?? null);

  // Engines + skills.
  const {
    data: clisData,
    error: clisError,
    loading: clisLoading,
  } = useApi<{ clis: AiCli[] }>("/terminals/ai-clis");
  const { data: skillsData } = useApi<{ skills: Skill[] }>("/skills");
  const clis = useMemo(() => clisData?.clis ?? [], [clisData]);
  const mediaSkills = useMemo(
    () => (skillsData?.skills ?? []).filter(isMediaSkill),
    [skillsData],
  );

  // Default engine: keep a still-installed stored pick, else claude, else first installed.
  useEffect(() => {
    if (clis.length === 0) return;
    setCli((prev) => {
      if (prev && clis.some((c) => c.id === prev && c.installed)) return prev;
      const preferred =
        clis.find((c) => c.id === "claude" && c.installed) ?? clis.find((c) => c.installed);
      return preferred?.id ?? "";
    });
  }, [clis]);

  // Remember setup choices so repeat sessions are two clicks.
  useEffect(() => {
    writeStudioStore({ ...(cli ? { cli } : {}), skill, autopilot });
  }, [cli, skill, autopilot]);

  // Destination picker — same navigation pieces as the Library view, compact.
  const {
    data: drivesData,
    error: drivesError,
    loading: drivesLoading,
  } = useApi<{ drives: Drive[] }>(pickDir === null ? "/fs/drives" : null);
  const drives = drivesData?.drives ?? [];

  const [pickListing, setPickListing] = useState<FsListing | null>(null);
  const [pickLoading, setPickLoading] = useState(false);
  const [pickError, setPickError] = useState<ApiError | null>(null);

  useEffect(() => {
    if (pickDir === null) {
      setPickListing(null);
      return;
    }
    let cancelled = false;
    setPickLoading(true);
    setPickError(null);
    setPickListing(null);
    get<FsListing>(`/fs/list?path=${encodeURIComponent(pickDir)}`)
      .then((d) => {
        if (cancelled) return;
        setPickListing(d);
        writeStudioStore({ dir: d.path });
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        setPickError(e instanceof ApiError ? e : new ApiError(String(e), 0));
      })
      .finally(() => {
        if (!cancelled) setPickLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [pickDir]);

  const chosenDir = pickListing?.path ?? null;
  const pickFolders = useMemo(
    () => (pickListing?.entries ?? []).filter((e) => e.is_dir),
    [pickListing],
  );

  // New-subfolder affordance.
  const [newFolder, setNewFolder] = useState("");
  const [mkdirBusy, setMkdirBusy] = useState(false);
  const [mkdirErr, setMkdirErr] = useState<string | null>(null);
  const createSubfolder = async () => {
    const name = newFolder.trim();
    if (!name || !chosenDir || mkdirBusy) return;
    setMkdirBusy(true);
    setMkdirErr(null);
    try {
      const res = await post<{ path: string; created: boolean }>("/fs/mkdir", {
        path: joinPath(chosenDir, name),
      });
      setNewFolder("");
      setPickDir(res.path); // descend into the new folder
    } catch (e) {
      setMkdirErr(e instanceof ApiError ? e.message : String(e));
    } finally {
      setMkdirBusy(false);
    }
  };

  // LIVE phase state.
  const [session, setSession] = useState<LiveSession | null>(null);
  const [resumeOffer, setResumeOffer] = useState<StudioSession | null>(null);
  const [startBusy, setStartBusy] = useState(false);
  const [startErr, setStartErr] = useState<{ detail: string; installUrl: string | null } | null>(
    null,
  );

  const [booting, setBooting] = useState(false);
  const [messages, setMessages] = useState<string[]>([]);
  const [sentFirst, setSentFirst] = useState(false);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [sayErr, setSayErr] = useState<string | null>(null);
  const [ending, setEnding] = useState(false);

  const [tail, setTail] = useState("");
  const [alive, setAlive] = useState(true);
  const [exitCode, setExitCode] = useState<number | null>(null);
  const [gone, setGone] = useState(false); // tail endpoint 404'd — terminal deleted
  const [mode, setMode] = useState<string | null>(null);
  const [automode, setAutomode] = useState(false);
  // Badge grace window: "engaging…" for ~30s after start, then honest "unverified".
  const [engaging, setEngaging] = useState(true);

  const baselineRef = useRef<Set<string>>(new Set());
  const [newFiles, setNewFiles] = useState<LibraryFile[]>([]);
  const [studioSelected, setStudioSelected] = useState<LibraryFile | null>(null);

  // Resume detection: a stored session whose terminal is still alive on the daemon.
  useEffect(() => {
    const saved = readStudioStore().session;
    if (!saved) return;
    let cancelled = false;
    get<StudioTail>(`/creative/studio/${saved.terminal_id}/tail?chars=1`)
      .then((t) => {
        if (cancelled) return;
        if (t.alive) setResumeOffer(saved);
        else writeStudioStore({ session: undefined }); // exited — nothing to resume
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        if (e instanceof ApiError && e.status === 404) writeStudioStore({ session: undefined });
        // Offline/transient errors: keep the stored session for the next visit.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // "Engine starting…" grace period so the first brief lands after the CLI booted.
  useEffect(() => {
    if (!booting) return;
    const t = setTimeout(() => setBooting(false), 3000);
    return () => clearTimeout(t);
  }, [booting]);

  // Automode badge: "engaging…" for the first ~30s after start, then "unverified".
  useEffect(() => {
    if (!session) return;
    const remaining = session.startedAt + 30_000 - Date.now();
    if (remaining <= 0) {
      setEngaging(false);
      return;
    }
    setEngaging(true);
    const t = setTimeout(() => setEngaging(false), remaining);
    return () => clearTimeout(t);
  }, [session]);

  // Console tail: poll every 2s while alive; stop (and show exit) when it isn't.
  useEffect(() => {
    if (!session || gone) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const poll = async () => {
      try {
        const t = await get<StudioTail>(
          `/creative/studio/${session.terminalId}/tail?chars=4000`,
          { timeoutMs: 8000 },
        );
        if (cancelled) return;
        setTail(t.tail);
        setAlive(t.alive);
        setExitCode(t.exit_code);
        setMode(t.mode ?? null);
        setAutomode(t.automode === true);
        if (t.alive) timer = setTimeout(poll, 2000);
      } catch (e) {
        if (cancelled) return;
        if (e instanceof ApiError && e.status === 404) {
          setGone(true);
          setAlive(false);
          writeStudioStore({ session: undefined });
        } else {
          timer = setTimeout(poll, 2000); // transient — keep watching
        }
      }
    };
    void poll();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [session, gone]);

  // New-media watcher: diff the destination against the session-start snapshot.
  useEffect(() => {
    if (!session) return;
    let cancelled = false;
    const tick = () => {
      get<FsListing>(`/fs/list?path=${encodeURIComponent(session.dest)}`, { timeoutMs: 15000 })
        .then((d) => {
          if (cancelled) return;
          const out: LibraryFile[] = [];
          for (const e of d.entries) {
            if (e.is_dir) continue;
            const kind = mediaKindOf(e.name);
            if (kind && !baselineRef.current.has(e.name))
              out.push({ path: e.path, name: e.name, kind, size: e.size });
          }
          setNewFiles(out);
        })
        .catch(() => {
          /* transient — the next tick retries */
        });
    };
    tick();
    const id = setInterval(tick, 5000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [session]);

  // Console auto-scroll — sticks to the bottom unless the user scrolled up.
  const consoleRef = useRef<HTMLDivElement | null>(null);
  const stickRef = useRef(true);
  const onConsoleScroll = () => {
    const el = consoleRef.current;
    if (!el) return;
    stickRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 48;
  };
  useEffect(() => {
    const el = consoleRef.current;
    if (el && stickRef.current) el.scrollTop = el.scrollHeight;
  }, [tail]);

  const resetLive = useCallback((clearStore: boolean) => {
    if (clearStore) writeStudioStore({ session: undefined });
    setSession(null);
    setTail("");
    setMessages([]);
    setSentFirst(false);
    setNewFiles([]);
    setGone(false);
    setAlive(true);
    setExitCode(null);
    setMode(null);
    setAutomode(false);
    setSayErr(null);
    setStudioSelected(null);
  }, []);

  const start = async () => {
    if (!cli || !chosenDir || startBusy) return;
    setStartBusy(true);
    setStartErr(null);
    // Snapshot the destination BEFORE launching — anything after this counts as new.
    let baseline: string[] = [];
    try {
      const d = await get<FsListing>(`/fs/list?path=${encodeURIComponent(chosenDir)}`);
      baseline = d.entries
        .filter((e) => !e.is_dir && mediaKindOf(e.name) !== null)
        .map((e) => e.name)
        .slice(0, STUDIO_BASELINE_CAP);
    } catch {
      /* unlistable folder — diff against empty; /start will surface a real 400 */
    }
    try {
      const res = await post<StudioStartResult>("/creative/studio/start", {
        cli,
        cwd: chosenDir,
        ...(skill ? { skill } : {}),
        autopilot,
      });
      const engine = clis.find((c) => c.id === cli);
      const live: LiveSession = {
        terminalId: res.terminal_id,
        dest: res.cwd || chosenDir,
        cliLabel: engine?.label ?? cli,
        command: res.command,
        startedAt: Date.now(),
      };
      baselineRef.current = new Set(baseline);
      resetLive(false);
      setSession(live);
      setBooting(true);
      writeStudioStore({
        session: {
          terminal_id: live.terminalId,
          dest: live.dest,
          cli_label: live.cliLabel,
          command: live.command,
          sent_first: false,
          baseline,
          messages: [],
          started_at: live.startedAt,
        },
      });
    } catch (e) {
      const err = e instanceof ApiError ? e : new ApiError(String(e), 0);
      const engine = clis.find((c) => c.id === cli);
      setStartErr({
        detail: err.status === 0 ? "Daemon offline — could not start the session." : err.message,
        installUrl: err.status === 424 ? (engine?.url ?? null) : null,
      });
    } finally {
      setStartBusy(false);
    }
  };

  const resume = () => {
    if (!resumeOffer) return;
    baselineRef.current = new Set(resumeOffer.baseline);
    resetLive(false);
    setMessages(resumeOffer.messages);
    setSentFirst(resumeOffer.sent_first);
    setSession({
      terminalId: resumeOffer.terminal_id,
      dest: resumeOffer.dest,
      cliLabel: resumeOffer.cli_label,
      command: resumeOffer.command,
      startedAt: resumeOffer.started_at,
    });
    setBooting(false);
    setResumeOffer(null);
  };

  const send = async () => {
    const text = draft.trim();
    if (!text || !session || sending || booting || !alive || gone) return;
    setSending(true);
    setSayErr(null);
    try {
      await post<{ typed: boolean }>(
        `/creative/studio/${session.terminalId}/say`,
        sentFirst
          ? { text }
          : { text, first: true, ...(skill ? { skill } : {}), save_dir: session.dest },
      );
      const nextMsgs = [...messages, text];
      setMessages(nextMsgs);
      setSentFirst(true);
      setDraft("");
      patchStoredSession({ sent_first: true, messages: nextMsgs });
    } catch (e) {
      const err = e instanceof ApiError ? e : new ApiError(String(e), 0);
      setSayErr(
        err.status === 404 || err.status === 409
          ? `The terminal is gone or has exited — ${err.message}`
          : err.status === 0
            ? "Daemon offline — message not sent."
            : err.message,
      );
    } finally {
      setSending(false);
    }
  };

  const endSession = async () => {
    if (!session || ending) return;
    if (!window.confirm("End this session? The terminal will be closed.")) return;
    setEnding(true);
    try {
      await del(`/terminals/${session.terminalId}`);
    } catch (e) {
      if (!(e instanceof ApiError && e.status === 404)) {
        setSayErr(
          e instanceof ApiError ? `Could not end the session: ${e.message}` : String(e),
        );
        setEnding(false);
        return;
      }
      // 404 = already gone — fall through and clean up.
    }
    setEnding(false);
    resetLive(true);
  };

  /* ---- LIVE phase ---- */
  if (session) {
    const inputDisabled = booting || !alive || gone || sending;
    return (
      <>
        <Reveal>
          <div className="card-surface flex flex-wrap items-center gap-x-4 gap-y-2 px-4 py-3">
            <span className="inline-flex shrink-0 items-center gap-2 text-sm font-semibold text-zinc-200">
              <Terminal size={15} className="text-accent-soft/80" />
              {session.cliLabel}
            </span>
            <code
              className="max-w-[18rem] truncate rounded bg-black/40 px-2 py-0.5 font-mono text-[11px] text-zinc-400"
              title={session.command}
            >
              {session.command}
            </code>
            {alive &&
              !gone &&
              (automode ? (
                <span
                  title={mode ?? "auto mode verified"}
                  className="inline-flex shrink-0 items-center gap-1.5 rounded-full border border-accent/30 bg-accent/[0.1] px-2.5 py-0.5 text-[11px] font-medium text-accent-soft"
                >
                  <span className="h-1.5 w-1.5 rounded-full bg-accent" aria-hidden="true" />
                  auto mode on
                </span>
              ) : (
                <span
                  title={
                    engaging
                      ? "Waiting for the CLI to confirm its mode…"
                      : "The CLI never confirmed an auto mode — open the terminal in Build to check."
                  }
                  className="inline-flex max-w-[22rem] items-center rounded-full border border-white/10 bg-white/[0.03] px-2.5 py-0.5 text-[11px] font-medium text-zinc-500"
                >
                  <span className="truncate">
                    {engaging
                      ? "auto mode: engaging…"
                      : "auto mode unverified — check the terminal in Build"}
                  </span>
                </span>
              ))}
            <code
              className="min-w-0 flex-1 truncate font-mono text-[11px] text-zinc-500"
              title={session.dest}
            >
              → {session.dest}
            </code>
            <Link
              href="/terminals"
              className="inline-flex shrink-0 items-center gap-1 text-xs font-medium text-accent-soft transition-colors hover:text-accent"
            >
              Open in Build <ArrowRight size={12} />
            </Link>
            <button
              type="button"
              onClick={endSession}
              disabled={ending}
              className="inline-flex shrink-0 items-center gap-1.5 rounded-xl border border-rose-500/25 bg-rose-500/[0.06] px-3 py-1.5 text-xs font-medium text-rose-300 transition-colors hover:bg-rose-500/[0.12] disabled:opacity-50"
            >
              {ending ? (
                <LoaderInline label="Ending…" />
              ) : (
                <>
                  <Square size={12} /> End session
                </>
              )}
            </button>
          </div>
        </Reveal>

        {(!alive || gone) && (
          <Reveal>
            <ErrorNote>
              {gone
                ? "This terminal no longer exists on the daemon."
                : `Terminal exited (code ${exitCode ?? "?"}).`}{" "}
              <button
                type="button"
                onClick={() => resetLive(true)}
                className="font-medium underline underline-offset-2 hover:text-rose-100"
              >
                Set up a new session
              </button>
            </ErrorNote>
          </Reveal>
        )}

        <Reveal>
          <Card title="Brief" icon={<Send size={15} />}>
            <div className="space-y-3">
              {messages.length === 0 ? (
                <p className="text-xs text-zinc-500">
                  Everything you type here is typed straight into the CLI. Your first message
                  becomes the brief — the daemon wraps it with the chosen skill and the save
                  folder, and the run continues on autopilot.
                </p>
              ) : (
                <div className="max-h-52 space-y-2 overflow-y-auto pr-1">
                  {messages.map((m, i) => (
                    <div key={i} className="flex justify-end">
                      <div className="max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-br-md border border-accent/25 bg-accent/[0.08] px-3.5 py-2 text-[13px] text-zinc-200">
                        {m}
                      </div>
                    </div>
                  ))}
                </div>
              )}
              {sayErr && <ErrorNote>{sayErr}</ErrorNote>}
              <div className="flex items-center gap-2">
                <input
                  type="text"
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") {
                      e.preventDefault();
                      void send();
                    }
                  }}
                  disabled={inputDisabled}
                  placeholder={
                    booting
                      ? "engine starting…"
                      : !alive || gone
                        ? "terminal ended"
                        : sentFirst
                          ? "Send a follow-up…"
                          : "Describe what to create…"
                  }
                  aria-label="Brief"
                  className="min-w-0 flex-1 rounded-xl border border-white/10 bg-ink-950 px-3 py-2 text-sm text-zinc-200 placeholder:text-zinc-600 focus:border-accent/40 focus:outline-none disabled:opacity-50"
                />
                <button
                  type="button"
                  onClick={() => void send()}
                  disabled={inputDisabled || !draft.trim()}
                  aria-label="Send"
                  className="inline-flex shrink-0 items-center gap-1.5 rounded-xl border border-accent/30 bg-accent/[0.08] px-3 py-2 text-xs font-medium text-accent-soft transition-colors hover:bg-accent/[0.14] disabled:opacity-50"
                >
                  {sending ? <LoaderInline label="Sending…" /> : <Send size={14} />}
                </button>
              </div>
            </div>
          </Card>
        </Reveal>

        <Reveal>
          <Card
            title="Console"
            icon={<Terminal size={15} />}
            right={
              alive && !gone ? (
                <span className="inline-flex items-center gap-1.5 text-[11px] text-emerald-300">
                  <span
                    className="h-1.5 w-1.5 animate-pulse rounded-full bg-emerald-400"
                    aria-hidden="true"
                  />
                  live
                </span>
              ) : (
                <span className="text-[11px] text-zinc-500">
                  exited{exitCode !== null ? ` (code ${exitCode})` : ""}
                </span>
              )
            }
          >
            <div
              ref={consoleRef}
              onScroll={onConsoleScroll}
              className="max-h-[40vh] overflow-y-auto rounded-xl border border-white/[0.06] bg-ink-950 p-3"
            >
              <pre className="whitespace-pre-wrap break-words font-mono text-[11px] leading-relaxed text-zinc-400">
                {tail || (booting ? "engine starting…" : "waiting for output…")}
              </pre>
            </div>
          </Card>
        </Reveal>

        <Reveal>
          <Card
            title="New media"
            icon={<Sparkles size={15} />}
            right={
              <span className="text-[11px] text-zinc-500">
                {newFiles.length === 0
                  ? "watching the folder…"
                  : `${newFiles.length} file${newFiles.length === 1 ? "" : "s"} created so far`}
              </span>
            }
          >
            {newFiles.length === 0 ? (
              <p className="py-4 text-center text-xs text-zinc-500">
                Nothing yet — media that lands in{" "}
                <code className="font-mono text-zinc-400">{folderLabel(session.dest)}</code> shows
                up here as it’s created.
              </p>
            ) : (
              <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 xl:grid-cols-4">
                {newFiles.map((f) => (
                  <LibraryTile key={f.path} file={f} onOpen={() => setStudioSelected(f)} />
                ))}
              </div>
            )}
          </Card>
        </Reveal>

        {studioSelected && (
          <MediaLightbox
            key={studioSelected.path}
            media={studioSelected.kind}
            src={filePathSrc(studioSelected.path)}
            title={studioSelected.name}
            downloadName={studioSelected.name}
            publishBody={{ path: studioSelected.path }}
            meta={
              <>
                {studioSelected.size !== null && (
                  <span className="font-mono">{formatSize(studioSelected.size)}</span>
                )}
                <span
                  className="min-w-0 max-w-full truncate font-mono"
                  title={studioSelected.path}
                >
                  {studioSelected.path}
                </span>
              </>
            }
            onClose={() => setStudioSelected(null)}
          />
        )}
      </>
    );
  }

  /* ---- Resume offer (a stored session is still running) ---- */
  if (resumeOffer) {
    return (
      <Reveal>
        <Card title="Session still running" icon={<Terminal size={15} />}>
          <div className="space-y-3">
            <p className="text-sm text-zinc-400">
              Your last studio session — <span className="text-zinc-200">{resumeOffer.cli_label}</span>{" "}
              in{" "}
              <code className="font-mono text-xs text-zinc-300" title={resumeOffer.dest}>
                {resumeOffer.dest}
              </code>{" "}
              — is still running in the background.
            </p>
            <div className="flex flex-wrap items-center gap-2">
              <button
                type="button"
                onClick={resume}
                className="inline-flex items-center gap-1.5 rounded-xl border border-accent/30 bg-accent/[0.1] px-4 py-2 text-sm font-semibold text-accent-soft transition-colors hover:bg-accent/[0.16]"
              >
                <Play size={14} /> Resume session
              </button>
              <Link
                href="/terminals"
                className="inline-flex items-center gap-1 rounded-xl border border-white/10 px-3 py-2 text-xs font-medium text-zinc-300 transition-colors hover:border-white/20 hover:bg-white/[0.04]"
              >
                Open in Build <ArrowRight size={12} />
              </Link>
              <button
                type="button"
                onClick={() => {
                  writeStudioStore({ session: undefined });
                  setResumeOffer(null);
                }}
                className="rounded-xl border border-white/10 px-3 py-2 text-xs font-medium text-zinc-400 transition-colors hover:border-white/20 hover:bg-white/[0.04] hover:text-zinc-200"
              >
                Start fresh (leave it running)
              </button>
            </div>
          </div>
        </Card>
      </Reveal>
    );
  }

  /* ---- SETUP phase ---- */
  const chosenSkill = skill ? mediaSkills.find((s) => s.name === skill) : undefined;
  return (
    <>
      <Reveal>
        <Card title="1 · Engine" icon={<Terminal size={15} />}>
          <div className="space-y-3">
            {clisError &&
              (clisError.status === 0 ? (
                <OfflineHint />
              ) : (
                <ErrorNote>Couldn’t detect AI CLIs: {clisError.message}</ErrorNote>
              ))}
            {clisLoading && clis.length === 0 ? (
              <div className="grid gap-2 sm:grid-cols-2">
                {Array.from({ length: 2 }).map((_, i) => (
                  <Skeleton key={i} className="h-16 w-full rounded-2xl" />
                ))}
              </div>
            ) : (
              <div className="grid gap-2 sm:grid-cols-2" role="radiogroup" aria-label="Engine">
                {clis.map((c) =>
                  c.installed ? (
                    <button
                      key={c.id}
                      type="button"
                      role="radio"
                      aria-checked={cli === c.id}
                      onClick={() => setCli(c.id)}
                      className={`flex items-start gap-2.5 rounded-2xl border px-3.5 py-3 text-left transition-colors ${
                        cli === c.id
                          ? "border-accent/40 bg-accent/[0.08]"
                          : "border-white/10 bg-white/[0.02] hover:border-white/20 hover:bg-white/[0.04]"
                      }`}
                    >
                      <Terminal
                        size={15}
                        className={`mt-0.5 shrink-0 ${cli === c.id ? "text-accent-soft" : "text-zinc-500"}`}
                      />
                      <span className="min-w-0 flex-1">
                        <span className="block text-sm font-medium text-zinc-200">{c.label}</span>
                        <span className="block truncate text-[11px] text-zinc-500">
                          {c.provider} · <code className="font-mono">{c.command}</code>
                        </span>
                      </span>
                      {cli === c.id && (
                        <Check size={14} className="mt-0.5 shrink-0 text-accent-soft" />
                      )}
                    </button>
                  ) : (
                    <div
                      key={c.id}
                      className="flex items-start gap-2.5 rounded-2xl border border-white/[0.06] bg-white/[0.01] px-3.5 py-3 opacity-70"
                    >
                      <Terminal size={15} className="mt-0.5 shrink-0 text-zinc-600" />
                      <span className="min-w-0 flex-1">
                        <span className="block text-sm font-medium text-zinc-500">{c.label}</span>
                        <span className="block text-[11px] text-zinc-600">
                          {c.provider} · not installed
                        </span>
                      </span>
                      <a
                        href={c.url}
                        target="_blank"
                        rel="noreferrer"
                        className="inline-flex shrink-0 items-center gap-1 text-[11px] font-medium text-accent-soft transition-colors hover:text-accent"
                      >
                        Install <ExternalLink size={11} />
                      </a>
                    </div>
                  ),
                )}
                {clis.length === 0 && !clisLoading && !clisError && (
                  <p className="text-xs text-zinc-500 sm:col-span-2">
                    No AI CLIs detected on this machine.
                  </p>
                )}
              </div>
            )}
            <label className="flex cursor-pointer select-none items-start gap-2 text-xs text-zinc-400">
              <input
                type="checkbox"
                checked={autopilot}
                onChange={(e) => setAutopilot(e.target.checked)}
                className="mt-0.5 h-3.5 w-3.5 accent-cyan-400"
              />
              <span>
                <span className="font-medium text-zinc-300">Autopilot</span> — runs unattended.
                For Claude: engages auto-accept via Shift+Tab after boot (no permission prompts);
                for Codex: launches with <code className="font-mono">--full-auto</code>.
              </span>
            </label>
          </div>
        </Card>
      </Reveal>

      <Reveal>
        <Card title="2 · Skill" icon={<Wand2 size={15} />}>
          <div className="space-y-2">
            <select
              value={skill}
              onChange={(e) => setSkill(e.target.value)}
              aria-label="Skill"
              className="w-full appearance-none rounded-xl border border-white/10 bg-ink-950 px-3 py-2 text-sm text-zinc-200 focus:border-accent/40 focus:outline-none"
            >
              <option value="">Auto — let the agent pick the best skill</option>
              {skill && !mediaSkills.some((s) => s.name === skill) && (
                <option value={skill}>{skill}</option>
              )}
              {mediaSkills.map((s) => (
                <option key={s.name} value={s.name}>
                  {s.name}
                </option>
              ))}
            </select>
            <p className="text-[11px] text-zinc-500">
              {skill
                ? (chosenSkill?.description ?? "Skill not found in the current registry.")
                : "The agent reads your brief and chooses the right media skill itself."}
            </p>
          </div>
        </Card>
      </Reveal>

      <Reveal>
        <Card title="3 · Destination folder" icon={<FolderOpen size={15} />}>
          <div className="space-y-3">
            <div className="flex flex-wrap items-center gap-2">
              <button
                type="button"
                onClick={() => setPickDir(null)}
                title="Back to drives"
                className="inline-flex items-center gap-1.5 rounded-lg border border-white/10 px-2.5 py-1.5 text-xs font-medium text-zinc-300 transition-colors hover:border-white/20 hover:bg-white/[0.04]"
              >
                <HardDrive size={13} /> Drives
              </button>
              <button
                type="button"
                onClick={() => {
                  const parent = pickListing?.parent ?? null;
                  if (parent) setPickDir(parent);
                }}
                disabled={!pickListing?.parent}
                title="Up one folder"
                aria-label="Up one folder"
                className="inline-flex items-center gap-1.5 rounded-lg border border-white/10 px-2.5 py-1.5 text-xs font-medium text-zinc-300 transition-colors hover:border-white/20 hover:bg-white/[0.04] disabled:cursor-not-allowed disabled:opacity-40"
              >
                <ArrowUp size={13} /> Up
              </button>
              <code
                className={`min-w-0 flex-1 truncate font-mono text-xs ${chosenDir ? "text-accent-soft" : "text-zinc-500"}`}
                title={chosenDir ?? undefined}
              >
                {chosenDir ?? (pickLoading ? pickDir : "choose a folder below")}
              </code>
            </div>

            {pins.length > 0 && <PinChips pins={pins} onGo={setPickDir} onUnpin={onUnpin} />}

            {pickError &&
              (pickError.status === 0 ? (
                <OfflineHint />
              ) : (
                <ErrorNote>Couldn’t open this folder: {pickError.message}</ErrorNote>
              ))}
            {drivesError && pickDir === null && drivesError.status !== 0 && (
              <ErrorNote>Couldn’t list drives: {drivesError.message}</ErrorNote>
            )}
            {drivesError && pickDir === null && drivesError.status === 0 && <OfflineHint />}

            {pickDir === null ? (
              drivesLoading && drives.length === 0 ? (
                <div className="flex flex-wrap gap-2">
                  {Array.from({ length: 3 }).map((_, i) => (
                    <Skeleton key={i} className="h-8 w-24 rounded-xl" />
                  ))}
                </div>
              ) : (
                <div className="flex flex-wrap gap-2">
                  {drives.map((d) => (
                    <button
                      key={d.path}
                      type="button"
                      onClick={() => setPickDir(d.path)}
                      title={d.path}
                      className="inline-flex items-center gap-1.5 rounded-xl border border-white/10 bg-white/[0.02] px-3 py-1.5 text-xs text-zinc-300 transition-colors hover:border-accent/30 hover:bg-accent/[0.06] hover:text-accent-soft"
                    >
                      <HardDrive size={13} className="shrink-0 text-accent-soft/70" />
                      <span className="truncate">{d.label}</span>
                    </button>
                  ))}
                </div>
              )
            ) : pickLoading ? (
              <LoaderInline label="Listing folder…" />
            ) : pickFolders.length > 0 ? (
              <div className="flex max-h-40 flex-wrap content-start gap-2 overflow-y-auto">
                {pickFolders.map((f: FsEntry) => (
                  <button
                    key={f.path}
                    type="button"
                    onClick={() => setPickDir(f.path)}
                    title={f.path}
                    className="inline-flex max-w-[16rem] items-center gap-1.5 rounded-xl border border-white/10 bg-white/[0.02] px-3 py-1.5 text-xs text-zinc-300 transition-colors hover:border-accent/30 hover:bg-accent/[0.06] hover:text-accent-soft"
                  >
                    <Folder size={13} className="shrink-0 text-accent-soft/70" />
                    <span className="truncate">{f.name}</span>
                  </button>
                ))}
              </div>
            ) : pickListing ? (
              <p className="text-[11px] text-zinc-500">No subfolders here.</p>
            ) : null}

            {chosenDir && (
              <div className="space-y-1.5">
                <div className="flex items-center gap-2">
                  <FolderPlus size={14} className="shrink-0 text-zinc-500" aria-hidden="true" />
                  <input
                    type="text"
                    value={newFolder}
                    onChange={(e) => setNewFolder(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") {
                        e.preventDefault();
                        void createSubfolder();
                      }
                    }}
                    placeholder="New subfolder name"
                    aria-label="New subfolder name"
                    className="min-w-0 max-w-xs flex-1 rounded-xl border border-white/10 bg-ink-950 px-3 py-1.5 text-xs text-zinc-200 placeholder:text-zinc-600 focus:border-accent/40 focus:outline-none"
                  />
                  <button
                    type="button"
                    onClick={() => void createSubfolder()}
                    disabled={!newFolder.trim() || mkdirBusy}
                    className="inline-flex shrink-0 items-center gap-1.5 rounded-xl border border-white/10 px-3 py-1.5 text-xs font-medium text-zinc-300 transition-colors hover:border-white/20 hover:bg-white/[0.04] disabled:opacity-50"
                  >
                    {mkdirBusy ? <LoaderInline label="Creating…" /> : "Create"}
                  </button>
                </div>
                {mkdirErr && <ErrorNote>{mkdirErr}</ErrorNote>}
              </div>
            )}
          </div>
        </Card>
      </Reveal>

      <Reveal>
        <div className="space-y-2">
          <button
            type="button"
            onClick={() => void start()}
            disabled={!cli || !chosenDir || startBusy}
            className="inline-flex items-center gap-2 rounded-xl border border-accent/30 bg-accent/[0.1] px-5 py-2.5 text-sm font-semibold text-accent-soft transition-colors hover:bg-accent/[0.16] disabled:cursor-not-allowed disabled:opacity-50"
          >
            {startBusy ? (
              <LoaderInline label="Starting…" />
            ) : (
              <>
                <Rocket size={15} /> Start creating
              </>
            )}
          </button>
          {!startBusy && (!cli || !chosenDir) && (
            <p className="text-[11px] text-zinc-500">
              {!cli
                ? "Pick an installed engine to continue."
                : "Pick a destination folder to continue."}
            </p>
          )}
          {startErr && (
            <ErrorNote>
              {startErr.detail}
              {startErr.installUrl && (
                <>
                  {" "}
                  <a
                    href={startErr.installUrl}
                    target="_blank"
                    rel="noreferrer"
                    className="font-medium text-accent-soft underline underline-offset-2 hover:text-accent"
                  >
                    Install it →
                  </a>
                </>
              )}
            </ErrorNote>
          )}
        </div>
      </Reveal>
    </>
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
  { id: "create", label: "Create", icon: <Wand2 size={14} /> },
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
      if (v === "creations" || v === "library" || v === "create") setView(v);
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
              : view === "create"
                ? "Pick an engine, a skill, and a folder — Iron Jarvis launches the CLI in a background terminal (it appears on Build) and new media lands here as it’s created."
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
      ) : view === "create" ? (
        /* ---- Create (studio): engine + skill + folder → live session ------ */
        <StudioView pins={pins} onUnpin={unpin} />
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
