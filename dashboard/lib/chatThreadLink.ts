/**
 * v1.477.1 — open a saved chat by its link while the chat page is ALREADY open.
 *
 * A USER BUG: the bell's "Blocked 1 suspicious passage" row, pressed from
 * Chat, went nowhere. The chat page reads `?thread=<id>` only when it first
 * mounts, and Next keeps a page's state when only its query changes, so any
 * `/chat?thread=…` link pressed from inside Chat (the bell, Ctrl K's chat
 * rows) changed the address and nothing else. The page now listens for this
 * event, for `popstate` and for the browser's own "address changed" signal
 * (the v1.445.0 Settings pattern, lib/settingsOpen).
 */

import { isChatPagePath } from "@/lib/chatSurface";

export const CHAT_THREAD_EVENT = "ij:chat-open-thread";

/** The saved chat a `/chat?thread=<id>` (or `/?thread=<id>`) address names,
 *  else "". */
export function threadOfHref(href: string | null | undefined): string {
  if (!href) return "";
  try {
    const url = new URL(href, "http://x");
    if (!isChatPagePath(url.pathname)) return "";
    return (url.searchParams.get("thread") || "").trim();
  } catch {
    return "";
  }
}

/** Tell an open chat page to open the chat `href` names. Does nothing unless
 *  this window is on the chat page and `href` names a chat. Answers whether
 *  it was announced. */
export function announceChatThreadLink(href: string | null | undefined): boolean {
  if (typeof window === "undefined") return false;
  if (!isChatPagePath(window.location.pathname)) return false;
  const thread = threadOfHref(href);
  if (!thread) return false;
  window.dispatchEvent(new CustomEvent<{ thread: string }>(CHAT_THREAD_EVENT, { detail: { thread } }));
  return true;
}

/** The chat a CHAT_THREAD_EVENT carries, else "". */
export function threadOfEvent(e: Event): string {
  const d = (e as CustomEvent<{ thread?: unknown }>).detail;
  return d && typeof d.thread === "string" ? d.thread.trim() : "";
}
