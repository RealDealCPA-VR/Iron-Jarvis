import { useSyncExternalStore } from "react";

/**
 * Whether THIS WINDOW's sidebar is shown (v1.500.0, one sidebar).
 *
 * The app has ONE left sidebar. Hiding it takes it off the screen entirely —
 * no strip of icons is left behind — and the content gets the room, like the
 * Claude desktop app. The title bar then shows one small "Show the sidebar"
 * button that brings it back.
 *
 * The choice is kept per window: sessionStorage (each window has its own, and
 * it survives a reload). A NEW window starts with the last choice made in any
 * window (localStorage), and with the sidebar shown when there is none.
 *
 * A hidden sidebar is still MOUNTED (display:none), so the module list it
 * holds (Chat's chats, Build's terminals or sessions) keeps its place and its
 * state, and never falls back into the page as a second column.
 */

export const SIDEBAR_HIDDEN_KEY = "ij_sidebar_collapsed";

let hidden: boolean | null = null;
const subs = new Set<() => void>();

function read(): boolean {
  try {
    const mine = window.sessionStorage.getItem(SIDEBAR_HIDDEN_KEY);
    if (mine === "1" || mine === "0") return mine === "1";
  } catch {
    /* storage blocked */
  }
  try {
    return window.localStorage.getItem(SIDEBAR_HIDDEN_KEY) === "1";
  } catch {
    return false;
  }
}

function current(): boolean {
  if (hidden === null) hidden = read();
  return hidden;
}

/** Show (false) or hide (true) this window's sidebar, and remember it. */
export function setSidebarHidden(next: boolean): void {
  hidden = next;
  const v = next ? "1" : "0";
  try {
    window.sessionStorage.setItem(SIDEBAR_HIDDEN_KEY, v);
  } catch {
    /* storage blocked */
  }
  try {
    window.localStorage.setItem(SIDEBAR_HIDDEN_KEY, v);
  } catch {
    /* storage blocked */
  }
  for (const f of subs) f();
}

/** Hide or show from a button, then put the focus on the button that undoes
 *  it (the pressed one has just left the screen), so the keyboard never
 *  loses its place. */
export function toggleSidebarFromButton(next: boolean): void {
  setSidebarHidden(next);
  const target = next ? "titlebar-show-sidebar" : "sidebar-hide";
  const go = () => document.querySelector<HTMLElement>(`[data-testid="${target}"]`)?.focus();
  if (typeof window.requestAnimationFrame === "function") window.requestAnimationFrame(go);
  else go();
}

/** Forget the cached choice (tests: storage was cleared between them). */
export function resetSidebarHidden(): void {
  hidden = null;
  for (const f of subs) f();
}

function subscribe(f: () => void): () => void {
  subs.add(f);
  return () => {
    subs.delete(f);
  };
}

/** True while this window's sidebar is hidden. The server render (and the
 *  first frame) say shown, the default. */
export function useSidebarHidden(): boolean {
  return useSyncExternalStore(subscribe, current, () => false);
}
