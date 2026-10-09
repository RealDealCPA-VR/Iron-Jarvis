/**
 * The chat's permission levels in plain words (Calm chat, v1.327.0).
 *
 * These are the SAME three approval postures the chat's `#chat-approval-mode`
 * select offers (v1.188.0) and the daemon validates
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
 * Order = strictest first, the select's order.
 */

export type PermissionMode = "always_ask" | "approve_for_me" | "yolo";

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
    description: "Asks before it edits files, uses the internet or runs commands",
    tone: "neutral",
  },
  approve_for_me: {
    label: "Ask when risky",
    description: "Edits files and searches the web on its own, asks before running commands",
    tone: "neutral",
  },
  yolo: {
    label: "Don't ask",
    description: "Runs commands and edits files without asking you first. Use with care",
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

/** Text classes for a level's tone. Theme tokens only, so every Mark inks it. */
export function permissionToneText(tone: PermissionTone): string {
  return tone === "warn" ? "text-tone-warn" : "";
}
