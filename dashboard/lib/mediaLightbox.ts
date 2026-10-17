/**
 * Pictures in the chat (v1.348.0, polish wave A, lane L4 — the agent-elements
 * Input Bar thumbnails idea and a lightbox; ideas only, no code installed).
 *
 * The pure half: which file is a picture worth a thumbnail, how a LOCAL path
 * becomes a URL the browser can load, what a picture is called in the
 * lightbox, and how Left/Right step through a message's pictures.
 *
 * `localMediaSrc` is THE local-media URL: components/Markdown.tsx (a picture
 * or a player in a reply) and the composer's attachment thumbnail both call
 * it. A browser cannot load `C:\…\out.png` itself, so the path goes through
 * the daemon's guarded `/creative/file-by-path` (media extensions + the fs
 * policy); `?token=` because an `<img>` cannot send an Authorization header.
 */

import { API_BASE, ijToken } from "@/lib/api";

/** A path the chat reads as THIS PC's file: a drive path, a root path, or a
 *  `file://` URL. (`//host/x` is a protocol-relative WEB address, never local.) */
export function isLocalMediaRef(raw: string): boolean {
  const s = raw || "";
  return /^([A-Za-z]:[\\/]|\/(?!\/))/.test(s) || s.startsWith("file://");
}

/** The file path behind a local reference (`file://` dropped). */
export function localPathOf(raw: string): string {
  return (raw || "").replace(/^file:\/\//, "");
}

/** The URL that loads a local media file through the daemon. */
export function localMediaSrc(path: string): string {
  const token = ijToken();
  return `${API_BASE}/creative/file-by-path?path=${encodeURIComponent(path)}${
    token ? `&token=${encodeURIComponent(token)}` : ""
  }`;
}

/** The pictures a composer chip shows as a thumbnail (by extension). Other
 *  files — a PDF, a spreadsheet, a video — keep the paperclip. */
const THUMB_RX = /\.(png|jpe?g|gif|webp)$/i;

export function isThumbImage(nameOrPath: string): boolean {
  return THUMB_RX.test((nameOrPath || "").trim());
}

/** What a picture is called: its file name for a path, the last part of a web
 *  address, "Picture" for an inline image with no name at all. */
export function mediaName(raw: string): string {
  const s = (raw || "").trim();
  if (!s || s.startsWith("data:")) return "Picture";
  let tail = s;
  if (!isLocalMediaRef(s)) {
    try {
      tail = new URL(s).pathname;
    } catch {
      /* not a URL: read it as written */
    }
  }
  const last = localPathOf(tail).split(/[\\/]/).filter(Boolean).pop() ?? "";
  try {
    return decodeURIComponent(last) || "Picture";
  } catch {
    return last || "Picture";
  }
}

/** Alt text that only repeats the kind of thing it is says nothing the name
 *  does not ("image" is what the daemon's own media line writes). */
const GENERIC_ALT = new Set(["", "image", "picture", "photo", "img", "generated media", "media"]);

/** The alt words worth a line under the name, or "" when they add nothing
 *  (a generic word, or the file name again — with or without its extension). */
export function altCaption(alt: string | undefined, name: string): string {
  const a = (alt || "").trim();
  const low = a.toLowerCase();
  if (GENERIC_ALT.has(low)) return "";
  const n = name.trim().toLowerCase();
  if (low === n || low === n.replace(/\.[a-z0-9]+$/, "")) return "";
  return a;
}

/** Left/Right through `count` pictures: one step, stopping at the ends (the
 *  Creative lightbox stops there too). */
export function stepIndex(index: number, count: number, dir: -1 | 1): number {
  if (count <= 0) return 0;
  return Math.min(count - 1, Math.max(0, index + dir));
}

/** How the lightbox's Open control opens this picture: a local file in its
 *  app (POST /documents/open, as the files rail does), a web picture in a new
 *  tab, an inline image not at all. */
export type OpenWay = { kind: "file"; path: string } | { kind: "web"; href: string } | null;

export function openWayFor(raw: string): OpenWay {
  const s = (raw || "").trim();
  if (!s || s.startsWith("data:")) return null;
  if (isLocalMediaRef(s)) {
    const path = localPathOf(s);
    // v1.348.0 (re-review): an SVG can carry script, and Open hands the file
    // to the OS default app (often a browser) — a model-written path must not
    // become a local page that runs. The picture still shows in the lightbox.
    if (/\.svg$/i.test(path.split(/[?#]/)[0])) return null;
    return { kind: "file", path };
  }
  if (/^https?:\/\//i.test(s)) return { kind: "web", href: s };
  return null;
}
