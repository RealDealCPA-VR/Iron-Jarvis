/**
 * WHY an approval card asks every time (its `once_reason`): the ONE
 * whitelist every reader of an approval frame or a waiting ask goes through,
 * so a reason the card has no words for never reaches it (and a card with
 * no known reason keeps the local-only line, as before).
 *
 * - "git" (v1.375.0): a commit, a push, a change on GitHub, a git setting or
 *   git that throws work away, on a Build session's own branch; since
 *   v1.511.0 also everything but a commit in the user's own folder;
 * - "page" (v1.376.0 review): a click in the agent's browser that reads as
 *   deleting, sending or paying;
 * - "command" (v1.381.0): a shell step a project's or a plugin's Claude Code
 *   slash command asked for (src/iron_jarvis/harness/command_shell.py);
 * - "memory" (v1.406.0): archiving, deleting, renaming or moving a note in
 *   the Brain Profile's memory base (src/iron_jarvis/brain/tools.py);
 * - "guarded" (v1.407.2): an edit of Git's own files (.git/**, .gitconfig…),
 *   Iron Jarvis's own rules (.ironjarvis/**) or Claude Code's (.claude/**) —
 *   src/iron_jarvis/core/auto_approve.py `guarded_edit`. A safety card:
 *   Enter never allows it.
 * - "privacy" (v1.435.0 review): turning THIS chat's Local only off from chat
 *   (src/iron_jarvis/settings/records_history.py LOCAL_ONLY_REASON) — what is
 *   written next may then go to a model off this PC. A safety card.
 * - "undo" (v1.435.0 review): undoing one of Iron Jarvis's changes from chat
 *   (records_history UNDO_REASON) — an undo cannot itself be undone. A safety
 *   card.
 * - "looked_up" (v1.479.0): a tool Jarvis brought into the reply by looking
 *   it up (src/iron_jarvis/capability_search/join.py): your words did not
 *   name it, so its card is Allow once / Deny and Enter never allows it.
 * - "address" (fourth review PG1 / C-PG-2): a new place Iron Jarvis's things
 *   go (an outgoing webhook, a model server, the backup copy folder —
 *   src/iron_jarvis/webhooks/tools.py outbound_address_check). A safety card.
 * - "extension" (C-PG-2): adding an extension or an app
 *   (src/iron_jarvis/settings/records_tools.py, records_apps.py). A safety
 *   card.
 *
 * NOT safety cards (Enter allows once, as before), each with the reason the
 * stream lane now sends on EVERY once-only card (fourth review PG-A):
 * "local_only" (this chat is Local only), "locked" (a locked app asks before
 * each use), "night" (the night shift asks), "batch" (several file changes at
 * once), "once" (its own check asks every time, or the plan door). Only
 * "local_only" — or no reason at all, from an older daemon or a listing row —
 * keeps the Local only line.
 *
 * Kept OUT of lib/useChatStream.ts on purpose: ~50 test files mock that
 * module with a fixed list of exports, and lib/buildHelpers reads this too.
 */

/** The reasons whose card Enter never allows (a click, or Tab then Enter). */
export type SafetyOnceReason =
  | "git" | "page" | "command" | "memory" | "guarded" | "privacy" | "undo" | "looked_up"
  | "address" | "extension";

export type OnceReason = SafetyOnceReason | "local_only" | "locked" | "night" | "batch" | "once";

/** The SAFETY reasons (tests/test_chat_history_review_v14350.py reads this list). */
const REASONS: ReadonlySet<string> = new Set([
  "git", "page", "command", "memory", "guarded", "privacy", "undo", "looked_up",
  "address", "extension",
]);

/** Every reason a card reads: the safety ones and the plain ones. */
const KNOWN: ReadonlySet<string> = new Set([
  ...REASONS, "local_only", "locked", "night", "batch", "once",
]);

/** The reason as a card reads it, or undefined for anything else. */
export function onceReasonOf(raw: unknown): OnceReason | undefined {
  return typeof raw === "string" && KNOWN.has(raw) ? (raw as OnceReason) : undefined;
}

/** Whether a reason makes the card a SAFETY card: Enter never allows it
 *  (lib/dockAsk.enterAllows, lib/buildApp.enterAllowsCodeAsk). */
export function isSafetyOnceReason(raw: unknown): raw is SafetyOnceReason {
  return typeof raw === "string" && REASONS.has(raw);
}
