/**
 * ONE READING OF "WAS THIS CODING REPLY CUT SHORT?" (fourth review CS1-CS3,
 * C-CS-1) — the dashboard twin of the daemon's `harness/reply_end.py`.
 *
 * A reply can end without having finished: its last answer ran out at the
 * model's output limit (`truncated`), or its model calls kept being cut off
 * mid-answer and the route never recovered (`route.cut_off` with `recovered`
 * not true). The screen decides "did this reply finish?" HERE only (its
 * next-step box, its line's "cut off"), so no screen file reads it with a
 * rule of its own (dashboard/__tests__/reply-end-twin-v1534 scans for one).
 *
 * The keys, their order and their words are the daemon's own; the same test
 * reads the Python file and fails when the two differ.
 */

/** The answer ran out of output tokens (`truncated`). */
export const CUT_LENGTH = "length";
/** A model call was cut off mid-answer and not recovered (`route.cut_off`). */
export const CUT_OFF = "cut_off";

export type CutKind = typeof CUT_LENGTH | typeof CUT_OFF;

/** What happened, in the user's words (the daemon's `CUT_WORDS`). */
export const CUT_WORDS: Readonly<Record<CutKind, string>> = {
  [CUT_LENGTH]: "Its answer was cut off at the model's output limit, so it did not finish.",
  [CUT_OFF]: "The model's answers kept being cut off, so it did not finish.",
};

const rec = (v: unknown): Record<string, unknown> | null =>
  v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : null;

/** "length" when the reply's `truncated` is set, "cut_off" when its
 *  `route.cut_off` is an object whose `recovered` is not set, "" otherwise
 *  (junk too). Reads a saved reply line or a `done` frame alike. Pure. */
export function cutShort(d: unknown): CutKind | "" {
  const r = rec(d);
  if (!r) return "";
  if (r.truncated) return CUT_LENGTH;
  const cut = rec(rec(r.route)?.cut_off);
  if (cut && !cut.recovered) return CUT_OFF;
  return "";
}
