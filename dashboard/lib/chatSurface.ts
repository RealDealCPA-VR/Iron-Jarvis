/**
 * CHAT | CODE (v1.406.0 part 1).
 *
 * The chat page carries one switch at the top, in the middle: **Chat** (the
 * chat surface, unchanged) and **Code** (the coding sessions — what was
 * Build's second view, components/harness/*). Build itself is the terminals
 * only now.
 *
 * THE ADDRESS: `/chat?view=code` is the Code view; `&session=<id>` opens one
 * coding session there and `&night=<date>` opens Last night. `/` reads the
 * same query (home IS the chat page). Every producer of a session link goes
 * through lib/buildLinks (buildSessionHref, lastNightHref), desktop/main.js
 * spells the same address, and the old `/terminals?session=` /
 * `/terminals?night=` addresses are redirected here by the Build page.
 *
 * THE MEMORY: the address wins; else this WINDOW's last choice
 * (sessionStorage — per window, survives a reload, a new window starts on
 * Chat), mirrored in module state so a return to the chat page paints the
 * right view on its first frame (lib/turnCache's reason for keepShape).
 *
 * Import-free (React only for the store): the sidebar reads the view, and
 * dozens of test files mock lib/api wholesale.
 */

import { useSyncExternalStore } from "react";

export type ChatSurfaceView = "chat" | "code";

/** sessionStorage key: the view this window last showed. */
export const CHAT_VIEW_KEY = "ij.chat.view";

/** The query value of the Code view (`?view=code`). */
export const CODE_VIEW_PARAM = "code";

/** The Code view's own address on the chat page. */
export const CODE_VIEW_HREF = "/chat?view=code";

/** Pathnames that render the chat page (home is a chat). */
export const CHAT_PAGE_PATHS: readonly string[] = ["/", "/chat"];

/** A same-page link to a view: announce it (Next keeps the page's state on
 *  a query-only navigation, so the address alone changes nothing). */
export const CHAT_VIEW_EVENT = "ij:chat-view";

/** True for a pathname the chat page renders. */
export function isChatPagePath(pathname: string | null | undefined): boolean {
  const p = (pathname ?? "").replace(/\/+$/, "") || "/";
  return CHAT_PAGE_PATHS.includes(p);
}

/** The chat page's own link parameters (a thread, a question, a new chat, a
 *  skill, a project …): an address carrying one is a link to the CHAT view,
 *  whatever this window last showed. */
export const CHAT_LINK_PARAMS: readonly string[] = [
  "thread",
  "ask",
  "new",
  "skill",
  "persona",
  "model",
  "about",
  "project",
];

/** The view an address asks for, or null (the address says nothing).
 *  `view=code`, or a `session` / `night` (a coding-session link), is Code;
 *  `view=chat`, or any of the chat's own link parameters, is Chat. */
export function viewFromSearch(search: string | null | undefined): ChatSurfaceView | null {
  let q: URLSearchParams;
  try {
    q = new URLSearchParams(search ?? "");
  } catch {
    return null;
  }
  const v = q.get("view");
  if (v === CODE_VIEW_PARAM) return "code";
  if (v === "chat") return "chat";
  if ((q.get("session") ?? "").trim() || (q.get("night") ?? "").trim()) return "code";
  if (CHAT_LINK_PARAMS.some((k) => q.has(k))) return "chat";
  return null;
}

/** The view a link to the chat page names, else null (not the chat page,
 *  or the link says nothing about the view). */
export function viewOfHref(href: string | null | undefined): ChatSurfaceView | null {
  const h = typeof href === "string" ? href.trim() : "";
  if (!h.startsWith("/")) return null;
  const q = h.indexOf("?");
  const path = q < 0 ? h : h.slice(0, q);
  if (!isChatPagePath(path.split("#")[0])) return null;
  return viewFromSearch(q < 0 ? "" : h.slice(q + 1).split("#")[0]);
}

// ── this window's memory ────────────────────────────────────────────────────

