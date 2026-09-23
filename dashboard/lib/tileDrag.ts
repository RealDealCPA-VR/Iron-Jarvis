/**
 * Overview tile drag geometry (v1.289.0) — pure, so it is pinned without a DOM.
 *
 * Two rules, in tension, which is why both are here:
 *
 * * **A tile never leaves the screen.** dnd-kit's sortable transform follows
 *   the pointer with no limit, so a tile could be dragged clean off the window
 *   (and the auto-scroller chased it), which wrecked the Overview until a
 *   reload. `clampToWindow` keeps the dragged tile's rectangle inside the
 *   viewport whatever the pointer does.
 * * **Trying to leave IS a gesture.** Pushing a tile past the edge is the most
 *   natural way to say "put this somewhere else" — so in the desktop app the
 *   drop opens that module in its own window (the v1.283.0 pop-out) instead of
 *   rearranging. `offscreenEdge` reads that intent from the UNCLAMPED
 *   translate: more than `fraction` of the tile beyond an edge.
 */

export interface DragRect {
  left: number;
  top: number;
  width: number;
  height: number;
}

export interface Viewport {
  width: number;
  height: number;
}

export interface Translate {
  x: number;
  y: number;
}

export type Edge = "left" | "right" | "top" | "bottom";

/** The translate that keeps `rect` fully inside `win` (a tile wider than the
 *  window pins to its left/top edge rather than oscillating). */
export function clampToWindow(t: Translate, rect: DragRect, win: Viewport): Translate {
  const minX = -rect.left;
  const maxX = Math.max(minX, win.width - rect.width - rect.left);
  const minY = -rect.top;
  const maxY = Math.max(minY, win.height - rect.height - rect.top);
  return {
    x: Math.min(Math.max(t.x, minX), maxX),
    y: Math.min(Math.max(t.y, minY), maxY),
  };
}

/**
 * Which edge the tile is being pushed past, or null. Judged on the RAW
 * translate (before clamping): the tile counts as leaving when more than
 * `fraction` of its width/height would sit outside the viewport. The larger
 * overshoot wins when two edges qualify (a corner).
 */
export function offscreenEdge(
  t: Translate,
  rect: DragRect,
  win: Viewport,
  fraction = 0.5,
): Edge | null {
  const left = rect.left + t.x;
  const top = rect.top + t.y;
  const over: Array<[Edge, number]> = [
    ["left", -left - rect.width * fraction],
    ["right", left + rect.width - win.width - rect.width * fraction],
    ["top", -top - rect.height * fraction],
    ["bottom", top + rect.height - win.height - rect.height * fraction],
  ];
  let best: [Edge, number] | null = null;
  for (const o of over) {
    if (o[1] > 0 && (best === null || o[1] > best[1])) best = o;
  }
  return best ? best[0] : null;
}
