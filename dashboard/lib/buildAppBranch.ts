/**
 * v1.441.0 — Build › Code's Branch from here and Rewind to here: the words and
 * what the daemon's view says each tool can do (`branch` of
 * `GET /build/app/sessions/{id}`; hosted/branching.py). Import-free.
 *
 * - `fork`   — "Branch" in the session row's ⋯ (the whole conversation).
 * - `forkAt` — "Branch from here" on one of the user's own messages.
 * - `rewind` — "Rewind to here" on one of them (two presses).
 * - `at`     — the messages (their `at`) the tool can do it from; a message
 *              sent before this version, or one from the tool's own saved
 *              history, offers nothing.
 *
 * An older Iron Jarvis sends no `branch`: `decodeBranch` answers null and the
 * screen shows no action at all.
 */

export interface BranchFacts {
  fork: boolean;
  forkAt: boolean;
  rewind: boolean;
  at: ReadonlySet<string>;
  /** The daemon's line while a Claude Code branch waits for its first message. */
  note: string;
}

export const BRANCH_FROM_HERE = "Branch from here";
export const REWIND_TO_HERE = "Rewind to here";
export const REWIND_CONFIRM = "Rewind? Later messages go";
export const BRANCH_ROW = "Branch";
export const BRANCH_FROM_HERE_TITLE =
  "A new session with the conversation before this message (the original stays as it is); the message goes in its box";
export const REWIND_TO_HERE_TITLE =
  "Drop this message and everything after it from the conversation (press twice). Your files are not changed.";
export const BRANCH_ROW_TITLE = "A new session with this whole conversation, on the same tool, model and account";
export const MESSAGE_MENU_LABEL = "More for this message";

function str(v: unknown, cap: number): string {
  return typeof v === "string" ? v.slice(0, cap) : "";
}

export function decodeBranch(raw: unknown): BranchFacts | null {
  const r = raw && typeof raw === "object" ? (raw as { branch?: unknown }).branch : null;
  if (!r || typeof r !== "object") return null;
  const b = r as Record<string, unknown>;
  const at = Array.isArray(b.at) ? b.at.filter((x): x is string => typeof x === "string").slice(0, 500) : [];
  return {
    fork: b.fork === true,
    forkAt: b.fork_at === true,
    rewind: b.rewind === true,
    at: new Set(at),
    note: str(b.note, 500),
  };
}

/** What a branch or rewind answers: the session it left on screen, the
 *  message's words for the box, and the daemon's line saying what was done. */
export interface BranchAnswer {
  sessionId: string;
  text: string;
  files: string[];
  note: string;
}

export function decodeBranchAnswer(raw: unknown): BranchAnswer | null {
  if (!raw || typeof raw !== "object") return null;
  const r = raw as Record<string, unknown>;
  const s = r.session && typeof r.session === "object" ? (r.session as Record<string, unknown>) : null;
  const id = s ? str(s.id, 200) : "";
  if (!id) return null;
  return {
    sessionId: id,
    text: str(r.text, 100_000),
    files: Array.isArray(r.files) ? r.files.filter((f): f is string => typeof f === "string").slice(0, 20) : [],
    note: str(r.note, 1_000),
  };
}
