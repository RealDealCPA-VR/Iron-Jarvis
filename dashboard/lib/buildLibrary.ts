/**
 * The EXPERIENCE LIBRARY of a project folder (v1.370.0, IJ harness plan
 * Phase 7) — the screen's half of `GET /build/sessions/{id}/library`,
 * `POST /build/library/{id}/accept|decline` and `DELETE /build/library/{id}`
 * (daemon `harness/library`).
 *
 * Once a session has been idle a while after replies that changed files or
 * ran commands, Iron Jarvis may SUGGEST one lesson for the session's folder,
 * and sometimes an edit to one of the folder's harness files. Nothing is kept until the user accepts it: an
 * accepted lesson is read by every later session in that folder; an accepted
 * edit is written into the folder's file at that moment (the daemon refuses
 * in words when the file changed since).
 */

import { ApiError, del, get, post } from "@/lib/api";

export interface LibraryItem {
  id: string;
  kind: "lesson" | "layer_edit";
  status: string;
  /** The lesson, or the one-line reason for an edit. */
  text: string;
  /** An edit's file (`memory.md`) and its place in the folder. */
  file: string;
  path: string;
  before: string;
  after: string;
  newFile: boolean;
  turnId: string;
}

/** When the session's next suggestion can arrive (v1.370.0 review): the
 *  daemon reflects ONCE a session has been idle a while after replies that
 *  did work — never after every reply — so the screen reads the library
 *  again only when one is due or running. */
export interface LibraryReflection {
  /** ISO time the idle wait ends; null when nothing waits for one. */
  dueAt: string | null;
  running: boolean;
}

export interface LibraryView {
  folderName: string;
  lessons: LibraryItem[];
  proposals: LibraryItem[];
  reflection: LibraryReflection;
}

const str = (v: unknown): string => (typeof v === "string" ? v : "");
const rec = (v: unknown): Record<string, unknown> =>
  v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : {};

export function decodeLibraryItem(raw: unknown): LibraryItem | null {
  const r = rec(raw);
  const kind = r.kind === "lesson" || r.kind === "layer_edit" ? r.kind : null;
  if (!kind || !str(r.id)) return null;
  return {
    id: str(r.id),
    kind,
    status: str(r.status),
    text: str(r.text),
    file: str(r.file),
    path: str(r.path) || (str(r.file) ? `.ironjarvis/harness/${str(r.file)}` : ""),
    before: str(r.before),
    after: str(r.after),
    newFile: r.new_file === true,
    turnId: str(r.turn_id),
  };
}

/** A library answer, or null when it is not one. */
export function decodeLibraryView(raw: unknown): LibraryView | null {
  const r = rec(raw);
  if (!Array.isArray(r.proposals) || !Array.isArray(r.lessons)) return null;
  const items = (v: unknown[]) => v.map(decodeLibraryItem).filter((x): x is LibraryItem => x !== null);
  const refl = rec(r.reflection);
  return {
    folderName: str(r.folder_name),
    lessons: items(r.lessons as unknown[]),
    proposals: items(r.proposals as unknown[]).filter((p) => p.status === "proposed"),
    reflection: { dueAt: str(refl.due_at) || null, running: refl.running === true },
  };
}

/** The session's folder library; null when this Iron Jarvis has none. */
export async function fetchSessionLibrary(threadId: string): Promise<LibraryView | null> {
  try {
    return decodeLibraryView(await get<unknown>(`/build/sessions/${encodeURIComponent(threadId)}/library`));
  } catch (e) {
    if (e instanceof ApiError && (e.status === 404 || e.status === 0)) return null;
    throw e;
  }
}

/** Accept a suggestion. Resolves with the daemon's own sentence on a refusal
 *  ("The file changed since…"), "" when it was accepted. */
export async function acceptLibraryItem(id: string): Promise<string> {
  try {
    await post(`/build/library/${encodeURIComponent(id)}/accept`, {});
    return "";
  } catch (e) {
    if (e instanceof ApiError && (e.status === 409 || e.status === 404)) return e.message;
    throw e;
  }
}

/** Decline a suggestion ("" = done, else the daemon's sentence). */
export async function declineLibraryItem(id: string): Promise<string> {
  try {
    await post(`/build/library/${encodeURIComponent(id)}/decline`, {});
    return "";
  } catch (e) {
    if (e instanceof ApiError && (e.status === 409 || e.status === 404)) return e.message;
    throw e;
  }
}

/** Forget a KEPT lesson (it is no longer read by any session); "" = done,
 *  else the daemon's sentence. */
export async function forgetLesson(id: string): Promise<string> {
  try {
    await del(`/build/library/${encodeURIComponent(id)}`);
    return "";
  } catch (e) {
    if (e instanceof ApiError && (e.status === 409 || e.status === 404)) return e.message;
    throw e;
  }
}

/** After a reply ends the library is read once at once and once more after
 *  these delays (the daemon notes the finished reply a moment after its last
 *  frame, which is when its idle wait — `reflection.dueAt` — starts). */
export const LIBRARY_REREAD_MS: readonly number[] = [3000];

/** A suggestion is read for this long after its idle wait ends (the one
 *  request to the model takes a little while). */
export const LIBRARY_DUE_GRACE_MS = 15_000;
/** While a suggestion is being made, the library is read this often. */
export const LIBRARY_RUNNING_MS = 15_000;

/** When to read the library next, from the last answer: `null` = not again
 *  until a reply ends or the session is opened (nothing is due) — never a
 *  poll that runs forever. */
export function nextLibraryRead(view: LibraryView | null, now: number = Date.now()): number | null {
  if (!view) return null;
  if (view.reflection.running) return LIBRARY_RUNNING_MS;
  if (!view.reflection.dueAt) return null;
  const due = Date.parse(view.reflection.dueAt);
  if (!Number.isFinite(due)) return null;
  return Math.max(1000, due - now + LIBRARY_DUE_GRACE_MS);
}
