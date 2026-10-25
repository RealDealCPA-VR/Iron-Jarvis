/**
 * Readiness 12B (G05): a chat waiting for your OK is seen WHERE YOU ARE.
 *
 * The chat page never ends its stream when the user moves to another page of
 * the same window — the fetch keeps reading, so the reply still lands and is
 * saved. The daemon therefore counted the turn as watched, and the bell (the
 * one thing on the page the user moved to) never listed a chat parked on its
 * approval card: only the sidebar's dot said so.
 *
 * So the chat page NAMES itself on its stream (the `X-IJ-Viewer` header, a
 * fresh id per mount) and, when it leaves the screen with a turn still running, SAYS
 * so (`POST /chat/turns/{id}/away {viewer}`). The daemon then lists the turn
 * (it is parked and nobody is looking — `TurnLog.looked_at`), and this window's
 * bell reads the list again at once (`CHAT_ASKS_CHANGED_EVENT`) instead of on
 * its next 15-second poll — unless a chat page in THIS window follows the same
 * turn again by then (it came straight back): its card is in front of the
 * user, one notice.
 *
 * Dependency-free on purpose: the bell is on every page, and ~50 test files
 * mock `@/lib/useChatStream` with a fixed export list.
 */

/** This window's bell reads `GET /chat/approvals/pending` again on it. */
export const CHAT_ASKS_CHANGED_EVENT = "ij:chat-asks-changed";

/** How long a page that left waits before telling the bell — long enough
 *  for a chat page that is mounting again (`/` ⇄ `/chat`) to follow the same
 *  turn, so its own card is the notice. */
export const ANNOUNCE_AFTER_MS = 1500;

/** The request header the daemon reads the viewer from (`routes/chat.py`) —
 *  a header, so the stream's address is exactly what it always was. */
export const VIEWER_HEADER = "X-IJ-Viewer";

/** A fresh viewer id for one mounted chat page. */
export function newViewerId(): string {
  const rand = Math.random().toString(36).slice(2, 10);
  return `view-${Date.now().toString(36)}-${rand}`;
}

/** Where a page says it left a turn. */
export function awayPath(turnId: string): string {
  return `/chat/turns/${encodeURIComponent(turnId)}/away`;
}

/** turn id -> the viewers in THIS window following it now. */
const following = new Map<string, Set<string>>();

export function noteFollowing(turnId: string, viewer: string): void {
  if (!turnId || !viewer) return;
  const set = following.get(turnId) ?? new Set<string>();
  set.add(viewer);
  following.set(turnId, set);
}

export function noteNotFollowing(turnId: string, viewer: string): void {
  const set = following.get(turnId);
  if (!set) return;
  set.delete(viewer);
  if (set.size === 0) following.delete(turnId);
}

/** Does a chat page in THIS window follow `turnId` now? */
export function followedHere(turnId: string): boolean {
  return (following.get(turnId)?.size ?? 0) > 0;
}

/** The chat page `viewer` left the screen while `turnId` was running: tell
 *  the daemon (best-effort — an older daemon answers 404 and nothing changes),
 *  then, unless a chat page here follows the same turn again, tell this
 *  window's bell to read the list. Never throws. */
export async function leaveTurn(
  turnId: string,
  viewer: string,
  post: (path: string, body: Record<string, unknown>) => Promise<unknown>,
  announceAfterMs: number = ANNOUNCE_AFTER_MS,
): Promise<void> {
  if (!turnId || !viewer) return;
  noteNotFollowing(turnId, viewer);
  try {
    await post(awayPath(turnId), { viewer });
  } catch {
    return;
  }
  if (typeof window === "undefined") return;
  setTimeout(() => {
    if (followedHere(turnId)) return;
    try {
      window.dispatchEvent(new Event(CHAT_ASKS_CHANGED_EVENT));
    } catch {
      /* a closed window */
    }
  }, Math.max(0, announceAfterMs));
}

/** Tests only. */
export function __resetChatAway(): void {
  following.clear();
}
