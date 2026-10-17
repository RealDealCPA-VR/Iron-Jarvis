/**
 * Lettered choices (v1.348.0, polish wave A; the idea from 21st.dev
 * agent-elements' question card, MIT, no code taken).
 *
 * When an app's question (an MCP elicitation) offers ONE fixed set of
 * choices, the card draws them as rows lettered A, B, C… and the matching
 * letter key picks one. This is the pure half: which field gets letters, the
 * letter for a row, which key is a letter, and which row a letter means.
 * The rows live in components/chat/ElicitationCard; the key is read by
 * components/chat/DockAsk, beside Enter and Esc, under the same rules: only
 * from inside the card, never from a field that takes typing, never in the
 * first moment after a question appears, and it presses the row itself, so
 * a key does exactly what a click does.
 *
 * WHICH FIELD: an ask with exactly ONE choice field gets letters. Two choice
 * fields would leave a letter ambiguous, so a form with several keeps its
 * menus (and its tests); a list longer than MAX_LETTERED stays a menu too,
 * because a long lettered column in the composer's place is not calm.
 */

import type { McpField } from "@/lib/mcpInteract";
// Elements that take typing: a letter typed there is a letter, not a pick.
// ONE selector, shared with the dock's Enter/Esc rules.
import { TAKES_TYPING } from "@/lib/dockAsk";

/** The most rows that get letters (A to J). */
export const MAX_LETTERED = 10;

/** Marks the group of rows a letter chooses from. */
export const CHOICE_GROUP_ATTR = "data-choice-group";
/** Marks one row with its letter ("A"). */
export const CHOICE_LETTER_ATTR = "data-choice-letter";

/** The letter for row `i` (0 → "A"). */
export function choiceLetter(i: number): string {
  return String.fromCharCode(65 + i);
}

/** True for a field whose answer is one of a fixed list. */
export function isChoiceField(f: McpField): boolean {
  return f.type !== "boolean" && Array.isArray(f.enum) && f.enum.length > 0;
}

/** The name of the ONE field that gets lettered rows, or null. */
export function letteredField(fields: readonly McpField[]): string | null {
  const choices = fields.filter(isChoiceField);
  if (choices.length !== 1) return null;
  const only = choices[0];
  return (only.enum?.length ?? 0) <= MAX_LETTERED ? only.name : null;
}


/** The upper-case letter a key press means, or null. Modifier chords (Ctrl,
 *  Alt, Meta), held keys and IME composition are never a pick; Shift is
 *  allowed (Caps Lock and Shift both give "A"). */
export function choiceKey(
  e: Pick<KeyboardEvent, "key" | "ctrlKey" | "altKey" | "metaKey" | "repeat" | "isComposing">,
  target: Element | null,
): string | null {
  if (e.isComposing || e.repeat) return null;
  if (e.ctrlKey || e.altKey || e.metaKey) return null;
  if (!/^[a-z]$/i.test(e.key)) return null;
  if (target) {
    if (target.closest(TAKES_TYPING)) return null;
    if ((target as HTMLElement).isContentEditable) return null;
  }
  return e.key.toUpperCase();
}

/** The row `letter` picks inside `root`: from the group holding `target`
 *  when there is one, else from the card's only group. Null when there is no
 *  such row, the row is disabled, or the card holds several groups. */
export function findChoice(root: Element | null, target: Element | null, letter: string): HTMLElement | null {
  if (!root || !/^[A-Z]$/.test(letter)) return null;
  let group = target?.closest?.(`[${CHOICE_GROUP_ATTR}]`) ?? null;
  if (!group || !root.contains(group)) {
    const groups = root.querySelectorAll(`[${CHOICE_GROUP_ATTR}]`);
    group = groups.length === 1 ? groups[0] : null;
  }
  if (!group) return null;
  const row = group.querySelector<HTMLElement>(`[${CHOICE_LETTER_ATTR}="${letter}"]`);
  if (!row || row.getAttribute("aria-disabled") === "true") return null;
  return row;
}
