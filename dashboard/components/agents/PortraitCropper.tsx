"use client";

/**
 * PortraitCropper — fit a picture to the square an agent is drawn in (v1.214.0).
 *
 * WHY IT EXISTS. A portrait is rendered as a circle at 14–34px on the roster,
 * the thread rail, the kanban board and the round table (`AgentFace` →
 * `rounded-full object-cover`). Before this, an upload took whatever shape the
 * file had: the daemon's `_normalize_avatar_png` calls `Image.thumbnail`, which
 * PRESERVES aspect ratio, so a 1600×900 photo was stored 512×288 and then
 * center-cropped by CSS at render time. The user never saw the crop coming and
 * could not change it — a head slightly left of centre came out as an ear.
 *
 * So the choice is the user's, always: EVERY picked image opens this, with the
 * selection pre-fitted to cover the square. Drag to pan, the slider to zoom,
 * and what is inside the square is exactly what gets stored — the same
 * geometry the app will draw, decided before the upload rather than after.
 *
 * THE MATH IS PURE AND EXPORTED. `coverScale` / `clampOffset` / `cropRect` are
 * plain functions over numbers, so the cropping rules are tested directly
 * rather than through a canvas jsdom does not implement. The component is the
 * thin part: pointer events in, `drawImage` out.
 *
 * The output is a SQUARE PNG at `OUTPUT_PX`, which is the daemon's own
 * `_AVATAR_MAX_DIM`. Producing exactly that size means the server-side
 * `thumbnail` is a no-op rather than a second, invisible resize — one place
 * decides what the picture looks like.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { Crop, ZoomIn } from "lucide-react";
import { Modal } from "@/components/Modal";
import { ErrorNote } from "@/components/ui";

/** Stored portrait edge, in pixels — the daemon's `_AVATAR_MAX_DIM`. */
export const OUTPUT_PX = 512;
/** The on-screen crop square. Display only; the export is always OUTPUT_PX. */
const VIEW_PX = 288;
/** How far in the user may zoom past "cover". */
export const MAX_ZOOM = 4;

/**
 * The scale at which the image just COVERS a square of `view` px.
 *
 * `max`, not `min`: the square must never contain a transparent gutter, so the
 * SHORTER edge is the one that has to reach the view. This is the floor for
 * every other scale — zoom is a multiplier on it, and zoom 1 is "as far out as
 * the picture can go without leaving a hole".
 */
export function coverScale(
  naturalW: number,
  naturalH: number,
  view: number = VIEW_PX,
): number {
  if (!(naturalW > 0) || !(naturalH > 0)) return 1;
  return Math.max(view / naturalW, view / naturalH);
}

/**
 * Keep the square covered while panning.
 *
 * `offset` is the displayed image's top-left corner relative to the square's
 * top-left, in view pixels, so it runs from `view - displayed` (dragged fully
 * left/up) to `0` (fully right/down). At exactly cover scale the range on the
 * matching axis is a single point, and the clamp pins it there — which is why
 * a landscape photo cannot be dragged vertically until it is zoomed in.
 */
export function clampOffset(
  offset: { x: number; y: number },
  displayedW: number,
  displayedH: number,
  view: number = VIEW_PX,
): { x: number; y: number } {
  const lo = (displayed: number) => Math.min(0, view - displayed);
  return {
    x: Math.min(0, Math.max(lo(displayedW), offset.x)),
    y: Math.min(0, Math.max(lo(displayedH), offset.y)),
  };
}

/**
 * The SOURCE rectangle to copy out of the original image.
 *
 * Everything above is in view pixels; this converts once, at the end, so the
 * export never accumulates the display size's rounding. The rect is clamped
 * into the image's own bounds: a half-pixel of float drift at maximum zoom
 * would otherwise ask `drawImage` for a strip outside the bitmap, which some
 * browsers render as a transparent edge.
 */
export function cropRect(
  naturalW: number,
  naturalH: number,
  scale: number,
  offset: { x: number; y: number },
  view: number = VIEW_PX,
): { sx: number; sy: number; sw: number; sh: number } {
  const side = view / scale;
  const sw = Math.min(side, naturalW);
  const sh = Math.min(side, naturalH);
  return {
    sx: Math.min(Math.max(0, -offset.x / scale), Math.max(0, naturalW - sw)),
    sy: Math.min(Math.max(0, -offset.y / scale), Math.max(0, naturalH - sh)),
    sw,
    sh,
  };
}

