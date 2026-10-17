"use client";

/**
 * A PICTURE IN THE CHAT OPENS FULL SCREEN (v1.348.0, polish wave A, lane L4).
 * Ideas ported from 21st.dev agent-elements (MIT) — the Input Bar's picture
 * thumbnails and a lightbox — no code installed. The chat's calm look is
 * untouched: a picture in a reply looks exactly as it did; it only gained a
 * zoom-in cursor and became a real button.
 *
 *  - `MediaGroup` is one message's set of pictures. It renders NO element of
 *    its own (a wrapper would move a reply's last child, which the streaming
 *    caret hangs on), so pictures register here and are put in page order
 *    when one is opened. `Markdown` opens a group per render; a nested group
 *    (a draft card's markdown inside a reply) joins the one around it.
 *  - `ZoomableImage` is the picture as a button: click, Enter or Space opens
 *    the lightbox on it; Left/Right then walk the pictures of the SAME group.
 *  - `MediaLightbox` is built on the shared `<Modal>` (portal, Escape, focus
 *    in / Tab trap / focus back): the picture contained to the window, its
 *    name (and its alt words when they say something), a close button, and an
 *    Open control that opens the file the way the files rail does
 *    (POST /documents/open) — or a web picture in a new tab.
 *  - `AttachmentThumb` is the composer chip's small square picture.
 *
 * Videos and audio never come here: they keep their native players. A web
 * picture only reaches a `ZoomableImage` after its "Load image" press
 * (`RemoteMediaGate` in components/Markdown.tsx) — nothing here loads one.
 * No animation is added, so there is nothing to stop under reduced motion
 * (the one spinner carries `motion-reduce:animate-none`).
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { ChevronLeft, ChevronRight, ExternalLink, Loader2, Paperclip, X } from "lucide-react";
import { Modal } from "@/components/Modal";
import { ApiError, post } from "@/lib/api";
import { altCaption, localMediaSrc, mediaName, openWayFor, stepIndex } from "@/lib/mediaLightbox";

/** One picture as the lightbox shows it. */
export interface LightboxPicture {
  /** What the <img> loads (a local file already resolved through the daemon). */
  src: string;
  /** The reference as written — a path or a web address. Open opens this. */
  raw: string;
  /** The file name (or the last part of the address). */
  name: string;
  /** The alt text as written ("" when none). */
  alt: string;
}

interface Entry {
  el: () => HTMLElement | null;
  picture: () => LightboxPicture;
}

interface Group {
  register: (entry: Entry) => () => void;
  open: (el: HTMLElement) => void;
}

const GroupContext = createContext<Group | null>(null);

/** One message's pictures. Joins the group around it when there is one. */
export function MediaGroup({ children }: { children: ReactNode }) {
  const outer = useContext(GroupContext);
  if (outer) return <>{children}</>;
  return <OwnGroup>{children}</OwnGroup>;
}

function inPageOrder(a: Entry, b: Entry): number {
  const x = a.el();
  const y = b.el();
  if (!x || !y || x === y) return 0;
  return x.compareDocumentPosition(y) & Node.DOCUMENT_POSITION_FOLLOWING ? -1 : 1;
}

function OwnGroup({ children }: { children: ReactNode }) {
  const entries = useRef(new Set<Entry>());
  const [view, setView] = useState<{ pictures: LightboxPicture[]; index: number } | null>(null);
  // Stable: a picture registers once, and opening the lightbox re-renders this
  // group alone — the markdown under it is the same element, so it is not
  // parsed again.
  const group = useMemo<Group>(
    () => ({
      register(entry) {
        entries.current.add(entry);
        return () => {
          entries.current.delete(entry);
        };
      },
      open(el) {
        const live = [...entries.current].filter((e) => e.el()?.isConnected).sort(inPageOrder);
        const index = live.findIndex((e) => e.el() === el);
        if (index < 0) return;
        setView({ pictures: live.map((e) => e.picture()), index });
      },
    }),
    [],
  );
  const close = useCallback(() => setView(null), []);
  const go = useCallback((index: number) => setView((v) => (v ? { ...v, index } : v)), []);
  return (
    <GroupContext.Provider value={group}>
      {children}
      {view && <MediaLightbox pictures={view.pictures} index={view.index} onIndex={go} onClose={close} />}
    </GroupContext.Provider>
  );
}

