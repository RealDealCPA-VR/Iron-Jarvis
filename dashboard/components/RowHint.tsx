"use client";

/**
 * A menu row's MEANING, shown beside the menu after a pause (v1.432.0; first
 * built for the chat's approvals menu in v1.430.0, lifted here so Build ›
 * Code's mode menu behaves the same way).
 *
 * The user: "the descriptions are too verbose … just the titles … a popup of
 * what it means if the user waves the mouse over it for long enough". So a
 * menu lists names only, and a row's meaning shows in a small box BESIDE the
 * menu once the pointer rests on the row for `ROW_HINT_DELAY_MS` (or the
 * keyboard rests on it; on a touch screen, once a finger HOLDS the row that
 * long — and that lift then picks nothing). Once one meaning is showing, the
 * next row's shows at once. Leaving or closing the menu hides it.
 *
 * Two pieces:
 *  - `useRowHint(open)` holds which row's meaning shows and the timers; it
 *    hands each row its pointer handlers (`rowProps`) and tells a row's click
 *    whether it is the lift after a hold (`heldClick`).
 *  - `RowHintBox` draws the box on the page's top layer (a portal, fixed to
 *    the window), placed by `rowHintPlacement` from the menu's and the row's
 *    real boxes and its own measured size, before paint.
 *
 * The box is for the eye only (aria-hidden, pointer-events-none): each row
 * keeps its meaning as its accessible description (`aria-describedby`), which
 * the menus draw themselves.
 */

import { useEffect, useLayoutEffect, useRef, useState, type MouseEvent, type PointerEvent } from "react";
import { createPortal } from "react-dom";

/** How long the pointer (or the keyboard, or a finger) rests on a row before
 *  its meaning shows. */
export const ROW_HINT_DELAY_MS = 500;
/** The box's width before it is measured (w-64). */
export const ROW_HINT_W = 256;
/** Breathing room kept between the box and the window's edge. */
const EDGE = 8;
/** The box's `max-w-[calc(100vw-2rem)]`: it never gets wider than this. */
const GUTTER = 32;
/** The gap between the menu (or its row) and the box. */
const GAP = 6;

/** Where a row's meaning box goes (fixed to the window, in window px). */
export interface HintPlacement {
  /** Beside the menu (right first, then left), or on a narrow window under
   *  the row (over the rows below it), or over it when under has no room. */
  side: "right" | "left" | "under";
  left: number;
  top: number;
}

type Box = { top: number; bottom: number; left: number; right: number };

/**
 * The box sits BESIDE the menu, level with its row: right of the menu when
 * that fits, else left of it. A phone has no room beside a menu that already
 * fills its width, so the box then hangs just under the row (covering the rows
 * below it while it shows), or just over the row when there is no room under
 * it. It always stays at least 8px inside the window.
 */
export function rowHintPlacement(
  menu: Box,
  row: Box,
  hint: { width: number; height: number },
  viewport: { width: number; height: number },
): HintPlacement {
  const width = Math.min(hint.width, Math.max(0, viewport.width - GUTTER));
  const level = Math.round(Math.max(EDGE, Math.min(row.top, viewport.height - hint.height - EDGE)));
  if (menu.right + GAP + width <= viewport.width - EDGE)
    return { side: "right", left: Math.round(menu.right + GAP), top: level };
  if (menu.left - GAP - width >= EDGE) return { side: "left", left: Math.round(menu.left - GAP - width), top: level };
  const left = Math.max(EDGE, Math.min(row.left, viewport.width - width - EDGE));
  const under = row.bottom + GAP;
  const top = under + hint.height <= viewport.height - EDGE ? under : Math.max(EDGE, row.top - GAP - hint.height);
  return { side: "under", left: Math.round(left), top: Math.round(top) };
}

function sameHint(a: HintPlacement, b: HintPlacement): boolean {
  return a.side === b.side && a.left === b.left && a.top === b.top;
}

function boxOf(el: Element): Box {
  const r = el.getBoundingClientRect();
  return { top: r.top, bottom: r.bottom, left: r.left, right: r.right };
}

/** The window the fixed box is placed in: the layout viewport (no scrollbars). */
function layoutViewport(): { width: number; height: number } {
  const de = document.documentElement;
  return { width: de.clientWidth || window.innerWidth, height: de.clientHeight || window.innerHeight };
}

/** What `useRowHint` hands a row: its pointer handlers. */
export interface RowHintProps {
  onMouseEnter: () => void;
  onPointerDown: (e: PointerEvent) => void;
  onPointerUp: (e: PointerEvent) => void;
  onPointerCancel: (e: PointerEvent) => void;
  onContextMenu: (e: MouseEvent) => void;
}

