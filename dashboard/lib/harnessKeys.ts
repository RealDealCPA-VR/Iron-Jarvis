/**
 * The IJ harness's keyboard (v1.377.0, IJ harness part 2 Phase 6): Ctrl+N a
 * new session, Esc stops the reply, Ctrl+L puts the cursor in the box,
 * Ctrl+K opens the model menu (⌘ on a Mac).
 *
 * WHERE THEY WORK — decided once, here, and pinned:
 *
 *  - INSIDE THE IJ HARNESS ONLY: the key is pressed with the keyboard focus
 *    inside Build's IJ harness view, or with nothing focused while the last
 *    click was inside it (a click on the conversation's text gives no focus).
 *    v1.420.0 (fix plan C2.9): the session LIST counts as inside too — it is
 *    portaled into the app's sidebar, outside the view's root, and after a
 *    click on a row Esc, Ctrl+N, Ctrl+L and Ctrl+K did nothing until the user
 *    clicked in the session. The list marks itself with `HARNESS_LIST_ATTR`.
 *    Anywhere else — the rest of the app's sidebar, the title bar, another
 *    page, the chat page — the keys are not the harness's.
 *  - Ctrl+K: inside the harness it is the MODEL MENU; everywhere else (the
 *    title bar's search, the sidebar, every other page) it stays the app's
 *    command palette (components/CommandPalette.tsx), which is untouched.
 *    The harness takes it in the CAPTURE phase and stops it there, so the
 *    palette's own window listener never sees a press the harness used.
 *  - Esc stops a reply ONLY while one runs, and never instead of an Escape
 *    something else is using: a dialog or an open menu or list in the
 *    harness (a popup — `aria-expanded="true"`, `role="dialog"`,
 *    `aria-modal` — read BEFORE anything handles the press, in the capture
 *    phase, so a menu that closes on this very Escape still counts), the
 *    question dock (its Esc answers the question), a text field that is not
 *    the message box (renaming the session, the search: their Escape is
 *    theirs), or an Escape a handler already took (`defaultPrevented` — the
 *    "/" and "@" lists). The stop itself runs AFTER React's handlers (bubble
 *    phase) to see that.
 *  - Never during IME composition; never with Alt or Shift held.
 *
 * Pure decision (`harnessKeyAction`) + the hook that wires it.
 */

import { useEffect, useRef } from "react";
import { ownsKeys } from "@/lib/codeSplit";

export type HarnessKeyAction = "new" | "stop" | "focus" | "model" | null;

export interface HarnessKeyEvent {
  key?: string;
  ctrlKey?: boolean;
  metaKey?: boolean;
  shiftKey?: boolean;
  altKey?: boolean;
  isComposing?: boolean;
  keyCode?: number;
  defaultPrevented?: boolean;
}

export interface HarnessKeyContext {
  /** The press belongs to the IJ harness (see the module docstring). */
  inside: boolean;
  /** A reply is running in the open session. */
  busy: boolean;
  /** A dialog, menu or list is open (its Escape is its own). */
  popupOpen: boolean;
  /** The question dock is up (its Escape answers it). */
  asking: boolean;
  /** The press is in a text field that is NOT the message box (the title,
   *  the search, a form): its Escape is that field's. */
  inOtherField?: boolean;
}

/** What a key press does in the IJ harness, or null (not ours). */
export function harnessKeyAction(e: HarnessKeyEvent, ctx: HarnessKeyContext): HarnessKeyAction {
  if (e.isComposing || e.keyCode === 229) return null;
  if (!ctx.inside || e.altKey || e.shiftKey) return null;
  const mod = !!(e.ctrlKey || e.metaKey);
  const key = (e.key ?? "").toLowerCase();
  if (mod) {
    if (key === "n") return "new";
    if (key === "l") return "focus";
    if (key === "k") return "model";
    return null;
  }
  if (key === "escape" || key === "esc") {
    if (!ctx.busy || ctx.popupOpen || ctx.asking || ctx.inOtherField || e.defaultPrevented) return null;
    return "stop";
  }
  return null;
}

