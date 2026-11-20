"use client";

/**
 * A row's quiet "More" menu (leap 06-a, G4: one main action per view). The
 * row keeps its one main press in sight (Rehearse on Rules and Schedules);
 * the rest (Run now) wait here.
 *
 * The menu is PORTALED to the page and FIXED at its button (the composer's
 * model menu does the same): a row inside a table that scrolls sideways on a
 * phone would otherwise clip it. It opens below the button, on the side that
 * has room (`rowMenuPlace`) and kept inside the window, so it never pans a
 * 390 px screen sideways nor starts off it; Escape, a press outside, a scroll
 * or a resize closes it.
 */

import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { MoreHorizontal } from "lucide-react";

export interface RowMenuItem {
  label: ReactNode;
  /** The item's title (also how tests and screen readers find it). */
  title: string;
  onSelect: () => void;
  disabled?: boolean;
  testId?: string;
}

/** The gap kept between the menu and the window's edge. */
const EDGE = 8;

/** Where the open menu sits: below its button, its left edge at the
 *  button's when the button is in the window's left half (a wrapped row on a
 *  390 px phone puts "…" at the left: a right-aligned menu then started off
 *  the screen), else its right edge at the button's. Never past an edge. */
export type RowMenuPlace =
  | { top: number; left: number; maxWidth: number }
  | { top: number; right: number; maxWidth: number };

export function rowMenuPlace(
  r: { left: number; right: number; bottom: number; width: number },
  windowWidth: number,
): RowMenuPlace {
  const top = r.bottom + 4;
  if (r.left + r.width / 2 < windowWidth / 2) {
    const left = Math.max(EDGE, r.left);
    return { top, left, maxWidth: Math.max(0, windowWidth - left - EDGE) };
  }
  const right = Math.max(EDGE, windowWidth - r.right);
  return { top, right, maxWidth: Math.max(0, windowWidth - right - EDGE) };
}

export function RowMenu({ label, items, testId }: { label: string; items: RowMenuItem[]; testId?: string }) {
  const [at, setAt] = useState<RowMenuPlace | null>(null);
  const button = useRef<HTMLButtonElement | null>(null);
  const menu = useRef<HTMLDivElement | null>(null);
  const menuId = useId();
  const open = at !== null;

  function toggle() {
    if (open) {
      setAt(null);
      return;
    }
    const r = button.current?.getBoundingClientRect();
    const width = typeof window !== "undefined" ? window.innerWidth : 0;
    setAt(r ? rowMenuPlace(r, width) : { top: 0, right: EDGE, maxWidth: Math.max(0, width - 2 * EDGE) });
  }

  useEffect(() => {
    if (!open) return;
    const close = () => setAt(null);
    const onDown = (e: MouseEvent) => {
      const t = e.target as Node;
      if (button.current?.contains(t) || menu.current?.contains(t)) return;
      close();
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") close();
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    window.addEventListener("resize", close);
    window.addEventListener("scroll", close, true);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
      window.removeEventListener("resize", close);
      window.removeEventListener("scroll", close, true);
    };
  }, [open]);

  return (
    <>
      <button
        ref={button}
        type="button"
        aria-label={label}
        title={label}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        data-testid={testId}
        onClick={toggle}
        className="rounded-lg border border-white/10 p-1.5 text-zinc-400 transition-colors hover:border-accent/40 hover:text-accent-soft"
      >
        <MoreHorizontal size={14} aria-hidden="true" />
      </button>
      {open &&
        typeof document !== "undefined" &&
        createPortal(
          <div
            ref={menu}
            id={menuId}
            role="menu"
            aria-label={label}
            style={{ position: "fixed", ...at }}
            className="z-50 min-w-[9rem] max-w-[calc(100vw-1rem)] rounded-xl bg-ink-800 p-1 shadow-card"
          >
            {items.map((it) => (
              <button
                key={it.title}
                type="button"
                role="menuitem"
                title={it.title}
                disabled={it.disabled}
                data-testid={it.testId}
                onClick={() => {
                  setAt(null);
                  it.onSelect();
                }}
                className="flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-left text-xs text-zinc-300 hover:bg-white/[0.06] disabled:opacity-40"
              >
                {it.label}
              </button>
            ))}
          </div>,
          document.body,
        )}
    </>
  );
}
