/**
 * IS THE USER LOOKING AT THIS BUILD SESSION? (v1.374.0, IJ harness part 2
 * Phase 3).
 *
 * A Build turn that ran longer than a minute and ended is said by the bell
 * (`build.turn_finished`) — but not to someone who watched it end: the
 * window SHOWING that session, in front and focused, says nothing (the
 * reply is right there). "Elsewhere in the app" (another page, another
 * session) or "the window in the background" (minimised, behind another
 * program) gets the notice.
 *
 * The session's screen tells this file while it is on screen AND its window
 * has focus (`noteLooking`), and stops when it is not (`stopLooking`). The
 * bell asks `lookingAt(thread)` when the event lands. ACROSS WINDOWS: a
 * popped-out module holds its own bell, and the window that speaks (lib/
 * desktopShell `quietWindow`) may not be the one showing the session — so
 * the fact is also written to localStorage (shared by every window of the
 * app), with a time so a window that closed without saying so stops
 * counting after `LOOKING_FRESH_MS`. A window writes it again every
 * `LOOKING_BEAT_MS` while it looks.
 *
 * IMPORT-FREE (no lib/api): the bell is on every page and ~71 test files mock
 * lib/api wholesale (lib/buildLinks's rule).
 */

export const LOOKING_KEY = "ij.build.looking";
/** A note older than this no longer counts (a window that went away). */
export const LOOKING_FRESH_MS = 20_000;
/** How often a looking window writes its note again. */
export const LOOKING_BEAT_MS = 8_000;

/** This window's own id (two windows may show sessions; only one has focus). */
const WINDOW_ID = `w${Math.random().toString(36).slice(2, 10)}`;
/** The session THIS window shows in front, focused (null = none). */
let here: string | null = null;

interface LookingNote {
  thread: string;
  win: string;
  at: number;
}

function readNote(): LookingNote | null {
  try {
    const raw = window.localStorage.getItem(LOOKING_KEY);
    if (!raw) return null;
    const n = JSON.parse(raw) as Partial<LookingNote>;
    return typeof n?.thread === "string" && typeof n.win === "string" && typeof n.at === "number"
      ? { thread: n.thread, win: n.win, at: n.at }
      : null;
  } catch {
    return null;
  }
}

/** This window shows `thread`, in front and focused. */
export function noteLooking(thread: string, now: number = Date.now()): void {
  here = thread;
  try {
    window.localStorage.setItem(LOOKING_KEY, JSON.stringify({ thread, win: WINDOW_ID, at: now }));
  } catch {
    /* no storage: this window still knows */
  }
}

/** This window no longer shows a session in front (blurred, hidden, left). */
export function stopLooking(): void {
  here = null;
  try {
    const n = readNote();
    if (n && n.win === WINDOW_ID) window.localStorage.removeItem(LOOKING_KEY);
  } catch {
    /* nothing to clear */
  }
}

/** Is the user looking at `thread` right now — in this window (shown, in
 *  front, focused) or in another window of the app that said so recently? */
export function lookingAt(thread: string | null | undefined, now: number = Date.now()): boolean {
  if (!thread) return false;
  try {
    if (
      here === thread &&
      typeof document !== "undefined" &&
      document.visibilityState === "visible" &&
      document.hasFocus()
    ) {
      return true;
    }
  } catch {
    /* a document that cannot say: ask the note */
  }
  const n = readNote();
  if (!n || n.thread !== thread) return false;
  if (n.win === WINDOW_ID) return false; // this window's own note: decided above
  return now - n.at >= 0 && now - n.at < LOOKING_FRESH_MS;
}

/** Review (v1.374.0): the finish notices the user WATCHED end (their event
 *  ids). The bell keeps such a row seen — but a page that is destroyed (the
 *  window closed to the tray) or reloaded forgets that, and its "While you
 *  were away" replay counted the watched turn on the badge again. Kept here,
 *  shared by every window of the app, newest `WATCHED_MAX`. */
export const WATCHED_KEY = "ij.build.watched";
export const WATCHED_MAX = 50;

function readWatched(): string[] {
  try {
    const raw = JSON.parse(window.localStorage.getItem(WATCHED_KEY) || "[]");
    return Array.isArray(raw) ? raw.filter((v): v is string => typeof v === "string") : [];
  } catch {
    return [];
  }
}

/** The bell decided the user watched this finish (`lookingAt` said so). */
export function noteWatched(id: string): void {
  if (!id) return;
  try {
    const kept = readWatched().filter((v) => v !== id);
    kept.push(id);
    window.localStorage.setItem(WATCHED_KEY, JSON.stringify(kept.slice(-WATCHED_MAX)));
  } catch {
    /* no storage: this page still knows (its own seen set) */
  }
}

/** Was this finish watched (by any window of the app)? */
export function wasWatched(id: string | null | undefined): boolean {
  return !!id && readWatched().includes(id);
}

/** Tests only: forget this window's state. */
export function __resetLooking(): void {
  here = null;
}