interface ZoomableImageProps {
  /** What the <img> loads. */
  src: string;
  /** The reference as written (path or address) — the lightbox's Open. */
  raw: string;
  alt: string;
  /** Classes of the <img>: the picture looks exactly as it did before. */
  imgClassName: string;
  /** Classes of the button around it (margins move here from the <img>). */
  buttonClassName?: string;
  /** Overrides the name read from `raw` (a chip knows the file's own name). */
  name?: string;
  loading?: "lazy" | "eager";
  onError?: () => void;
  testId?: string;
}

/** A picture that opens full screen. Safe outside a group: it makes its own. */
export function ZoomableImage(props: ZoomableImageProps) {
  return (
    <MediaGroup>
      <ZoomButton {...props} />
    </MediaGroup>
  );
}

function ZoomButton({
  src,
  raw,
  alt,
  imgClassName,
  buttonClassName = "",
  name,
  loading = "lazy",
  onError,
  testId,
}: ZoomableImageProps) {
  const group = useContext(GroupContext);
  const ref = useRef<HTMLButtonElement>(null);
  const shownName = name || mediaName(raw);
  // The lightbox reads the picture when it opens, so a re-render that changes
  // a prop (a streaming reply) never needs a fresh registration.
  const latest = useRef<LightboxPicture>({ src, raw, name: shownName, alt });
  latest.current = { src, raw, name: shownName, alt };
  useEffect(() => {
    if (!group) return;
    return group.register({ el: () => ref.current, picture: () => latest.current });
  }, [group]);
  const label = altCaption(alt, shownName) || shownName;
  return (
    <button
      ref={ref}
      type="button"
      data-testid={testId ?? "zoomable-image"}
      onClick={(e) => {
        // Never also follow a link the model wrapped the picture in, nor
        // reach a row's own click behind it.
        e.preventDefault();
        e.stopPropagation();
        if (ref.current) group?.open(ref.current);
      }}
      aria-label={`View ${label} full screen`}
      title="View full screen"
      className={`inline-block max-w-full cursor-zoom-in focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60 ${buttonClassName}`}
    >
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img src={src} alt={alt || shownName} loading={loading} onError={onError} className={imgClassName} />
    </button>
  );
}

const ICON_BTN =
  "grid h-7 w-7 place-items-center rounded-lg text-zinc-400 transition-colors hover:bg-white/[0.05] hover:text-zinc-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60 disabled:cursor-not-allowed disabled:opacity-30 motion-reduce:transition-none";
const OPEN_BTN =
  "inline-flex h-7 items-center gap-1.5 rounded-lg px-2 text-[12px] text-zinc-300 transition-colors hover:bg-white/[0.05] hover:text-zinc-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60 disabled:opacity-50 motion-reduce:transition-none";

