/**
 * THE SESSION TAB's readings (v1.404.0; the user, beside a screenshot of
 * Claude Code's own session panel: "add a sessions rail to the coding
 * sessions, next to the other icons shown, and have it display when selected
 * information like the last attachment").
 *
 * Pure words and decoders over what the screen already holds (the session
 * view, its messages), plus the one new read: the programs the session
 * started in the background (`GET /build/sessions/{id}/programs`) and the
 * user's Stop for one (`POST …/programs/{pid}/stop`). Nothing here guesses:
 * a fact the daemon did not say is left out, never filled in.
 */

import { get, post } from "@/lib/api";
import { normalizeIso, timeAgo } from "@/lib/format";
import { openWayFor } from "@/lib/mediaLightbox";

/* ------------------------------------------------------ the last attachment */

/** One message's things that rode beside its words. */
export interface SentThings {
  /** Uploaded pictures and files (absolute paths in the uploads folder). */
  files: string[];
  /** Files picked with "@" in the session's folder. */
  picks: string[];
  /** The skill picked with "/", or "". */
  skill: string;
}

/** The newest message that carried anything, and when. */
export interface LastAttachment extends SentThings {
  /** When it was sent (the message's own stamp), or null. */
  at: string | null;
  /** Its first words (one line, cut), or "" for a message sent without words. */
  words: string;
}

/** The message fields this module reads (the chat page's own shape). */
export interface RailMessage {
  role: string;
  content?: string;
  at?: string;
  steer?: boolean;
  continuation?: boolean;
  attachmentPaths?: unknown;
  filePaths?: unknown;
  skill?: unknown;
}

const strings = (v: unknown): string[] =>
  Array.isArray(v) ? v.filter((x): x is string => typeof x === "string" && x.trim() !== "") : [];

/** What `m` carried, or null when no file rode it (or it is not the user's
 *  own message: a reply, a note sent while it worked, the app's own "carry
 *  on"). A "/" skill alone is not an attachment (review: "/review fix it"
 *  after an upload buried the upload); it is shown with the files it rode
 *  beside. */
export function sentThings(m: RailMessage): SentThings | null {
  if (m.role !== "user" || m.steer === true || m.continuation === true) return null;
  const files = strings(m.attachmentPaths);
  const picks = strings(m.filePaths);
  if (!files.length && !picks.length) return null;
  const skill = typeof m.skill === "string" ? m.skill.trim() : "";
  return { files, picks, skill };
}

/** The words shown for a message: its first line, cut at 120 characters. */
export const WORDS_CHARS = 120;

function firstWords(content: string | undefined): string {
  const line = (content ?? "").split(/\r?\n/).map((l) => l.trim()).find((l) => l) ?? "";
  return line.length > WORDS_CHARS ? `${line.slice(0, WORDS_CHARS - 1).trimEnd()}…` : line;
}

/** The NEWEST message that carried a picture, a file or an "@" file — the
 *  one being sent counts (it is the newest the user did). */
export function lastAttachment(messages: readonly RailMessage[]): LastAttachment | null {
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    const m = messages[i];
    const things = sentThings(m);
    if (!things) continue;
    return { ...things, at: typeof m.at === "string" && m.at ? m.at : null, words: firstWords(m.content) };
  }
  return null;
}

/** How many earlier uploads the tab lists under the last one. */
export const EARLIER_MAX = 10;

/** Uploaded files sent BEFORE the last attachment, newest first, each once
 *  (a file sent twice is listed at its newest), never one the last message
 *  carried. `total` counts them all. */
export function earlierAttachments(
  messages: readonly RailMessage[],
  max = EARLIER_MAX,
): { paths: string[]; total: number } {
  let i = messages.length - 1;
  // Skip down to (and past) the newest message that carried anything.
  for (; i >= 0; i -= 1) if (sentThings(messages[i])) break;
  const newest = i >= 0 ? new Set(sentThings(messages[i])?.files ?? []) : new Set<string>();
  const seen = new Set<string>(newest);
  const all: string[] = [];
  for (i -= 1; i >= 0; i -= 1) {
    for (const p of sentThings(messages[i])?.files ?? []) {
      if (seen.has(p)) continue;
      seen.add(p);
      all.push(p);
    }
  }
  return { paths: all.slice(0, Math.max(0, max)), total: all.length };
}

/** "cart.png" from a path (either slash). */
export function baseName(path: string): string {
  return path.split(/[\\/]/).filter(Boolean).pop() || path;
}

/** "Sent 5m ago" ("Sent just now" inside a minute) — or "" when the stamp
 *  is unknown. */
export function sentWhen(at: string | null, now: number = Date.now()): string {
  if (!at) return "";
  const t = new Date(normalizeIso(at)).getTime();
  if (Number.isNaN(t)) return "";
  if (now - t < 60_000) return "Sent just now";
  return `Sent ${timeAgo(at)}`;
}

/** The path an Open press hands POST /documents/open, or null: a file on this
 *  PC, never an .svg (it can carry script and its default app is often a
 *  browser — lib/mediaLightbox openWayFor's rule). The daemon still decides
 *  what it will open. */
export function openablePath(path: string): string | null {
  const way = openWayFor(path);
  return way?.kind === "file" ? way.path : null;
}

/* -------------------------------------------------------- where it works */

