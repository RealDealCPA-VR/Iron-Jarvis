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
 *                   every file-writing tool AND the two web tools AND
 *                   (v1.376.0 review) a click or a typed entry in a Build
 *                   session's own browser (`STRICT_ASK_TOOLS`);
 *   approve_for_me  cards the engine's ask tier only. File edits and web
 *                   lookups are `allow` by default, so they go ahead. The
 *                   default, and what an unknown value falls back to;
 *   accept_edits    (v1.406.0 part 2) approve_for_me, and a file edit inside
 *                   the folder never cards (even under a folder's own rule);
 *   auto            (v1.406.0 part 2) approve_for_me's cards, each decided by
 *                   the daemon's rules (`core/auto_approve`) and then one quick
 *                   model check that may only say allow — risky always asks;
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
 *
 * v1.360.0 (the v1.356.0 G81 regression): since "Folders Jarvis may read"
 * exists, "anywhere" is only true while nothing limits reading. The reading
 * sentence is worked out from the SAME facts the Access summary's Reading
 * files row reads (GET /settings/access `reads`): `readScope` is the one
 * decision, `readsLevelLine` the short sentence a level carries, and
 * `permissionLevel(mode, readsLine)` a level with it. `PERMISSION_LEVELS`
 * keeps the desktop default ("anywhere") for a caller that knows nothing
 * about the setting; the chat chip (via lib/readFacts) and the Access
 * summary pass what they know.
 */

export type PermissionMode = "always_ask" | "approve_for_me" | "accept_edits" | "auto" | "yolo";

/** The reading sentence while nothing limits reading: reading never asks
 *  (v1.352.0, G27). Also what a level says while the limit is not known (an
 *  older Iron Jarvis, or before the first answer). */
export const READS_LINE = "Reads files anywhere on this PC without asking";

export type PermissionTone = "neutral" | "warn";

export interface PermissionLevel {
  /** The chip's word for the level. */
  label: string;
  /** What Jarvis does in this level. Since v1.430.0 the menu shows it beside
   *  the row after a pause (PermissionChip's hint), never under the name. */
  description: string;
  /** `warn` = the level that runs things without asking, shown in amber. */
  tone: PermissionTone;
  /** v1.406.0 part 2: the chip's shorter word when the menu's name is long
   *  ("Ask before every action" reads "Ask every time" in the toolbar). */
  chip?: string;
}

/** What each level does, before its reading sentence. */
const LEVEL_DOES: Readonly<Record<PermissionMode, { label: string; does: string; tone: PermissionTone; chip?: string }>> = {
  // v1.377.0 (Phase 6): the words name what v1.376.0's review made it card —
  // a click or a typed entry in a browser (a Build session's own browser,
  // and the browser add-on's acting tools).
  // v1.406.0 part 2 (one switch, Claude Code's shape): renamed — the strictest
  // level is "Ask before every action", and "Ask first" now names the default
  // (it was "Ask when risky"). The wire values never change. v1.420.0 (D3):
  // the default is "Ask before commands" now (below).
  always_ask: {
    label: "Ask before every action",
    does: "Asks before it edits files, uses the internet, runs commands, or clicks or types in a browser.",
    tone: "neutral",
    chip: "Ask every time",
  },
  // v1.420.0 (fix plan C1.8, decision D3): every level is named by what it
  // does, the same words Build › Code's `about` lines use (hosted/claude.py
  // MODE_ABOUT): the default asks before COMMANDS (edits are free), so it is
  // "Ask before commands" — "Ask first" meant the opposite in Build › Code,
  // where it also asks before a file change. The old words still find the
  // level (settings/tools._RETIRED_OPTION_WORDS).
  approve_for_me: {
    label: "Ask before commands",
    does: "Edits files and searches the web on its own, asks before running commands.",
    tone: "neutral",
    chip: "Ask for commands",
  },
  // v1.406.0 part 2 (core/auto_approve on the daemon). v1.420.0: the line
  // says what differs from the default.
  accept_edits: {
    label: "Accept edits",
    does: "Like Ask before commands, and a file edit in this folder never asks, even where the folder's own rules would.",
    tone: "neutral",
  },
  // v1.420.0 (D3) named it "Auto (its own safety check)". v1.430.0 (the
  // user: "just the titles"): the menu shows names only and each meaning on
  // a pause over the row, so the name is "Auto" and the safety check moved
  // into what it does. The old name still finds the level
  // (settings/tools._RETIRED_OPTION_WORDS).
  auto: {
    label: "Auto",
    does: "Decides each step with its own safety check: routine work goes ahead without asking (reading, edits in this folder, running the tests, and anything else it finds routine for what you asked). Always asks before anything that deletes, sends, pays or acts in your browser.",
    tone: "neutral",
  },
  // v1.420.0 (C6.7): the sentence names what it covers (connected apps too)
  // and what still asks (a coding session's commits and pushes on its own
  // branch — harness/git_guard — and the safety cards no posture lifts).
  yolo: {
    label: "Don't ask",
    does: "Runs commands, edits files and uses connected apps without asking; commits and pushes on a session's own branch, and a few safety cards, still ask.",
    tone: "warn",
  },
};

/** v1.406.0 part 2: a coding session's PLAN mode, offered in the same picker
 *  (it is the session's `mode`, not an approval level: picking it keeps the
 *  level, and picking a level turns plan mode off). */
export const PLAN_LEVEL: PermissionLevel = {
  label: "Plan",
  description: "Only reads and writes a plan for you to approve before it changes anything.",
  tone: "neutral",
};

/** One level whose description carries `readsLine` (a `readsLevelLine`;
 *  left out or blank = READS_LINE). An unknown mode reads as the default. */
export function permissionLevel(mode: PermissionMode, readsLine: string = READS_LINE): PermissionLevel {
  const row = LEVEL_DOES[mode] ?? LEVEL_DOES.approve_for_me;
  const reads = (readsLine || "").trim() || READS_LINE;
  const description = row.tone === "warn" ? `${row.does} ${reads}. Use with care` : `${row.does} ${reads}`;
  return { label: row.label, description, tone: row.tone, ...(row.chip ? { chip: row.chip } : {}) };
}

export const PERMISSION_LEVELS: Readonly<Record<PermissionMode, PermissionLevel>> = {
  always_ask: permissionLevel("always_ask"),
  approve_for_me: permissionLevel("approve_for_me"),
  accept_edits: permissionLevel("accept_edits"),
  auto: permissionLevel("auto"),
  yolo: permissionLevel("yolo"),
};

/* ------------------------------------------------------- what it may read */

/** GET /settings/access `reads` (daemon/routes/access_summary.py): `limited`
 *  = something limits reading; `chosen` = the user's "Folders Jarvis may
 *  read" as typed (v1.356.0, G81); `install` = the installation's own limit
 *  is on; `folders` = what the gate allows. An older Iron Jarvis sends some
 *  of these or none. */
export interface ReadFacts {
  limited?: boolean;
  folders?: string[];
  chosen?: string[];
  install?: boolean;
}

/** Where reading reaches, decided ONCE for every sentence about it. */
export type ReadScope =
  | { kind: "unknown" }
  | { kind: "anywhere" }
  | { kind: "chosen"; folders: string[]; install: boolean }
  | { kind: "install"; folders: string[] }
  | { kind: "nothing" };

function nonBlank(list: unknown): string[] {
  return Array.isArray(list) ? list.filter((f): f is string => typeof f === "string" && f.trim() !== "") : [];
}

/** The reading facts in one decision: the user's own folders first (named
 *  as typed), else the install's list, else nothing readable; no limit is
 *  "anywhere"; no facts is "unknown" (never a claim). */
export function readScope(reads: ReadFacts | null | undefined): ReadScope {
  if (!reads || typeof reads !== "object") return { kind: "unknown" };
  if (!reads.limited) return { kind: "anywhere" };
  const chosen = nonBlank(reads.chosen);
  if (chosen.length > 0) return { kind: "chosen", folders: chosen, install: reads.install === true };
  const folders = nonBlank(reads.folders);
  if (folders.length === 0) return { kind: "nothing" };
  return { kind: "install", folders };
}

/** The reading sentence a level carries, from the same facts as the Access
 *  summary's Reading files row. Short on purpose (the chip's menu is narrow):
 *  that row names the folders themselves. Unknown = READS_LINE. */
export function readsLevelLine(reads: ReadFacts | null | undefined): string {
  const scope = readScope(reads);
  switch (scope.kind) {
    case "chosen":
      return `Reads files only in the folders you chose${scope.install ? " (where this install allows)" : ""}, without asking`;
    case "install":
      return "Reads files only in the folders this install allows, without asking";
    case "nothing":
      return "Reads no files, because none of the folders it may read could be found";
    default:
      return READS_LINE;
  }
}

/** Strictest first, the order the menu lists them ("Don't ask" last, with its
 *  warning). A coding session's picker puts Plan between Auto and Don't ask. */
export const PERMISSION_MODES: readonly PermissionMode[] = ["always_ask", "approve_for_me", "accept_edits", "auto", "yolo"];

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