/** The full-screen view of one group's pictures. */
export function MediaLightbox({
  pictures,
  index,
  onIndex,
  onClose,
}: {
  pictures: LightboxPicture[];
  index: number;
  onIndex: (index: number) => void;
  onClose: () => void;
}) {
  const count = pictures.length;
  const at = Math.min(Math.max(index, 0), Math.max(count - 1, 0));
  const pic = pictures[at];
  const several = count > 1;
  const [opening, setOpening] = useState(false);
  const [openErr, setOpenErr] = useState<string | null>(null);

  useEffect(() => setOpenErr(null), [at]);

  // Left/Right walk the group, stopping at the ends — never while a key is
  // held with a modifier (the app's own shortcuts) or typed into a field.
  useEffect(() => {
    if (!several) return;
    function onKey(e: KeyboardEvent) {
      if (e.defaultPrevented || e.altKey || e.ctrlKey || e.metaKey) return;
      const tag = (e.target as HTMLElement | null)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
      if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
      e.preventDefault();
      const next = stepIndex(at, count, e.key === "ArrowLeft" ? -1 : 1);
      if (next !== at) onIndex(next);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [several, at, count, onIndex]);

  if (!pic) return null;
  const way = openWayFor(pic.raw);
  const caption = altCaption(pic.alt, pic.name);

  async function openFile(path: string) {
    if (opening) return;
    setOpening(true);
    setOpenErr(null);
    try {
      await post("/documents/open", { path });
    } catch (e) {
      // Said plainly, never a pretend launch (the files rail's rule).
      setOpenErr(e instanceof ApiError ? e.message : String(e));
    } finally {
      setOpening(false);
    }
  }

  return (
    <Modal
      label={`Picture: ${caption || pic.name}`}
      onClose={onClose}
      testId="media-lightbox"
      className="w-full max-w-[96vw]"
    >
      <header className="flex items-center gap-3 border-b hairline px-4 py-2.5">
        <div className="min-w-0 flex-1">
          <div
            data-testid="media-lightbox-name"
            title={pic.raw.startsWith("data:") ? undefined : pic.raw}
            className="truncate text-[13px] font-medium text-zinc-100"
          >
            {pic.name}
          </div>
          {caption && (
            <div data-testid="media-lightbox-alt" className="truncate text-[12px] text-zinc-500">
              {caption}
            </div>
          )}
        </div>
        {several && (
          <span
            data-testid="media-lightbox-count"
            aria-live="polite"
            className="shrink-0 text-[12px] tabular-nums text-zinc-500"
          >
            {at + 1} of {count}
          </span>
        )}
        <div className="flex shrink-0 items-center gap-1">
          {several && (
            <>
              <button
                type="button"
                onClick={() => onIndex(stepIndex(at, count, -1))}
                disabled={at === 0}
                aria-label="Previous picture"
                title="Previous picture (Left arrow)"
                className={ICON_BTN}
              >
                <ChevronLeft size={16} />
              </button>
              <button
                type="button"
                onClick={() => onIndex(stepIndex(at, count, 1))}
                disabled={at === count - 1}
                aria-label="Next picture"
                title="Next picture (Right arrow)"
                className={ICON_BTN}
              >
                <ChevronRight size={16} />
              </button>
            </>
          )}
          {way?.kind === "file" && (
            <button
              type="button"
              data-testid="media-lightbox-open"
              onClick={() => void openFile(way.path)}
              disabled={opening}
              title={`Open ${pic.name} in its app`}
              className={OPEN_BTN}
            >
              {opening ? (
                <Loader2 size={13} className="animate-spin motion-reduce:animate-none" />
              ) : (
                <ExternalLink size={13} />
              )}
              Open
            </button>
          )}
          {way?.kind === "web" && (
            <a
              data-testid="media-lightbox-open"
              href={way.href}
              target="_blank"
              rel="noreferrer noopener"
              title={`Open ${pic.name} in a new tab`}
              className={OPEN_BTN}
            >
              <ExternalLink size={13} />
              Open
            </a>
          )}
          <button
            type="button"
            autoFocus
            onClick={onClose}
            aria-label="Close"
            title="Close (Esc)"
            className={ICON_BTN}
          >
            <X size={16} />
          </button>
        </div>
      </header>
      {openErr && (
        <p role="alert" data-testid="media-lightbox-error" className="px-4 pt-2 text-[12px] text-tone-danger">
          Couldn&apos;t open it: {openErr}
        </p>
      )}
      <div className="flex min-h-0 flex-1 items-center justify-center bg-ink-950 p-2">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          key={pic.src}
          data-testid="media-lightbox-picture"
          src={pic.src}
          alt={pic.alt || pic.name}
          className="max-h-[calc(88vh-5rem)] w-auto max-w-full object-contain"
        />
      </div>
    </Modal>
  );
}

/** The small square picture inside a composer chip (attached, not sent yet).
 *  A picture that will not load falls back to the chip's paperclip. */
export function AttachmentThumb({ path, name }: { path: string; name: string }) {
  const [failed, setFailed] = useState(false);
  if (failed) return <Paperclip size={11} className="shrink-0 text-accent-soft" />;
  return (
    <ZoomableImage
      src={localMediaSrc(path)}
      raw={path}
      name={name}
      alt={name}
      loading="eager"
      onError={() => setFailed(true)}
      testId="attachment-thumb"
      buttonClassName="-my-0.5 -ml-1 shrink-0 rounded"
      imgClassName="block h-5 w-5 rounded object-cover"
    />
  );
}