let windowView: ChatSurfaceView | null = null;
const subs = new Set<() => void>();

/** The view this window last showed: module state first (a return within
 *  the app), else sessionStorage (a reload), else null. */
export function rememberedView(): ChatSurfaceView | null {
  if (windowView) return windowView;
  try {
    const saved = window.sessionStorage.getItem(CHAT_VIEW_KEY);
    if (saved === "code" || saved === "chat") return saved;
  } catch {
    /* storage blocked */
  }
  return null;
}

/** The module half only (what the first render may read without a
 *  hydration mismatch: null on a fresh load, exactly as the server). */
export function cachedView(): ChatSurfaceView | null {
  return windowView;
}

/** Remember `view` for this window and tell the sidebar. */
export function keepView(view: ChatSurfaceView): void {
  const changed = windowView !== view;
  windowView = view;
  try {
    // Chat is the default: only Code is written (a window that never
    // opened Code keeps its sessionStorage exactly as before).
    if (view === "code") window.sessionStorage.setItem(CHAT_VIEW_KEY, view);
    else window.sessionStorage.removeItem(CHAT_VIEW_KEY);
  } catch {
    /* the module copy still holds it */
  }
  if (changed) for (const f of subs) f();
}

/** Forget the window's choice (tests; a reset). */
export function resetViewMemory(): void {
  windowView = null;
  for (const f of subs) f();
}

/** The view the chat page shows right now, for the sidebar (which list its
 *  slot holds: chats, or coding sessions). "chat" until the page says. */
export function useChatSurfaceView(): ChatSurfaceView {
  return useSyncExternalStore(
    (f) => {
      subs.add(f);
      return () => {
        subs.delete(f);
      };
    },
    () => windowView ?? "chat",
    () => "chat",
  );
}

/** Put the view in the address bar without a navigation. Chat clears the
 *  Code view's own parameters (`view`, `session`, `night`). */
export function writeViewToAddress(view: ChatSurfaceView): void {
  try {
    const url = new URL(window.location.href);
    if (view === "code") {
      url.searchParams.set("view", CODE_VIEW_PARAM);
    } else {
      url.searchParams.delete("view");
      url.searchParams.delete("session");
      url.searchParams.delete("night");
      // v1.406.0 part 7: the second column of a split (lib/codeSplit).
      url.searchParams.delete("beside");
    }
    window.history.replaceState(window.history.state, "", url.toString());
  } catch {
    /* no history API: the window's memory still holds it */
  }
}

/** A link to a chat-page view pressed while the chat page is open: announce
 *  it (the page switches). Answers whether it was announced. */
export function announceChatViewLink(href: string | null | undefined): boolean {
  if (typeof window === "undefined") return false;
  const view = viewOfHref(href);
  if (!view) return false;
  if (!isChatPagePath(window.location.pathname)) return false;
  window.dispatchEvent(new CustomEvent(CHAT_VIEW_EVENT, { detail: { view } }));
  return true;
}

/** v1.406.0 review: ask an open chat page for `view` outright — the
 *  sidebar's Chat row (`/chat`, an address that names no view) pressed while
 *  the Code view is in front. Without it Next kept the page on the
 *  same-page navigation and Code stayed in front: the row did nothing. (From
 *  home, `/` → `/chat` remounts the page: the surface's own `choose` has set
 *  the window's memory by then.) Answers whether it was announced. */
export function announceChatView(view: ChatSurfaceView): boolean {
  if (typeof window === "undefined") return false;
  if (!isChatPagePath(window.location.pathname)) return false;
  window.dispatchEvent(new CustomEvent(CHAT_VIEW_EVENT, { detail: { view } }));
  return true;
}

/** The view a CHAT_VIEW_EVENT carries, else null. */
export function viewOfEvent(e: Event): ChatSurfaceView | null {
  const d = (e as CustomEvent<{ view?: unknown }>).detail;
  return d && (d.view === "chat" || d.view === "code") ? d.view : null;
}
