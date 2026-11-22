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
 *
 * A HAND-OVER GOES WITH ITS PAGE (36-a review). A door that carries the
 * user's request (the coding door, the team door, a workflow draft's "Open in
 * editor") stages it in this window's sessionStorage, which is this window's
 * alone: the main window's page would open empty. So `toMainWindow({path})`
 * takes what this window staged for that page (`MAIN_WINDOW_HAND_OVERS`) and
 * gives it to the desktop with the path, never in the address; main.js holds
 * it once and the main window's preload puts it in that window's storage
 * before the page reads it. A file pressed in the record does the same with
 * the chat ("Continue in the main window", the file open there).
 */

import { createContext, useEffect } from "react";
import { CODING_OFFER_HREF, CODING_START_KEY } from "@/lib/codingStart";
import { MISSION_OFFER_HREF, MISSION_START_KEY } from "@/lib/missionStart";

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

/** Is the chat page drawn compact? In the desktop app the desktop's word
 *  alone (THIS window is the answer window, through a reload or a changed
 *  address): a `?compact=1` typed or written into the MAIN window must not
 *  draw it as one, where its links would go nowhere (36-a review). Outside
 *  the desktop app (a browser), `?compact=1`. Never throws. */
export function isCompactChat(search: string): boolean {
  const word = bridge()?.isAnswerWindow;
  if (typeof word === "boolean") return word;
  try {
    return new URLSearchParams(search).get(QUICK_COMPACT_PARAM) === "1";
  } catch {
    /* a malformed address is not the flag */
    return false;
  }
}

/** The answer window's own title (desktop/main.js gives it the same one):
 *  the taskbar and Alt+Tab name it Quick Ask, not the chat page it shows. */
export const QUICK_ASK_WINDOW_TITLE = "Quick Ask — Iron Jarvis";

/** Is THIS window Quick Ask's answer window? The desktop's word only; false
 *  in a browser and in every other window. */
export function isAnswerWindow(): boolean {
  return bridge()?.isAnswerWindow === true;
}

/** Can this window hand a chat or a page to the main window? */
export function canReachMainWindow(): boolean {
  return typeof bridge()?.toMain === "function";
}

/** The workflow editor's hand-over (components/chat/WorkflowDraftCard writes
 *  it, app/workflows takes it) and the page that takes it. */
export const PENDING_WORKFLOW_KEY = "ij_pending_workflow";
export const WORKFLOWS_HREF = "/workflows";

/** A page of the main window that opens with a hand-over → the
 *  sessionStorage key it waits under. desktop/main.js `QUICK_CARRY_KEYS` is
 *  its twin (leap-36a-compact-chat.test.tsx holds the two equal). */
export const MAIN_WINDOW_HAND_OVERS: Readonly<Record<string, string>> = Object.freeze({
  [CODING_OFFER_HREF]: CODING_START_KEY,
  [MISSION_OFFER_HREF]: MISSION_START_KEY,
  [WORKFLOWS_HREF]: PENDING_WORKFLOW_KEY,
});

/** The file a press in the answer window opens beside the chat in the main
 *  window (desktop/main.js `QUICK_CARRY_FILE_KEY`). */
export const MAIN_WINDOW_FILE_KEY = "ij.chat.openFile";

/** How long a file handed to the main window waits for its chat to open. */
export const MAIN_WINDOW_FILE_TTL_MS = 2 * 60_000;

/** The longest hand-over the desktop holds (desktop/main.js
 *  `QUICK_CARRY_MAX_CHARS`). */
export const MAIN_WINDOW_CARRY_MAX_CHARS = 200_000;

type Carry = { key: string; value: string };

/** Take what this window staged for `path` (gone from here: it is the main
 *  window's now), or null. Never throws. */
function takeHandOver(path: string): Carry | null {
  const key = Object.prototype.hasOwnProperty.call(MAIN_WINDOW_HAND_OVERS, path) ? MAIN_WINDOW_HAND_OVERS[path] : "";
  if (!key) return null;
  try {
    const value = window.sessionStorage.getItem(key);
    if (!value || value.length > MAIN_WINDOW_CARRY_MAX_CHARS) return null;
    window.sessionStorage.removeItem(key);
    return { key, value };
  } catch {
    return null;
  }
}