/**
 * Which row's meaning shows (`shown`, null = none), for a menu that is
 * `open`. Keys are the caller's own row keys.
 */
export function useRowHint<K extends string>(open: boolean) {
  const [shown, setShown] = useState<K | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const shownRef = useRef<K | null>(null);
  shownRef.current = shown;
  /** A finger held a row until its meaning showed: lifting it picks nothing. */
  const heldRef = useRef(false);
  /** The last press on a row was a finger, so the browser's own long-press
   *  menu is kept off the row. */
  const touchRef = useRef(false);

  function clear() {
    if (timer.current !== null) {
      clearTimeout(timer.current);
      timer.current = null;
    }
  }

  /** Row k's meaning after the pause, or at once when one is already showing
   *  (moving down the list then reads each row's meaning without waiting). */
  function afterPause(k: K) {
    clear();
    if (shownRef.current !== null) {
      setShown(k);
      return;
    }
    timer.current = setTimeout(() => {
      timer.current = null;
      setShown(k);
    }, ROW_HINT_DELAY_MS);
  }

  function hide() {
    clear();
    setShown(null);
  }

  // The meaning goes with the menu (a reopened menu starts with none), and no
  // timer outlives the component.
  useEffect(() => {
    if (!open) hide();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);
  useEffect(() => () => clear(), []);

  /** Row k's pointer handlers. `onActive` is told when the pointer (or a
   *  finger's hold) makes the row the active one. */
  function rowProps(k: K, onActive?: () => void): RowHintProps {
    return {
      onMouseEnter: () => {
        onActive?.();
        afterPause(k);
      },
      onPointerDown: (e) => {
        heldRef.current = false;
        touchRef.current = e.pointerType === "touch";
        if (!touchRef.current) return;
        // A finger has no hover: holding the row shows its meaning.
        clear();
        timer.current = setTimeout(() => {
          timer.current = null;
          heldRef.current = true;
          onActive?.();
          setShown(k);
        }, ROW_HINT_DELAY_MS);
      },
      onPointerUp: (e) => {
        if (e.pointerType === "touch" && !heldRef.current) clear();
      },
      onPointerCancel: (e) => {
        if (e.pointerType === "touch" && !heldRef.current) clear();
      },
      onContextMenu: (e) => {
        if (touchRef.current) e.preventDefault();
      },
    };
  }

  /** For a row's click: true when it is the lift after a hold that showed the
   *  meaning, which picks nothing (and is used up). */
  function heldClick(): boolean {
    if (!heldRef.current) return false;
    heldRef.current = false;
    return true;
  }

  return { shown, afterPause, hide, rowProps, heldClick };
}

/**
 * The meaning box: fixed to the window on the page's top layer, rendered
 * hidden until it has been measured and placed (a layout effect, before
 * paint), and placed again whenever `watch` changes (the menu moved, or the
 * rows' words changed).
 */
export function RowHintBox({
  text,
  mode,
  menuRef,
  rowId,
  watch = "",
  testId,
}: {
  text: string;
  /** The row's key, as `data-mode` (tests and styles read it). */
  mode: string;
  menuRef: { current: HTMLElement | null };
  /** The row element's id. */
  rowId: string;
  /** Anything whose change should place the box again. */
  watch?: string;
  testId: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [at, setAt] = useState<HintPlacement | null>(null);

  useLayoutEffect(() => {
    const menuEl = menuRef.current;
    const el = ref.current;
    const row = typeof document !== "undefined" ? document.getElementById(rowId) : null;
    if (!menuEl || !el || !row) return;
    const drawn = el.getBoundingClientRect();
    const size = { width: drawn.width > 0 ? drawn.width : ROW_HINT_W, height: Math.max(0, drawn.height) };
    const next = rowHintPlacement(boxOf(menuEl), boxOf(row), size, layoutViewport());
    setAt((prev) => (prev && sameHint(prev, next) ? prev : next));
  }, [menuRef, rowId, text, watch]);

  if (typeof document === "undefined") return null;
  return createPortal(
    <div
      ref={ref}
      aria-hidden="true"
      data-testid={testId}
      data-mode={mode}
      data-side={at?.side}
      style={{
        position: "fixed",
        left: at?.left ?? 0,
        top: at?.top ?? 0,
        ...(at ? {} : { visibility: "hidden" as const }),
      }}
      className="pointer-events-none z-[51] w-64 max-w-[calc(100vw-2rem)] rounded-lg border border-white/10 bg-ink-900/95 px-3 py-2 text-meta leading-snug text-zinc-300 shadow-xl shadow-black/30 backdrop-blur-xl"
    >
      {text}
    </div>,
    document.body,
  );
}