/** The IJ harness view's root element (components/harness/IjHarnessView). */
export const HARNESS_ROOT_SELECTOR = '[data-testid="ij-harness-view"]';
/** The session's message box (its Escape stops a running reply). */
export const HARNESS_BOX_SELECTOR = '[data-testid="harness-input"]';
/** v1.420.0 (C2.9): an element OUTSIDE the view's root whose key presses are
 *  the harness's (the session list, portaled into the app's sidebar). */
export const HARNESS_LIST_ATTR = "data-harness-keys-root";
/** v1.420.0: a session row ↑/↓ move between (inside a HARNESS_LIST_ATTR). */
export const HARNESS_ROW_ATTR = "data-harness-row";

/** Is `node` inside the harness: the view's root, or a list marked with
 *  HARNESS_LIST_ATTR (the portaled session list)? */
export function inHarness(node: Node | null, root: Element | null = null): boolean {
  if (!node) return false;
  const view = root ?? (typeof document !== "undefined" ? document.querySelector(HARNESS_ROOT_SELECTOR) : null);
  if (view && view.contains(node)) return true;
  const el = node instanceof Element ? node : node.parentElement;
  return !!el?.closest(`[${HARNESS_LIST_ATTR}]`);
}

/** v1.420.0 (C2.9): ↑/↓ on a session row move the focus to the row above or
 *  below (Enter then opens it — the row is a button). Returns whether it
 *  moved. Pure DOM: the rows are read in document order. */
export function moveRowFocus(from: Element, delta: 1 | -1): boolean {
  const list = from.closest(`[${HARNESS_LIST_ATTR}]`);
  if (!list) return false;
  const rows = Array.from(list.querySelectorAll<HTMLElement>(`[${HARNESS_ROW_ATTR}]`));
  const i = rows.indexOf(from as HTMLElement);
  if (i < 0) return false;
  const next = rows[i + delta];
  if (!next) return false;
  next.focus();
  return true;
}

/** What counts as an OPEN POPUP whose Escape is its own (v1.377.0 review): a
 *  control that says it opens a popup and has it open (`aria-haspopup` +
 *  `aria-expanded="true"` — the model menu, the account list, the session's
 *  ⋯ menu), an open combobox, and a dialog, menu or listbox on screen. NOT a
 *  bare `aria-expanded="true"`: a DISCLOSURE (a reply's folded plan, its
 *  "Worked for …" line, its receipt, the thinking fold) is not a popup, and
 *  counting one meant that after the user opened ANY of them, Esc stopped
 *  nothing for the rest of the session. */
export const POPUP_SELECTOR = [
  '[aria-expanded="true"][aria-haspopup]:not([aria-haspopup="false"])',
  '[role="combobox"][aria-expanded="true"]',
  '[role="dialog"]',
  '[role="alertdialog"]',
  '[role="menu"]',
  '[role="listbox"]',
].join(", ");

/** A popup the Escape belongs to, inside `root` (or a page-level modal). */
export function popupOpenIn(root: Element | null): boolean {
  if (typeof document !== "undefined" && document.querySelector('[aria-modal="true"]')) return true;
  if (!root) return false;
  return !!root.querySelector(POPUP_SELECTOR);
}

/** Is `target` a text field other than the message box? */
export function isOtherField(target: EventTarget | null): boolean {
  if (!(target instanceof Element)) return false;
  if (target.closest(HARNESS_BOX_SELECTOR)) return false;
  const el = target as HTMLElement;
  if (el.isContentEditable) return true;
  if (el.tagName === "INPUT") {
    return !["checkbox", "radio", "button", "submit", "reset"].includes((el as HTMLInputElement).type);
  }
  return el.tagName === "TEXTAREA" || el.tagName === "SELECT";
}

let lastPointerInside = false;
let pointerWatchers = 0;
function onPointerDown(e: Event) {
  const t = e.target;
  lastPointerInside = t instanceof Element && (!!t.closest(HARNESS_ROOT_SELECTOR) || !!t.closest(`[${HARNESS_LIST_ATTR}]`));
}

/** Does this key press belong to the harness? The focus is inside the view
 *  (or its session list, v1.420.0), or nothing is focused and the last click
 *  was inside one of them. */
export function pressIsInside(target: EventTarget | null): boolean {
  if (typeof document === "undefined") return false;
  const root = document.querySelector(HARNESS_ROOT_SELECTOR);
  if (!root) return false;
  if (
    target instanceof Node &&
    target !== document.body &&
    target !== document.documentElement &&
    target !== document
  ) {
    return inHarness(target, root);
  }
  return lastPointerInside;
}

