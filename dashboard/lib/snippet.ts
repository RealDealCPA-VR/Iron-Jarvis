// Screen-snippet capture + shrink-to-fit (v1.194.0).
//
// WHY THIS MODULE EXISTS. A terminal pane on the Build page is a BYTE STREAM
// (ConPTY): there is no image channel to paste an image into. But every AI CLI
// we launch there reads images OFF DISK when handed a path, and the daemon runs
// on the same machine as the CLI child, so a path is genuinely shared state.
// The flow is therefore: capture the bitmap in the browser -> shrink it to fit
// the CLI's byte budget -> write it to disk daemon-side -> type the PATH into
// the pane. We deliberately do NOT synthesize a clipboard-paste keystroke: that
// is CLI- and platform-specific, and on this user's exact platform (Windows
// native) Ctrl+V after Win+Shift+S is documented to do nothing at all.
//
// THE QUALITY BAR. An image is never rejected merely for being big — it is
// SHRUNK TO FIT: a quality ladder first, and only if the lowest quality still
// overflows do we sacrifice resolution. A budget failure ("too-large") and a
// decode failure ("unreadable") are different outcomes because a caller must
// report them differently ("that snip won't fit" vs "I couldn't read that").
//
// Pure by design: the two browser seams (decode, encode) are INJECTABLE, so the
// ladder order — the whole quality argument — is testable under jsdom, which
// has neither createImageBitmap nor canvas.

/**
 * Default byte budget for one image handed to a CLI.
 *
 * Claude Code and Codex CLI both cap attached images at roughly 5MB and reject
 * anything larger outright, so 5MB is the ceiling we aim UNDER, not at: we keep
 * a small margin because the daemon writes the bytes verbatim and a file that
 * lands one byte over is a hard rejection at the far end of the pipeline.
 */
export const CLI_IMAGE_BUDGET_BYTES = 4_800_000; // ~4.6 MiB, under every CLI's ~5MB cap

/**
 * Longest-edge cap for a re-encoded image. 2048 is chosen so a retina/4K
 * screenshot stays LEGIBLE — halving a 2560px-wide capture of a code editor
 * turns 12px type into mush, which defeats the point of sending it to a model
 * that has to read the text in it.
 */
export const MAX_EDGE = 2048;

/**
 * Hard refusal above this many SOURCE bytes. Decoding hundreds of megapixels
 * can OOM the tab (the decode happens before we know the dimensions, so there
 * is no cheaper guard), and a killed tab loses the user's whole Build canvas.
 * A refusal here is a real, reported outcome — not a crash.
 */
export const MAX_SOURCE_BYTES = 64 * 1024 * 1024;

/** Tried IN ORDER, at full (capped) resolution, BEFORE any pixels are dropped. */
export const QUALITY_LADDER = [0.92, 0.85, 0.78, 0.68] as const;

/** Only reached when the lowest quality still overflows. Relative to the capped size. */
export const SCALE_LADDER = [1, 0.8, 0.64, 0.5, 0.4, 0.3, 0.22] as const;

/** Formats every supported CLI accepts as-is. Anything else gets re-encoded. */
export const CLI_IMAGE_TYPES = new Set(["image/png", "image/jpeg", "image/gif", "image/webp"]);

/** Re-encode target. JPEG is accepted by every CLI and is the only format whose
 *  quality ladder actually buys bytes (PNG ignores the quality argument). */
const RECOMPRESS_TYPE = "image/jpeg";

// ---------------------------------------------------------------------------
// (a) Getting image blobs out of a paste / a drop
// ---------------------------------------------------------------------------

/** SVG is an image MIME but not a raster: no CLI takes it and canvas decode of
 *  it is a security-tainted mess. Everything else image/* is fair game. */
function isUsableImageType(type: string): boolean {
  return type.startsWith("image/") && type !== "image/svg+xml";
}

const EXT_BY_TYPE: Record<string, string> = {
  "image/png": "png",
  "image/jpeg": "jpg",
  "image/gif": "gif",
  "image/webp": "webp",
  "image/bmp": "bmp",
  "image/avif": "avif",
};

let snipCounter = 0;

