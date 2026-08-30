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
 */

import { useEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";

export function Modal({
  label,
  onClose,
  busy = false,
  children,
  className = "w-full max-w-2xl",
  testId,
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
      className="fixed inset-0 z-[60] flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm"
      onClick={() => {
        if (!busy) onClose();
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label={label}
        onClick={(e) => e.stopPropagation()}
        className={`flex max-h-[88vh] flex-col overflow-hidden rounded-2xl border border-white/10 bg-ink-850/95 shadow-card-hover backdrop-blur-xl ${className}`}
      >
        {children}
      </div>
    </div>,
    host,
  );
}

export default Modal;
