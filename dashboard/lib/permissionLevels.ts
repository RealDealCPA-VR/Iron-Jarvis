/**
 * The chat's permission levels in plain words (Calm chat, v1.327.0).
 *
 * These are the SAME three approval postures the chat has offered since
 * v1.188.0 (the composer's `#chat-approval-mode` chip, a select until
 * v1.327.0) and the daemon validates
 * (`chat_turn.APPROVAL_MODES`). Only the words are new: each level is named
 * for what it really does, read off the gate in `routes/chat.py`:
 *
 *   always_ask      cards the engine's ask tier (shell, code, connectors) AND
 *                   every file-writing tool AND the two web tools
 *                   (`STRICT_ASK_TOOLS`);
 *   approve_for_me  cards the engine's ask tier only. File edits and web
 *                   lookups are `allow` by default, so they go ahead. The
 *                   default, and what an unknown value falls back to;
 *   yolo            cards nothing but protected settings (`ALWAYS_CARD_TOOLS`).
 *                   A `deny` stays a deny in every level; no posture lifts it.
 *
 * The keys are the WIRE vocabulary: rename a label freely, never a key.
 * Order = strictest first, the old select's order and the menu's.
 *
 * v1.352.0 (G27): READING is not gated by any level. Every file tool reads
 * through `core/fs_policy` (`fs_read_ok`), which refuses only the folders
 * holding the app's keys and records, plus anything outside
 * `IRONJARVIS_FS_ALLOWLIST` when an install sets one, and no level cards a
 * read. So every level's description says so (`READS_LINE`), and Settings ›
 * Permissions & ledger opens with the same fact (components/settings/
 * AccessSummary.tsx).
 */

export type PermissionMode = "always_ask" | "approve_for_me" | "yolo";

/** The one sentence every level carries: reading never asks (v1.352.0, G27). */
export const READS_LINE = "Reads files anywhere on this PC without asking";

export type PermissionTone = "neutral" | "warn";

export interface PermissionLevel {
  /** The chip's word for the level. */
  label: string;
  /** One line under the label in the menu: what Jarvis does in this level. */
  description: string;
  /** `warn` = the level that runs things without asking, shown in amber. */
  tone: PermissionTone;
}

export const PERMISSION_LEVELS: Readonly<Record<PermissionMode, PermissionLevel>> = {
  always_ask: {
    label: "Ask first",
    description: `Asks before it edits files, uses the internet or runs commands. ${READS_LINE}`,
    tone: "neutral",
  },
  approve_for_me: {
    label: "Ask when risky",
    description: `Edits files and searches the web on its own, asks before running commands. ${READS_LINE}`,
    tone: "neutral",
  },
  yolo: {
    label: "Don't ask",
    description: `Runs commands and edits files without asking you first. ${READS_LINE}. Use with care`,
    tone: "warn",
  },
};

/** Strictest first, the order the menu lists them. */
export const PERMISSION_MODES: readonly PermissionMode[] = ["always_ask", "approve_for_me", "yolo"];

/** The level a conversation starts in, and what an unknown value means. */
export const DEFAULT_PERMISSION_MODE: PermissionMode = "approve_for_me";

/** Coerce anything to a known level. An unknown value (a newer client's mode,
 *  a stale stored string) becomes the DEFAULT, never the no-ask level. */
export function asPermissionMode(raw: unknown): PermissionMode {
  return typeof raw === "string" && (PERMISSION_MODES as readonly string[]).includes(raw)
    ? (raw as PermissionMode)
    : DEFAULT_PERMISSION_MODE;
}

/** Where the chat page keeps the level a NEW chat starts in, on this window
 *  (`app/chat/page.tsx`'s `APPROVAL_MODE_KEY`; a test keeps the two equal).
 *  A chat's own saved setup overrides it once that chat is open. */
export const CHAT_LEVEL_KEY = "ij_chat_approval_mode";

/** The level a new chat starts in on this window, read the way the chat page
 *  reads it (an unknown or missing value is the default). Never throws. */
export function readChatPermissionMode(): PermissionMode {
  try {
    return asPermissionMode(window.localStorage.getItem(CHAT_LEVEL_KEY));
  } catch {
    return DEFAULT_PERMISSION_MODE;
  }
}

/** Text classes for a level's tone. Theme tokens only, so every Mark inks it. */
export function permissionToneText(tone: PermissionTone): string {
  return tone === "warn" ? "text-tone-warn" : "";
}