/** Put a hand-over back when the desktop did not take it. Never throws. */
function putBack(carry: Carry | null): void {
  if (!carry) return;
  try {
    window.sessionStorage.setItem(carry.key, carry.value);
  } catch {
    /* storage blocked */
  }
}

/** Ask the desktop to open `target` in the main window: the saved chat
 *  ("Continue in the main window"), or a page of the app (a door pressed in
 *  the small window) with what this window staged for it. True when the
 *  desktop took it. */
export async function toMainWindow(
  target: { thread: string; file?: string } | { path: string },
): Promise<boolean> {
  const fn = bridge()?.toMain;
  if (typeof fn !== "function") return false;
  let carry: Carry | null = null;
  let sent: Record<string, unknown>;
  if ("path" in target) {
    carry = takeHandOver(target.path);
    sent = carry ? { path: target.path, carry } : { path: target.path };
  } else if (typeof target.file === "string" && target.file) {
    const value = JSON.stringify({ thread: target.thread, path: target.file, at: Date.now() });
    sent = { thread: target.thread, carry: { key: MAIN_WINDOW_FILE_KEY, value } };
  } else {
    sent = { thread: target.thread };
  }
  try {
    const res = (await (fn as (t: unknown) => Promise<unknown>)(sent)) as { ok?: unknown } | null;
    const ok = !!res && res.ok === true;
    if (!ok) putBack(carry);
    return ok;
  } catch {
    putBack(carry);
    return false;
  }
}

/** A door pressed in the answer window (the chat page's own door handler and
 *  a workflow draft's "Open in editor"): `stage` puts its hand-over in this
 *  window's storage, then the page opens in the MAIN window with it. True when
 *  handled here (the link must not also navigate); false outside the desktop
 *  app and for a link that stays in this window (a chat link). */
export function doorToMainWindow(href: string, stage: () => void = () => {}): boolean {
  if (typeof window === "undefined" || !canReachMainWindow()) return false;
  const path = mainWindowPath(href, window.location);
  if (!path) return false;
  try {
    stage();
  } catch {
    /* the page opens without it */
  }
  void toMainWindow({ path });
  return true;
}

/** The main window's chat, opened with `thread`: the file the answer window
 *  handed over with it, once (gone after this read), or null. Never throws. */
export function takeMainWindowFile(thread: string, now: number = Date.now()): string | null {
  try {
    const raw = window.sessionStorage.getItem(MAIN_WINDOW_FILE_KEY);
    if (!raw) return null;
    window.sessionStorage.removeItem(MAIN_WINDOW_FILE_KEY);
    const r = JSON.parse(raw) as { thread?: unknown; path?: unknown; at?: unknown };
    const at = typeof r.at === "number" && Number.isFinite(r.at) ? r.at : 0;
    if (!at || now - at > MAIN_WINDOW_FILE_TTL_MS || at - now > 60_000) return null;
    if (r.thread !== thread || typeof r.path !== "string") return null;
    const path = r.path.trim();
    return path && path.length <= 4096 ? path : null;
  } catch {
    return null;
  }
}

/** A link whose own press handler decides where it goes (a reply's door,
 *  which may stage a request first): the answer window's link catch leaves
 *  it alone. */
export const OWN_PRESS_ATTR = "data-own-press";

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
 *  being composed, or an open dialog or menu, which Esc closes first. A
 *  question card in the composer's place is NOT in front: Esc hides the
 *  window and never answers it (components/chat/DockAsk, 36-a review), unless
 *  Esc answers that card (`data-esc-answers`, lib/dockAsk.escAnswersCard: the
 *  Local only question, whose words nothing has saved yet), which is then in
 *  front like a dialog. */
export function escapeHidesWindow(e: KeyboardEvent, doc: Document): boolean {
  if (e.key !== "Escape" || e.defaultPrevented || e.isComposing) return false;
  try {
    if (doc.querySelector('[aria-modal="true"], [role="menu"], [role="listbox"], [data-esc-answers]')) return false;
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
 *    (`mainWindowPath`), caught before the page's router follows it; a
 *    door (`OWN_PRESS_ATTR`) is left to its own handler, which carries its
 *    request (`doorToMainWindow`).
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
      // A door's own handler stages its request first and opens the page in
      // the main window itself (doorToMainWindow): caught here, it went empty.
      if (a.hasAttribute(OWN_PRESS_ATTR)) return;
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
