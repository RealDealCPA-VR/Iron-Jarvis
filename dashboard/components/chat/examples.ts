// Empty-state example prompts for the chat page (v1.198.0).
//
// Why this module exists: the chat page used to hard-code THREE generic
// strings, which undersold the product — a fresh install with just a model
// connected can already read PDFs, redact PII offline, search the web without
// keys, and write real Word documents. Every prompt below maps to a built-in
// capability that auto-arm covers (files / documents / web / images); nothing
// here needs paid media keys, shell access, or extra setup.

/**
 * Curated example prompts. Order here is the curation order, not the display
 * order — display picks a rotating subset via pickExamples().
 *
 * PINNED: CHAT_EXAMPLES[0] is the anchor "What can you do?". The chat page is
 * PRERENDERED, so its deterministic initial render uses CHAT_EXAMPLES.slice(0, 4)
 * — the anchor leading here guarantees the server-rendered chips and the
 * post-mount pickExamples() rotation both start with the same first chip
 * (no visible first-chip swap on hydration).
 */
export const CHAT_EXAMPLES: string[] = [
  "What can you do?",
  "Summarize the files in a folder",
  "Read this PDF and pull out the key numbers",
  "Redact the personal info in a document",
  "Draft a follow-up email to a client",
  "Search the web for today's IRS mileage rate",
  "Turn my notes into a clean Word document",
  "Make a markdown checklist for onboarding a new client",
];

/**
 * Pick `count` example prompts (default 4: 1 anchor + 3 rotating).
 *
 * Contract (v1.198.0):
 * - "What can you do?" is ALWAYS first — a brand-new user's honest first
 *   question is "what can you do", so the anchor never rotates out.
 * - The remaining slots are drawn at random from the rest of CHAT_EXAMPLES
 *   without repeats, so the empty state shows a different facet of the
 *   product on each visit instead of the same three static chips.
 * - Pure function of the module list: every returned prompt is a member of
 *   CHAT_EXAMPLES and the result never contains duplicates. The order of the
 *   rotating picks is unspecified.
 * - Callers should pick ONCE per mount (e.g. useState initializer) so the
 *   set does not shuffle mid-visit.
 */
export function pickExamples(count = 4): string[] {
  const anchor = CHAT_EXAMPLES[0];
  const rest = CHAT_EXAMPLES.slice(1);
  // Fisher–Yates shuffle of a copy, then take what we need.
  for (let i = rest.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [rest[i], rest[j]] = [rest[j], rest[i]];
  }
  const wanted = Math.max(1, Math.min(count, CHAT_EXAMPLES.length));
  return [anchor, ...rest.slice(0, wanted - 1)];
}
