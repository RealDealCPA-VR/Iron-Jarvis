"use client";

/**
 * v1.350.1: a side panel that stays inside the window.
 *
 * The composer's model menu opens ABOVE the composer, and each provider's
 * models open in a panel beside its row, anchored to the row's bottom so a
 * long catalog grows upward. A provider near the TOP of the menu (the user's
 * RTX6000ada endpoint) grew its panel past the window's top edge: the first
 * models were cut off and could not be scrolled to.
 *
 * On open (and on resize) this measures the anchor row: with room above, it
 * grows upward, its height capped at the room there; with too little room
 * above and more below, it opens downward from the row's top instead. The
 * list scrolls inside either way. Pure layout: no new look.
 *
 * `float` (the model menu fits the window, 2026-10-24): the menu's rows now
 * scroll inside the menu, and a scrolling box clips anything that hangs out
 * of it — this panel hangs out to the menu's left. A floating panel is
 * `position: fixed` at its row instead (beside it on the left, as before; when
 * the window has no room there, shifted to stay inside it), follows the row
 * when the list scrolls, and keeps below the app's title bar. Under
 * `inlineQuery` (the IJ harness's phone sheet) it stays in the flow, under
 * its row, exactly as before.
 */

import { useLayoutEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { appTopLimit, menuViewport } from "@/lib/modelMenuPlacement";

/** Below this much room above the row, opening downward is tried. */
export const MIN_ROOM_PX = 160;
/** Space kept between the panel and the window's edge. */
export const EDGE_PX = 8;
/** The gap between a floating panel and its row (the panel's `mr-1`). */
export const FLOAT_GAP_PX = 4;

export interface FitPlacement {
  /** "up" = bottom-anchored to the row (grows up); "down" = top-anchored. */
  side: "up" | "down";
  maxHeight: number;
}

/** Where the panel goes for an anchor row at `rowTop`..`rowBottom` in a
 *  window `viewportH` tall, with at most `cap` pixels of height. `topLimit`:
 *  where the room above begins (the app's title bar; 0 = the window's top).
 *  Pure. */
export function fitPlacement(
  rowTop: number,
  rowBottom: number,
  viewportH: number,
  cap: number,
  topLimit = 0,
): FitPlacement {
  const above = Math.floor(rowBottom - Math.max(0, topLimit) - EDGE_PX);
  const below = Math.floor(viewportH - rowTop - EDGE_PX);
  if (above >= Math.min(cap, MIN_ROOM_PX) || above >= below) {
    return { side: "up", maxHeight: Math.max(0, Math.min(cap, above)) };
  }
  return { side: "down", maxHeight: Math.max(0, Math.min(cap, below)) };
}

/** A floating panel's fixed edges for a row at `row`, in a window `vw` × `vh`,
 *  `width` wide. Beside the row on its left (its right edge `FLOAT_GAP_PX`
 *  left of the row, which the panel's `mr-1` makes); shifted to stay inside
 *  the window when the left has no room for it. Pure. */
export function floatEdges(
  row: { top: number; bottom: number; left: number },
  place: FitPlacement,
  vw: number,
  vh: number,
  width: number,
): { top?: number; bottom?: number; left?: number; right?: number; align: "beside" | "shifted" } {
  const vertical = place.side === "up" ? { bottom: Math.round(vh - row.bottom) } : { top: Math.round(row.top) };
  if (row.left - FLOAT_GAP_PX - width >= EDGE_PX) {
    return { ...vertical, right: Math.round(vw - row.left), align: "beside" };
  }
  const left = Math.max(EDGE_PX, Math.min(row.left - FLOAT_GAP_PX - width, vw - EDGE_PX - width));
  return { ...vertical, left: Math.round(left), align: "shifted" };
}

export function FitPanel({
  className,
  cap = 384,
  children,
  testId,
  float = false,
  inlineQuery,
}: {
  /** Everything but the vertical anchor (no bottom-0/top-0, no max-h). */
  className: string;
  cap?: number;
  children: ReactNode;
  testId?: string;
  /** Fixed to the window at its row (a panel inside a scrolling list). */
  float?: boolean;
  /** A media query under which a floating panel stays in the flow instead. */
  inlineQuery?: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [place, setPlace] = useState<FitPlacement | null>(null);
  const [edges, setEdges] = useState<ReturnType<typeof floatEdges> | null>(null);

  useLayoutEffect(() => {
    const el = ref.current;
    const anchor = el?.parentElement;
    if (!el || !anchor || typeof window === "undefined") return;
    const fit = () => {
      const r = anchor.getBoundingClientRect();
      const inline = !!inlineQuery && !!window.matchMedia?.(inlineQuery)?.matches;
      if (!float || inline) {
        setPlace(fitPlacement(r.top, r.bottom, window.innerHeight, cap));
        setEdges(null);
        return;
      }
      const vp = menuViewport();
      const next = fitPlacement(r.top, r.bottom, vp.height, cap, appTopLimit());
      setPlace(next);
      setEdges(floatEdges(r, next, vp.width, vp.height, el.offsetWidth || 224));
    };
    fit();
    // A floating panel follows its row when anything scrolls (the menu's
    // list, the page) — but not when its own list does.
    const onScroll = (e: Event) => {
      if (e.target instanceof Node && el.contains(e.target)) return;
      fit();
    };
    window.addEventListener("resize", fit);
    if (float) window.addEventListener("scroll", onScroll, true);
    return () => {
      window.removeEventListener("resize", fit);
      if (float) window.removeEventListener("scroll", onScroll, true);
    };
  }, [cap, float, inlineQuery]);

  const side = place?.side ?? "up";
  const style: CSSProperties = { maxHeight: place ? place.maxHeight : cap };
  if (edges) {
    style.position = "fixed";
    if (edges.top !== undefined) style.top = edges.top;
    if (edges.bottom !== undefined) style.bottom = edges.bottom;
    if (edges.right !== undefined) style.right = edges.right;
    if (edges.left !== undefined) {
      style.left = edges.left;
      style.right = "auto";
    }
  }
  return (
    <div
      ref={ref}
      data-testid={testId}
      data-side={side}
      data-align={edges?.align}
      className={`${className} ${side === "up" ? "bottom-0" : "top-0"} overflow-y-auto`}
      style={style}
    >
      {children}
    </div>
  );
}
