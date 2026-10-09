"use client";

/**
 * The chat top bar's "⋯" menu (v1.326.0, calm chat W1-1).
 *
 * The old chat card carried a header row of buttons (Voice, read aloud,
 * Persona, edit, Project). The calm top bar keeps only Share and this one
 * quiet button; everything else lives in the panel it opens, so nothing
 * that was reachable before became unreachable.
 *
 * It is a DISCLOSURE, not an ARIA menu: the panel holds a native <select>
 * (the persona choice), and a role="menu" may only own menu items. So the
 * trigger says aria-expanded / aria-controls and the panel is a labelled
 * group of ordinary controls that Tab walks in order.
 *
 * What it owns, so the page does not have to:
 *   * open moves focus to the panel's first control;
 *   * Escape closes and puts focus back on the trigger;
 *   * a press outside closes (focus stays where the user put it);
 *   * Tab past the last control closes it, so it never lingers behind focus.
 * The page passes `children` as a function of `close`, so an action that
 * should end the visit (Voice, Project panel) can close it on the way out.
 */

import { MoreHorizontal } from "lucide-react";
import { useEffect, useId, useRef, useState, type ReactNode } from "react";

const TABBABLE = 'a[href], button, input, select, textarea, [tabindex]:not([tabindex="-1"])';

export function ChatMoreMenu({
  label = "More chat options",
  children,
}: {
  /** The trigger's accessible name. */
  label?: string;
  /** `trigger` reads the "⋯" button: an action that opens something whose
   *  focus should come back here (the project drawer) names it as the
   *  return target, since the row that was pressed unmounts with the panel. */
  children: (close: () => void, trigger: () => HTMLElement | null) => ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const panelId = useId();

  function close(refocus = false) {
    setOpen(false);
    if (refocus) triggerRef.current?.focus();
  }

  // Focus in: the first enabled control of the panel.
  useEffect(() => {
    if (!open) return;
    const panel = panelRef.current;
    if (!panel) return;
    const first = Array.from(panel.querySelectorAll<HTMLElement>(TABBABLE)).find(
      (el) => !el.hasAttribute("disabled"),
    );
    (first ?? panel).focus();
  }, [open]);

  // A press anywhere outside the trigger + panel closes it.
  useEffect(() => {
    if (!open) return;
    function onDown(e: PointerEvent | MouseEvent) {
      if (!wrapRef.current?.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("pointerdown", onDown);
    document.addEventListener("mousedown", onDown);
    return () => {
      document.removeEventListener("pointerdown", onDown);
      document.removeEventListener("mousedown", onDown);
    };
  }, [open]);

  return (
    <div
      ref={wrapRef}
      className="relative"
      onKeyDown={(e) => {
        if (e.key === "Escape" && open) {
          // Ours: the composer's own Escape (Stop) and any drawer must not
          // also act on the key that closed this panel.
          e.preventDefault();
          e.stopPropagation();
          close(true);
        }
      }}
      onBlur={(e) => {
        // Focus left the trigger + panel (Tab past the end): close.
        const next = e.relatedTarget as Node | null;
        if (open && next && !wrapRef.current?.contains(next)) setOpen(false);
      }}
    >
      <button
        ref={triggerRef}
        type="button"
        data-testid="chat-more-trigger"
        aria-label={label}
        title={label}
        aria-haspopup="true"
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        onClick={() => setOpen((v) => !v)}
        className={`inline-flex h-7 w-7 items-center justify-center rounded-lg text-zinc-400 transition-colors hover:bg-white/[0.06] hover:text-zinc-100 ${
          open ? "bg-white/[0.06] text-zinc-100" : ""
        }`}
      >
        <MoreHorizontal size={16} />
      </button>
      {open && (
        <div
          ref={panelRef}
          id={panelId}
          role="group"
          aria-label="Chat options"
          data-testid="chat-more-panel"
          tabIndex={-1}
          className="absolute right-0 top-full z-40 mt-1.5 w-[min(18rem,calc(100vw-2rem))] rounded-xl border hairline bg-ink-900 p-1.5 shadow-lg outline-none"
        >
          {children(
            () => close(false),
            () => triggerRef.current,
          )}
        </div>
      )}
    </div>
  );
}

export default ChatMoreMenu;
