/**
 * v1.449.0 (Wave B, item 16): what a screen reader hears when a chat turn
 * ends.
 *
 * The chat page's live region used to exist only WHILE a reply streamed
 * (`aria-live="polite"` on the live bubble): the words arrived, the region
 * unmounted, and nothing said the reply was ready — a screen-reader user
 * heard tokens go by and then silence. The bell's v1.352.0 rule applies: ONE
 * `role="status"` announcer mounted with the page, EMPTY at first, filled
 * when the turn ends (a region that mounts with its words already in it is
 * often never announced). The page clears it when the next turn starts, so
 * two replies in a row are two changes — and one reply is never read twice,
 * because its words are set once, at the one moment the turn ends.
 *
 * A FAILED turn is announced by the page's own failure line, which is a
 * `role="alert"` (an assertive live region of its own): this announcer says
 * nothing then, or the ear would hear the same sentence twice. A turn the
 * user stopped says "Reply stopped." — not the transcript's own "Stopped."
 * line word for word, so the two are never one and the same text.
 *
 * Pure words; the page passes what it knows at that moment.
 */

/** A turn that ended with a reply. */
export const REPLY_READY_WORDS = "Reply ready.";
/** A turn the user stopped (the transcript's "Stopped." line is the record). */
export const REPLY_STOPPED_WORDS = "Reply stopped.";

/**
 * The announcement for a turn that just ended: nothing when the page's own
 * failure line (an alert) carries the sentence, "Reply stopped." when the
 * user stopped it, else "Reply ready.".
 */
export function replyLiveWords(opts: { stopped: boolean; errorLine: string | null | undefined }): string {
  if ((opts.errorLine ?? "").trim()) return "";
  if (opts.stopped) return REPLY_STOPPED_WORDS;
  return REPLY_READY_WORDS;
}
