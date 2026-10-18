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
 */

import { useLayoutEffect, useRef, useState, type ReactNode } from "react";

/** Below this much room above the row, opening downward is tried. */
export const MIN_ROOM_PX = 160;
/** Space kept between the panel and the window's edge. */
export const EDGE_PX = 8;

export interface FitPlacement {
  /** "up" = bottom-anchored to the row (grows up); "down" = top-anchored. */
  side: "up" | "down";
  maxHeight: number;
}

/** Where the panel goes for an anchor row at `rowTop`..`rowBottom` in a
 *  window `viewportH` tall, with at most `cap` pixels of height. Pure. */
export function fitPlacement(rowTop: number, rowBottom: number, viewportH: number, cap: number): FitPlacement {
  const above = Math.floor(rowBottom - EDGE_PX);
  const below = Math.floor(viewportH - rowTop - EDGE_PX);
  if (above >= Math.min(cap, MIN_ROOM_PX) || above >= below) {
    return { side: "up", maxHeight: Math.max(0, Math.min(cap, above)) };
  }
  return { side: "down", maxHeight: Math.max(0, Math.min(cap, below)) };
}

export function FitPanel({
  className,
  cap = 384,
  children,
  testId,
}: {
  /** Everything but the vertical anchor (no bottom-0/top-0, no max-h). */
  className: string;
  cap?: number;
  children: ReactNode;
  testId?: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [place, setPlace] = useState<FitPlacement | null>(null);

  useLayoutEffect(() => {
    const el = ref.current;
    const anchor = el?.parentElement;
    if (!el || !anchor || typeof window === "undefined") return;
    const fit = () => {
      const r = anchor.getBoundingClientRect();
      setPlace(fitPlacement(r.top, r.bottom, window.innerHeight, cap));
    };
    fit();
    window.addEventListener("resize", fit);
    return () => window.removeEventListener("resize", fit);
  }, [cap]);

  const side = place?.side ?? "up";
  return (
    <div
      ref={ref}
      data-testid={testId}
      data-side={side}
      className={`${className} ${side === "up" ? "bottom-0" : "top-0"} overflow-y-auto`}
      style={{ maxHeight: place ? place.maxHeight : cap }}
    >
      {children}
    </div>
  );
}
