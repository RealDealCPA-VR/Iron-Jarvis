/**
 * The Build pane chat's calm look (calm chat wave 4, v1.329.0).
 *
 * The chat page's look at pane scale: a reply is prose with no box, your own
 * message is a tinted bubble with no border, and the composer is ONE card
 * holding the box, a quiet Engine chip and a round send button. The class
 * strings live here so the pane and its tests read one definition.
 *
 * Theme tokens only (`ink-*`, `zinc-*`, `accent`, `white` overlays and the
 * `--white` / `--accent-rgb` vars), so the dark Marks, Daylight and the
 * user's own palettes all read right. No literal colours.
 */

import { COMPOSER_CARD_EDGE } from "@/lib/composerChips";

/** A reply: prose on the page. Tables inside it read as part of the text:
 *  hairline rules under each row, no grid, no filled header (the chat page's
 *  REPLY_PROSE rule, scoped to the pane's replies). */
export const PANE_REPLY_PROSE =
  "min-w-0 text-sm leading-relaxed text-zinc-200 " +
  "[&_table]:text-[13px] " +
  "[&_th]:border-x-0 [&_th]:border-t-0 [&_th]:border-white/[0.16] [&_th]:bg-transparent [&_th]:py-1.5 [&_th]:pl-0 [&_th]:pr-3 [&_th]:font-medium [&_th]:text-zinc-400 " +
  "[&_td]:border-x-0 [&_td]:border-t-0 [&_td]:border-white/[0.08] [&_td]:py-1.5 [&_td]:pl-0 [&_td]:pr-3";

/** Your own message: a soft tinted bubble on the right, no border. */
export const PANE_USER_BUBBLE =
  "min-w-0 max-w-[88%] whitespace-pre-wrap break-words rounded-[18px] bg-accent/[0.1] px-3.5 py-2 text-sm leading-relaxed text-zinc-100 [overflow-wrap:anywhere]";

/** The one composer card: the chat page's hairline edge, no border. */
export const PANE_COMPOSER_CARD = `relative flex-col rounded-[18px] bg-ink-800 ${COMPOSER_CARD_EDGE}`;

/** The box inside the card: no border, no fill of its own. */
export const PANE_COMPOSER_BOX =
  "min-h-[44px] w-full resize-none bg-transparent px-3.5 pb-1 pt-2.5 text-sm text-zinc-100 placeholder:text-zinc-500 focus:outline-none disabled:opacity-50";

/** A quiet control: transparent at rest, a soft fill on hover, a ring for a
 *  keyboard user. Used for Open / Undo / Run in terminal / Retry. */
export const PANE_GHOST_BUTTON =
  "inline-flex shrink-0 items-center gap-1 rounded-lg px-2 py-1 text-[11px] text-zinc-400 transition-colors hover:bg-white/[0.06] hover:text-zinc-200 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/50 disabled:cursor-not-allowed disabled:opacity-40";

/** A quiet row (a tool step, a runnable command, a changed file): a faint
 *  fill, no border. */
export const PANE_QUIET_ROW = "rounded-lg bg-white/[0.03] px-2.5 py-1.5";

/** The round send button: quiet until there is something to send. */
export function paneSendClass(ready: boolean): string {
  return `ml-auto grid h-[30px] w-[30px] shrink-0 place-items-center rounded-full p-0 transition-colors ${
    ready ? "btn-accent" : "cursor-not-allowed bg-white/[0.06] text-zinc-500"
  }`;
}
