/**
 * The EXPERIENCE LIBRARY of a project folder (v1.370.0, IJ harness plan
 * Phase 7) — the screen's half of `GET /build/sessions/{id}/library`,
 * `POST /build/library/{id}/accept|decline` and `DELETE /build/library/{id}`
 * (daemon `harness/library`).
 *
 * After a reply that changed files or ran commands, Iron Jarvis may SUGGEST
 * one lesson for the session's folder, and sometimes an edit to one of the
 * folder's harness files. Nothing is kept until the user accepts it: an
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

export interface LibraryView {
  folderName: string;
  lessons: LibraryItem[];
  proposals: LibraryItem[];
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
  return {
    folderName: str(r.folder_name),
    lessons: items(r.lessons as unknown[]),
    proposals: items(r.proposals as unknown[]).filter((p) => p.status === "proposed"),
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

/** After a reply ends, the suggestion arrives a little later (one more
 *  request to the model, in the background): the library is read again at
 *  these delays, and no more. */
export const LIBRARY_REREAD_MS: readonly number[] = [4000, 10_000, 20_000, 40_000];
