/**
 * WHERE THE USER WAS, kept while they are elsewhere in the app (v1.384.0).
 *
 * The user's report: "when I briefly switched away and back to the IJ
 * harness, my work jumped, there was a lag getting back to where I was".
 * Leaving the Build view unmounted the session: coming back read the session
 * again (a spinner), rejoined its running reply with a FULL replay of every
 * frame (~840 frames / 1.2 MB on the user's turn) redrawn as it arrived, and
 * put the transcript back at the bottom.
 *
 * This module keeps, per Build session, for as long as this window lives
 * (module state, like `components/terminal/paneHost` keeps terminals — a
 * real reload starts empty):
 *
 *  - the session VIEW last read (painted at once on return, then read again);
 *  - the RUNNING REPLY's state (`CachedTurn`): the stream's folded state
 *    (`StreamSeed`), the session's own folds (helpers, questions, steer
 *    notes) and the RESUME POSITION of the last frame it processed — the
 *    page paints it at once and asks the daemon only for what came after
 *    (`GET /chat/turns/{id}/stream?after=<i>:<c>`, daemon/durable_turns);
 *    dropped when the turn ends;
 *  - the SCROLL position (at the bottom, or where the user was).
 *
 * BOUNDED: at most `MAX_TURNS` replies, `MAX_VIEWS` views and `MAX_SCROLLS`
 * positions, the oldest dropped first. Nothing here is persisted.
 */

import type { StreamSeed } from "./useChatStream";

/** The session's own folds of a running reply (HarnessSession). Opaque
 *  here: kept as the session handed it over. */
export interface CachedPage {
  helpers: unknown[];
  questions: unknown[];
  /** v1.397.0: the running turn's attempts (absent on an older kept reply). */
  attempts?: unknown[];
  steerNotes: string[];
  /** v1.384.0 review: the question the user sent HERE for this reply (the
   *  page's `pending` bubble). The session view a return paints was read
   *  before the send; without it the question popped in above the live reply
   *  once the open read landed. null/absent = none of its own. */
  pending?: unknown;
  /** v1.390.0: the running reply's Timeline as folded so far (lib/
   *  buildTimeline `createLiveTimeline` adopts it back); absent = none. */
  timeline?: unknown;
}

export interface CachedTurn {
  threadId: string;
  turnId: string;
  /** The resume position of the last frame the page processed ("" = none). */
  lastId: string;
  stream: StreamSeed;
  page: CachedPage;
  savedAt: number;
}

export interface CachedScroll {
  top: number;
  atBottom: boolean;
}

export const MAX_TURNS = 8;
export const MAX_VIEWS = 16;
export const MAX_SCROLLS = 32;

const turns = new Map<string, CachedTurn>();
const views = new Map<string, unknown>();
const scrolls = new Map<string, CachedScroll>();
let folders: unknown = null;

function bound<V>(map: Map<string, V>, max: number): void {
  while (map.size > max) {
    const oldest = map.keys().next().value;
    if (oldest === undefined) break;
    map.delete(oldest);
  }
}

/** Keep (or refresh) the running reply of a session. The newest goes last. */
export function keepTurn(entry: Omit<CachedTurn, "savedAt">): void {
  turns.delete(entry.threadId);
  turns.set(entry.threadId, { ...entry, savedAt: Date.now() });
  bound(turns, MAX_TURNS);
}

/** The running reply kept for a session, or null. */
export function turnFor(threadId: string): CachedTurn | null {
  return turns.get(threadId) ?? null;
}

/** The reply ended (or the session is gone): forget it. With `turnId`,
 *  only when that is the reply kept (a newer one is left alone). */
export function dropTurn(threadId: string, turnId?: string): void {
  const kept = turns.get(threadId);
  if (!kept) return;
  if (turnId !== undefined && kept.turnId !== turnId) return;
  turns.delete(threadId);
}

/** v1.384.0 review: the session list answered — every kept reply of a
 *  session it does NOT list as running has ended (or the session is gone):
 *  forget it, whether or not the user ever goes back to that session. The
 *  session on screen is unaffected (it read its kept reply at mount). */
export function dropEndedTurns(running: ReadonlySet<string>): void {
  for (const threadId of [...turns.keys()]) {
    if (!running.has(threadId)) turns.delete(threadId);
  }
}

export function keepView<V>(threadId: string, view: V): void {
  views.delete(threadId);
  views.set(threadId, view);
  bound(views, MAX_VIEWS);
}

export function viewFor<V>(threadId: string): V | null {
  return (views.get(threadId) as V | undefined) ?? null;
}

export function dropView(threadId: string): void {
  views.delete(threadId);
}

export function keepScroll(threadId: string, pos: CachedScroll): void {
  scrolls.delete(threadId);
  scrolls.set(threadId, pos);
  bound(scrolls, MAX_SCROLLS);
}

export function scrollFor(threadId: string): CachedScroll | null {
  return scrolls.get(threadId) ?? null;
}

/** The session list last read (the left pane paints it at once). */
export function keepFolders<F>(list: F): void {
  folders = list;
}

export function cachedFolders<F>(): F | null {
  return (folders as F | null) ?? null;
}

/** Build's view (Terminals or IJ harness) this window last showed. */
let shape: unknown = null;

export function keepShape<S>(s: S): void {
  shape = s;
}

export function cachedShape<S>(): S | null {
  return (shape as S | null) ?? null;
}

/** Tests: forget everything. */
export function resetTurnCache(): void {
  turns.clear();
  views.clear();
  scrolls.clear();
  folders = null;
  shape = null;
}

/** Is a scroll box at its bottom (within a few pixels)? */
export function atBottomOf(el: { scrollTop: number; scrollHeight: number; clientHeight: number }): boolean {
  return el.scrollHeight - el.scrollTop - el.clientHeight < 32;
}
