"use client";

/**
 * The chat's project panel as a DRAWER (v1.326.0, calm chat W1-1).
 *
 * It used to be a permanent third column beside the conversation (or a
 * vertical "PROJECT" strip when collapsed). Now it opens on demand from the
 * chat top bar and slides over the right edge; its contents (project picker,
 * Files / Knowledge, the files this chat made, the workspace folder) are the
 * page's, unchanged. This component owns only the shell:
 *
 *   * a PORTAL to <body>, so `fixed` means the window and no ancestor's
 *     transform or backdrop-filter can re-anchor it (the Modal lesson);
 *   * it sits under the title bar and the demo strip (`--ij-strip-h`);
 *   * the whole width on a phone with a scrim behind it (a press on the
 *     scrim closes), a resizable column on md+ with no scrim, so the chat
 *     stays usable while a file preview is open beside it;
 *   * Escape closes when focus is in the drawer (or nowhere): a key typed in
 *     the composer is the composer's, and so is a key typed in a text field
 *     INSIDE the drawer (a knowledge-file rename cancels its own edit with
 *     Escape; that must not also close the panel around it);
 *   * FOCUS like the app's other drawers: a drawer the USER opened takes
 *     focus, and on close focus goes back to whatever opened it. A drawer
 *     the APP opened (a reply made a file, so its preview shows) never steals
 *     the caret from the composer — `takeFocus` says which one this is.
 *     The opener can be GONE by then: the "⋯" menu's row unmounts in the
 *     same press that opens the drawer. So the page names a return target
 *     (`returnFocusTo`), used whenever the opener is gone, and the focus
 *     never falls to the page body.
 */

import { X } from "lucide-react";
import { useEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { createPortal } from "react-dom";

/** A field where Escape is the field's own key (cancel an edit), not ours. */
export function isTextEntry(t: EventTarget | null): boolean {
  if (!(t instanceof HTMLElement)) return false;
  if (t.isContentEditable) return true;
  const tag = t.tagName;
  if (tag === "TEXTAREA") return true;
  if (tag !== "INPUT") return false;
  const type = ((t as HTMLInputElement).type || "text").toLowerCase();
  return !NOT_TEXT_INPUTS.has(type);
}

const NOT_TEXT_INPUTS = new Set([
  "checkbox",
  "radio",
  "button",
  "submit",
  "reset",
  "image",
  "range",
  "color",
  "file",
]);

/** Somewhere focus can usefully go back to. */
function focusable(el: HTMLElement | null | undefined): el is HTMLElement {
  return !!el && el !== document.body && el.isConnected;
}

export function ProjectDrawer({
  width,
  takeFocus,
  returnFocusTo,
  onClose,
  onResizeStart,
  onResizeReset,
  children,
}: {
  /** The md+ width in px (the page's remembered rail width). */
  width: number;
  /** True when a press opened it: move focus in, and back out on close. */
  takeFocus: boolean;
  /** Where focus goes on close when the opener is gone (or the app opened
   *  it and focus was inside). Read at close time. */
  returnFocusTo?: () => HTMLElement | null;
  onClose: () => void;
  onResizeStart?: (e: React.PointerEvent<HTMLDivElement>) => void;
  onResizeReset?: () => void;
  children: ReactNode;
}) {
  const [host, setHost] = useState<HTMLElement | null>(null);
  useEffect(() => setHost(document.body), []);
  const boxRef = useRef<HTMLElement>(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  const returnRef = useRef(returnFocusTo);
  returnRef.current = returnFocusTo;

  // FOCUS BACK. Captured on the first render, before the portal exists, so
  // it records the opener (the top-bar button), never something inside.
  const openerRef = useRef<HTMLElement | null>(
    typeof document !== "undefined" && takeFocus ? (document.activeElement as HTMLElement | null) : null,
  );
  useEffect(() => {
    const box = boxRef;
    const opener = openerRef;
    const fallback = returnRef;
    return () => {
      const now = document.activeElement;
      const lost = !now || now === document.body || (box.current?.contains(now) ?? false);
      if (!lost) return;
      const o = opener.current;
      if (focusable(o) && !(box.current?.contains(o) ?? false)) {
        o.focus();
        return;
      }
      // The opener is gone (the "⋯" row unmounted as it opened us) or the
      // app opened the drawer: the page's named target, never the body.
      const back = fallback.current?.();
      if (focusable(back)) back.focus();
    };
  }, []);

  // FOCUS IN, only when a press opened it.
  useEffect(() => {
    if (!host || !takeFocus) return;
    const box = boxRef.current;
    if (!box || box.contains(document.activeElement)) return;
    const close = box.querySelector<HTMLElement>("[data-drawer-close]");
    (close ?? box).focus();
    // Only on the open edge: later renders must not pull focus back in.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [host]);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key !== "Escape" || e.defaultPrevented) return;
      const t = e.target as Node | null;
      const inside = !!t && (boxRef.current?.contains(t) ?? false);
      // A text field inside the drawer owns its Escape (cancel the edit),
      // the way the composer owns its own.
      if (inside && isTextEntry(e.target)) return;
      if (inside || t === document.body || t === null) closeRef.current();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  if (!host) return null;

  // Under the title bar (2.5rem) and the demo strip, down to the window's
  // bottom edge.
  const top = "calc(2.5rem + var(--ij-strip-h, 0px))";
  return createPortal(
    <>
      <div
        aria-hidden
        data-testid="chat-project-scrim"
        onClick={() => closeRef.current()}
        className="fixed inset-x-0 bottom-0 z-40 bg-ink-950/60 md:hidden"
        style={{ top }}
      />
      <aside
        ref={boxRef}
        data-testid="chat-project-drawer"
        role="dialog"
        aria-modal="false"
        aria-label="Project panel"
        tabIndex={-1}
        className="fixed bottom-0 right-0 z-40 flex w-full flex-col border-l hairline bg-ink-900 shadow-lg outline-none md:w-[var(--rail-w)] md:max-w-[85vw]"
        style={{ top, "--rail-w": `${width}px` } as CSSProperties}
      >
        {onResizeStart && (
          // Drag the left edge (md+): a wider preview when wanted; a
          // double-click puts the default width back.
          <div
            role="separator"
            aria-orientation="vertical"
            aria-label="Resize the side panel"
            title="Drag to resize. Double-click to reset."
            onPointerDown={onResizeStart}
            onDoubleClick={onResizeReset}
            className="group/resize absolute -left-1.5 top-0 z-10 hidden h-full w-3 cursor-col-resize touch-none items-center justify-center md:flex"
          >
            <span className="h-12 w-1 rounded-full bg-white/10 transition-colors group-hover/resize:bg-accent/60" />
          </div>
        )}
        <div className="flex h-12 shrink-0 items-center justify-between gap-2 px-3">
          <span className="text-[13px] font-medium text-zinc-200">Project panel</span>
          <button
            type="button"
            data-drawer-close
            onClick={() => closeRef.current()}
            aria-label="Close project panel"
            title="Close (Esc)"
            className="inline-flex h-7 w-7 items-center justify-center rounded-lg text-zinc-400 transition-colors hover:bg-white/[0.06] hover:text-zinc-100"
          >
            <X size={15} />
          </button>
        </div>
        <div className="flex min-h-0 flex-1 flex-col gap-2 px-3 pb-3">{children}</div>
      </aside>
    </>,
    host,
  );
}

export default ProjectDrawer;
