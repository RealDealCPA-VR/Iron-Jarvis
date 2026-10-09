/**
 * Calm chat W1-4 (v1.326.0): the quiet action row under a chat message.
 *
 * Every control in the row is a GHOST: no border and no fill until the
 * pointer is on it, one 28px square so the row lines up, the theme's hairline
 * fill on hover. Copy, Try again, the 👍 / 👎 pair, read aloud, save to the
 * project and the "‹ 1 / 2 ›" version arrows all use it, so the row reads as
 * one thing instead of a mix of chips and pills.
 *
 * Which row is visible when is decided on the chat page (the newest reply
 * always; older ones on hover, on focus and on touch screens).
 */

/** The 28px ghost square with no hover colour for the icon, for a button
 *  that brings its own (👍 turns green, 👎 red). */
export const REPLY_ACTION_SQUARE =
  "grid h-7 w-7 shrink-0 place-items-center rounded-lg text-zinc-500 transition-colors hover:bg-white/[0.06] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/50 disabled:cursor-not-allowed disabled:opacity-40";

/** One 28px ghost icon button. */
export const REPLY_ACTION_BTN = `${REPLY_ACTION_SQUARE} hover:text-zinc-200`;
