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
 *
 * v1.543.0 — the folder's files (hosted/checkpoints.py):
 * - `filesAt`    — the messages whose checkpoint of the folder is kept: their
 *                  "Rewind to here…" opens a preview first, and the files can
 *                  go back too (for a tool with no conversation rewind, the
 *                  files only).
 * - `undoRewind` — the newest rewind changed files and can still be undone.
 * An older Iron Jarvis sends neither: no preview, the two-press rewind as before.
 */

export interface BranchFacts {
  fork: boolean;
  forkAt: boolean;
  rewind: boolean;
  at: ReadonlySet<string>;
  /** The daemon's line while a Claude Code branch waits for its first message. */
  note: string;
  /** v1.543.0: the messages with a kept checkpoint of the folder. */
  filesAt: ReadonlySet<string>;
  /** v1.543.0: the newest rewind's file changes can still be undone. */
  undoRewind: boolean;
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

// v1.543.0 — the folder's files.
export const REWIND_TO_HERE_ASK = "Rewind to here…";
export const FILES_BACK_TO_HERE = "Put files back to here…";
export const REWIND_ASK_TITLE =
  "See what a rewind to this message would change, then choose: the conversation, the folder's files, or both";
export const FILES_BACK_TITLE =
  "See which files would go back to how they were when this message started, then choose";
export const REWIND_DIALOG_TITLE = "Rewind to here";
export const FILES_DIALOG_TITLE = "Put the files back";
export const REWIND_BOTH = "Rewind conversation and files";
export const REWIND_CONVERSATION_ONLY = "Conversation only";
export const REWIND_FILES_ONLY = "Files only";
export const REWIND_FILES_PRESS = "Put the files back";
export const REWIND_READING = "Reading the folder…";
export const REWIND_READ_AGAIN = "Read again";
export const REWIND_NOTHING = "The folder's files are already as they were when this message started.";
export const REWIND_FOOT =
  "Files that git ignores are not touched. You can undo the file changes until you send your next message.";
export const UNDO_REWIND = "Undo rewind";
export const UNDO_REWIND_CONFIRM = "Undo? Files go back";
export const UNDO_REWIND_WORDS = "The last rewind changed files in this folder.";
export const UNDO_REWIND_TITLE =
  "Put the folder's files back as they were just before the rewind (press twice). The conversation is not changed.";

export function filesBackWords(n: number): string {
  return n === 1
    ? "1 file goes back to how it was when this message started:"
    : `${n} files go back to how they were when this message started:`;
}
export function filesNewWords(n: number): string {
  return n === 1
    ? "1 file made since then moves to Iron Jarvis's trash:"
    : `${n} files made since then move to Iron Jarvis's trash:`;
}
export function filesLeftWords(n: number): string {
  return n === 1 ? "1 is left as it is:" : `${n} are left as they are:`;
}
export function filesProblemWords(n: number): string {
  return n === 1
    ? "1 file cannot be put back right now, so nothing would change:"
    : `${n} files cannot be put back right now, so nothing would change:`;
}
export function commitsStayWords(n: number): string {
  return n === 1
    ? "1 commit made since then stays in git; git will show the difference as changes."
    : `${n} commits made since then stay in git; git will show the difference as changes.`;
}
export function andMoreWords(n: number): string {
  return `and ${n} more`;
}

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
    filesAt: new Set(
      Array.isArray(b.files_at) ? b.files_at.filter((x): x is string => typeof x === "string").slice(0, 500) : [],
    ),
    undoRewind: b.undo_rewind === true,
  };
}

/** v1.543.0: one file of a rewind's preview that is not simply put back. */
export interface RewindFileNote {
  rel: string;
  why: string;
}

/** v1.543.0: what `GET …/rewind/preview?to=` answers. Counts are exact; the
 *  name lists are the first fifty. */
export interface RewindPlan {
  id: string;
  can: boolean;
  back: string[];
  backCount: number;
  made: string[];
  madeCount: number;
  left: RewindFileNote[];
  leftCount: number;
  problems: RewindFileNote[];
  problemsCount: number;
  commitsSince: number;
  tooMany: boolean;
  /** Why the files cannot go back right now ("" when they can). */
  blocked: string;
  /** This tool can rewind the conversation from this message too. */
  conversation: boolean;
}

function names(v: unknown): string[] {
  return Array.isArray(v) ? v.filter((x): x is string => typeof x === "string").slice(0, 50) : [];
}

function notes(v: unknown): RewindFileNote[] {
  if (!Array.isArray(v)) return [];
  const out: RewindFileNote[] = [];
  for (const x of v.slice(0, 50)) {
    if (x && typeof x === "object") {
      const r = x as Record<string, unknown>;
      if (typeof r.rel === "string") out.push({ rel: r.rel.slice(0, 1000), why: str(r.why, 300) });
    }
  }
  return out;
}

function count(v: unknown, least: number): number {
  return typeof v === "number" && Number.isFinite(v) && v >= least ? Math.floor(v) : least;
}

export function decodeRewindPlan(raw: unknown): RewindPlan | null {
  if (!raw || typeof raw !== "object") return null;
  const r = raw as Record<string, unknown>;
  const id = str(r.id, 80);
  if (!id) return null;
  const back = names(r.back);
  const made = names(r.new);
  const left = notes(r.left);
  const problems = notes(r.problems);
  return {
    id,
    can: r.can === true,
    back,
    backCount: count(r.back_count, back.length),
    made,
    madeCount: count(r.new_count, made.length),
    left,
    leftCount: count(r.left_count, left.length),
    problems,
    problemsCount: count(r.problems_count, problems.length),
    commitsSince: count(r.commits_since, 0),
    tooMany: r.too_many === true,
    blocked: str(r.blocked, 500),
    conversation: r.conversation === true,
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
