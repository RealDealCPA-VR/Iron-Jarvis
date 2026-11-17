/**
 * v1.518.0 — the owner's chats in OTHER coding tools (Claude Code, Codex), for
 * the sidebar's quiet "Other tools" row.
 *
 * The daemon reads those tools' own files, read-only, and answers names only
 * (`GET /work-record/other-tools`): by tool, then by folder, newest first. A
 * chat a program started, a chat in a temporary folder, and a chat that is
 * already an Iron Jarvis coding session are left out there, not here.
 *
 * Pressing a chat opens it as a coding session on that tool
 * (`POST /build/app/sessions` with the tool's own chat id, the request
 * Build › Code's "saved by …" rows already make), so the next message
 * continues it in the tool it was had in.
 *
 * "New" is per window owner, not per chat: the newest moment this browser saw
 * each tool's list open (localStorage). The first time a tool is seen nothing
 * is "new": a first run must not greet the owner with "346 new".
 *
 * No imports from lib/api: tests mock that module wholesale.
 */

export interface OtherChat {
  id: string;
  title: string;
  /** v1.525.0: where the chat ran, which is where it is opened. Its group's
   *  folder is the project; a chat in Claude Code's own copy of the project
   *  (`<project>\.claude\worktrees\<name>`) ran in that copy. */
  folder: string;
  started: string;
  ended: string;
}

export interface OtherFolder {
  folder: string;
  name: string;
  chats: OtherChat[];
}

export interface OtherTool {
  tool: string;
  label: string;
  total: number;
  folders: OtherFolder[];
}

export const OTHER_TOOLS_PATH = "/work-record/other-tools";
/** Build › Code's own "open a saved conversation" request. */
export const OPEN_SAVED_PATH = "/build/app/sessions";
/** Chats shown per folder before "Show more". */
export const FOLDER_LIMIT = 5;
/** The list is read again when it is opened and this much older. */
export const STALE_MS = 60_000;
/** The first read waits this long after the sidebar appears: it is never
 *  part of a page's first paint. */
export const FIRST_READ_MS = 3_000;
export const SEEN_KEY = "ij_other_tools_seen";

function text(v: unknown, cap = 400): string {
  return typeof v === "string" ? v.slice(0, cap) : "";
}

function obj(v: unknown): Record<string, unknown> | null {
  return v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : null;
}

/** The daemon's answer, checked piece by piece; anything that is not a row is
 *  left out, and a tool or folder left with nothing is left out too. */
export function decodeOtherTools(raw: unknown): OtherTool[] {
  const list = obj(raw)?.tools;
  if (!Array.isArray(list)) return [];
  const out: OtherTool[] = [];
  for (const t of list) {
    const o = obj(t);
    const tool = text(o?.tool, 60).trim();
    if (!o || !tool) continue;
    const folders: OtherFolder[] = [];
    for (const f of Array.isArray(o.folders) ? o.folders : []) {
      const fo = obj(f);
      const folder = text(fo?.folder, 1000).trim();
      if (!fo || !folder) continue;
      const chats: OtherChat[] = [];
      for (const c of Array.isArray(fo.chats) ? fo.chats : []) {
        const co = obj(c);
        const id = text(co?.id, 200).trim();
        const title = text(co?.title, 300).trim();
        if (!co || !id || !title) continue;
        chats.push({
          id,
          title,
          folder: text(co.folder, 1000).trim() || folder,
          started: text(co.started, 60),
          ended: text(co.ended, 60),
        });
      }
      if (chats.length) folders.push({ folder, name: text(fo.name, 200).trim() || folder, chats });
    }
    if (!folders.length) continue;
    out.push({
      tool,
      label: text(o.label, 60).trim() || tool,
      total: folders.reduce((n, f) => n + f.chats.length, 0),
      folders,
    });
  }
  return out;
}

export type Seen = Record<string, string>;

export function readSeen(): Seen {
  try {
    const raw = JSON.parse(window.localStorage.getItem(SEEN_KEY) || "{}");
    const o = obj(raw);
    const out: Seen = {};
    for (const [k, v] of Object.entries(o ?? {})) if (typeof v === "string" && v) out[k] = v;
    return out;
  } catch {
    return {};
  }
}

export function writeSeen(seen: Seen): void {
  try {
    window.localStorage.setItem(SEEN_KEY, JSON.stringify(seen));
  } catch {
    // Storage is a convenience: without it nothing is ever "new".
  }
}

function at(iso: string): number {
  const t = Date.parse(iso);
  return Number.isNaN(t) ? 0 : t;
}

/** The newest moment any of the tool's chats was written. */
export function newestOf(tool: OtherTool): string {
  let best = "";
  for (const f of tool.folders) for (const c of f.chats) if (at(c.ended) > at(best)) best = c.ended;
  return best;
}

/** How many of the tool's chats were written after it was last looked at.
 *  A tool never looked at has none (see the header). */
export function newCount(tool: OtherTool, seen: Seen): number {
  const since = seen[tool.tool];
  if (!since) return 0;
  const from = at(since);
  let n = 0;
  for (const f of tool.folders) for (const c of f.chats) if (at(c.ended) > from) n += 1;
  return n;
}

/** `seen` with every tool not yet in it marked as seen now (its newest
 *  chat), or the same object when nothing was added. */
export function withFirstSight(tools: OtherTool[], seen: Seen): Seen {
  let next = seen;
  for (const t of tools) {
    if (next[t.tool]) continue;
    const newest = newestOf(t);
    if (newest) next = { ...next, [t.tool]: newest };
  }
  return next;
}

/** The request that opens one of these chats as a coding session. */
export function openSavedBody(tool: string, folder: string, id: string) {
  return { harness: tool, folder, harness_session_id: id };
}

/** The id of the session the daemon made (flat, or under `session`). */
export function madeSessionId(raw: unknown): string {
  const o = obj(raw);
  const inner = obj(o?.session);
  return text(o?.id, 200).trim() || text(inner?.id, 200).trim();
}

/** The list without one chat (it became a session and lives in that list
 *  now); a folder or tool left empty goes with it. */
export function withoutChat(tools: OtherTool[], tool: string, id: string): OtherTool[] {
  const out: OtherTool[] = [];
  for (const t of tools) {
    if (t.tool !== tool) {
      out.push(t);
      continue;
    }
    const folders = t.folders
      .map((f) => ({ ...f, chats: f.chats.filter((c) => c.id !== id) }))
      .filter((f) => f.chats.length);
    if (folders.length) out.push({ ...t, folders, total: folders.reduce((n, f) => n + f.chats.length, 0) });
  }
  return out;
}
