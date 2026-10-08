"use client";

/**
 * Modal — a dialog that is actually attached to the PAGE (v1.214.0).
 *
 * THE BUG THIS EXISTS FOR, reported verbatim: the add-agent "+" popup "is
 * bound by the size of the thread (chat window) and on a small card doesn't
 * show everything from this pop up".
 *
 * It was not a sizing mistake. `PanelPicker` is `fixed inset-0`, which every
 * reader takes to mean "the viewport" — and it did not, because of where it
 * was RENDERED. It is returned from inside `RoundTable`, whose root is
 * `<div class="card-surface … overflow-hidden">`, and `.card-surface` carries
 *
 *     backdrop-filter: blur(18px) saturate(150%);   (globals.css)
 *
 * A non-`none` `backdrop-filter` makes an element the CONTAINING BLOCK for its
 * fixed-position descendants (CSS Filter Effects §Containing Blocks — the same
 * rule `transform`, `perspective`, `filter` and `contain: paint` have). So
 * `inset-0` resolved to the thread card's box instead of the viewport, and the
 * card's own `overflow-hidden` then CLIPPED whatever did not fit. On a short
 * card the picker's footer — the Save button — was simply cut off.
 *
 * There is no CSS fix from inside: the modal cannot opt out of an ancestor's
 * containing block. It has to leave the subtree, so this renders through a
 * PORTAL into `document.body`, where `fixed` means what it says.
 *
 * The portal is also why this is a shared primitive rather than a line changed
 * in one file. Any `fixed inset-0` overlay rendered inside a `.card-surface`
 * has the same defect waiting, and the class is on nearly every panel in the
 * app — so the safe shape is one component that every dialog uses.
 *
 * What it owns, so no caller has to remember it:
 *   * the portal (SSR-safe: nothing renders until mounted, since
 *     `document` does not exist while Next prerenders);
 *   * the backdrop, and click-outside to close;
 *   * Escape to close;
 *   * `role="dialog"` + `aria-modal` + the accessible name;
 *   * BODY SCROLL LOCK while open — with the overlay out in `body`, a wheel
 *     over the backdrop scrolls the page behind it, which reads as the dialog
 *     sliding away.
 * `busy` freezes the two dismissals (Escape, backdrop) and nothing else: a
 * dialog must not vanish out from under a request it has in flight.
 *
 * FOCUS (v1.313.0). A keyboard or screen-reader user who opened a dialog was
 * left standing on the page behind it: Tab walked the page under the scrim,
 * and on close focus fell to <body>. Now the dialog
 *   * takes focus when it opens: a control the dialog autofocuses keeps it,
 *     else the first tabbable control, else the dialog box itself
 *     (tabIndex -1), so there is always somewhere to stand;
 *   * keeps Tab and Shift+Tab inside it, wrapping at the edges, skipping
 *     disabled, [hidden] and tabindex="-1" controls. Only the TOP dialog of a
 *     stack traps, so a cropper opened over a room keeps the keys;
 *   * gives focus back to whatever had it before it opened.
 * The tabbable test reads ATTRIBUTES, never layout (offsetParent,
 * getClientRects): layout is empty in jsdom and would call every control
 * hidden. Dismissal (Escape, backdrop, busy) is exactly what it was.
 *
 * `z` (default 60) lets an overlay that already sat above the default layer
 * (the Build files panel at 80) move onto this primitive without changing its
 * stacking.
 */

import { useEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";

const TABBABLE =
  'a[href], area[href], button, input, select, textarea, iframe, summary, [tabindex], [contenteditable="true"]';

/** The controls Tab can reach inside `root`, in document order. */
function tabbables(root: HTMLElement): HTMLElement[] {
  return Array.from(root.querySelectorAll<HTMLElement>(TABBABLE)).filter((el) => {
    if (el.hasAttribute("disabled")) return false;
    if (el.getAttribute("tabindex") === "-1") return false;
    if (el.closest("[hidden], [inert]")) return false;
    if (el instanceof HTMLInputElement && el.type === "hidden") return false;
    return true;
  });
}

/** Open dialogs, oldest first. Only the last one traps Tab. */
const openStack: object[] = [];

export function Modal({
  label,
  onClose,
  busy = false,
  children,
  className = "w-full max-w-2xl",
  testId,
  z = 60,
}: {
  /** The dialog's accessible name. */
  label: string;
  onClose: () => void;
  /** A submit is in flight — Escape and the backdrop stop dismissing. */
  busy?: boolean;
  children: ReactNode;
  /** Sizing for the dialog box. Height is capped here, not by the caller. */
  className?: string;
  testId?: string;
  /** Stacking layer of the overlay (default 60, the layer every dialog used). */
  z?: number;
}) {
  // The portal target. `null` until mounted so the server render and the first
  // client render agree (there is no `document` during prerender).
  const [host, setHost] = useState<HTMLElement | null>(null);
  useEffect(() => setHost(document.body), []);

  // `onClose` through a ref so the key listener is bound ONCE. A caller that
  // passes an inline arrow (all of them do) would otherwise rebind the
  // document listener on every render of the parent.
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  const busyRef = useRef(busy);
  busyRef.current = busy;

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape" && !busyRef.current) closeRef.current();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  // FOCUS IN / BACK. The capture runs on the first render, before the portal
  // exists, so it records the OPENER and never something inside the dialog.
  // Focus is given back only when it would otherwise be lost (on <body>, or
  // inside the box that is going away), so a caller that moves focus on
  // purpose in its own onClose keeps that choice.
  const boxRef = useRef<HTMLDivElement>(null);
  const tokenRef = useRef<object>({});
  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null;
    const token = tokenRef.current;
    openStack.push(token);
    const box = boxRef;
    return () => {
      const i = openStack.lastIndexOf(token);
      if (i >= 0) openStack.splice(i, 1);
      const now = document.activeElement;
      const lost = !now || now === document.body || (box.current?.contains(now) ?? false);
      if (lost && opener && opener !== document.body && opener.isConnected) opener.focus();
    };
  }, []);

  useEffect(() => {
    const box = boxRef.current;
    if (!host || !box) return;
    if (box.contains(document.activeElement)) return; // the dialog's own autofocus wins
    (tabbables(box)[0] ?? box).focus();
  }, [host]);

  // TAB STAYS INSIDE the top dialog. preventDefault only at the edges, so the
  // browser still moves focus normally between the controls in between.
  useEffect(() => {
    function onTab(e: KeyboardEvent) {
      if (e.key !== "Tab") return;
      const box = boxRef.current;
      if (!box || openStack[openStack.length - 1] !== tokenRef.current) return;
      const items = tabbables(box);
      if (items.length === 0) {
        e.preventDefault();
        box.focus();
        return;
      }
      const first = items[0];
      const last = items[items.length - 1];
      const active = document.activeElement;
      const inside = !!active && box.contains(active);
      if (e.shiftKey && (!inside || active === first || active === box)) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && (!inside || active === last)) {
        e.preventDefault();
        first.focus();
      }
    }
    document.addEventListener("keydown", onTab);
    return () => document.removeEventListener("keydown", onTab);
  }, []);

  // Scroll lock. The previous value is restored rather than cleared: two
  // stacked dialogs (the portrait cropper opens over the agents room) would
  // otherwise have the inner one's unmount unlock the page under the outer.
  useEffect(() => {
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = prev;
    };
  }, []);

  if (!host) return null;

  return createPortal(
    <div
      data-testid={testId}
      className="fixed inset-0 flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm"
      style={{ zIndex: z }}
      onClick={() => {
        if (!busy) onClose();
      }}
    >
      <div
        ref={boxRef}
        role="dialog"
        aria-modal="true"
        aria-label={label}
        tabIndex={-1}
        onClick={(e) => e.stopPropagation()}
        className={`flex max-h-[88vh] flex-col overflow-hidden rounded-2xl border border-white/10 bg-ink-850/95 shadow-card-hover outline-none backdrop-blur-xl ${className}`}
      >
        {children}
      </div>
    </div>,
    host,
  );
}

export default Modal;