/** The branch line: "ij/fix-login-3a1c from main", with what became of it. */
export function branchWords(git: { branch: string; base: string; merged: boolean; mergedInto: string; removed: boolean } | null): string {
  if (!git) return "Works in the folder directly, on no branch of its own.";
  const from = git.base ? ` from ${git.base}` : "";
  if (git.removed) return `${git.branch}${from} (its folder was removed; the branch is kept)`;
  if (git.merged) return `${git.branch}${from} (merged${git.mergedInto ? ` into ${git.mergedInto}` : ""})`;
  return `${git.branch}${from}`;
}

/** "Follows CLAUDE.md and AGENTS.md" for the instruction files the newest
 *  reply read, or null when it read none (or no reply says). */
export function instructionWords(names: readonly string[]): string | null {
  if (!names.length) return null;
  const list = names.length === 1 ? names[0] : `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
  return `Follows ${list}`;
}

/* --------------------------------------------------------- background */

/** One program the session started in the background (process_start). */
export interface SessionProgram {
  id: string;
  /** The command, masked and cut to one line by the daemon. */
  command: string;
  /** It, or something it started, still runs. */
  running: boolean;
  /** Its command ended but what it started still runs (`start /b …`). */
  leftRunning: boolean;
  /** The user (or the session) stopped it. */
  stopped: boolean;
  /** Its exit code once it ended by itself, else null. */
  exitCode: number | null;
  /** How long it ran (to now while it runs), in seconds. */
  ranS: number;
}

const rec = (v: unknown): Record<string, unknown> | null =>
  v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : null;
const secs = (v: unknown): number =>
  typeof v === "number" && Number.isFinite(v) && v >= 0 ? Math.floor(v) : 0;

export function decodeProgram(raw: unknown): SessionProgram | null {
  const r = rec(raw);
  const id = typeof r?.id === "string" ? r.id.trim().slice(0, 40) : "";
  if (!r || !id) return null;
  const running = r.running === true;
  const stopped = !running && r.stopped === true;
  return {
    id,
    command: typeof r.command === "string" ? r.command.slice(0, 400) : "",
    running,
    leftRunning: running && r.left_running === true,
    stopped,
    exitCode: !running && !stopped && typeof r.exit_code === "number" && Number.isInteger(r.exit_code) ? r.exit_code : null,
    ranS: secs(r.ran_s),
  };
}

/** `GET /build/sessions/{id}/programs`: the list (junk rows dropped) and
 *  `others` — how many of the session's commands left a program running
 *  with no handle to it (`npm run dev &`). */
export interface SessionPrograms {
  programs: SessionProgram[];
  others: number;
}

export function decodePrograms(raw: unknown): SessionPrograms {
  const r = rec(raw);
  const list = r?.programs;
  return {
    programs: Array.isArray(list) ? list.map(decodeProgram).filter((p): p is SessionProgram => p !== null) : [],
    others: secs(r?.others),
  };
}

/** "12 s", "3 min", "1 h 5 min". */
export function durationWords(s: number): string {
  const v = Math.max(0, Math.floor(s));
  if (v < 60) return `${v} s`;
  const m = Math.floor(v / 60);
  if (m < 60) return `${m} min`;
  const h = Math.floor(m / 60);
  const rest = m % 60;
  return rest ? `${h} h ${rest} min` : `${h} h`;
}

/** A program's state in words: "Running · 3 min", "Stopped after 4 s",
 *  "Ended (exit 0) after 12 s". */
export function programWords(p: SessionProgram): string {
  if (p.leftRunning) return `Its command ended; what it started still runs · ${durationWords(p.ranS)}`;
  if (p.running) return `Running · ${durationWords(p.ranS)}`;
  if (p.stopped) return `Stopped after ${durationWords(p.ranS)}`;
  return p.exitCode !== null
    ? `Ended (exit ${p.exitCode}) after ${durationWords(p.ranS)}`
    : `Ended after ${durationWords(p.ranS)}`;
}

export async function fetchPrograms(threadId: string): Promise<SessionPrograms> {
  return decodePrograms(await get<unknown>(`/build/sessions/${encodeURIComponent(threadId)}/programs`));
}

/** "2 commands left programs running. They are stopped when the session is
 *  archived or deleted, or when you quit Iron Jarvis." — or "" for none. Only
 *  windowless programs are counted: a window a command opened is spared. */
export function othersWords(n: number): string {
  if (n <= 0) return "";
  const head = n === 1 ? "1 command left a program running" : `${n} commands left programs running`;
  return `${head}. They are stopped when the session is archived or deleted, or when you quit Iron Jarvis.`;
}

/** The user's Stop: the program and everything it started. The daemon keeps
 *  it listed, as stopped. */
export async function stopProgram(threadId: string, programId: string): Promise<SessionProgram | null> {
  const res = await post<unknown>(
    `/build/sessions/${encodeURIComponent(threadId)}/programs/${encodeURIComponent(programId)}/stop`,
    {},
  );
  return decodeProgram(rec(res)?.program);
}

/** How often the tab reads the programs again while one runs or the
 *  session works (and only while the tab and the window are shown). */
export const PROGRAMS_POLL_MS = 5_000;

/** How often the changed-files count is read again while a reply works. */
export const CHANGES_POLL_MS = 15_000;

/** The Local only line (the chat's own promise, LOCAL_ONLY_TITLE_ON, said of
 *  a session: a cloud model may still be used once, after asking). */
export const LOCAL_ONLY_LINE = "Local only: nothing in this session leaves this PC without asking you.";

/** Helper agents counted as working now. */
export function helpersWorking(helpers: readonly { state: string }[]): number {
  return helpers.filter((h) => h.state === "starting" || h.state === "queued" || h.state === "running" || h.state === "waiting").length;
}