/** A Win+Shift+S snip arrives as an image/png item with NO usable filename —
 *  browsers hand back `""` or a generic `image.png` for EVERY snip, so naming
 *  by it would make two snips collide on disk. Synthesize a stable unique one. */
function namedImageFile(blob: Blob, given: string | undefined | null): File {
  const generic = !given || given === "blob" || /^image\.(png|jpe?g|gif|webp)$/i.test(given);
  if (!generic) {
    return blob instanceof File ? blob : new File([blob], given as string, { type: blob.type });
  }
  const ext = EXT_BY_TYPE[blob.type] ?? "png";
  snipCounter += 1;
  const name = `snip-${Date.now()}-${snipCounter}.${ext}`;
  return new File([blob], name, { type: blob.type });
}

/**
 * Image files carried by a paste. Non-image items (the normal case — text) are
 * IGNORED, so a plain text paste into a terminal pane is untouched by this.
 */
export function imageItemsFromPaste(e: ClipboardEvent): File[] {
  const dt = e.clipboardData;
  if (!dt) return [];
  const out: File[] = [];
  const items = dt.items ? Array.from(dt.items) : [];
  for (const item of items) {
    if (item.kind !== "file") continue; // a text/plain item is not a snip
    if (!isUsableImageType(item.type || "")) continue;
    const blob = item.getAsFile?.();
    if (!blob) continue;
    out.push(namedImageFile(blob, (blob as File).name));
  }
  // Some browsers expose a pasted screenshot only through `files`.
  if (out.length === 0 && dt.files?.length) {
    for (const f of Array.from(dt.files)) {
      if (isUsableImageType(f.type || "")) out.push(namedImageFile(f, f.name));
    }
  }
  return out;
}

/** Image files carried by a drop. Dragged text/URLs are ignored. */
export function imageFilesFromDrop(e: DragEvent): File[] {
  const dt = e.dataTransfer;
  if (!dt) return [];
  const out: File[] = [];
  for (const f of Array.from(dt.files ?? [])) {
    if (isUsableImageType(f.type || "")) out.push(namedImageFile(f, f.name));
  }
  if (out.length === 0 && dt.items) {
    for (const item of Array.from(dt.items)) {
      if (item.kind !== "file" || !isUsableImageType(item.type || "")) continue;
      const blob = item.getAsFile?.();
      if (blob) out.push(namedImageFile(blob, (blob as File).name));
    }
  }
  return out;
}

/** True when a paste/drop event carries at least one image we would take. */
export function hasImagePayload(e: ClipboardEvent | DragEvent): boolean {
  const dt =
    (e as ClipboardEvent).clipboardData ?? (e as DragEvent).dataTransfer ?? null;
  if (!dt) return false;
  for (const item of Array.from(dt.items ?? [])) {
    if (item.kind === "file" && isUsableImageType(item.type || "")) return true;
  }
  for (const f of Array.from(dt.files ?? [])) {
    if (isUsableImageType(f.type || "")) return true;
  }
  return false;
}

// ---------------------------------------------------------------------------
// (b) shrink-to-fit
// ---------------------------------------------------------------------------

export type DecodedImage = {
  width: number;
  height: number;
  /** Whatever the encoder can draw: an ImageBitmap or an HTMLImageElement. */
  source: unknown;
  /** Optional cleanup (ImageBitmap.close / URL.revokeObjectURL). */
  close?: () => void;
};

export type ShrinkDeps = {
  decode: (file: Blob) => Promise<DecodedImage>;
  encode: (
    img: DecodedImage,
    width: number,
    height: number,
    type: string,
    quality: number,
  ) => Promise<Blob>;
};

export type ShrinkOk = {
  ok: true;
  file: File;
  /** TRUE means the bytes are NOT the original — the UI must say so rather than
   *  implying the model is looking at exactly what the user captured. */
  recompressed: boolean;
  bytes: number;
  originalBytes: number;
  /** null when the image was passed through undecoded — we do not guess. */
  width: number | null;
  height: number | null;
  /** null on pass-through; otherwise the ladder rung that fit. */
  quality: number | null;
  /** 1 on pass-through and whenever no resolution had to be sacrificed. */
  scale: number;
  attempts: number;
};

