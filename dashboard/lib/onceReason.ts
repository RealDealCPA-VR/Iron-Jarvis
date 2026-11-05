/**
 * WHY an approval card asks every time (its `once_reason`): the ONE
 * whitelist every reader of an approval frame or a waiting ask goes through,
 * so a reason the card has no words for never reaches it (and a card with
 * no known reason keeps the local-only line, as before).
 *
 * - "git" (v1.375.0): a commit, a push, a change on GitHub, a git setting or
 *   git that throws work away, on a Build session's own branch;
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
 *
 * Kept OUT of lib/useChatStream.ts on purpose: ~50 test files mock that
 * module with a fixed list of exports, and lib/buildHelpers reads this too.
 */

export type OnceReason = "git" | "page" | "command" | "memory" | "guarded" | "privacy" | "undo";

const REASONS: ReadonlySet<string> = new Set(["git", "page", "command", "memory", "guarded", "privacy", "undo"]);

/** The reason as a card reads it, or undefined for anything else. */
export function onceReasonOf(raw: unknown): OnceReason | undefined {
  return typeof raw === "string" && REASONS.has(raw) ? (raw as OnceReason) : undefined;
}
