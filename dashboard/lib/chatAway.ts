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

/** Readiness 12B review: viewer -> the saved chat (thread id) that mounted
 *  chat page has OPEN ("" = a new, unsaved chat). Set synchronously when the
 *  page opens a chat — before any read answers — so a chat page mounting
 *  again (a reload, `/` ⇄ `/chat`) has claimed its chat before the bell can
 *  read a list in which the daemon, for a moment, sees nobody showing it. */
const showing = new Map<string, string>();

/** Who reads the two maps (the bell) re-reads on a change. */
const listeners = new Set<() => void>();
let version = 0;

function changed(): void {
  version += 1;
  for (const l of [...listeners]) {
    try {
      l();
    } catch {
      /* a listener's fault is its own */
    }
  }
}

/** `useSyncExternalStore` pair for the bell. */
export function subscribeChatHere(cb: () => void): () => void {
  listeners.add(cb);
  return () => {
    listeners.delete(cb);
  };
}

export function chatHereVersion(): number {
  return version;
}

export function noteFollowing(turnId: string, viewer: string): void {
  if (!turnId || !viewer) return;
  const set = following.get(turnId) ?? new Set<string>();
  set.add(viewer);
  following.set(turnId, set);
  changed();
}

export function noteNotFollowing(turnId: string, viewer: string): void {
  const set = following.get(turnId);
  if (!set) return;
  set.delete(viewer);
  if (set.size === 0) following.delete(turnId);
  changed();
}

/** The chat page `viewer` has chat `threadId` open ("" = a new chat). */
export function noteShowing(viewer: string, threadId: string): void {
  if (!viewer) return;
  const t = (threadId || "").trim();
  if (showing.get(viewer) === t) return;
  showing.set(viewer, t);
  changed();
}

/** The chat page `viewer` is gone (unmounted). */
export function noteNotShowing(viewer: string): void {
  if (showing.delete(viewer)) changed();
}

/** Does a chat page in THIS window have saved chat `threadId` open? */
export function threadShownHere(threadId: string): boolean {
  const t = (threadId || "").trim();
  if (!t) return false;
  for (const v of showing.values()) if (v === t) return true;
  return false;
}

/** THE BELL'S RULE (readiness 12B review): is a waiting chat's card in front
 *  of the user in THIS window? A chat page here has that chat open (its card
 *  is on it, or comes back with its rejoin), or — a new chat's first turn,
 *  listed as `chat:turn:<id>` — follows that turn. Then the card is the one
 *  notice, and the bell neither lists nor pings it: the daemon cannot know
 *  that a page mounting again is about to show it (its old page already said
 *  it left; the new one has not rejoined yet). */
export function shownHere(row: { id: string; threadId: string }): boolean {
  if (threadShownHere(row.threadId)) return true;
  const m = /^chat:turn:(.+)$/.exec(row.id || "");
  return !!m && followedHere(m[1]);
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
  const born = generation;
  noteNotFollowing(turnId, viewer);
  try {
    await post(awayPath(turnId), { viewer });
  } catch {
    return;
  }
  // `born`: a test's reset ran while the daemon was still being told.
  if (typeof window === "undefined" || born !== generation) return;
  const timer = setTimeout(() => {
    announcing.delete(timer);
    if (followedHere(turnId)) return;
    try {
      window.dispatchEvent(new Event(CHAT_ASKS_CHANGED_EVENT));
    } catch {
      /* a closed window */
    }
  }, Math.max(0, announceAfterMs));
  announcing.add(timer);
}

/** The announcements pages that left have armed and not made yet, and a count
 *  the tests' reset moves. Both exist only so `__resetChatAway` can take back
 *  what earlier pages armed: a page unmounted mid-turn in one test told the
 *  NEXT test's bell 1.5 s later (a red gate, v1.477.0). Nothing else moves
 *  `generation`, so the app itself behaves exactly as before. */
const announcing = new Set<ReturnType<typeof setTimeout>>();
let generation = 0;

/** v1.455.0 review: the chat page `viewer`'s WINDOW was hidden (minimised,
 *  covered by another window, the screen locked) or shows it again
 *  (`hidden` false) while `turnId` runs. Nobody can see a hidden chat, so the
 *  daemon must not hold back a failed turn's phone line for it ("the chat
 *  already shows it"); the same `away` route, with `back: true` when the page
 *  is seen again. Best-effort, never throws (an older daemon ignores `back`
 *  and simply keeps the page away until it ends). */
export async function windowShown(
  turnId: string,
  viewer: string,
  hidden: boolean,
  post: (path: string, body: Record<string, unknown>) => Promise<unknown>,
): Promise<void> {
  if (!turnId || !viewer) return;
  try {
    await post(awayPath(turnId), hidden ? { viewer } : { viewer, back: true });
  } catch {
    /* best-effort */
  }
}

/** Tests only. Also takes back every announcement a page armed before it. */
export function __resetChatAway(): void {
  generation += 1;
  for (const timer of announcing) clearTimeout(timer);
  announcing.clear();
  following.clear();
  showing.clear();
  changed();
}