export type ShrinkFail =
  | {
      ok: false;
      reason: "too-large";
      bytes: number;
      limitBytes: number;
      attempts: number;
      detail: string;
    }
  | { ok: false; reason: "unreadable"; detail: string };

export type ShrinkResult = ShrinkOk | ShrinkFail;

export type EncodeAttempt = {
  scale: number;
  width: number;
  height: number;
  quality: number;
};

/**
 * The ladder, materialized. Quality is exhausted at the capped full size FIRST;
 * only then does each downscale step run its own full quality ladder. Pinning
 * this order is the point of the module: sacrificing resolution before quality
 * would throw away legibility we did not have to.
 */
export function encodeAttempts(
  srcWidth: number,
  srcHeight: number,
  maxEdge: number = MAX_EDGE,
): EncodeAttempt[] {
  const longest = Math.max(srcWidth, srcHeight);
  // The cap-to-maxEdge fit is not a "sacrifice" — it is the legibility ceiling.
  const fit = longest > maxEdge ? maxEdge / longest : 1;
  const out: EncodeAttempt[] = [];
  for (const step of SCALE_LADDER) {
    const scale = fit * step;
    const width = Math.max(1, Math.round(srcWidth * scale));
    const height = Math.max(1, Math.round(srcHeight * scale));
    const prev = out[out.length - 1];
    // Tiny sources collapse several steps onto identical pixel sizes; encoding
    // the same dimensions twice only burns CPU.
    if (prev && prev.width === width && prev.height === height) continue;
    for (const quality of QUALITY_LADDER) out.push({ scale: step, width, height, quality });
  }
  return out;
}

function renamed(name: string, ext: string): string {
  const base = name.replace(/\.[^./\\]+$/, "") || "snip";
  return `${base}.${ext}`;
}

/**
 * Fit *file* into *maxBytes*.
 *
 * - already within budget and in a CLI-native format -> passed through UNTOUCHED
 * - over budget -> quality ladder, then downscale steps (see `encodeAttempts`)
 * - above MAX_SOURCE_BYTES -> refused before decoding (OOM guard)
 * - undecodable -> `{ reason: "unreadable" }`, distinct from a budget failure
 */
export async function shrinkToFit(
  file: File,
  maxBytes: number = CLI_IMAGE_BUDGET_BYTES,
  deps: ShrinkDeps = defaultDeps(),
): Promise<ShrinkResult> {
  const originalBytes = file.size;

  if (originalBytes > MAX_SOURCE_BYTES) {
    return {
      ok: false,
      reason: "too-large",
      bytes: originalBytes,
      limitBytes: MAX_SOURCE_BYTES,
      attempts: 0,
      detail:
        `Image is ${Math.round(originalBytes / 1048576)}MB; refusing to decode above ` +
        `${Math.round(MAX_SOURCE_BYTES / 1048576)}MB because decoding it could crash the app.`,
    };
  }

  if (originalBytes <= maxBytes && CLI_IMAGE_TYPES.has(file.type)) {
    return {
      ok: true,
      file,
      recompressed: false,
      bytes: originalBytes,
      originalBytes,
      width: null,
      height: null,
      quality: null,
      scale: 1,
      attempts: 0,
    };
  }

  let img: DecodedImage;
  try {
    img = await deps.decode(file);
  } catch (err) {
    return { ok: false, reason: "unreadable", detail: describe(err) };
  }
  if (!img || !(img.width > 0) || !(img.height > 0)) {
    img?.close?.();
    return { ok: false, reason: "unreadable", detail: "Decoded image has no pixels." };
  }

  const plan = encodeAttempts(img.width, img.height);
  let attempts = 0;
  let smallest = Number.POSITIVE_INFINITY;
  try {
    for (const step of plan) {
      attempts += 1;
      let blob: Blob;
      try {
        blob = await deps.encode(img, step.width, step.height, RECOMPRESS_TYPE, step.quality);
      } catch (err) {
        return { ok: false, reason: "unreadable", detail: describe(err) };
      }
      if (blob.size < smallest) smallest = blob.size;
      if (blob.size <= maxBytes) {
        return {
          ok: true,
          file: new File([blob], renamed(file.name, "jpg"), { type: RECOMPRESS_TYPE }),
          recompressed: true,
          bytes: blob.size,
          originalBytes,
          width: step.width,
          height: step.height,
          quality: step.quality,
          scale: step.scale,
          attempts,
        };
      }
    }
  } finally {
    img.close?.();
  }

  return {
    ok: false,
    reason: "too-large",
    bytes: Number.isFinite(smallest) ? smallest : originalBytes,
    limitBytes: maxBytes,
    attempts,
    detail:
      `Could not get this image under ${Math.round(maxBytes / 1024)}KB — smallest ` +
      `encode was ${Math.round((Number.isFinite(smallest) ? smallest : originalBytes) / 1024)}KB.`,
  };
}

