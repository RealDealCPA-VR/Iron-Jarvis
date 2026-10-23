/**
 * The IJ harness's smaller touches (v1.377.0, IJ harness part 2 Phase 6):
 * the NEXT STEP buttons under a reply, BRANCH FROM A MESSAGE, the left pane's
 * SEARCH, and what a rewound message hands back to the box.
 *
 *  - Next step (`POST /build/sessions/{id}/followups`): the chat's follow-ups
 *    on the session's own model — off unless "Suggest follow-up questions" is
 *    on (Settings), at most two. A press puts the words in the box; it never
 *    sends.
 *  - Branch (`POST /build/sessions/{id}/branch`): a NEW session with the
 *    conversation up to a message, in the same folder, on the same model
 *    and account (a session on its own git branch gets its own new branch
 *    from the same base); nothing of this session changes. From a question:
 *    the conversation before it, and its words and files go in the new
 *    session's box.
 *  - Search: titles and folder names, the chat list's search as the model.
 *
 * Decoders whitelist what they read (an older Iron Jarvis answers 404 or
 * leaves a key out: nothing is shown, never a wrong thing).
 */

import { ApiError, post } from "@/lib/api";
import type { BuildFolder } from "@/lib/buildSessions";

/** At most this many Next step buttons (the daemon caps it too). */
export const MAX_NEXT_STEPS = 2;
const MAX_STEP_CHARS = 120;

const rec = (v: unknown): Record<string, unknown> | null =>
  v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : null;
const str = (v: unknown, max = 2000): string => (typeof v === "string" ? v.slice(0, max) : "");
const paths = (v: unknown): string[] =>
  Array.isArray(v) ? v.filter((p): p is string => typeof p === "string" && p.trim() !== "").slice(0, 20) : [];

/** The daemon's suggestions: strings, trimmed, at most MAX_NEXT_STEPS. */
export function decodeNextSteps(raw: unknown): string[] {
  const list = rec(raw)?.suggestions;
  if (!Array.isArray(list)) return [];
  const out: string[] = [];
  for (const s of list) {
    const t = typeof s === "string" ? s.trim() : "";
    if (t && t.length <= MAX_STEP_CHARS && !out.includes(t)) out.push(t);
    if (out.length >= MAX_NEXT_STEPS) break;
  }
  return out;
}

/** Ask for the Next step suggestions after the newest reply. Never throws:
 *  an older Iron Jarvis, the switch being off or a model failure is []. */
export async function fetchNextSteps(threadId: string): Promise<string[]> {
  try {
    return decodeNextSteps(await post<unknown>(`/build/sessions/${encodeURIComponent(threadId)}/followups`, {}));
  } catch {
    return [];
  }
}

/** What a Next step press does to the box: the words go in (after what is
 *  already typed, on a new line — typed words are never lost). */
export function withNextStep(box: string, step: string): string {
  const cur = box.trimEnd();
  return cur ? `${cur}\n${step}` : step;
}

// ------------------------------------------------------------- branching

export type BranchAt = "reply" | "question";

/** What a branched question hands to the new session's box. */
export interface BranchDraft {
  text: string;
  attachments: string[];
  pickedFiles: string[];
}

export function decodeBranchDraft(raw: unknown): BranchDraft | null {
  const r = rec(raw);
  if (!r) return null;
  const draft = {
    text: str(r.text, 20_000),
    attachments: paths(r.attachments),
    pickedFiles: paths(r.picked_files),
  };
  return draft.text || draft.attachments.length || draft.pickedFiles.length ? draft : null;
}

/** Where a session was branched from (its view's `branched_from`). */
export interface BranchedFrom {
  threadId: string;
  title: string;
  turnId: string;
  at: BranchAt;
  branch: string;
}

export function decodeBranchedFrom(raw: unknown): BranchedFrom | null {
  const r = rec(raw);
  const threadId = str(r?.thread_id, 120).trim();
  if (!r || !threadId) return null;
  return {
    threadId,
    title: str(r.title, 200).trim(),
    turnId: str(r.turn_id, 80),
    at: r.at === "question" ? "question" : "reply",
    branch: str(r.branch, 300),
  };
}

/** The header's line for a branched session. */
export function branchedFromWords(b: BranchedFrom): string {
  const from = b.title ? `Branched from “${b.title}”` : "Branched from another session";
  return b.branch ? `${from}, on its own branch ${b.branch}` : from;
}

/** The control's words (one place; the vocabulary scan reads them). */
export const BRANCH_LABEL = "Branch from here";
export const BRANCH_TITLE_REPLY =
  "Start a new session with the conversation up to this reply. This session does not change.";
export const BRANCH_TITLE_QUESTION =
  "Start a new session with the conversation before this message; the message goes in its box. This session does not change.";

export interface BranchResult {
  id: string;
  draft: BranchDraft | null;
}

/** Start the new session. The daemon's refusal is thrown in its own words. */
export async function branchSession(threadId: string, turnId: string, at: BranchAt): Promise<BranchResult> {
  const raw = rec(
    await post<unknown>(`/build/sessions/${encodeURIComponent(threadId)}/branch`, { turn_id: turnId, at }),
  );
  const id = str(raw?.id, 120).trim();
  if (!id) throw new ApiError("The new session could not be started.", 500);
  return { id, draft: decodeBranchDraft(raw?.draft) };
}

// ------------------------------------------------------------- the search

/** The left pane's search: a folder whose NAME matches keeps every session;
 *  otherwise only the sessions whose TITLE matches; folders left empty are
 *  not shown. Case-insensitive; a blank query is the list as it is. */
export function filterFolders(folders: readonly BuildFolder[], query: string): BuildFolder[] {
  const q = query.trim().toLowerCase();
  if (!q) return [...folders];
  const out: BuildFolder[] = [];
  for (const f of folders) {
    if (f.name.toLowerCase().includes(q)) {
      out.push(f);
      continue;
    }
    const sessions = f.sessions.filter((s) => s.title.toLowerCase().includes(q));
    if (sessions.length) out.push({ ...f, sessions });
  }
  return out;
}

// ------------------------------------------------------------- rewind / files

/** A saved path as a box chip (lib's HarnessFile shape): the file's own name
 *  — "Pasted picture" for a pasted one (its upload name is a stamp). */
export function fileFromPath(path: string): { name: string; path: string; bytes: number; label: string } {
  const name = path.split(/[\\/]/).filter(Boolean).pop() ?? path;
  return { name, path, bytes: 0, label: /^pasted-/i.test(name) ? "Pasted picture" : name };
}

/** An "@" file chip from a saved absolute path and the session's folder. */
export function atFileFromPath(path: string, folder: string): { path: string; rel: string; name: string } {
  const norm = (p: string) => p.replace(/\\/g, "/").replace(/\/+$/, "");
  const base = norm(folder);
  const full = norm(path);
  const rel =
    base && full.toLowerCase().startsWith(`${base.toLowerCase()}/`) ? full.slice(base.length + 1) : full;
  const name = full.split("/").filter(Boolean).pop() ?? full;
  return { path, rel, name };
}
