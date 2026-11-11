/**
 * v1.473.0: which rows of a Build › Code conversation are drawn.
 *
 * A long session drew every row it held (thousands), so each streamed word
 * made the browser lay out the whole conversation again and React visit
 * every row (measured: 40–53 ms frames at 600 turns). Only the newest
 * `ROW_WINDOW` rows are drawn; earlier ones wait behind "Show earlier", which
 * draws another window. The cut is moved back to the start of a message when
 * one is near, so a reply is not split from its question.
 */
import type { ConvItem } from "@/lib/buildApp";

/** How many rows are drawn at first, and added by each "Show earlier". */
export const ROW_WINDOW = 120;
/** Fewer earlier rows than this are simply drawn (no press for a handful). */
export const MIN_HIDDEN = 30;
/** How far back the cut may move to land on the start of a message. */
export const SNAP_ROWS = 40;
/** A conversation that grew past this many drawn rows while the reader sat at
 *  the bottom is cut back to one window. */
export const REWINDOW_AT = ROW_WINDOW * 2;

function isMessage(it: ConvItem | undefined): boolean {
  return !!it && it.kind === "user" && !it.carry;
}

/** The first row to draw so that about `size` rows before `end` show. */
export function windowStart(items: readonly ConvItem[], end = items.length, size = ROW_WINDOW): number {
  const target = end - size;
  if (target < MIN_HIDDEN) return 0;
  for (let i = target; i >= Math.max(0, target - SNAP_ROWS); i -= 1) {
    if (isMessage(items[i])) return i;
  }
  return target;
}

/** How many messages the user sent in the rows not drawn. */
export function hiddenMessages(items: readonly ConvItem[], from: number): number {
  let n = 0;
  for (let i = 0; i < Math.min(from, items.length); i += 1) if (isMessage(items[i])) n += 1;
  return n;
}

export const SHOW_EARLIER = "Show earlier";

/** The quiet line above the drawn rows. */
export function hiddenWords(messages: number): string {
  if (messages <= 0) return "Earlier steps are folded away.";
  return messages === 1 ? "1 earlier message" : `${messages.toLocaleString("en-US")} earlier messages`;
}