function describe(err: unknown): string {
  if (err instanceof Error) return err.message || err.name;
  return String(err ?? "unknown error");
}

// ---------------------------------------------------------------------------
// Default browser seams (never exercised under jsdom — tests inject instead)
// ---------------------------------------------------------------------------

export function defaultDeps(): ShrinkDeps {
  return { decode: decodeImage, encode: encodeImage };
}

async function decodeImage(file: Blob): Promise<DecodedImage> {
  if (typeof createImageBitmap === "function") {
    const bmp = await createImageBitmap(file);
    return { width: bmp.width, height: bmp.height, source: bmp, close: () => bmp.close() };
  }
  const url = URL.createObjectURL(file);
  try {
    const el = await new Promise<HTMLImageElement>((resolve, reject) => {
      const im = new Image();
      im.onload = () => resolve(im);
      im.onerror = () => reject(new Error("The browser could not decode this image."));
      im.src = url;
    });
    return {
      width: el.naturalWidth,
      height: el.naturalHeight,
      source: el,
      close: () => URL.revokeObjectURL(url),
    };
  } catch (err) {
    URL.revokeObjectURL(url);
    throw err;
  }
}

async function encodeImage(
  img: DecodedImage,
  width: number,
  height: number,
  type: string,
  quality: number,
): Promise<Blob> {
  const source = img.source as CanvasImageSource;
  if (typeof OffscreenCanvas === "function") {
    const canvas = new OffscreenCanvas(width, height);
    const ctx = canvas.getContext("2d");
    if (!ctx) throw new Error("No 2D context available to resize this image.");
    paint(ctx as unknown as CanvasRenderingContext2D, source, width, height, type);
    return canvas.convertToBlob({ type, quality });
  }
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("No 2D context available to resize this image.");
  paint(ctx, source, width, height, type);
  return new Promise<Blob>((resolve, reject) => {
    canvas.toBlob(
      (b) => (b ? resolve(b) : reject(new Error("The browser could not re-encode this image."))),
      type,
      quality,
    );
  });
}

function paint(
  ctx: CanvasRenderingContext2D,
  source: CanvasImageSource,
  width: number,
  height: number,
  type: string,
) {
  // JPEG has no alpha: without this, a transparent PNG's background comes out
  // BLACK and a light-theme screenshot becomes unreadable.
  if (type === "image/jpeg") {
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, 0, width, height);
  }
  ctx.imageSmoothingEnabled = true;
  ctx.imageSmoothingQuality = "high";
  ctx.drawImage(source, 0, 0, width, height);
}

// ---------------------------------------------------------------------------
// Handing the bytes to the daemon
// ---------------------------------------------------------------------------

/** base64 of a file's bytes, for a JSON POST to the daemon (which writes it to
 *  disk next to the pane's workspace and hands the CLI the path). Chunked
 *  because `String.fromCharCode(...bytes)` blows the stack on multi-MB inputs. */
export async function fileToBase64(file: Blob): Promise<string> {
  const bytes = new Uint8Array(await file.arrayBuffer());
  let binary = "";
  const CHUNK = 0x8000;
  for (let i = 0; i < bytes.length; i += CHUNK) {
    binary += String.fromCharCode(...bytes.subarray(i, i + CHUNK));
  }
  return btoa(binary);
}
