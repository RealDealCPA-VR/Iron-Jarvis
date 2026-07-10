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
  ChevronDown,
  ChevronLeft,
  ChevronRight,
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
  Search,
  Send,
  Share2,
  Square,
  Star,
  Terminal,
  Trash2,
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

/** Cached 512px JPEG thumbnail for a creations artifact (name/version variant). */
function thumbSrcByName(name: string, version: number): string {
  const token = ijToken();
  return `${API_BASE}/creative/thumb?name=${encodeURIComponent(name)}&version=${version}${
    token ? `&token=${encodeURIComponent(token)}` : ""
  }`;
}

/** Cached 512px JPEG thumbnail for any local media file by absolute path. */
function thumbSrcByPath(absPath: string): string {
  const token = ijToken();
  return `${API_BASE}/creative/thumb?path=${encodeURIComponent(absPath)}${
    token ? `&token=${encodeURIComponent(token)}` : ""
  }`;
}

/** Full, token-carrying URL for a daemon-relative media path (e.g. the `url`
 *  a /creative/transcode gallery result hands back). */
function apiSrc(relUrl: string): string {
  const token = ijToken();
  if (!token) return `${API_BASE}${relUrl}`;
  const sep = relUrl.includes("?") ? "&" : "?";
  return `${API_BASE}${relUrl}${sep}token=${encodeURIComponent(token)}`;
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
 * The reusable share-destinations control for a PUBLISHED (public) url — the
 * SINGLE source of truth for where a creation can be shared. Used by ShareRow
 * (lightbox) and by TileShare (the per-tile popover) so the two never diverge.
 * YouTube has no URL-prefill upload, so that button opens YouTube Studio and
 * triggers the caller's download action.
 */
function ShareMenu({
  url,
  isVideo,
  onYouTubeDownload,
}: {
  url: string;
  isVideo: boolean;
  /** YouTube needs the file itself — the caller downloads it. Omit to hide YouTube. */
  onYouTubeDownload?: () => void;
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
        {isVideo && onYouTubeDownload && (
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
    </div>
  );
}

/**
 * Lightbox share block: the shared destinations + the "public link" reminder.
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
  return (
    <div className="space-y-1.5">
      <ShareMenu url={url} isVideo={isVideo} onYouTubeDownload={onYouTubeDownload} />
      <p className="text-[11px] text-zinc-500">The link is public — anyone with it can view.</p>
    </div>
  );
}

/**
 * Per-tile Share affordance — a corner button on every media tile that publishes
 * the item (same /creative/publish endpoint + 424 handling as the lightbox) then
 * opens a compact popover of the SAME ShareMenu destinations, WITHOUT opening the
 * lightbox. The popover is fixed-positioned (anchored to the button) so the tile's
 * overflow-hidden card never clips it.
 */
function TileShare({
  publishBody,
  isVideo,
  downloadUrl,
  downloadName,
}: {
  /** { name } for gallery items, { path } for path items — mirrors the lightbox. */
  publishBody: Record<string, string>;
  isVideo: boolean;
  downloadUrl: string;
  downloadName: string;
}) {
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<{ top: number; right: number } | null>(null);
  const [busy, setBusy] = useState(false);
  const [url, setUrl] = useState<string | null>(null);
  const [err, setErr] = useState<{ detail: string; notConnected: boolean } | null>(null);
  const btnRef = useRef<HTMLButtonElement | null>(null);
  const popRef = useRef<HTMLDivElement | null>(null);
  const dlRef = useRef<HTMLAnchorElement | null>(null);

  const publish = useCallback(async () => {
    // Publishing is OUTWARD-FACING: a permanent public CDN link anyone can
    // open. Never do that on a single click without saying so.
    if (
      !window.confirm(
        "Publish this file to Pixio's public CDN? Anyone with the link can view it, and the URL is permanent.",
      )
    )
      return;
    setBusy(true);
    setErr(null);
    try {
      const res = await post<{ url: string }>("/creative/publish", publishBody);
      setUrl(res.url);
    } catch (e) {
      const ae = e instanceof ApiError ? e : new ApiError(String(e), 0);
      setErr({
        detail: ae.status === 0 ? "Daemon offline — could not publish." : ae.message,
        notConnected: ae.status === 424,
      });
    } finally {
      setBusy(false);
    }
    // publishBody is a fresh object each render; the tile identity is stable, so
    // exclude it from deps to avoid re-creating publish every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const toggle = (e: React.MouseEvent) => {
    e.stopPropagation();
    e.preventDefault();
    if (open) {
      setOpen(false);
      return;
    }
    const r = btnRef.current?.getBoundingClientRect();
    if (r) setPos({ top: r.bottom + 6, right: Math.max(8, window.innerWidth - r.right) });
    setOpen(true);
    if (!url && !busy) void publish();
  };

  // Close the popover on outside click / Esc / scroll / resize (fixed anchor).
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      const t = e.target as Node;
      if (popRef.current?.contains(t) || btnRef.current?.contains(t)) return;
      setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    const onReflow = () => setOpen(false);
    window.addEventListener("mousedown", onDown);
    window.addEventListener("keydown", onKey);
    window.addEventListener("scroll", onReflow, true);
    window.addEventListener("resize", onReflow);
    return () => {
      window.removeEventListener("mousedown", onDown);
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("scroll", onReflow, true);
      window.removeEventListener("resize", onReflow);
    };
  }, [open]);

  return (
    <>
      <button
        ref={btnRef}
        type="button"
        onClick={toggle}
        aria-label="Share this item"
        aria-haspopup="menu"
        aria-expanded={open}
        title="Share"
        className={`absolute left-2 top-2 z-10 grid h-7 w-7 place-items-center rounded-lg border bg-black/50 backdrop-blur transition-all focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/50 ${
          open
            ? "border-accent/40 text-accent-soft opacity-100"
            : "border-white/15 text-zinc-200 opacity-100 hover:border-accent/40 hover:text-accent-soft focus-visible:opacity-100 md:opacity-0 md:group-hover:opacity-100"
        }`}
      >
        <Share2 size={13} />
      </button>
      {open && pos && (
        <div
          ref={popRef}
          role="menu"
          onClick={(e) => e.stopPropagation()}
          style={{ position: "fixed", top: pos.top, right: pos.right }}
          className="z-[70] w-[min(20rem,calc(100vw-1rem))] space-y-2 rounded-xl border border-white/10 bg-ink-950/95 p-3 shadow-card-hover backdrop-blur"
        >
          {busy && <LoaderInline label="Preparing share link…" />}
          {err && (
            <p className="text-[11px] text-rose-300">
              {err.detail}
              {err.notConnected && (
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
            </p>
          )}
          {url && (
            <ShareMenu url={url} isVideo={isVideo} onYouTubeDownload={() => dlRef.current?.click()} />
          )}
          <a
            ref={dlRef}
            href={downloadUrl}
            download={downloadName}
            className="hidden"
            aria-hidden="true"
            tabIndex={-1}
          >
            download
          </a>
        </div>
      )}
    </>
  );
}

/* ---- Grid tiles --------------------------------------------------------------- */

/** The play-glyph overlay/tile shared by every video presentation. */
function PlayGlyph() {
  return (
    <span
      aria-hidden="true"
      className="grid h-12 w-12 place-items-center rounded-full border border-white/15 bg-black/50 text-zinc-200 backdrop-blur transition-colors group-hover:border-accent/40 group-hover:text-accent-soft"
    >
      <Play size={20} className="ml-0.5" />
    </span>
  );
}

/**
 * Shared tile image for ALL media grids — tries the daemon's cached thumbnail
 * (512px JPEG) first, then degrades gracefully (the endpoint 404s for audio,
 * SVG, undecodable files, and video without ffmpeg):
 *
 * - Images: thumb → the original full file (SVG lands here by design) → glyph.
 * - Videos: thumb as a real frame poster (play glyph on top) → per-view
 *   fallback: "video" (Creations) renders the metadata-preload hover-play
 *   <video>; "glyph" (Library / studio strip) keeps the disk-safe glyph tile.
 * - Audio never reaches this component (callers keep their icon tiles).
 */
function Thumb({
  thumbUrl,
  fullUrl,
  alt,
  kind,
  videoFallback,
}: {
  thumbUrl: string;
  /** Original media URL — image fallback src and the fallback <video> src. */
  fullUrl: string;
  alt: string;
  kind: "image" | "video";
  videoFallback: "video" | "glyph";
}) {
  // Fallback ladder: 0 = thumbnail, 1 = full file (images only), 2 = last resort.
  const [step, setStep] = useState(0);

  if (kind === "image") {
    if (step >= 2) {
      return (
        <div className="flex h-full w-full items-center justify-center">
          <ImageIcon size={22} className="text-accent-soft/70" aria-hidden="true" />
        </div>
      );
    }
    return (
      // eslint-disable-next-line @next/next/no-img-element
      <img
        src={step === 0 ? thumbUrl : fullUrl}
        alt={alt}
        loading="lazy"
        onError={() => setStep((s) => s + 1)}
        className="h-full w-full object-cover transition-transform duration-300 group-hover:scale-[1.03]"
      />
    );
  }

  // Video.
  if (step === 0) {
    return (
      <>
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          src={thumbUrl}
          alt={alt}
          loading="lazy"
          onError={() => setStep(2)}
          className="h-full w-full object-cover transition-transform duration-300 group-hover:scale-[1.03]"
        />
        <span className="pointer-events-none absolute inset-0 grid place-items-center">
          <PlayGlyph />
        </span>
      </>
    );
  }
  if (videoFallback === "video") {
    return (
      <video
        src={fullUrl}
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
    );
  }
  return (
    <div className="flex h-full w-full items-center justify-center">
      <PlayGlyph />
    </div>
  );
}

/* ---- Make playable (the "can't open — unsupported encoding 0x80004005" fix) --- */

interface PlayableInfo {
  playable: boolean;
  reason: string;
  codec: string;
  pix_fmt: string;
  can_transcode: boolean;
}

/** Exactly one of a local absolute path or a gallery artifact (+ optional version). */
type PlayableTarget = { path: string } | { name: string; version?: number };

interface TranscodeResult {
  path?: string; // local: the new "<name>.playable.mp4" sibling's absolute path
  name?: string; // gallery: the new artifact's name
  filename: string;
  url: string;
}

function playableQuery(t: PlayableTarget): string {
  if ("path" in t) return `path=${encodeURIComponent(t.path)}`;
  return `name=${encodeURIComponent(t.name)}${
    t.version !== undefined ? `&version=${t.version}` : ""
  }`;
}

/** Probe whether a video is broadly playable (short ffprobe — bounded 25s). */
function probePlayable(t: PlayableTarget): Promise<PlayableInfo> {
  return get<PlayableInfo>(`/creative/playable?${playableQuery(t)}`, { timeoutMs: 25000 });
}

/** Re-encode to a universally-playable MP4 and hand back a token-carrying src for
 *  the result (local → the sibling by path; gallery → the new artifact's url). */
async function requestTranscode(t: PlayableTarget): Promise<{ src: string; filename: string }> {
  const body =
    "path" in t
      ? { path: t.path }
      : { name: t.name, ...(t.version !== undefined ? { version: t.version } : {}) };
  // A real re-encode can take minutes; match the daemon's 900s ceiling.
  const res = await post<TranscodeResult>("/creative/transcode", body, { timeoutMs: 900_000 });
  const src = res.path ? filePathSrc(res.path) : apiSrc(res.url);
  return { src, filename: res.filename };
}

/** Honest message for a failed probe/transcode — 424 (no ffmpeg) / 422 (encode
 *  failure) details come straight from the daemon. */
function transcodeError(e: unknown): string {
  const ae = e instanceof ApiError ? e : new ApiError(String(e), 0);
  return ae.status === 0 ? "Daemon offline — could not convert." : ae.message;
}

/**
 * Lightbox "Make playable" control — probes ONCE on open (one video at a time,
 * so no probe stampede) and, when the encoding is one Windows / `<video>` may
 * refuse, offers a prominent re-encode that re-points the player at the result.
 */
function MakePlayable({
  target,
  onTranscoded,
}: {
  target: PlayableTarget;
  onTranscoded: (src: string, filename: string) => void;
}) {
  const [info, setInfo] = useState<PlayableInfo | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [doneName, setDoneName] = useState<string | null>(null);
  const key = playableQuery(target);

  useEffect(() => {
    let cancelled = false;
    setInfo(null);
    setDoneName(null);
    setErr(null);
    probePlayable(target)
      .then((i) => {
        if (!cancelled) setInfo(i);
      })
      .catch(() => {
        /* probe failed — leave the control hidden rather than nag on an unknown */
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- key encodes target
  }, [key]);

  const convert = async () => {
    setBusy(true);
    setErr(null);
    try {
      const { src, filename } = await requestTranscode(target);
      setDoneName(filename);
      onTranscoded(src, filename);
    } catch (e) {
      setErr(transcodeError(e));
    } finally {
      setBusy(false);
    }
  };

  if (doneName) {
    return (
      <span className="inline-flex items-center gap-1.5 text-[11px] text-emerald-300">
        <Check size={13} className="shrink-0" /> Converted — now playing a playable copy (
        <code className="font-mono text-emerald-200/90">{doneName}</code>).
      </span>
    );
  }
  if (busy) {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-xl border border-accent/30 bg-accent/[0.08] px-3 py-1.5 text-xs font-medium text-accent-soft">
        <LoaderInline label="Converting… (re-encoding to H.264)" />
      </span>
    );
  }
  if (!info) return null; // not probed yet — never flash a button that then vanishes
  if (info.playable) {
    // Broadly playable already — a subtle note, with a quiet "convert anyway"
    // escape hatch for the rare file that still won't open locally.
    return (
      <span className="inline-flex items-center gap-2 text-[11px] text-zinc-500">
        <Check size={12} className="text-emerald-400/80" /> Plays everywhere
        {info.can_transcode && (
          <button
            type="button"
            onClick={() => void convert()}
            className="underline underline-offset-2 transition-colors hover:text-zinc-300"
          >
            convert anyway
          </button>
        )}
        {err && <span className="text-rose-300">{err}</span>}
      </span>
    );
  }
  if (!info.can_transcode) {
    return (
      <span
        title={info.reason}
        className="inline-flex items-center gap-1.5 rounded-xl border border-white/10 bg-white/[0.02] px-3 py-1.5 text-[11px] text-zinc-500"
      >
        <Wand2 size={13} /> Can’t play ({info.codec || "unknown"}) — install ffmpeg to fix
      </span>
    );
  }
  return (
    <span className="inline-flex flex-col items-start gap-1">
      <button
        type="button"
        onClick={() => void convert()}
        title={info.reason}
        className="inline-flex items-center gap-1.5 rounded-xl border border-amber-500/30 bg-amber-500/[0.08] px-3 py-1.5 text-xs font-medium text-amber-200 transition-colors hover:bg-amber-500/[0.14]"
      >
        <Wand2 size={13} /> Make playable
      </button>
      <span className="text-[10px] text-zinc-500">{info.reason}</span>
      {err && <span className="text-[11px] text-rose-300">{err}</span>}
    </span>
  );
}

/**
 * Compact per-tile "Make playable" affordance for VIDEO tiles. On-demand
 * (probe → transcode on click) rather than probing every tile on mount, so a
 * folder of videos never stampedes the daemon. The new file surfaces through the
 * same channels every other creation does (gallery reload / studio watcher).
 */
function MakePlayableChip({
  target,
  onConverted,
}: {
  target: PlayableTarget;
  onConverted?: () => void;
}) {
  const [state, setState] = useState<"idle" | "busy" | "ok" | "playable" | "noffmpeg">("idle");
  const [err, setErr] = useState<string | null>(null);

  const run = async (e: React.MouseEvent) => {
    e.stopPropagation();
    e.preventDefault();
    if (state === "busy") return;
    setErr(null);
    setState("busy");
    try {
      const info = await probePlayable(target);
      if (info.playable) {
        setState("playable");
        return;
      }
      if (!info.can_transcode) {
        setState("noffmpeg");
        return;
      }
      await requestTranscode(target);
      setState("ok");
      onConverted?.();
    } catch (e2) {
      setErr(transcodeError(e2));
      setState("idle");
    }
  };

  const label =
    state === "busy"
      ? "Converting…"
      : state === "ok"
        ? "Playable ✓"
        : state === "playable"
          ? "Already playable"
          : state === "noffmpeg"
            ? "Needs ffmpeg"
            : "Make playable";

  return (
    <button
      type="button"
      onClick={(e) => void run(e)}
      title={err ?? chipTitle(state)}
      className={`absolute bottom-2 left-2 z-10 inline-flex items-center gap-1 rounded-lg border px-2 py-1 text-[10px] font-medium backdrop-blur transition-all focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/50 ${
        state === "ok"
          ? "border-emerald-400/40 bg-black/55 text-emerald-300 opacity-100"
          : err
            ? "border-rose-500/40 bg-black/55 text-rose-300 opacity-100"
            : "border-white/15 bg-black/55 text-zinc-200 opacity-100 hover:border-accent/40 hover:text-accent-soft md:opacity-0 md:group-hover:opacity-100"
      }`}
    >
      {state === "busy" ? (
        <span className="h-2.5 w-2.5 animate-spin rounded-full border border-current border-t-transparent" />
      ) : (
        <Wand2 size={11} />
      )}
      {label}
    </button>
  );
}

/** Tooltip copy for the compact chip's current state. */
function chipTitle(
  state: "idle" | "busy" | "ok" | "playable" | "noffmpeg",
): string {
  if (state === "ok") return "Converted — a playable copy was created";
  if (state === "playable") return "Already plays everywhere";
  if (state === "noffmpeg") return "Install ffmpeg to make videos playable";
  if (state === "busy") return "Converting…";
  return "Re-encode to a universally-playable MP4 (H.264)";
}

function MediaTile({
  item,
  onOpen,
  onConverted,
}: {
  item: CreativeItem;
  onOpen: () => void;
  onConverted?: () => void;
}) {
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
        <TileShare
          publishBody={{ name: item.name }}
          isVideo={item.media === "video"}
          downloadUrl={src}
          downloadName={item.filename}
        />
        {item.media !== "audio" ? (
          <Thumb
            thumbUrl={thumbSrcByName(item.name, item.version)}
            fullUrl={src}
            alt={item.filename}
            kind={item.media}
            videoFallback="video"
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
        {item.media === "video" && (
          <MakePlayableChip target={{ name: item.name }} onConverted={onConverted} />
        )}
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
 * Library tile — same card language as MediaTile, but disk-friendly: images and
 * videos show the daemon's small cached thumbnail; when a video thumbnail isn't
 * possible (no ffmpeg) the tile stays glyph-only — NO full video element (a big
 * folder of videos must not hammer the drive); media loads only in the lightbox.
 */
function LibraryTile({
  file,
  onOpen,
  onConverted,
}: {
  file: LibraryFile;
  onOpen: () => void;
  onConverted?: () => void;
}) {
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
        <TileShare
          publishBody={{ path: file.path }}
          isVideo={file.kind === "video"}
          downloadUrl={filePathSrc(file.path)}
          downloadName={file.name}
        />
        {file.kind !== "audio" ? (
          <Thumb
            thumbUrl={thumbSrcByPath(file.path)}
            fullUrl={filePathSrc(file.path)}
            alt={file.name}
            kind={file.kind}
            videoFallback="glyph"
          />
        ) : (
          <div className="flex h-full w-full flex-col items-center justify-center gap-2 px-4">
            <Music size={22} className="text-accent-soft/70" />
            <span className="text-[11px] text-zinc-500">click to play</span>
          </div>
        )}
        <span className="absolute right-2 top-2 inline-flex items-center gap-1 rounded-full border border-white/10 bg-black/50 px-2 py-0.5 text-[10px] font-medium capitalize text-zinc-300 backdrop-blur">
          {mediaIcon(file.kind, 10)} {file.kind}
        </span>
        {file.kind === "video" && (
          <MakePlayableChip target={{ path: file.path }} onConverted={onConverted} />
        )}
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
  deleteName,
  onDeleted,
  onPrev,
  onNext,
}: {
  media: MediaKind;
  src: string;
  title: string;
  downloadName: string;
  publishBody: Record<string, string>;
  meta: ReactNode;
  onClose: () => void;
  /** CREATIONS only: artifact name for DELETE /creative/items/{name}. Never set for path-based (library/studio) items. */
  deleteName?: string;
  /** Called after a successful delete — the caller closes the lightbox and reloads. */
  onDeleted?: () => void;
  /** Prev/next within the caller's filtered+sorted visible list: null at the ends, undefined = no navigation. */
  onPrev?: (() => void) | null;
  onNext?: (() => void) | null;
}) {
  const [pubBusy, setPubBusy] = useState(false);
  const [pubUrl, setPubUrl] = useState<string | null>(null);
  const [pubErr, setPubErr] = useState<{ detail: string; notConnected: boolean } | null>(null);
  const [shareOpen, setShareOpen] = useState(false);
  const [confirmDel, setConfirmDel] = useState(false);
  const [delBusy, setDelBusy] = useState(false);
  const [delErr, setDelErr] = useState<string | null>(null);
  // Once a video is re-encoded to a playable copy, the player points here instead.
  const [playableSrc, setPlayableSrc] = useState<string | null>(null);
  const downloadRef = useRef<HTMLAnchorElement | null>(null);
  const dialogRef = useRef<HTMLDivElement | null>(null);

  // "Make playable" target — a gallery name or a local path, mirroring publishBody.
  const playableTarget: PlayableTarget | null = publishBody.path
    ? { path: publishBody.path }
    : publishBody.name
      ? { name: publishBody.name }
      : null;

  // Esc closes; ←/→ step through the caller's visible list — never while
  // typing (Escape included: it should clear/blur the field, not nuke the
  // dialog), and never while a video/audio element owns the arrows (seeking).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement | null)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
      if (e.key === "Escape") {
        onClose();
        return;
      }
      if (tag === "VIDEO" || tag === "AUDIO") return;
      if (e.key === "ArrowLeft" && onPrev) onPrev();
      else if (e.key === "ArrowRight" && onNext) onNext();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, onPrev, onNext]);

  // Body scroll lock while the dialog is open (restored on close/unmount).
  useEffect(() => {
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = prev;
    };
  }, []);

  // Initial focus into the dialog; focus returns to the opener on close.
  useEffect(() => {
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    dialogRef.current?.focus();
    return () => opener?.focus();
  }, []);

  /** Minimal focus trap: Tab / Shift+Tab wrap within the dialog. */
  const trapTab = (e: React.KeyboardEvent<HTMLDivElement>) => {
    if (e.key !== "Tab") return;
    const root = dialogRef.current;
    if (!root) return;
    const focusables = Array.from(
      root.querySelectorAll<HTMLElement>(
        'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), audio[controls], video[controls], [tabindex]:not([tabindex="-1"])',
      ),
    );
    if (focusables.length === 0) {
      e.preventDefault();
      return;
    }
    const first = focusables[0];
    const last = focusables[focusables.length - 1];
    const active = document.activeElement;
    if (e.shiftKey) {
      if (active === first || active === root) {
        e.preventDefault();
        last.focus();
      }
    } else if (active === last) {
      e.preventDefault();
      first.focus();
    }
  };

  const doDelete = async () => {
    if (!deleteName || delBusy) return;
    setDelBusy(true);
    setDelErr(null);
    try {
      await del<{ deleted: string }>(`/creative/items/${encodeURIComponent(deleteName)}`);
      onDeleted?.(); // closes the lightbox + reloads — no state reset needed here
    } catch (e) {
      const err = e instanceof ApiError ? e : new ApiError(String(e), 0);
      setDelErr(err.status === 0 ? "Daemon offline — could not delete." : err.message);
      setDelBusy(false);
    }
  };

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
      onKeyDown={trapTab}
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4 backdrop-blur-sm"
    >
      <div
        ref={dialogRef}
        tabIndex={-1}
        onClick={(e) => e.stopPropagation()}
        className="card-surface flex max-h-[90vh] w-full max-w-3xl flex-col overflow-hidden focus:outline-none"
      >
        <header className="flex items-center justify-between gap-3 border-b hairline px-5 py-3.5">
          <h2 className="flex min-w-0 items-center gap-2 text-[13px] font-semibold tracking-wide text-zinc-200">
            <span className="shrink-0 text-accent-soft/80">{mediaIcon(media, 15)}</span>
            <span className="truncate">{title}</span>
          </h2>
          <span className="flex shrink-0 items-center gap-1">
            {(onPrev !== undefined || onNext !== undefined) && (
              <>
                <button
                  type="button"
                  onClick={() => onPrev?.()}
                  disabled={!onPrev}
                  aria-label="Previous item"
                  title="Previous (←)"
                  className="rounded-lg border border-transparent p-1.5 text-zinc-400 transition-colors hover:border-white/10 hover:bg-white/[0.04] hover:text-zinc-200 disabled:cursor-not-allowed disabled:opacity-30"
                >
                  <ChevronLeft size={16} />
                </button>
                <button
                  type="button"
                  onClick={() => onNext?.()}
                  disabled={!onNext}
                  aria-label="Next item"
                  title="Next (→)"
                  className="rounded-lg border border-transparent p-1.5 text-zinc-400 transition-colors hover:border-white/10 hover:bg-white/[0.04] hover:text-zinc-200 disabled:cursor-not-allowed disabled:opacity-30"
                >
                  <ChevronRight size={16} />
                </button>
              </>
            )}
            <button
              type="button"
              onClick={onClose}
              aria-label="Close"
              className="rounded-lg border border-transparent p-1.5 text-zinc-400 transition-colors hover:border-white/10 hover:bg-white/[0.04] hover:text-zinc-200"
            >
              <X size={16} />
            </button>
          </span>
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
              <video
                key={playableSrc ?? src}
                src={playableSrc ?? src}
                controls
                autoPlay
                playsInline
                className="max-h-[55vh] w-full"
              />
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
              {media === "video" && playableTarget && (
                <MakePlayable
                  target={playableTarget}
                  onTranscoded={(newSrc) => setPlayableSrc(newSrc)}
                />
              )}
              {deleteName !== undefined && !confirmDel && (
                <button
                  type="button"
                  onClick={() => {
                    setConfirmDel(true);
                    setDelErr(null);
                  }}
                  className="ml-auto inline-flex items-center gap-1.5 rounded-xl border border-rose-500/25 bg-rose-500/[0.06] px-3 py-1.5 text-xs font-medium text-rose-300 transition-colors hover:bg-rose-500/[0.12]"
                >
                  <Trash2 size={13} /> Delete
                </button>
              )}
            </div>

            {deleteName !== undefined && confirmDel && (
              <div className="flex flex-wrap items-center gap-2 rounded-xl border border-rose-500/25 bg-rose-500/[0.06] px-3 py-2">
                <p className="min-w-0 flex-1 text-xs text-rose-200">
                  Delete this creation? The file and all its versions are removed.
                </p>
                <button
                  type="button"
                  onClick={() => void doDelete()}
                  disabled={delBusy}
                  className="inline-flex shrink-0 items-center gap-1.5 rounded-lg border border-rose-500/40 bg-rose-500/[0.14] px-2.5 py-1 text-[11px] font-semibold text-rose-200 transition-colors hover:bg-rose-500/[0.22] disabled:opacity-50"
                >
                  {delBusy ? <LoaderInline label="Deleting…" /> : "Confirm"}
                </button>
                <button
                  type="button"
                  onClick={() => setConfirmDel(false)}
                  disabled={delBusy}
                  className="shrink-0 rounded-lg border border-white/10 px-2.5 py-1 text-[11px] font-medium text-zinc-300 transition-colors hover:border-white/20 hover:bg-white/[0.04] disabled:opacity-50"
                >
                  Cancel
                </button>
              </div>
            )}
            {delErr && <ErrorNote>{delErr}</ErrorNote>}

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
/** Quiet window after the last tail change / new file before a turn reads as "done". */
const STUDIO_IDLE_MS = 8000;

/** A live studio session, persisted so a page unmount doesn't lose the terminal. */
interface StudioSession {
  terminal_id: string;
  dest: string;
  cli_label: string;
  /** The skill chosen at start ("Auto" when the agent picks) — shown in the live header. */
  skill?: string;
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

/** Agent lifecycle phase, derived server-side from the CLI's own output.
 *  "exited" = the CLI quit back to the shell (the terminal itself is alive) —
 *  sending another brief would run it as a SHELL COMMAND, so the composer
 *  refuses honestly. */
type StudioPhase = "booting" | "thinking" | "generating" | "idle" | "done" | "exited";

/** Phases where the agent is actively busy (vs. idle/done = waiting on us). */
const PHASE_BUSY: Record<StudioPhase, boolean> = {
  booting: true,
  thinking: true,
  generating: true,
  idle: false,
  done: false,
  exited: false,
};

/** One media file found by the recursive studio-media scan. */
interface StudioMediaFile {
  path: string;
  /** RELATIVE path from the session folder (posix separators) — shows the
   *  subfolder a generation landed in, e.g. "shots/shot-03.mp4". */
  name: string;
  media: "image" | "video" | "audio";
  size: number | null;
  mtime: number;
}

interface StudioTail {
  tail: string;
  alive: boolean;
  exit_code: number | null;
  /** Human-readable CLI mode line (e.g. "auto-accept edits on") — null when unknown. */
  mode: string | null;
  /** True once the daemon has verified an auto/full-auto mode is engaged. */
  automode: boolean;
  /** True once the CLI has booted and is ready to accept a brief (gates the
   *  composer instead of a blind boot timer). ABSENT on older daemons. */
  ready?: boolean;
  /** Authoritative lifecycle phase; ABSENT on older daemons → idle-timer fallback. */
  phase?: StudioPhase;
  /** Already-human-readable progress line, e.g. "Rendering shot 3/5…". */
  status_line?: string | null;
  /**
   * Bumps when the agent finishes a reply-turn. INTENTIONALLY UNUSED: our visual
   * turns are keyed to USER briefs (briefTimes) and media attaches by first-seen
   * time; turn_seq counts agent-internal reply-turns (different cardinality, no
   * per-file mapping), so bucketing on it would fight the timestamp model.
   */
  turn_seq?: number;
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

/** One clarifying question the intake step proposes for the FIRST brief. */
interface IntakeQuestion {
  key: string;
  label: string;
  options: string[];
  allow_custom: boolean;
}

/** In-memory shape of the running session (camelCase mirror of StudioSession). */
interface LiveSession {
  terminalId: string;
  dest: string;
  cliLabel: string;
  /** Skill chosen at start ("Auto" when the agent picks); null for legacy stored sessions. */
  skillLabel: string | null;
  command: string;
  /** Epoch ms — drives the "auto mode: engaging…" grace window on the badge. */
  startedAt: number;
  /** True when this CLI engages autopilot by Shift+Tab (Claude): the composer
   *  waits for auto mode to actually be CONFIRMED before the first brief, so it
   *  can't land in a not-yet-ready TUI or stall on a permission prompt. */
  awaitsAutomode: boolean;
}

/* ---- Studio chat (LIVE phase) presentational bits ---------------------------- */

/** A brief the user typed — a right-aligned chat bubble. */
function UserBubble({ text }: { text: string }) {
  return (
    <div className="flex justify-end">
      <div className="max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-br-md border border-accent/25 bg-accent/[0.08] px-3.5 py-2 text-[13px] text-zinc-200">
        {text}
      </div>
    </div>
  );
}

/** Three staggered pulsing dots — the "Iron Jarvis is working" indicator. */
function WorkingDots() {
  return (
    <span className="inline-flex items-center gap-1" aria-hidden="true">
      <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-accent [animation-delay:0ms]" />
      <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-accent [animation-delay:150ms]" />
      <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-accent [animation-delay:300ms]" />
    </span>
  );
}

/** Left-aligned assistant chat bubble with an Iron Jarvis avatar. */
function AssistantBubble({ children }: { children: ReactNode }) {
  return (
    <div className="flex justify-start gap-2.5">
      <span className="mt-0.5 grid h-7 w-7 shrink-0 place-items-center rounded-full border border-accent/30 bg-accent/[0.08] text-accent-soft">
        <Sparkles size={14} />
      </span>
      <div className="min-w-0 max-w-[42rem] flex-1 space-y-2.5 rounded-2xl rounded-tl-md border border-white/10 bg-white/[0.03] px-3.5 py-2.5">
        {children}
      </div>
    </div>
  );
}

type TurnState = "working" | "done" | "ready" | "exited";

/**
 * The assistant's turn for one brief — a status line (working / done / ready /
 * exited) plus the media it produced, rendered INLINE as tiles that open the
 * lightbox. Media is the "wow": generations appear in the conversation as they
 * land, not as raw console text.
 */
function AssistantTurn({
  state,
  media,
  exitCode,
  statusText,
  onOpenMedia,
}: {
  state: TurnState;
  media: LibraryFile[];
  exitCode: number | null;
  /** Live daemon status_line shown while working (e.g. "Rendering shot 3/5…"). */
  statusText?: string | null;
  onOpenMedia: (f: LibraryFile) => void;
}) {
  const count = media.length;
  return (
    <AssistantBubble>
      {state === "working" ? (
        <div className="flex items-center gap-2 text-[13px] text-zinc-300">
          <WorkingDots />
          <span>
            {statusText || "Working in the terminal…"}
            {count > 0 ? ` · ${count} file${count === 1 ? "" : "s"} so far` : ""}
          </span>
        </div>
      ) : state === "exited" ? (
        <p className="text-[13px] text-zinc-400">
          The engine exited{exitCode !== null ? ` (code ${exitCode})` : ""}.
        </p>
      ) : state === "done" ? (
        <p className="flex items-center gap-1.5 text-[13px] text-zinc-300">
          <Check size={14} className="shrink-0 text-emerald-400" />
          {count > 0 ? `Done — created ${count} file${count === 1 ? "" : "s"}.` : "Done."}
        </p>
      ) : (
        <p className="text-[13px] text-zinc-400">Ready — send another instruction.</p>
      )}
      {count > 0 && (
        <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-3">
          {media.map((f) => (
            <LibraryTile key={f.path} file={f} onOpen={() => onOpenMedia(f)} />
          ))}
        </div>
      )}
    </AssistantBubble>
  );
}

/**
 * The CREATE view: pick an engine (AI CLI) + skill + destination folder, launch
 * a background terminal (it appears on Build), brief it chat-style, and watch
 * new media land in the folder — the honest completion signal.
 */
function StudioView({
  pins,
  onUnpin,
  active,
}: {
  pins: PinnedFolder[];
  onUnpin: (path: string) => void;
  /** Whether the Create tab is the visible one. The component stays MOUNTED
   *  across tab switches (a live session must survive a peek at the Gallery);
   *  `active` only gates focus stealing. */
  active: boolean;
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
  // Honest banner: the stored session's terminal is gone (daemon restart etc.).
  const [lostNote, setLostNote] = useState(false);
  const [startBusy, setStartBusy] = useState(false);
  const [startErr, setStartErr] = useState<{ detail: string; installUrl: string | null } | null>(
    null,
  );

  const [booting, setBooting] = useState(false);
  // A brief typed WHILE the engine boots — the composer never locks; the text
  // queues here (visible in the transcript) and auto-fires the moment the
  // readiness gate opens. Multiple sends during boot merge into one brief
  // (studio_say flattens newlines anyway), so nothing is ever silently lost.
  const [queuedBrief, setQueuedBrief] = useState<string | null>(null);
  const [messages, setMessages] = useState<string[]>([]);
  // Epoch-ms send time per brief (parallel to `messages`), for bucketing media
  // into turns. In-memory only — resume reconstructs it from the session start.
  const [briefTimes, setBriefTimes] = useState<number[]>([]);
  const [sentFirst, setSentFirst] = useState(false);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [sayErr, setSayErr] = useState<string | null>(null);
  const [ending, setEnding] = useState(false);

  // FIRST-brief clarifying-questions intake (Fix 1). `intakeBrief` holds the
  // pending first brief while we ask; `intakeQuestions` drives the inline panel.
  const [intakeBrief, setIntakeBrief] = useState<string | null>(null);
  const [intakeBusy, setIntakeBusy] = useState(false);
  const [intakeQuestions, setIntakeQuestions] = useState<IntakeQuestion[] | null>(null);
  const [intakeAnswers, setIntakeAnswers] = useState<Record<string, string>>({});

  const [tail, setTail] = useState("");
  const [alive, setAlive] = useState(true);
  const [exitCode, setExitCode] = useState<number | null>(null);
  const [gone, setGone] = useState(false); // tail endpoint 404'd — terminal deleted
  const [mode, setMode] = useState<string | null>(null);
  const [automode, setAutomode] = useState(false);
  // Badge grace window: "engaging…" for ~30s after start, then honest "unverified".
  const [engaging, setEngaging] = useState(true);
  // Raw terminal is tucked behind a disclosure — the chat is the main view.
  const [showRaw, setShowRaw] = useState(false);
  // Authoritative agent lifecycle from the daemon (null when it doesn't report one).
  const [phase, setPhase] = useState<StudioPhase | null>(null);
  const [statusLine, setStatusLine] = useState<string | null>(null);
  // Daemon "ready to accept input" flag — drives the "engine may be waiting" hint.
  const [ready, setReady] = useState(false);

  const baselineRef = useRef<Set<string>>(new Set());
  // Files already pushed into the gallery (auto-ingest is fire-and-forget and
  // idempotent server-side; this just avoids re-POSTing every poll).
  const ingestedRef = useRef<Set<string>>(new Set());
  // Media the session has produced, with first-seen epoch-ms — the source for
  // both the inline chat thumbnails and the "N files" counts. A ref tracks
  // first-seen (stable across the 5s folder diffs); state drives rendering.
  const mediaSeenRef = useRef<Map<string, number>>(new Map());
  const mediaSigRef = useRef("");
  const [mediaTimeline, setMediaTimeline] = useState<{ file: LibraryFile; at: number }[]>([]);
  const [studioSelected, setStudioSelected] = useState<LibraryFile | null>(null);

  // Composer focus: give the input focus when the tab is visible and typing is
  // possible — after a send round-trips, and right when a session starts. The
  // browser drops focus every time `disabled` flips on, so restore it.
  const composerRef = useRef<HTMLInputElement | null>(null);
  useEffect(() => {
    if (active && session && alive && !gone && !sending) composerRef.current?.focus();
  }, [active, session, alive, gone, sending, booting]);

  // Activity clock: the assistant turn reads as "working" until the tail + the
  // folder have been quiet for STUDIO_IDLE_MS. lastTailRef detects tail changes;
  // nowTick re-evaluates idleness once a second while the run is alive.
  const lastTailRef = useRef("");
  const [lastActivityAt, setLastActivityAt] = useState(0);
  const [nowTick, setNowTick] = useState(0);

  // Resume detection: a stored session whose terminal is still alive on the daemon.
  useEffect(() => {
    const saved = readStudioStore().session;
    if (!saved) return;
    let cancelled = false;
    get<StudioTail>(`/creative/studio/${saved.terminal_id}/tail?chars=1`)
      .then((t) => {
        if (cancelled) return;
        if (t.alive) setResumeOffer(saved);
        else {
          // Exited — nothing to resume; say so instead of silently clearing.
          writeStudioStore({ session: undefined });
          setLostNote(true);
        }
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        if (e instanceof ApiError && e.status === 404) {
          writeStudioStore({ session: undefined });
          setLostNote(true);
        }
        // Offline/transient errors: keep the stored session for the next visit.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // The composer stays locked until the CLI is ACTUALLY ready — the tail poll
  // clears `booting` on the real readiness signal (auto mode confirmed for a
  // Shift+Tab autopilot like Claude, else the daemon's `ready` flag). A blind
  // 3s timer used to unlock too early, so the first brief landed in a
  // not-yet-listening TUI and vanished. This timer is only a SAFETY NET so a
  // CLI that never reports readiness still unlocks (with the honest "unverified"
  // badge and the Build handoff to fall back on).
  useEffect(() => {
    if (!booting) return;
    const t = setTimeout(() => setBooting(false), 45_000);
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
        setAlive(t.alive);
        setExitCode(t.exit_code);
        setMode(t.mode ?? null);
        setAutomode(t.automode === true);
        setPhase(t.phase ?? null);
        setStatusLine(t.status_line ?? null);
        setReady(t.ready === true);
        // Unlock the composer once the CLI is genuinely ready. For a Shift+Tab
        // autopilot (Claude) that means auto mode CONFIRMED — firing a brief
        // before it engages would stall on an unanswered permission prompt;
        // for other CLIs the daemon's `ready` flag. Only ever CLEARS booting
        // (the safety-net timer is the sole other path), so a later automode
        // flicker can't re-lock a composer the user is already typing in.
        setBooting((b) => {
          if (!b) return b;
          const bootReady = session.awaitsAutomode ? t.automode === true : t.ready === true;
          return bootReady ? false : b;
        });
        // A changed tail = the CLI is doing something → mark activity (keeps the
        // idle-timer FALLBACK "working"); an unchanged tail leaves state untouched.
        if (t.tail !== lastTailRef.current) {
          lastTailRef.current = t.tail;
          setTail(t.tail);
          setLastActivityAt(Date.now());
        }
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

  // New-media watcher: RECURSIVELY diff the destination against the
  // session-start snapshot (skills save into subfolders — a flat listing
  // missed them), stamp each first sighting, auto-ingest it into the durable
  // gallery, and only re-render when the media set actually changes.
  useEffect(() => {
    if (!session || gone) return;
    let cancelled = false;
    const tick = () => {
      get<{ files: StudioMediaFile[]; truncated: boolean }>(
        `/creative/studio-media?path=${encodeURIComponent(session.dest)}`,
        { timeoutMs: 15000 },
      )
        .then((d) => {
          if (cancelled) return;
          const now = Date.now();
          let added = false;
          const timeline: { file: LibraryFile; at: number }[] = [];
          for (const f of d.files) {
            if (baselineRef.current.has(f.name)) continue;
            let at = mediaSeenRef.current.get(f.path);
            if (at === undefined) {
              at = now;
              mediaSeenRef.current.set(f.path, at);
              added = true;
              // Studio → Gallery bridge: every generation becomes a durable
              // artifact, so the Create tab's output shows up in Gallery /
              // Share / chat like every other creation. Fire-and-forget +
              // idempotent server-side; oversized files stay disk-only.
              if (
                !ingestedRef.current.has(f.path) &&
                (f.size ?? 0) <= 200 * 1024 * 1024
              ) {
                ingestedRef.current.add(f.path);
                void post("/creative/ingest", { path: f.path }).catch(() => {});
              }
            }
            timeline.push({
              file: { path: f.path, name: f.name, kind: f.media, size: f.size },
              at,
            });
          }
          timeline.sort((a, b) => a.at - b.at || a.file.name.localeCompare(b.file.name));
          const sig = timeline.map((m) => `${m.file.path}:${m.file.size ?? ""}`).join("|");
          if (sig !== mediaSigRef.current) {
            mediaSigRef.current = sig;
            setMediaTimeline(timeline);
          }
          if (added) setLastActivityAt(Date.now());
        })
        .catch(() => {
          /* transient — the next tick retries */
        });
    };
    tick();
    // A dead terminal makes no new media: take the one final diff above (a
    // file may have landed in the exit instant) and stop polling.
    if (!alive) {
      return () => {
        cancelled = true;
      };
    }
    const id = setInterval(tick, 5000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [session, gone, alive]);

  // Console auto-scroll — sticks to the bottom unless the user scrolled up (only
  // matters while the raw-terminal disclosure is open).
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
  }, [tail, showRaw]);

  // Transcript auto-scroll — keeps the newest turn / media in view.
  const transcriptRef = useRef<HTMLDivElement | null>(null);
  const transcriptStickRef = useRef(true);
  const onTranscriptScroll = () => {
    const el = transcriptRef.current;
    if (!el) return;
    transcriptStickRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 64;
  };
  useEffect(() => {
    const el = transcriptRef.current;
    if (el && transcriptStickRef.current) el.scrollTop = el.scrollHeight;
  }, [messages, mediaTimeline, booting, alive, gone, queuedBrief]);

  // Idle clock: re-evaluate the "working → done" transition each second while the
  // run is alive (the tail poll goes quiet when the CLI stops emitting output).
  useEffect(() => {
    if (!session || !alive || gone) return;
    const id = setInterval(() => setNowTick(Date.now()), 1000);
    return () => clearInterval(id);
  }, [session, alive, gone]);

  const resetLive = useCallback((clearStore: boolean) => {
    if (clearStore) writeStudioStore({ session: undefined });
    setSession(null);
    setTail("");
    lastTailRef.current = "";
    setMessages([]);
    setBriefTimes([]);
    setQueuedBrief(null);
    setSentFirst(false);
    mediaSeenRef.current = new Map();
    mediaSigRef.current = "";
    ingestedRef.current = new Set();
    setMediaTimeline([]);
    setGone(false);
    setAlive(true);
    setExitCode(null);
    setMode(null);
    setAutomode(false);
    setSayErr(null);
    setShowRaw(false);
    setPhase(null);
    setStatusLine(null);
    setReady(false);
    setIntakeBrief(null);
    setIntakeBusy(false);
    setIntakeQuestions(null);
    setIntakeAnswers({});
    setStudioSelected(null);
    const now = Date.now();
    setLastActivityAt(now);
    setNowTick(now);
  }, []);

  const start = async () => {
    if (!cli || !chosenDir || startBusy) return;
    setStartBusy(true);
    setStartErr(null);
    // Snapshot the destination BEFORE launching — anything after this counts
    // as new. Recursive (rel-path keys) to match the recursive watcher.
    let baseline: string[] = [];
    try {
      const d = await get<{ files: StudioMediaFile[] }>(
        `/creative/studio-media?path=${encodeURIComponent(chosenDir)}`,
        { timeoutMs: 10000 },
      );
      baseline = d.files.map((f) => f.name).slice(0, STUDIO_BASELINE_CAP);
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
      const skillLabel = skill || "Auto";
      const live: LiveSession = {
        terminalId: res.terminal_id,
        dest: res.cwd || chosenDir,
        cliLabel: engine?.label ?? cli,
        skillLabel,
        command: res.command,
        startedAt: Date.now(),
        awaitsAutomode: res.automode_method === "shift-tab",
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
          skill: skillLabel,
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
    // No stored per-brief times — anchor them all at the session start so any
    // media detected now attaches to the LAST brief, and the prior turns start
    // resolved (done/ready) rather than pretending to still be working.
    setBriefTimes(resumeOffer.messages.map(() => resumeOffer.started_at));
    setSentFirst(resumeOffer.sent_first);
    setLastActivityAt(resumeOffer.started_at);
    setSession({
      terminalId: resumeOffer.terminal_id,
      dest: resumeOffer.dest,
      cliLabel: resumeOffer.cli_label,
      skillLabel: resumeOffer.skill ?? null, // legacy stored sessions predate the field
      command: resumeOffer.command,
      startedAt: resumeOffer.started_at,
      awaitsAutomode: false, // already booted long ago — no boot gate on resume
    });
    setBooting(false);
    setResumeOffer(null);
  };

  /** POST one brief into the studio terminal and record the turn. Shared by
   *  the composer's send and the boot-queue auto-fire. Returns false on error
   *  (sayErr is set and the text is put back in the draft so nothing is lost). */
  const postBrief = async (text: string): Promise<boolean> => {
    if (!session) return false;
    setSending(true);
    setSayErr(null);
    try {
      await post<{ typed: boolean }>(
        `/creative/studio/${session.terminalId}/say`,
        sentFirst
          ? { text }
          : { text, first: true, ...(skill ? { skill } : {}), save_dir: session.dest },
        // A hung request must not lock the composer forever — `sending` only
        // clears in finally, so bound the wait.
        { timeoutMs: 15000 },
      );
      const nextMsgs = [...messages, text];
      setMessages(nextMsgs);
      setBriefTimes((prev) => [...prev, Date.now()]);
      setSentFirst(true);
      setLastActivityAt(Date.now()); // a fresh brief = the turn is working again
      patchStoredSession({ sent_first: true, messages: nextMsgs });
      return true;
    } catch (e) {
      const err = e instanceof ApiError ? e : new ApiError(String(e), 0);
      setDraft(text); // never lose typed work — put it back for a manual retry
      setSayErr(
        err.status === 404 || err.status === 409
          ? `The terminal is gone or has exited — ${err.message}`
          : err.status === 0
            ? "Daemon offline — message not sent."
            : err.message,
      );
      return false;
    } finally {
      setSending(false);
    }
  };

  /** Route a brief to the terminal: queue it while the engine boots (the
   *  auto-fire effect sends it on ready), else POST it right now. `booting`
   *  only ever flips true→false, so a stale-true read just defers to the queue. */
  const dispatchBrief = (text: string) => {
    if (!session) return;
    if (booting) {
      setQueuedBrief((prev) => (prev ? `${prev} ${text}` : text));
      return;
    }
    void postBrief(text);
  };

  const closeIntake = () => {
    setIntakeBrief(null);
    setIntakeQuestions(null);
    setIntakeAnswers({});
  };

  /** FIRST brief only: fetch a few quick clarifying questions, then either show
   *  them inline (compact panel) or — no questions / any error — send the brief
   *  straight through. Intake must NEVER block generation. */
  const beginIntake = async (brief: string) => {
    setIntakeBrief(brief);
    setIntakeQuestions(null);
    setIntakeAnswers({});
    setIntakeBusy(true);
    try {
      const res = await post<{ questions?: IntakeQuestion[] }>(
        "/creative/intake",
        { brief, ...(skill ? { skill } : {}) },
        { timeoutMs: 15000 },
      );
      const qs = Array.isArray(res.questions)
        ? res.questions.filter((q) => q && q.label && Array.isArray(q.options))
        : [];
      if (qs.length === 0) {
        setIntakeBrief(null);
        dispatchBrief(brief); // nothing to ask — go straight to generation
        return;
      }
      setIntakeQuestions(qs);
    } catch {
      setIntakeBrief(null);
      dispatchBrief(brief); // intake failed — never block the user; just generate
    } finally {
      setIntakeBusy(false);
    }
  };

  /** Fold the answered questions into a concise "Preferences:" line and send. */
  const submitIntake = () => {
    if (intakeBrief === null) return;
    const prefs: string[] = [];
    for (const q of intakeQuestions ?? []) {
      const ans = (intakeAnswers[q.key] ?? "").trim();
      if (ans) prefs.push(`${q.key} = ${ans}`);
    }
    const brief = prefs.length
      ? `${intakeBrief}\n\nPreferences: ${prefs.join("; ")}`
      : intakeBrief;
    closeIntake();
    dispatchBrief(brief);
  };

  /** Skip the questions — send the original brief unchanged. */
  const skipIntake = () => {
    if (intakeBrief === null) return;
    const brief = intakeBrief;
    closeIntake();
    dispatchBrief(brief);
  };

  const send = async () => {
    const text = draft.trim();
    if (!text || !session || sending || !alive || gone) return;
    // The intake panel owns the send while it's open/loading.
    if (intakeBrief !== null || intakeBusy) return;
    setDraft("");
    if (!sentFirst) {
      // First brief → optional, fast clarifying-questions step (works while the
      // engine is still booting — the answers fold in, then it queues/sends).
      void beginIntake(text);
      return;
    }
    // Follow-ups go straight through (queued if still booting) — a full two-way
    // conduit, so there's never a need to detour to the Build terminal.
    dispatchBrief(text);
  };

  // Auto-fire the boot-queued brief the moment the readiness gate opens (or
  // surface an honest error if the engine died before it ever became ready).
  useEffect(() => {
    if (booting || !queuedBrief || !session) return;
    if (!alive || gone || phase === "exited") {
      setQueuedBrief(null);
      setDraft(queuedBrief); // keep the text — the user can start a new session
      setSayErr("The engine exited before your queued brief could be sent.");
      return;
    }
    const text = queuedBrief;
    setQueuedBrief(null); // clear FIRST so a re-run of this effect can't double-send
    void postBrief(text);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- postBrief reads fresh state; queuedBrief is cleared synchronously above
  }, [booting, queuedBrief, session, alive, gone, phase]);

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

  /* ---- LIVE phase (a conversation, not a console) ---- */
  if (session) {
    // The CLI quit back to the shell (terminal still alive): another brief
    // would run as a SHELL COMMAND — the daemon refuses it and so do we.
    const engineExited = phase === "exited" && alive && !gone;
    // While the clarifying-questions panel is open/loading, its own controls
    // drive the send, so the free-text composer is inert.
    const intakeOpen = intakeBrief !== null || intakeBusy;
    // The composer NEVER locks for boot — a brief typed while the engine starts
    // is queued (visible in the transcript) and auto-fires on ready. Only a
    // dead terminal/engine, an in-flight send, or the intake panel disables input.
    const inputDisabled = !alive || gone || sending || engineExited || intakeOpen;
    const awaitingAutomode = booting && session.awaitsAutomode && !automode;
    const bootStatus = awaitingAutomode
      ? "Engaging auto mode…"
      : `Starting ${session.cliLabel}…`;
    // The daemon's phase is authoritative (idle/done = genuinely waiting, not just
    // "no bytes for 8s"); the idle timer is only a fallback for older daemons.
    // Only the LAST turn can ever be "working"; earlier turns are always resolved.
    const idleByTimer = nowTick - lastActivityAt >= STUDIO_IDLE_MS;
    const working = phase !== null ? PHASE_BUSY[phase] : !idleByTimer;
    const activeTurn = alive && !gone && !booting && working;

    // Fix 3 — surface when the engine looks like it's waiting on the user (a
    // prompt/question in the tail, or a ready+idle daemon signal) so they answer
    // in-place instead of opening the Build terminal. Only after the first brief,
    // never while booting/sending/working, and never once the engine has exited.
    const lastTailLine = (() => {
      const trimmed = tail.trimEnd();
      const nl = trimmed.lastIndexOf("\n");
      return (nl >= 0 ? trimmed.slice(nl + 1) : trimmed).trim();
    })();
    const tailLooksLikePrompt =
      /[?:]$|\(y\/n\)|\[y\/n\]|press enter|your answer|continue\??$|waiting for/i.test(
        lastTailLine,
      );
    const idlePhase = phase === "idle" || phase === "done";
    const maybeWaiting =
      alive &&
      !gone &&
      !booting &&
      !engineExited &&
      !intakeOpen &&
      sentFirst &&
      !sending &&
      !activeTurn &&
      (tailLooksLikePrompt || (ready && idlePhase));

    // Bucket each produced media file into the turn it belongs to: the most
    // recent brief sent at-or-before the file's first sighting (anything before
    // the first brief falls to turn 0). Never orphans a file.
    const turns = messages.map((text) => ({ text, media: [] as LibraryFile[] }));
    for (const m of mediaTimeline) {
      let idx = 0;
      for (let i = 0; i < turns.length; i++) {
        if (m.at >= (briefTimes[i] ?? 0)) idx = i;
      }
      turns[idx]?.media.push(m.file);
    }
    const lastIdx = turns.length - 1;

    return (
      <>
        <Reveal>
          <div className="card-surface flex flex-wrap items-center gap-x-3 gap-y-2 px-4 py-2.5">
            <span className="inline-flex shrink-0 items-center gap-2 text-sm font-semibold text-zinc-200">
              <Terminal size={15} className="text-accent-soft/80" />
              {session.cliLabel}
            </span>
            {session.skillLabel && (
              <span
                title={`Skill: ${session.skillLabel}`}
                className="inline-flex shrink-0 items-center gap-1.5 text-xs font-medium text-zinc-400"
              >
                <span aria-hidden="true" className="text-zinc-600">
                  ·
                </span>
                <Wand2 size={12} className="text-accent-soft/70" aria-hidden="true" />
                {session.skillLabel}
              </span>
            )}
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
              href={`/terminals?focus=${encodeURIComponent(session.terminalId)}`}
              className="inline-flex shrink-0 items-center gap-1 text-xs font-medium text-accent-soft transition-colors hover:text-accent"
            >
              Open terminal in Build <ArrowRight size={12} />
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

        {(!alive || gone || engineExited) && (
          <Reveal>
            <ErrorNote>
              {gone
                ? "This terminal no longer exists on the daemon."
                : !alive
                  ? `Terminal exited (code ${exitCode ?? "?"}).`
                  : "The engine exited in this terminal — its shell is still open on Build."}{" "}
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
          <div className="card-surface flex flex-col overflow-hidden">
            {/* Transcript — the conversation IS the view. */}
            <div
              ref={transcriptRef}
              onScroll={onTranscriptScroll}
              className="min-h-[38vh] max-h-[60vh] flex-1 space-y-4 overflow-y-auto p-4"
            >
              {turns.length === 0 && !queuedBrief ? (
                <AssistantBubble>
                  {booting || (phase !== null && PHASE_BUSY[phase]) ? (
                    <div className="flex items-center gap-2 text-[13px] text-zinc-300">
                      <WorkingDots />
                      <span>
                        {statusLine || bootStatus}
                        {booting ? " — type your brief now; it sends the moment the engine is ready." : ""}
                      </span>
                    </div>
                  ) : (
                    <p className="text-[13px] text-zinc-400">
                      Ready — describe what to create in{" "}
                      <code className="font-mono text-zinc-300">{folderLabel(session.dest)}</code>.
                      I’ll run it on autopilot and drop the results right here as they land.
                    </p>
                  )}
                </AssistantBubble>
              ) : (
                turns.map((turn, i) => {
                  const isLast = i === lastIdx;
                  const state: TurnState =
                    isLast && (!alive || gone || engineExited)
                      ? "exited"
                      : isLast && activeTurn
                        ? "working"
                        : turn.media.length > 0
                          ? "done"
                          : isLast
                            ? "ready"
                            : "done";
                  return (
                    <div key={i} className="space-y-3">
                      <UserBubble text={turn.text} />
                      <AssistantTurn
                        state={state}
                        media={turn.media}
                        exitCode={exitCode}
                        statusText={isLast ? statusLine : null}
                        onOpenMedia={setStudioSelected}
                      />
                    </div>
                  );
                })
              )}
              {queuedBrief && (
                <div className="space-y-3">
                  <UserBubble text={queuedBrief} />
                  <AssistantBubble>
                    <div className="flex items-center gap-2 text-[13px] text-zinc-300">
                      <WorkingDots />
                      <span>Queued — sending the moment the engine is ready…</span>
                    </div>
                  </AssistantBubble>
                </div>
              )}
            </div>

            {/* Composer + raw-terminal disclosure. */}
            <div className="space-y-2.5 border-t hairline p-4">
              {/* Fix 1 — clarifying-questions intake before the FIRST brief. */}
              {intakeBusy && !intakeQuestions && (
                <div className="rounded-xl border border-accent/20 bg-accent/[0.05] px-3.5 py-3 text-[13px] text-accent-soft/90">
                  <LoaderInline label="A couple quick questions to sharpen your brief…" />
                </div>
              )}
              {intakeQuestions && intakeBrief !== null && (
                <div className="space-y-3 rounded-xl border border-accent/25 bg-accent/[0.05] p-3.5">
                  <div className="flex items-start justify-between gap-3">
                    <p className="min-w-0 flex-1 text-[13px] text-zinc-300">
                      <Sparkles
                        size={13}
                        className="mr-1 inline align-[-1px] text-accent-soft/80"
                        aria-hidden="true"
                      />
                      A couple quick questions to sharpen your brief — all optional.
                    </p>
                    <button
                      type="button"
                      onClick={skipIntake}
                      className="shrink-0 rounded-lg border border-white/10 px-2.5 py-1 text-[11px] font-medium text-zinc-300 transition-colors hover:border-white/20 hover:bg-white/[0.04]"
                    >
                      Skip — just generate
                    </button>
                  </div>
                  <div className="space-y-3">
                    {intakeQuestions.map((q) => {
                      const answer = intakeAnswers[q.key] ?? "";
                      const isCustom = answer !== "" && !q.options.includes(answer);
                      return (
                        <div key={q.key} className="space-y-1.5">
                          <p className="text-[12px] font-medium text-zinc-400">{q.label}</p>
                          <div className="flex flex-wrap items-center gap-1.5">
                            {q.options.map((opt) => {
                              const on = answer === opt;
                              return (
                                <button
                                  key={opt}
                                  type="button"
                                  onClick={() =>
                                    setIntakeAnswers((prev) => ({
                                      ...prev,
                                      [q.key]: prev[q.key] === opt ? "" : opt,
                                    }))
                                  }
                                  className={`rounded-full border px-2.5 py-1 text-[11px] font-medium transition-colors ${
                                    on
                                      ? "border-accent/40 bg-accent/[0.14] text-accent-soft"
                                      : "border-white/10 bg-white/[0.02] text-zinc-300 hover:border-white/20 hover:bg-white/[0.04]"
                                  }`}
                                >
                                  {opt}
                                </button>
                              );
                            })}
                            {q.allow_custom && (
                              <input
                                type="text"
                                value={isCustom ? answer : ""}
                                onChange={(e) =>
                                  setIntakeAnswers((prev) => ({ ...prev, [q.key]: e.target.value }))
                                }
                                onKeyDown={(e) => {
                                  if (e.key === "Enter") {
                                    e.preventDefault();
                                    submitIntake();
                                  }
                                }}
                                placeholder="or type your own…"
                                aria-label={`Custom answer for ${q.label}`}
                                className="min-w-0 max-w-[12rem] flex-1 rounded-full border border-white/10 bg-ink-950 px-2.5 py-1 text-[11px] text-zinc-200 placeholder:text-zinc-600 focus:border-accent/40 focus:outline-none"
                              />
                            )}
                          </div>
                        </div>
                      );
                    })}
                  </div>
                  <div className="flex items-center gap-2 pt-0.5">
                    <button
                      type="button"
                      onClick={submitIntake}
                      className="inline-flex items-center gap-1.5 rounded-xl border border-accent/30 bg-accent/[0.12] px-3.5 py-1.5 text-xs font-semibold text-accent-soft transition-colors hover:bg-accent/[0.18]"
                    >
                      <Send size={13} /> Generate with these
                    </button>
                    <button
                      type="button"
                      onClick={skipIntake}
                      className="rounded-xl border border-white/10 px-3 py-1.5 text-xs font-medium text-zinc-300 transition-colors hover:border-white/20 hover:bg-white/[0.04]"
                    >
                      Skip
                    </button>
                  </div>
                </div>
              )}
              {/* Fix 3 — gentle "engine may be waiting" nudge so the user answers here. */}
              {maybeWaiting && (
                <div className="flex items-center gap-2 rounded-xl border border-accent/20 bg-accent/[0.05] px-3 py-2 text-[11px] text-accent-soft/90">
                  <MessageCircle size={13} className="shrink-0" aria-hidden="true" />
                  The engine may be waiting for your input — type your answer below to keep going
                  here.
                </div>
              )}
              {sayErr && <ErrorNote>{sayErr}</ErrorNote>}
              <div className="flex items-center gap-2">
                <input
                  ref={composerRef}
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
                    !alive || gone
                      ? "terminal ended"
                      : engineExited
                        ? "engine exited — start a new session"
                        : intakeOpen
                          ? "Answer the quick questions above (or skip)…"
                          : booting
                            ? "Describe what to create — sends when the engine is ready…"
                            : sentFirst
                              ? "Send another instruction…"
                              : "Describe what to create…"
                  }
                  aria-label="Brief"
                  className="min-w-0 flex-1 rounded-xl border border-white/10 bg-ink-950 px-3.5 py-2.5 text-sm text-zinc-200 placeholder:text-zinc-600 focus:border-accent/40 focus:outline-none disabled:opacity-50"
                />
                <button
                  type="button"
                  onClick={() => void send()}
                  disabled={inputDisabled || !draft.trim()}
                  aria-label="Send"
                  className="inline-flex shrink-0 items-center gap-1.5 rounded-xl border border-accent/30 bg-accent/[0.1] px-4 py-2.5 text-xs font-medium text-accent-soft transition-colors hover:bg-accent/[0.16] disabled:opacity-50"
                >
                  {sending ? <LoaderInline label="Sending…" /> : <Send size={14} />}
                </button>
              </div>

              <div className="flex items-center justify-between gap-2">
                <button
                  type="button"
                  onClick={() => setShowRaw((v) => !v)}
                  aria-expanded={showRaw}
                  className="inline-flex items-center gap-1 text-[11px] font-medium text-zinc-500 transition-colors hover:text-zinc-300"
                >
                  {showRaw ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
                  {showRaw ? "Hide raw terminal output" : "Show raw terminal output"}
                </button>
                {alive && !gone ? (
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
                )}
              </div>

              {showRaw && (
                <div
                  ref={consoleRef}
                  onScroll={onConsoleScroll}
                  className="max-h-[32vh] overflow-y-auto rounded-xl border border-white/[0.06] bg-ink-950 p-3"
                >
                  <pre className="whitespace-pre-wrap break-words font-mono text-[11px] leading-relaxed text-zinc-400">
                    {tail || (booting ? "engine starting…" : "waiting for output…")}
                  </pre>
                </div>
              )}
            </div>
          </div>
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
                href={`/terminals?focus=${encodeURIComponent(resumeOffer.terminal_id)}`}
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
      {lostNote && (
        <Reveal>
          <div className="flex items-start gap-3 rounded-xl border border-amber-500/25 bg-amber-500/[0.06] px-4 py-2.5">
            <p className="min-w-0 flex-1 text-xs text-amber-200">
              Your previous studio session ended (the daemon may have restarted). The terminal
              and its output are gone; your media is still in the destination folder.
            </p>
            <button
              type="button"
              onClick={() => setLostNote(false)}
              aria-label="Dismiss"
              title="Dismiss"
              className="shrink-0 rounded-lg border border-transparent p-1 text-amber-200/70 transition-colors hover:border-white/10 hover:bg-white/[0.04] hover:text-amber-100"
            >
              <X size={13} />
            </button>
          </div>
        </Reveal>
      )}
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

/* ---- Sort + search (client-side, per view) ---- */

type SortKey = "newest" | "oldest" | "name";

const SORTS: { key: SortKey; label: string }[] = [
  { key: "newest", label: "Newest" },
  { key: "oldest", label: "Oldest" },
  { key: "name", label: "Name A–Z" },
];

// Folder listings carry NO timestamps (/fs/list has no mtime) — offering
// "Newest" there would silently sort by name and lie. Library gets honest
// name orders only ("newest" key doubles as the Z–A mapping in sortLibrary).
const LIB_SORTS: { key: SortKey; label: string }[] = [
  { key: "name", label: "Name A–Z" },
  { key: "newest", label: "Name Z–A" },
];

/** Numeric-aware, case-insensitive filename comparator ("shot 2" < "shot 10"). */
const nameCmp = new Intl.Collator(undefined, { numeric: true, sensitivity: "base" }).compare;

function sortCreations(list: CreativeItem[], sort: SortKey): CreativeItem[] {
  const out = [...list];
  if (sort === "name") out.sort((a, b) => nameCmp(a.filename, b.filename));
  else if (sort === "oldest") out.sort((a, b) => a.created_at.localeCompare(b.created_at));
  else out.sort((a, b) => b.created_at.localeCompare(a.created_at));
  return out;
}

/**
 * Library entries carry no timestamp (FsEntry has name/size only), so
 * Newest/Oldest fall back to natural-name order — generated media embeds
 * sequence numbers/timestamps in filenames, which this tracks honestly.
 */
function sortLibrary(list: LibraryFile[], sort: SortKey): LibraryFile[] {
  const out = [...list];
  if (sort === "newest") out.sort((a, b) => nameCmp(b.name, a.name));
  else out.sort((a, b) => nameCmp(a.name, b.name)); // oldest + name A–Z
  return out;
}

const VIEWS: { id: View; label: string; icon: ReactNode }[] = [
  { id: "creations", label: "Creations", icon: <Sparkles size={14} /> },
  { id: "library", label: "Library", icon: <FolderOpen size={14} /> },
  { id: "create", label: "Create", icon: <Wand2 size={14} /> },
];

export default function CreativePage() {
  const { data, error, loading, reload } = useApi<{ items: CreativeItem[]; count: number }>(
    "/creative/items?limit=500",
  );
  const [filter, setFilter] = useState<Filter>("all");
  const [selected, setSelected] = useState<CreativeItem | null>(null);
  const closeLightbox = useCallback(() => setSelected(null), []);

  // Sort + search — each view keeps its own state.
  const [creSort, setCreSort] = useState<SortKey>("newest");
  const [creQuery, setCreQuery] = useState("");
  const [libSort, setLibSort] = useState<SortKey>("name");
  const [libQuery, setLibQuery] = useState("");

  // View switcher — SSR-safe: default Creations, hydrate from localStorage in an
  // effect (this page is statically prerendered, so no lazy-initializer reads).
  const [view, setView] = useState<View>("creations");
  const [libDir, setLibDir] = useState<string | null>(null);
  // Bumped after a "make playable" conversion to re-list the folder (the new
  // `.playable.mp4` sibling then shows up without a manual navigation).
  const [libRefresh, setLibRefresh] = useState(0);
  const [libSelected, setLibSelected] = useState<LibraryFile | null>(null);
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
    // An open lightbox must not survive a tab switch with stale content.
    setSelected(null);
    setLibSelected(null);
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
  // Incremental render cap ("Show more") — resets when the folder changes.
  const [libCap, setLibCap] = useState(LIB_RENDER_CAP);
  useEffect(() => {
    setLibCap(LIB_RENDER_CAP);
  }, [libDir]);

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
  }, [view, libDir, libRefresh]);

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

  /**
   * THE upload path — the picker and drag-and-drop both land here. Uploads up
   * to 5 files sequentially, keeping every guard (size cap, offline handling,
   * publish errors). `extraErrs` carries pre-flight rejections (non-media drops).
   */
  const uploadFiles = useCallback(
    async (files: File[], extraErrs: string[] = []) => {
      if (uploading) return;
      setUploadOk(null);
      setUploadErr(null);
      setUploadUrl(null);
      const errs = [...extraErrs];
      const batch = files.slice(0, 5);
      if (batch.length === 0) {
        if (errs.length > 0) setUploadErr(errs.join(" "));
        return;
      }
      setUploading(true);
      const oks: string[] = [];
      let lastUrl: string | null = null;
      let uploadedAny = false;
      for (const file of batch) {
        if (file.size > MAX_UPLOAD_BYTES) {
          errs.push(`"${file.name}" (${formatSize(file.size)}) exceeds the 100 MB upload limit.`);
          continue;
        }
        try {
          const content_b64 = await readAsBase64(file);
          const res = await post<UploadResult>("/creative/upload", {
            filename: file.name,
            content_b64,
            ...(alsoPublish ? { publish: true } : {}),
          });
          uploadedAny = true;
          oks.push(`${file.name} (${formatSize(res.size)})`);
          if (res.url) lastUrl = res.url;
          if (res.publish_error)
            errs.push(`${file.name}: upload saved, but publishing failed: ${res.publish_error}`);
        } catch (err) {
          const ae = err instanceof ApiError ? err : new ApiError(String(err), 0);
          if (ae.status === 0) {
            errs.push("Daemon offline — could not upload.");
            break; // no point retrying the rest of the batch
          }
          errs.push(`"${file.name}": ${ae.message}`);
        }
      }
      if (oks.length > 0)
        setUploadOk(
          oks.length === 1 ? `Uploaded ${oks[0]}.` : `Uploaded ${oks.length} files: ${oks.join(", ")}.`,
        );
      if (lastUrl) setUploadUrl(lastUrl);
      if (errs.length > 0) setUploadErr(errs.join(" "));
      if (uploadedAny) reload();
      setUploading(false);
    },
    [uploading, alsoPublish, reload],
  );

  const onFile = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files ?? []);
    e.target.value = ""; // allow re-picking the same file
    void uploadFiles(files);
  };

  // Drag-and-drop upload (Creations + Library views): drop anywhere on the page.
  const dragDepth = useRef(0);
  const [dragActive, setDragActive] = useState(false);
  useEffect(() => {
    if (view === "create") return; // the studio view is not a drop target
    const hasFiles = (e: DragEvent) => Array.from(e.dataTransfer?.types ?? []).includes("Files");
    const onDragEnter = (e: DragEvent) => {
      if (!hasFiles(e)) return;
      e.preventDefault();
      dragDepth.current += 1;
      setDragActive(true);
    };
    const onDragOver = (e: DragEvent) => {
      if (hasFiles(e)) e.preventDefault(); // required, or the browser opens the file
    };
    const onDragLeave = (e: DragEvent) => {
      if (!hasFiles(e)) return;
      dragDepth.current = Math.max(0, dragDepth.current - 1);
      if (dragDepth.current === 0) setDragActive(false);
    };
    const onDrop = (e: DragEvent) => {
      if (!hasFiles(e)) return;
      e.preventDefault();
      dragDepth.current = 0;
      setDragActive(false);
      const all = Array.from(e.dataTransfer?.files ?? []);
      if (all.length === 0) return;
      const media = all.filter((f) => mediaKindOf(f.name) !== null);
      const rejected = all
        .filter((f) => mediaKindOf(f.name) === null)
        .map((f) => `"${f.name}" isn't a supported media type — images, video, and audio only.`);
      void uploadFiles(media, rejected);
    };
    window.addEventListener("dragenter", onDragEnter);
    window.addEventListener("dragover", onDragOver);
    window.addEventListener("dragleave", onDragLeave);
    window.addEventListener("drop", onDrop);
    return () => {
      window.removeEventListener("dragenter", onDragEnter);
      window.removeEventListener("dragover", onDragOver);
      window.removeEventListener("dragleave", onDragLeave);
      window.removeEventListener("drop", onDrop);
      dragDepth.current = 0;
      setDragActive(false);
    };
  }, [view, uploadFiles]);

  const items = useMemo(() => data?.items ?? [], [data]);

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

  // Search first (counts reflect it), then the kind filter, then the sort.
  const creSearched = useMemo(() => {
    const q = creQuery.trim().toLowerCase();
    return q ? items.filter((i) => i.filename.toLowerCase().includes(q)) : items;
  }, [items, creQuery]);
  const libSearched = useMemo(() => {
    const q = libQuery.trim().toLowerCase();
    return q ? mediaFiles.filter((f) => f.name.toLowerCase().includes(q)) : mediaFiles;
  }, [mediaFiles, libQuery]);

  const counts = useMemo(() => {
    const c: Record<Filter, number> = { all: 0, image: 0, video: 0, audio: 0 };
    if (view === "creations") {
      c.all = creSearched.length;
      for (const it of creSearched) c[it.media] = (c[it.media] ?? 0) + 1;
    } else {
      c.all = libSearched.length;
      for (const f of libSearched) c[f.kind] = (c[f.kind] ?? 0) + 1;
    }
    return c;
  }, [view, creSearched, libSearched]);

  const visible = useMemo(
    () =>
      sortCreations(
        filter === "all" ? creSearched : creSearched.filter((i) => i.media === filter),
        creSort,
      ),
    [creSearched, filter, creSort],
  );
  const libVisible = useMemo(
    () =>
      sortLibrary(
        filter === "all" ? libSearched : libSearched.filter((f) => f.kind === filter),
        libSort,
      ),
    [libSearched, filter, libSort],
  );
  const libShown = libVisible.slice(0, libCap);
  const libRemaining = libVisible.length - libShown.length;

  // Lightbox prev/next positions within the CURRENT visible lists.
  const selIdx =
    selected === null
      ? -1
      : visible.findIndex((i) => i.name === selected.name && i.version === selected.version);
  const libIdx =
    libSelected === null ? -1 : libShown.findIndex((f) => f.path === libSelected.path);

  const offline = error !== null && error.status === 0;
  const drivesOffline = drivesError !== null && drivesError.status === 0;
  const libOffline = libError !== null && libError.status === 0;

  const curPath = listing?.path ?? libDir;
  const curPinned = curPath !== null && isPinned(curPath);

  // Sort/search controls bind to the ACTIVE view's own state.
  const sortValue = view === "creations" ? creSort : libSort;
  const setSortValue = view === "creations" ? setCreSort : setLibSort;
  const queryValue = view === "creations" ? creQuery : libQuery;
  const setQueryValue = view === "creations" ? setCreQuery : setLibQuery;

  const filterRow = (
    <div className="flex flex-wrap items-center gap-2">
      {FILTERS.map((f) => (
        <button
          key={f.key}
          type="button"
          aria-pressed={filter === f.key}
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
      <span className="relative ml-auto">
        <Search
          size={12}
          aria-hidden="true"
          className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-zinc-500"
        />
        <input
          type="search"
          value={queryValue}
          onChange={(e) => setQueryValue(e.target.value)}
          placeholder="Search filenames…"
          aria-label="Search by filename"
          className="w-44 rounded-full border border-white/10 bg-ink-950 py-1.5 pl-7 pr-3 text-xs text-zinc-200 placeholder:text-zinc-600 focus:border-accent/40 focus:outline-none"
        />
      </span>
      <select
        value={sortValue}
        onChange={(e) => setSortValue(e.target.value as SortKey)}
        aria-label="Sort order"
        title="Sort order"
        className="appearance-none rounded-full border border-white/10 bg-ink-950 px-3 py-1.5 text-xs text-zinc-300 focus:border-accent/40 focus:outline-none"
      >
        {(view === "creations" ? SORTS : LIB_SORTS).map((s) => (
          <option key={s.key} value={s.key}>
            {s.label}
          </option>
        ))}
      </select>
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

      {/* The studio stays MOUNTED across tab switches — unmounting killed the
          live conversation (messages, media timeline, polls) every time the
          user peeked at the Gallery, leaving only the lossy resume flow. */}
      <div className={view === "create" ? "contents" : "hidden"}>
        <StudioView pins={pins} onUnpin={unpin} active={view === "create"} />
      </div>

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
                  {creQuery.trim()
                    ? `No creations match “${creQuery.trim()}” — try another search or filter.`
                    : `No ${filter} creations yet — try another filter.`}
                </Empty>
              </Card>
            ) : (
              <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 xl:grid-cols-4">
                {visible.map((item) => (
                  <MediaTile
                    key={`${item.name}:${item.version}`}
                    item={item}
                    onOpen={() => setSelected(item)}
                    onConverted={reload}
                  />
                ))}
              </div>
            )}
          </Reveal>
        </>
      ) : view === "create" ? (
        /* ---- Create (studio): rendered ABOVE the chain, always mounted ---- */
        null
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
          {listing?.truncated && (
            <Reveal>
              <p className="rounded-xl border border-amber-500/20 bg-amber-500/[0.06] px-3.5 py-2 text-xs text-amber-200/90">
                This folder holds more than the 2,000 entries shown — the view is
                partial. Open a subfolder to narrow it down.
              </p>
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
                  {libQuery.trim()
                    ? `No files match “${libQuery.trim()}” in this folder — try another search or filter.`
                    : `No ${filter} files in this folder — try another filter.`}
                </Empty>
              </Card>
            ) : (
              <div className="space-y-3">
                <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 xl:grid-cols-4">
                  {libShown.map((f) => (
                    <LibraryTile
                      key={f.path}
                      file={f}
                      onOpen={() => setLibSelected(f)}
                      onConverted={() => setLibRefresh((n) => n + 1)}
                    />
                  ))}
                </div>
                {libRemaining > 0 && (
                  <div className="flex justify-center">
                    <button
                      type="button"
                      onClick={() => setLibCap((c) => c + LIB_RENDER_CAP)}
                      className="inline-flex items-center gap-1.5 rounded-xl border border-white/10 px-4 py-2 text-xs font-medium text-zinc-300 transition-colors hover:border-white/20 hover:bg-white/[0.04] hover:text-zinc-100"
                    >
                      Show {Math.min(LIB_RENDER_CAP, libRemaining)} more ({libRemaining} remaining)
                    </button>
                  </div>
                )}
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
          deleteName={selected.name}
          onDeleted={() => {
            setSelected(null);
            reload();
          }}
          onPrev={selIdx > 0 ? () => setSelected(visible[selIdx - 1]) : null}
          onNext={
            selIdx >= 0 && selIdx < visible.length - 1
              ? () => setSelected(visible[selIdx + 1])
              : null
          }
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
          onPrev={libIdx > 0 ? () => setLibSelected(libShown[libIdx - 1]) : null}
          onNext={
            libIdx >= 0 && libIdx < libShown.length - 1
              ? () => setLibSelected(libShown[libIdx + 1])
              : null
          }
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

      {dragActive && (
        <div
          aria-hidden="true"
          className="pointer-events-none fixed inset-0 z-[60] flex items-center justify-center bg-black/60 backdrop-blur-sm"
        >
          <div className="flex items-center gap-3 rounded-2xl border-2 border-dashed border-accent/50 bg-ink-950/85 px-8 py-6 text-sm font-semibold text-accent-soft">
            <Upload size={18} /> Drop to add to the gallery
          </div>
        </div>
      )}
    </PageShell>
  );
}
