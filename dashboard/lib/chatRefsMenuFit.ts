"use client";

/**
 * Where the composer's "@" menu opens, and how tall it may be (calm chat
 * W3-1, v1.328.0).
 *
 * The menu hangs off the composer card. In an open chat the card sits at the
 * bottom of the screen and the menu opens upward with room to spare. On a NEW
 * chat the card is centred (wave 1), and the chat section that clips the menu
 * starts only a little way above it: once the menu grew a Chats section it
 * ran past that edge, and its first rows (the agents) could not be seen or
 * clicked. So the menu measures the room it really has: it opens upward when
 * there is enough room there, otherwise on the side with more room, and its
 * height is held to that room so every row is reachable by scrolling inside
 * the menu. A squeezed menu would still fill with agents before the Chats
 * section began, so then the sections above the chats are cut to their first
 * rows (`squeezedLeadRows`) and say how many more there are.
 *
 * Its own module (not in the page) so the arithmetic can be tested on its own.
 */

import { useLayoutEffect, useState, type RefObject } from "react";

/** The tallest the menu gets anywhere: 28rem. */
export const AT_MENU_MAX_PX = 448;
/** ... and never more than this share of the window. */
export const AT_MENU_MAX_VH = 0.6;
/** The gap between the card and the menu (mb-2 / mt-2). */
export const AT_MENU_GAP_PX = 8;
/** Room kept free between the menu and the clipping edge. */
export const AT_MENU_EDGE_PX = 8;
/** Upward is preferred while it gives at least this much room. */
export const AT_MENU_COMFORT_PX = 240;
/** At or above this height each section keeps its own short scroll; below
 *  it the menu scrolls as one list (no scroll inside a scroll). */
export const AT_MENU_ROOMY_PX = 400;
/** Never squeeze the menu below this; a row or two must always show. */
export const AT_MENU_MIN_PX = 96;
/** When the menu is squeezed and saved chats match, the sections above the
 *  chats (apps, agents) show only their first rows plus a quiet "N more" line,
 *  so the Chats heading and its first rows are in view when the menu opens.
 *  Three rows while the menu is at least this tall, else one. */
export const AT_MENU_LEAD_TALL_PX = 260;
export const AT_MENU_LEAD_ROWS = 3;

/** How many rows each section above the chats keeps in a squeezed menu. */
export function squeezedLeadRows(maxHeight: number): number {
  return maxHeight >= AT_MENU_LEAD_TALL_PX ? AT_MENU_LEAD_ROWS : 1;
}

export type AtMenuSide = "above" | "below";
export interface AtMenuFit {
  side: AtMenuSide;
  /** The menu's max height in px. */
  maxHeight: number;
}

/**
 * The pure decision. Every number is a viewport coordinate:
 * the card's top and bottom, the clipping box's top and bottom, the window
 * height. Returns null when the card has no size (not laid out yet, or a test
 * environment), so the menu keeps its CSS defaults.
 */
export function fitAtMenu(m: {
  cardTop: number;
  cardBottom: number;
  clipTop: number;
  clipBottom: number;
  viewportHeight: number;
}): AtMenuFit | null {
  if (!(m.cardBottom > m.cardTop) || !(m.viewportHeight > 0)) return null;
  const top = Math.max(m.clipTop, 0);
  const bottom = Math.min(m.clipBottom, m.viewportHeight);
  const cap = Math.min(AT_MENU_MAX_PX, Math.floor(m.viewportHeight * AT_MENU_MAX_VH));
  const above = Math.floor(m.cardTop - AT_MENU_GAP_PX - AT_MENU_EDGE_PX - top);
  const below = Math.floor(bottom - m.cardBottom - AT_MENU_GAP_PX - AT_MENU_EDGE_PX);
  const side: AtMenuSide =
    above >= Math.min(cap, AT_MENU_COMFORT_PX) || above >= below ? "above" : "below";
  const room = side === "above" ? above : below;
  return { side, maxHeight: Math.max(AT_MENU_MIN_PX, Math.min(cap, room)) };
}

/** The nearest ancestor that clips its overflow (the chat section). */
function clippingAncestor(el: HTMLElement): HTMLElement | null {
  let cur = el.parentElement;
  while (cur && cur !== document.body) {
    const oy = getComputedStyle(cur).overflowY;
    if (oy === "auto" || oy === "scroll" || oy === "hidden" || oy === "clip") return cur;
    cur = cur.parentElement;
  }
  return null;
}

function sameFit(a: AtMenuFit | null, b: AtMenuFit | null): boolean {
  return a === b || (!!a && !!b && a.side === b.side && a.maxHeight === b.maxHeight);
}

/**
 * Measure the menu's card (its parent) against the clipping ancestor while
 * the menu is open, before paint, and again when the window resizes or
 * anything scrolls. `key` changes when the menu's content changes size.
 */
export function useAtMenuFit(
  menuRef: RefObject<HTMLElement | null>,
  open: boolean,
  key: string,
): AtMenuFit | null {
  const [fit, setFit] = useState<AtMenuFit | null>(null);
  useLayoutEffect(() => {
    if (!open) {
      setFit((prev) => (prev === null ? prev : null));
      return;
    }
    const measure = () => {
      const menu = menuRef.current;
      const card = menu?.parentElement;
      if (!menu || !card) return;
      const c = card.getBoundingClientRect();
      const clip = clippingAncestor(card)?.getBoundingClientRect();
      const vh = window.innerHeight || document.documentElement.clientHeight || 0;
      const next = fitAtMenu({
        cardTop: c.top,
        cardBottom: c.bottom,
        clipTop: clip ? clip.top : 0,
        clipBottom: clip ? clip.bottom : vh,
        viewportHeight: vh,
      });
      setFit((prev) => (sameFit(prev, next) ? prev : next));
    };
    measure();
    window.addEventListener("resize", measure);
    window.addEventListener("scroll", measure, true);
    return () => {
      window.removeEventListener("resize", measure);
      window.removeEventListener("scroll", measure, true);
    };
  }, [menuRef, open, key]);
  return fit;
}