/** A data: URL's payload — what the daemon's `image_b64` field takes. */
function bareBase64(dataUrl: string): string {
  return dataUrl.slice(dataUrl.indexOf(",") + 1);
}

export function PortraitCropper({
  file,
  agentName,
  onCancel,
  onCropped,
}: {
  /** The picked file. A new file remounts this (the caller keys on it). */
  file: File;
  agentName: string;
  onCancel: () => void;
  /** Bare base64 of a square PNG, ready to POST as `image_b64`. */
  onCropped: (imageB64: string) => void;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const imgRef = useRef<HTMLImageElement | null>(null);
  const [natural, setNatural] = useState<{ w: number; h: number } | null>(null);
  const [zoom, setZoom] = useState(1);
  const [offset, setOffset] = useState({ x: 0, y: 0 });
  const [error, setError] = useState<string | null>(null);
  const dragRef = useRef<{ x: number; y: number; ox: number; oy: number } | null>(null);

  // Decode the pick. The object URL is revoked on unmount — a cropper opened
  // and cancelled a dozen times must not leak a dozen decoded bitmaps.
  useEffect(() => {
    const url = URL.createObjectURL(file);
    const img = new Image();
    img.onload = () => {
      imgRef.current = img;
      const w = img.naturalWidth || img.width;
      const h = img.naturalHeight || img.height;
      setNatural({ w, h });
      // Open CENTRED at cover: the middle of the picture is where the subject
      // usually is, and it is the crop the user would otherwise have got by
      // accident — so the pre-fit is the old behaviour, now visible and movable.
      const s = coverScale(w, h);
      setOffset({ x: (VIEW_PX - w * s) / 2, y: (VIEW_PX - h * s) / 2 });
    };
    img.onerror = () =>
      setError("that file could not be decoded as an image — try a PNG, JPEG or WebP");
    img.src = url;
    return () => {
      URL.revokeObjectURL(url);
      imgRef.current = null;
    };
  }, [file]);

  const scale = natural ? coverScale(natural.w, natural.h) * zoom : 1;
  const dispW = natural ? natural.w * scale : 0;
  const dispH = natural ? natural.h * scale : 0;

  // Re-clamp whenever the geometry changes. Zooming OUT shrinks the displayed
  // image under an offset that was legal at the old scale, which would open a
  // gutter along one edge; this pulls it back in the same frame.
  useEffect(() => {
    if (!natural) return;
    setOffset((prev) => clampOffset(prev, dispW, dispH));
  }, [natural, dispW, dispH]);

  const paint = useCallback(() => {
    const canvas = canvasRef.current;
    const img = imgRef.current;
    if (!canvas || !img || !natural) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return; // jsdom, or a context-starved browser — the buttons still work
    ctx.clearRect(0, 0, VIEW_PX, VIEW_PX);
    ctx.drawImage(img, offset.x, offset.y, dispW, dispH);
  }, [natural, offset, dispW, dispH]);

  useEffect(() => {
    paint();
  }, [paint]);

  function onPointerDown(e: React.PointerEvent<HTMLCanvasElement>) {
    if (!natural) return;
    dragRef.current = { x: e.clientX, y: e.clientY, ox: offset.x, oy: offset.y };
    e.currentTarget.setPointerCapture(e.pointerId);
  }

  function onPointerMove(e: React.PointerEvent<HTMLCanvasElement>) {
    const d = dragRef.current;
    if (!d) return;
    setOffset(
      clampOffset(
        { x: d.ox + (e.clientX - d.x), y: d.oy + (e.clientY - d.y) },
        dispW,
        dispH,
      ),
    );
  }

  function endDrag(e: React.PointerEvent<HTMLCanvasElement>) {
    dragRef.current = null;
    try {
      e.currentTarget.releasePointerCapture(e.pointerId);
    } catch {
      /* the pointer was never captured (a synthetic event in a test) */
    }
  }

  /** Export the square. Nothing is uploaded from here — the caller owns that,
   *  so this dialog stays a pure "what shape is it" question. */
  function useThis() {
    const img = imgRef.current;
    if (!img || !natural) return;
    const out = document.createElement("canvas");
    out.width = OUTPUT_PX;
    out.height = OUTPUT_PX;
    const ctx = out.getContext("2d");
    if (!ctx) {
      setError("this browser could not render the crop — try a square image instead");
      return;
    }
    const { sx, sy, sw, sh } = cropRect(natural.w, natural.h, scale, offset);
    ctx.drawImage(img, sx, sy, sw, sh, 0, 0, OUTPUT_PX, OUTPUT_PX);
    try {
      onCropped(bareBase64(out.toDataURL("image/png")));
    } catch (err) {
      setError(String(err));
    }
  }

  return (
    <Modal
      label={`Fit ${agentName}'s portrait`}
      onClose={onCancel}
      className="w-full max-w-md"
      testId="portrait-cropper"
    >
      <header className="flex shrink-0 items-center gap-2 border-b hairline px-4 py-3">
        <Crop size={15} className="text-accent-soft/80" aria-hidden />
        <h2 className="text-[13px] font-semibold tracking-wide text-zinc-200">
          Fit the portrait
        </h2>
        <span className="ml-auto truncate text-[11px] text-zinc-500">{agentName}</span>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto p-4">
        <p className="mb-3 text-[11.5px] leading-relaxed text-zinc-500">
          Drag the picture and zoom until what you want is inside the circle —
          that is exactly what gets stored, at {OUTPUT_PX}×{OUTPUT_PX}.
        </p>
        <div className="flex justify-center">
          {/* The square IS the crop. The circular mask over it shows the shape
              the app actually draws (every portrait renders `rounded-full`),
              so the corners the roster will never show are visibly dimmed
              rather than silently discarded. */}
          <div
            className="relative overflow-hidden rounded-xl border border-white/10 bg-ink-900"
            style={{ width: VIEW_PX, height: VIEW_PX }}
          >
            <canvas
              ref={canvasRef}
              width={VIEW_PX}
              height={VIEW_PX}
              data-testid="cropper-canvas"
              aria-label={`Drag to position ${agentName}'s portrait`}
              onPointerDown={onPointerDown}
              onPointerMove={onPointerMove}
              onPointerUp={endDrag}
              onPointerCancel={endDrag}
              className="block cursor-grab touch-none active:cursor-grabbing"
            />
            <div
              aria-hidden
              className="pointer-events-none absolute inset-0 rounded-xl"
              style={{
                // The whole box is darkened, then MASKED back to transparent
                // inside the circle — so the dimming is exactly the area the
                // app will never draw. (A clip-path "donut" needs a
                // self-intersecting polygon and renders differently across
                // engines; a mask is one declaration and degrades to no
                // dimming at all where it is unsupported.)
                boxShadow: `inset 0 0 0 ${VIEW_PX}px rgb(0 0 0 / 0.45)`,
                WebkitMaskImage:
                  "radial-gradient(circle at 50% 50%, transparent 49.5%, black 50%)",
                maskImage:
                  "radial-gradient(circle at 50% 50%, transparent 49.5%, black 50%)",
              }}
            />
          </div>
        </div>

        <div className="mt-4 flex items-center gap-3">
          <ZoomIn size={14} className="shrink-0 text-zinc-500" aria-hidden />
          <label className="sr-only" htmlFor="portrait-zoom">
            Zoom {agentName}&rsquo;s portrait
          </label>
          <input
            id="portrait-zoom"
            type="range"
            min={1}
            max={MAX_ZOOM}
            step={0.01}
            value={zoom}
            disabled={!natural}
            onChange={(e) => setZoom(Number(e.target.value))}
            className="h-1 min-w-0 flex-1 accent-[rgb(var(--accent-rgb))]"
          />
          <span className="w-10 shrink-0 text-right text-[11px] tabular-nums text-zinc-500">
            {zoom.toFixed(1)}×
          </span>
        </div>
        {natural && (
          <p className="mt-2 text-[10.5px] tabular-nums text-zinc-600">
            {natural.w}×{natural.h}
            {natural.w === natural.h ? " · already square" : " · will be cropped square"}
          </p>
        )}
        {error && (
          <div className="mt-3">
            <ErrorNote>{error}</ErrorNote>
          </div>
        )}
      </div>

      <footer className="flex shrink-0 items-center justify-end gap-2 border-t hairline px-4 py-3">
        <button type="button" onClick={onCancel} className="btn-ghost py-1.5 text-xs">
          Cancel
        </button>
        <button
          type="button"
          onClick={useThis}
          disabled={!natural}
          className="btn-accent py-1.5 text-xs"
        >
          Use this
        </button>
      </footer>
    </Modal>
  );
}

export default PortraitCropper;
