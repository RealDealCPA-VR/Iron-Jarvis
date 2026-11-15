"use client";

/**
 * v1.500.2 (the owner, on the right-hand panel of Chat › Code and Build ›
 * Code: it "should be draggable so when the left is hidden the user can focus
 * on the task at hand easier"): the panel's LEFT EDGE is a drag handle.
 *
 * - Dragging left widens the panel, right narrows it. While dragging, the
 *   panel's own inline `width` follows the pointer (the view does not redraw
 *   on every move); letting go saves the width and the view draws it through
 *   `--side-w` (`PANE_WIDTH_CLASS`, from `xl`, where the panel sits beside
 *   the conversation; below `xl` it is stacked and the edge is hidden).
 * - The arrow keys move it by `PANE_STEP`; a double-click goes back to the
 *   view's standard width (`null`).
 * - The width is kept between `PANE_MIN` and `PANE_MAX`, and never leaves the
 *   conversation less than `MAIN_MIN`. It is remembered per view in
 *   localStorage (read in the first render: both views load in the browser
 *   only).
 */

import { useCallback, useRef, useState, type CSSProperties, type KeyboardEvent, type PointerEvent } from "react";

export const PANE_MIN = 260;
export const PANE_MAX = 960;
/** The least room the conversation keeps beside a widened panel. */
export const MAIN_MIN = 420;
/** One arrow-key press. */
export const PANE_STEP = 16;

/** The panel's width classes once a width was chosen (from `xl`). */
export const PANE_WIDTH_CLASS = "xl:w-[var(--side-w)] xl:max-w-[calc(100%-420px)]";

/** Where each view remembers its panel's width. */
export const CHAT_CODE_PANE_WIDTH_KEY = "ij.harness.side.width";
export const BUILD_CODE_PANE_WIDTH_KEY = "ij.buildCode.side.width";

/** A width kept in range; `container` (the row the panel sits in, 0 when not
 *  known) leaves the conversation at least `MAIN_MIN`. */
export function clampPaneWidth(width: number, container = 0): number {
  if (!Number.isFinite(width)) return PANE_MIN;
  const room = container > 0 ? container - MAIN_MIN : PANE_MAX;
  const most = Math.max(PANE_MIN, Math.min(PANE_MAX, room));
  return Math.round(Math.min(most, Math.max(PANE_MIN, width)));
}

export function readPaneWidth(key: string): number | null {
  try {
    const raw = window.localStorage.getItem(key);
    if (raw === null) return null;
    const n = Number(raw);
    return Number.isFinite(n) && n > 0 ? clampPaneWidth(n) : null;
  } catch {
    return null;
  }
}

export function writePaneWidth(key: string, width: number | null): void {
  try {
    if (width === null) window.localStorage.removeItem(key);
    else window.localStorage.setItem(key, String(Math.round(width)));
  } catch {
    /* the width lasts this visit */
  }
}

/** The view's chosen width (null: its standard one) and the way to set it. */
export function usePaneWidth(key: string): [number | null, (width: number | null) => void] {
  const [width, setWidth] = useState<number | null>(() => readPaneWidth(key));
  const set = useCallback(
    (next: number | null) => {
      const kept = next === null ? null : clampPaneWidth(next);
      setWidth(kept);
      writePaneWidth(key, kept);
    },
    [key],
  );
  return [width, set];
}

/** The panel's style for a chosen width (none: the view's own classes). */
export function paneWidthStyle(width: number | null): CSSProperties | undefined {
  return width === null ? undefined : ({ "--side-w": `${width}px` } as CSSProperties);
}

/**
 * The handle. Drawn INSIDE the panel (which must be `relative`), on its left
 * edge; the panel is the handle's parent element.
 */
export function PaneEdge({
  width,
  fallback,
  onCommit,
  testId = "pane-edge",
}: {
  /** The chosen width, or null (the view's standard one). */
  width: number | null;
  /** The standard width in px: where a drag starts when the panel cannot be
   *  measured. */
  fallback: number;
  /** Save a width (null: back to the standard one). */
  onCommit: (width: number | null) => void;
  testId?: string;
}) {
  const drag = useRef<{ x: number; start: number; container: number; last: number | null } | null>(null);
  const [dragging, setDragging] = useState(false);
  const now = width ?? fallback;

  const panelOf = (el: Element | null): HTMLElement | null =>
    el && el.parentElement instanceof HTMLElement ? el.parentElement : null;

  const onPointerDown = (e: PointerEvent<HTMLDivElement>) => {
    if (e.button !== 0) return;
    const panel = panelOf(e.currentTarget);
    const measured = panel?.getBoundingClientRect().width ?? 0;
    const container = panel?.parentElement?.getBoundingClientRect().width ?? 0;
    drag.current = { x: e.clientX, start: measured > 0 ? measured : now, container, last: null };
    try {
      e.currentTarget.setPointerCapture(e.pointerId);
    } catch {
      /* no capture: the moves still arrive while the pointer is on the edge */
    }
    setDragging(true);
    e.preventDefault();
  };

  const onPointerMove = (e: PointerEvent<HTMLDivElement>) => {
    const d = drag.current;
    if (!d) return;
    const next = clampPaneWidth(d.start + (d.x - e.clientX), d.container);
    d.last = next;
    const panel = panelOf(e.currentTarget);
    if (panel) panel.style.width = `${next}px`;
  };

  const end = (e: PointerEvent<HTMLDivElement>) => {
    const d = drag.current;
    if (!d) return;
    drag.current = null;
    setDragging(false);
    const panel = panelOf(e.currentTarget);
    if (panel) panel.style.removeProperty("width");
    if (d.last !== null) onCommit(d.last);
  };

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const container = panelOf(e.currentTarget)?.parentElement?.getBoundingClientRect().width ?? 0;
    if (e.key === "ArrowLeft") {
      e.preventDefault();
      onCommit(clampPaneWidth(now + PANE_STEP, container));
    } else if (e.key === "ArrowRight") {
      e.preventDefault();
      onCommit(clampPaneWidth(now - PANE_STEP, container));
    }
  };

  return (
    <div
      role="separator"
      aria-orientation="vertical"
      aria-label="Resize the side panel"
      aria-valuenow={now}
      aria-valuemin={PANE_MIN}
      aria-valuemax={PANE_MAX}
      tabIndex={0}
      title="Drag to resize. Double-click for the standard width."
      data-testid={testId}
      data-dragging={dragging ? "true" : undefined}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={end}
      onPointerCancel={end}
      onKeyDown={onKeyDown}
      onDoubleClick={() => onCommit(null)}
      className="group/edge absolute inset-y-0 -left-1.5 z-20 hidden w-3 cursor-col-resize touch-none select-none focus-visible:outline-none xl:block"
    >
      <span
        aria-hidden
        className={`absolute inset-y-0 left-1/2 w-px -translate-x-1/2 transition-colors ${
          dragging ? "bg-accent/70" : "bg-transparent group-hover/edge:bg-white/20 group-focus-visible/edge:bg-accent/60"
        }`}
      />
    </div>
  );
}
