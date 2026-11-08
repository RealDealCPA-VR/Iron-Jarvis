/**
 * v1.450.0 (Wave D, item 38): a DISABLED press says why.
 *
 * A greyed-out Send, Start or Connect with nothing beside it reads as broken:
 * the user cannot tell "type something first" from "this is not working".
 * Every such press now carries its reason — as its `title` (the tooltip and
 * what a screen reader reads for a disabled control) and, where the reason
 * is not obvious from the empty box itself, as a visible one-line note
 * (components/DisabledNote.tsx).
 *
 * Pure strings; no imports (the chat page, Build › Code and the settings
 * pages all read it).
 */

/** One candidate reason: a sentence, or anything falsy for "not this one". */
export type DisabledReason = string | false | null | undefined | 0;

/** The FIRST reason that applies, in the order given (the most important
 *  first), or null when the press is enabled. */
export function whyDisabled(...reasons: DisabledReason[]): string | null {
  for (const r of reasons) if (typeof r === "string" && r.trim()) return r;
  return null;
}

/** The chat's Send with an empty box. */
export const SEND_NEEDS_WORDS = "Type a message or attach a file first.";

/** Build › Code's Start with an empty box. */
export const START_NEEDS_WORDS = "Describe a task or ask a question first.";

/** A tool with several sign-ins and none picked yet. */
export const START_NEEDS_ACCOUNT = "Choose which account this session runs on first (the Account chip below).";

/** A form press whose required boxes are empty: "Fill in API key to connect." */
export function fillInFirst(labels: readonly string[], action: string): string | null {
  const names = labels.map((l) => l.trim()).filter(Boolean);
  if (!names.length) return null;
  const list =
    names.length === 1
      ? names[0]
      : `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
  return `Fill in ${list} to ${action}.`;
}
