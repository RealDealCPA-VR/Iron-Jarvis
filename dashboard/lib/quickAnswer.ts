/**
 * QUICK ASK'S ANSWER WINDOW (next leaps 36-a, R2.2): the answer appears right
 * there.
 *
 * The hotkey box (Ctrl+Shift+Space) used to open the MAIN window on a new
 * chat (v1.506.0). Now desktop/main.js opens one small window where the box
 * was, on THIS chat page at `/chat?new=1&quick=1&compact=1&popout=1`, and the
 * page draws itself compact: the messages, the composer at the bottom and a
 * slim header with "Continue in the main window". Everything else is the chat
 * page as it is (the same stream lane, save, cards and Undo), so a Quick Ask
 * is an ordinary chat in the chat list.
 *
 * The desktop's half is the shell bridge `window.ironjarvis.quickAsk`
 * (desktop/preload.js): `isAnswerWindow` (this window IS the answer window,
 * which survives a reload where the address could be changed), `toMain`
 * (open a chat or a page in the main window; sender-checked in main.js, and
 * only the answer window may ask), `hide` (Esc) and `onFocus` (the hotkey
 * pressed while the window is open). Outside the desktop app every call here
 * does nothing and says so by its answer.
 */

import { createContext, useEffect } from "react";

/** The address flag the desktop's answer window opens the chat page with. */
export const QUICK_COMPACT_PARAM = "compact";

/** Is the chat drawn compact? Provided by the chat page's surface
 *  (components/chat/ChatCodeSurface), which reads `isCompactChat` once, and
 *  read by the chat itself. False everywhere else. */
export const CompactChatContext = createContext(false);

type QuickBridge = {
  isAnswerWindow?: unknown;
  toMain?: unknown;
  hide?: unknown;
  onFocus?: unknown;
};

function bridge(): QuickBridge | null {
  if (typeof window === "undefined") return null;
  try {
    const b = (window as unknown as { ironjarvis?: { quickAsk?: QuickBridge } }).ironjarvis?.quickAsk;
    return b && typeof b === "object" ? b : null;
  } catch {
    return null;
  }
}

/** Is the chat page drawn compact? `?compact=1` (the address the desktop
 *  opens), or the desktop saying THIS window is the answer window. Never
 *  throws. */
export function isCompactChat(search: string): boolean {
  try {
    if (new URLSearchParams(search).get(QUICK_COMPACT_PARAM) === "1") return true;
  } catch {
    /* a malformed address is not the flag */
  }
  return bridge()?.isAnswerWindow === true;
}

/** Can this window hand a chat or a page to the main window? */
export function canReachMainWindow(): boolean {
  return typeof bridge()?.toMain === "function";
}

/** Ask the desktop to open `target` in the main window: the saved chat
 *  ("Continue in the main window") or a page of the app (a door pressed in
 *  the small window). True when the desktop took it. */
export async function toMainWindow(target: { thread: string } | { path: string }): Promise<boolean> {
  const fn = bridge()?.toMain;
  if (typeof fn !== "function") return false;
  try {
    const res = (await (fn as (t: unknown) => Promise<unknown>)(target)) as { ok?: unknown } | null;
    return !!res && res.ok === true;
  } catch {
    return false;
  }
}

/** Esc: hide the answer window. False outside the desktop app. */
export function hideAnswerWindow(): boolean {
  const fn = bridge()?.hide;
  if (typeof fn !== "function") return false;
  try {
    const done = (fn as () => unknown)();
    if (done && typeof (done as Promise<unknown>).catch === "function") (done as Promise<unknown>).catch(() => {});
  } catch {
    return false;
  }
  return true;
}

/** Where a link pressed in the answer window belongs (pure): the path to open
 *  in the MAIN window, or null to leave the press alone. Another page of the
 *  app is the main window's (a door opens there, as the plan says: it is
 *  another page), and so is a chat address that asks for more than a chat
 *  (`?view=code`, `?ask=`: read only when a page opens). A chat link
 *  (`/chat?thread=`), the chat itself, an in-page anchor and another site
 *  (the desktop's own guard sends that to the system browser) stay. */
export function mainWindowPath(href: string, here: { origin: string; pathname: string }): string | null {
  const raw = String(href || "").trim();
  if (!raw || raw.startsWith("#")) return null;
  let url: URL;
  try {
    url = new URL(raw, `${here.origin}${here.pathname}`);
  } catch {
    return null;
  }
  if (url.origin !== here.origin) return null;
  if (url.pathname === "/chat") {
    const keys = [...url.searchParams.keys()];
    if (keys.every((k) => k === "thread")) return null;
  }
  return `${url.pathname}${url.search}${url.hash}`;
}

/** Esc hides the window unless something on the page is in front: a key a
 *  handler already took (a menu's own Escape calls preventDefault), a word
 *  being composed, or an open dialog or menu, which Esc closes first. */
export function escapeHidesWindow(e: KeyboardEvent, doc: Document): boolean {
  if (e.key !== "Escape" || e.defaultPrevented || e.isComposing) return false;
  try {
    if (doc.querySelector('[aria-modal="true"], [role="menu"], [role="listbox"]')) return false;
  } catch {
    /* an unreadable page: leave the key alone */
    return false;
  }
  return true;
}

/**
 * The answer window's keys and links, for the chat page while it is drawn
 * compact (no-op otherwise):
 *  - Esc hides the window (`escapeHidesWindow`), after the page's own
 *    handlers had their turn (a bubble listener on the window);
 *  - the hotkey pressed while the window is open calls `focusBox`;
 *  - a press on a link to another page opens it in the main window
 *    (`mainWindowPath`), caught before the page's router follows it.
 */
export function useAnswerWindow(compact: boolean, focusBox: () => void): void {
  useEffect(() => {
    if (!compact) return;
    const onKey = (e: KeyboardEvent) => {
      if (escapeHidesWindow(e, document)) hideAnswerWindow();
    };
    const onClick = (e: MouseEvent) => {
      if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
      if (!canReachMainWindow()) return;
      const el = e.target instanceof Element ? e.target.closest("a[href]") : null;
      const a = el instanceof HTMLAnchorElement ? el : null;
      if (!a || (a.target && a.target !== "_self") || a.hasAttribute("download")) return;
      const path = mainWindowPath(a.getAttribute("href") || "", window.location);
      if (!path) return;
      e.preventDefault();
      e.stopPropagation();
      void toMainWindow({ path });
    };
    window.addEventListener("keydown", onKey);
    window.addEventListener("click", onClick, true);
    let unsubscribe: (() => void) | null = null;
    const onFocus = bridge()?.onFocus;
    if (typeof onFocus === "function") {
      try {
        const off = (onFocus as (cb: () => void) => unknown)(() => focusBox());
        if (typeof off === "function") unsubscribe = off as () => void;
      } catch {
        unsubscribe = null;
      }
    }
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("click", onClick, true);
      unsubscribe?.();
    };
  }, [compact, focusBox]);
}
