/**
 * Where the composer's MODEL MENU goes so it stays inside the window
 * (components/chat/ModelMenu; the chat page's composer and the IJ harness's).
 *
 * The user's report (2026-10-24): "in chat the module, the model selection is
 * cut off". On a new chat the composer sits in the middle of the window; the
 * menu hung ABOVE the chip at its full height (13 providers, Recent, the
 * filter box, "Make this my default" — taller than the room above), and its
 * top — the "Type to find a model" box and Recent — was cut off: past the
 * window's top, and clipped by the chat's own section (a scroll container)
 * before that. Nothing could scroll to it.
 *
 * The menu is now drawn on the page's top layer (a portal, `position: fixed`
 * at the chip, as the permission chip's menu is) and this decides its box:
 * above the chip when the whole menu fits there (as before), else on the
 * side with more room; its height capped to that room, so the rows scroll
 * inside it under a filter box that stays put. The top of the room is the
 * app's title bar, not the window's edge: in the desktop app that strip is
 * the window's drag region (a row drawn over it would move the window
 * instead of taking the click) and holds the native window buttons.
 *
 * Pure maths, plus the two DOM reads it needs (the title bar's bottom and the
 * layout viewport), so the rule is tested without a browser.
 */

/** Space kept between the menu and the window's (or title bar's) edge. */
export const MENU_EDGE_PX = 8;
/** The gap between the chip and the menu (the old `mb-1.5`). */
export const MENU_GAP_PX = 6;
/** The menu's width before it is measured (`w-60`). */
export const MENU_WIDTH_PX = 240;

export interface MenuAnchor {
  top: number;
  bottom: number;
  left: number;
  right: number;
}

export interface ModelMenuPlacement {
  /** Above the chip (the composer sits low) or below it. */
  side: "above" | "below";
  /** The most the menu may be tall there: its rows scroll past this. */
  maxHeight: number;
  /** Below: the menu's top edge, px from the window's top. */
  top?: number;
  /** Above: the menu's bottom edge, px from the window's BOTTOM (so the
   *  menu's own height never moves it off the chip). */
  bottom?: number;
  /** The menu's left edge, px from the window's left. */
  left: number;
}

/**
 * The menu's box for a chip at `anchor` in a window `viewport` big, a menu
 * `menu.height` tall with everything in it (its natural height) and
 * `menu.width` wide, with the room above ending at `topLimit` (the title
 * bar's bottom; 0 = the window's top).
 *
 * - Above when the whole menu fits there, or when there is at least as much
 *   room above as below; otherwise below. `keepSide` keeps a side already
 *   chosen (a scroll that moves the chip must not flip the menu over it).
 * - Its height is capped to the room on that side, never negative.
 * - Right-aligned to the chip (the menu has always hung from the chip's right
 *   edge), shifted so both edges stay inside the window.
 */
export function modelMenuPlacement(
  anchor: MenuAnchor,
  viewport: { width: number; height: number },
  menu: { width: number; height: number },
  opts: { topLimit?: number; keepSide?: "above" | "below" } = {},
): ModelMenuPlacement {
  const top = Math.max(0, opts.topLimit ?? 0);
  const roomAbove = Math.floor(anchor.top - MENU_GAP_PX - top - MENU_EDGE_PX);
  const roomBelow = Math.floor(viewport.height - anchor.bottom - MENU_GAP_PX - MENU_EDGE_PX);
  const side =
    opts.keepSide ?? (roomAbove >= menu.height || roomAbove >= roomBelow ? "above" : "below");
  const room = side === "above" ? roomAbove : roomBelow;
  const width = Math.max(0, Math.min(menu.width, viewport.width - 2 * MENU_EDGE_PX));
  const maxLeft = viewport.width - MENU_EDGE_PX - width;
  const left = Math.round(Math.max(MENU_EDGE_PX, Math.min(anchor.right - width, maxLeft)));
  const place: ModelMenuPlacement = { side, maxHeight: Math.max(0, room), left };
  if (side === "above") place.bottom = Math.round(viewport.height - anchor.top + MENU_GAP_PX);
  else place.top = Math.round(anchor.bottom + MENU_GAP_PX);
  return place;
}

export function samePlacement(a: ModelMenuPlacement | null, b: ModelMenuPlacement | null): boolean {
  if (!a || !b) return a === b;
  return (
    a.side === b.side && a.maxHeight === b.maxHeight && a.top === b.top && a.bottom === b.bottom && a.left === b.left
  );
}

/** The window a `position: fixed` box is placed in: the layout viewport
 *  (without scroll bars). jsdom reports 0 for it: the window's size then. */
export function menuViewport(): { width: number; height: number } {
  if (typeof window === "undefined") return { width: 0, height: 0 };
  const de = document.documentElement;
  return { width: de.clientWidth || window.innerWidth, height: de.clientHeight || window.innerHeight };
}

/** The bottom of the app's title bar (`data-ij-titlebar`, components/TitleBar),
 *  where the room for a menu begins; 0 when there is none on the page. */
export function appTopLimit(): number {
  if (typeof document === "undefined") return 0;
  const bar = document.querySelector("[data-ij-titlebar]");
  if (!bar) return 0;
  const r = bar.getBoundingClientRect();
  return r.height > 0 ? Math.max(0, r.bottom) : 0;
}
