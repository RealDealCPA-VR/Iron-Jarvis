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
 *   the Brain Profile's memory base (src/iron_jarvis/brain/tools.py).
 *
 * Kept OUT of lib/useChatStream.ts on purpose: ~50 test files mock that
 * module with a fixed list of exports, and lib/buildHelpers reads this too.
 */

export type OnceReason = "git" | "page" | "command" | "memory";

const REASONS: ReadonlySet<string> = new Set(["git", "page", "command", "memory"]);

/** The reason as a card reads it, or undefined for anything else. */
export function onceReasonOf(raw: unknown): OnceReason | undefined {
  return typeof raw === "string" && REASONS.has(raw) ? (raw as OnceReason) : undefined;
}
