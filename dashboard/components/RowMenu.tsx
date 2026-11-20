"use client";

/**
 * A row's quiet "More" menu (leap 06-a, G4: one main action per view). The
 * row keeps its one main press in sight (Rehearse on Rules and Schedules);
 * the rest (Run now) wait here.
 *
 * The menu is PORTALED to the page and FIXED at its button (the composer's
 * model menu does the same): a row inside a table that scrolls sideways on a
 * phone would otherwise clip it. It opens below the button, right-aligned and
 * kept inside the window, so it never pans a 390 px screen sideways; Escape,
 * a press outside, a scroll or a resize closes it.
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

export function RowMenu({ label, items, testId }: { label: string; items: RowMenuItem[]; testId?: string }) {
  const [at, setAt] = useState<{ top: number; right: number } | null>(null);
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
    setAt(r ? { top: r.bottom + 4, right: Math.max(EDGE, width - r.right) } : { top: 0, right: EDGE });
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
            style={{ position: "fixed", top: at.top, right: at.right }}
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