export interface HarnessKeyHandlers {
  onNew?: () => void;
  onStop?: () => void;
  onFocus?: () => void;
  onModel?: () => void;
}

/** Wire the harness keys for the handlers given (each component handles its
 *  own: the view Ctrl+N, the session Esc / Ctrl+L / Ctrl+K). `state()` is
 *  read at the press (busy, asking). v1.406.0 part 7: `owner()` is the
 *  element the handlers belong to — with two sessions side by side
 *  (lib/codeSplit) a press is a session's only when it belongs to that
 *  session's column, so Esc never stops the other column's reply. */
export function useHarnessKeys(
  handlers: HarnessKeyHandlers,
  state: () => { busy: boolean; asking: boolean },
  owner?: () => Element | null,
): void {
  const ref = useRef({ handlers, state, owner });
  ref.current = { handlers, state, owner };
  useEffect(() => {
    if (pointerWatchers++ === 0) document.addEventListener("pointerdown", onPointerDown, true);
    // What the page looked like BEFORE anyone handled the press (a menu that
    // closes on this Escape must still count as open for it).
    const seen = new WeakMap<Event, HarnessKeyContext>();
    const contextOf = (e: KeyboardEvent): HarnessKeyContext => {
      const { busy, asking } = ref.current.state();
      const root = document.querySelector(HARNESS_ROOT_SELECTOR);
      const own = ref.current.owner;
      return {
        inside: pressIsInside(e.target) && (!own || ownsKeys(own(), e.target)),
        busy,
        asking,
        popupOpen: popupOpenIn(root),
        inOtherField: isOtherField(e.target),
      };
    };
    // Ctrl+N / Ctrl+L / Ctrl+K: CAPTURE, so the app's palette (a window
    // listener in the bubble phase) never sees a press the harness took.
    const onCapture = (e: KeyboardEvent) => {
      // Only the harness's own keys are read (v1.377.0 review): the context
      // walks the page (a few querySelector calls), and this runs on EVERY
      // key press in the window — every letter typed in the box.
      const k = (e.key ?? "").toLowerCase();
      const ours = k === "escape" || k === "esc" || ((e.ctrlKey || e.metaKey) && (k === "n" || k === "l" || k === "k"));
      if (!ours) return;
      const ctx = contextOf(e);
      if (e.key === "Escape" || e.key === "Esc") {
        seen.set(e, ctx);
        return;
      }
      const action = harnessKeyAction(e, ctx);
      const h = ref.current.handlers;
      const run =
        action === "new" ? h.onNew : action === "focus" ? h.onFocus : action === "model" ? h.onModel : undefined;
      if (!run) return;
      e.preventDefault();
      e.stopPropagation();
      run();
    };
    // Esc: BUBBLE, after React's own handlers (a list or a field that used
    // the Escape marks it defaultPrevented), on the context seen at capture.
    const onBubble = (e: KeyboardEvent) => {
      if (e.key !== "Escape" && e.key !== "Esc") return;
      const ctx = seen.get(e) ?? contextOf(e);
      const run = harnessKeyAction(e, ctx) === "stop" ? ref.current.handlers.onStop : undefined;
      if (!run) return;
      e.preventDefault();
      run();
    };
    window.addEventListener("keydown", onCapture, true);
    window.addEventListener("keydown", onBubble);
    return () => {
      window.removeEventListener("keydown", onCapture, true);
      window.removeEventListener("keydown", onBubble);
      if (--pointerWatchers === 0) document.removeEventListener("pointerdown", onPointerDown, true);
    };
  }, []);
}

/** The keys in words (the box's title) — only keys that WORK where the user
 *  is (v1.377.0 review): an ordinary Chrome or Edge tab keeps Ctrl+N for a
 *  new window and the page never sees it, so the hint names Ctrl+N only in
 *  the desktop app (whose menu claims no Ctrl+N). */
export function harnessKeysHint(desktop: boolean): string {
  const keys = ["Esc stops a reply", "Ctrl+L message box", "Ctrl+K model"];
  if (desktop) keys.push("Ctrl+N new session");
  return keys.join(" · ");
}
