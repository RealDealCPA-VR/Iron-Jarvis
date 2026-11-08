/**
 * v1.450.0 (Wave D, item 38): the chat composer's words that change while a
 * reply runs, and the fold for a long pasted message.
 *
 * FOLD. A pasted log or document of hundreds of lines turned the box into a
 * wall the user could only scroll inside 160 px of. Past LONG_PASTE_LINES the
 * box stays at its normal height and ONE quiet line under it says how long
 * the message is, with "Show all N lines" (the box grows to most of the
 * window) and "Show less". The fold is a VIEW only: the composer store, and so
 * the text sent, is never touched — pinned by sending a 40-line paste whole.
 *
 * STEER. While a reply runs Enter does not send a new message: it sends a
 * note the running reply reads at its next step, and Ctrl+Enter holds a
 * message until the reply ends. The placeholder says so (from sm up; a phone
 * has no Ctrl key to name).
 *
 * Pure; no imports.
 */

/** Lines after which a message in the box folds. */
export const LONG_PASTE_LINES = 15;

/** The box's height cap while folded (the old cap, about six lines). */
export const FOLDED_BOX_PX = 160;

/** The share of the window the box may take once "Show all" is pressed. */
export const SHOWN_BOX_SHARE = 0.6;

/** How many lines `text` holds (an empty box holds none). */
export function lineCount(text: string): number {
  if (!text) return 0;
  let n = 1;
  for (let i = 0; i < text.length; i++) if (text.charCodeAt(i) === 10) n += 1;
  return n;
}

/** True when the message is long enough to fold. */
export function foldsAt(lines: number): boolean {
  return lines > LONG_PASTE_LINES;
}

/** The fold line's words. */
export function foldWords(lines: number): string {
  return `Long message: ${lines} lines, all of them are sent.`;
}

export function showAllWords(lines: number): string {
  return `Show all ${lines} lines`;
}

export const SHOW_LESS_WORDS = "Show less";

/** The placeholder while a reply runs and Enter steers it. */
export const STEER_PLACEHOLDER_WIDE =
  "Steer Jarvis mid-turn: Enter sends a note to this reply; Ctrl+Enter queues a message";

/** The same on a phone (no keys to name). */
export const STEER_PLACEHOLDER_NARROW = "Steer Jarvis mid-turn…";
