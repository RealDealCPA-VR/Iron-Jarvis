/**
 * REWIND a Build session (v1.370.0, IJ harness plan Phase 7) — the screen's
 * half of `GET|POST /build/sessions/{id}/rewind` (daemon `harness/rewind`).
 *
 * Rewinding TO a message puts every file the session's replies changed from
 * that message on back the way it was (through the undo journal), and cuts
 * the conversation back to before that message — later messages too. ALL OR
 * NOTHING: when a file cannot go back (it changed since, its saved copy is
 * gone), the daemon names it and touches nothing. Commands are never undone,
 * and the daemon says so.
 *
 * Decoders whitelist what they read: an older or odd answer reads as "no
 * preview", never as a wrong one.
 */

import { ApiError, get, post } from "@/lib/api";

export interface RewindFile {
  path: string;
  rel: string;
  /** "put back" | "removed" | "renamed back" (the daemon's words). */
  change: string;
}

export interface RewindProblem {
  path: string;
  reason: string;
}

export interface RewindCommand {
  tool: string;
  what: string;
}

export interface RewindPreview {
  to: string;
  question: string;
  turns: string[];
  files: RewindFile[];
  problems: RewindProblem[];
  commands: RewindCommand[];
  canRewind: boolean;
  /** The daemon's sentence about commands ("" when none ran). */
  note: string;
  /** The daemon's refusal sentence ("" when it can rewind). */
  detail: string;
}

export type RewindOutcome =
  | { ok: true; question: string; files: RewindFile[]; note: string; libraryDropped: number }
  | { ok: false; detail: string; problems: RewindProblem[]; restored: string[]; partial: boolean };

const str = (v: unknown): string => (typeof v === "string" ? v : "");
const rec = (v: unknown): Record<string, unknown> =>
  v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : {};
const list = (v: unknown): unknown[] => (Array.isArray(v) ? v : []);

function files(v: unknown): RewindFile[] {
  return list(v)
    .map(rec)
    .filter((f) => str(f.rel) || str(f.path))
    .map((f) => ({ path: str(f.path), rel: str(f.rel) || str(f.path), change: str(f.change) }));
}

function problems(v: unknown): RewindProblem[] {
  return list(v)
    .map(rec)
    .filter((p) => str(p.reason))
    .map((p) => ({ path: str(p.path), reason: str(p.reason) }));
}

function commands(v: unknown): RewindCommand[] {
  return list(v)
    .map(rec)
    .filter((c) => str(c.tool) || str(c.what))
    .map((c) => ({ tool: str(c.tool), what: str(c.what) }));
}

/** A preview answer, or null when it is not one. */
export function decodeRewindPreview(raw: unknown): RewindPreview | null {
  const r = rec(raw);
  if (!Array.isArray(r.files) || !Array.isArray(r.problems)) return null;
  const probs = problems(r.problems);
  return {
    to: str(r.to),
    question: str(r.question),
    turns: list(r.turns).filter((t): t is string => typeof t === "string"),
    files: files(r.files),
    problems: probs,
    commands: commands(r.commands),
    canRewind: r.can_rewind === true && probs.length === 0,
    note: str(r.note),
    detail: str(r.detail),
  };
}

/** What rewinding to ``turnId`` would do — nothing is touched. */
export async function previewRewind(threadId: string, turnId: string): Promise<RewindPreview | null> {
  const raw = await get<unknown>(
    `/build/sessions/${encodeURIComponent(threadId)}/rewind?to=${encodeURIComponent(turnId)}`,
  );
  return decodeRewindPreview(raw);
}

/** Rewind the session to before ``turnId``. A refusal (409) is an outcome,
 *  never a throw: it carries the daemon's sentence and what it names. Other
 *  failures throw. */
export async function rewindTo(threadId: string, turnId: string): Promise<RewindOutcome> {
  try {
    const raw = rec(
      await post<unknown>(`/build/sessions/${encodeURIComponent(threadId)}/rewind`, { to: turnId }),
    );
    return {
      ok: true,
      question: str(raw.question),
      files: files(raw.files),
      note: str(raw.note),
      libraryDropped: typeof raw.library_dropped === "number" ? raw.library_dropped : 0,
    };
  } catch (e) {
    if (e instanceof ApiError && e.status === 409) {
      const body = rec((e as ApiError & { body?: unknown }).body);
      return {
        ok: false,
        detail: str(body.detail) || e.message,
        problems: problems(body.problems),
        restored: list(body.restored).filter((x): x is string => typeof x === "string"),
        partial: body.partial === true,
      };
    }
    throw e;
  }
}

/** One file's line in the confirm, in plain words. */
export function rewindFileWords(f: RewindFile): string {
  const name = f.rel || f.path;
  if (f.change === "removed") return `${name} is removed (this session made it)`;
  if (f.change === "renamed back") return `${name} gets its old name back`;
  return `${name} goes back to how it was`;
}

/** A problem's line: the reason, naming the file when the reason does not. */
export function rewindProblemWords(p: RewindProblem): string {
  const name = (p.path || "").split(/[\\/]/).filter(Boolean).pop() ?? "";
  return name && !p.reason.includes(name) ? `${name}: ${p.reason}` : p.reason;
}
