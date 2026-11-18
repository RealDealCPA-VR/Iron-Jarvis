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
  /** v1.528.0: what the app said it did (Claude Chat; a Codex chat Codex
   *  reported on). Data, shown as text. */
  summary: string;
  /** v1.528.0: a web link back to the chat in its own app ("" when none). */
  link: string;
  /** v1.530.0: its tool wrote to it a moment ago (Claude Code, Codex). */
  working: boolean;
  /** v1.530.0: seconds since its tool last wrote to it (-1 = not known). */
  quietS: number;
  /** v1.530.0: its newest step in plain words ("Ran: pytest -q"). */
  doing: string;
}

export interface OtherFolder {
  /** "" is "No folder" (a Claude Chat chat that named none). */
  folder: string;
  name: string;
  chats: OtherChat[];
}

export interface OtherTool {
  tool: string;
  label: string;
  total: number;
  /** v1.528.0: false for Claude Chat: those chats are carried on in Claude,
   *  so a press shows what is known about one instead of opening it. */
  openable: boolean;
  folders: OtherFolder[];
}

/** v1.528.0: an app that can be connected to Iron Jarvis. */
export interface OtherApp {
  app: string;
  label: string;
  installed: boolean;
  connected: boolean;
  /** Connected here, but the app's own list lost the entry. */
  entryMissing: boolean;
  /** v1.538.0 (OT4): not connected, but Iron Jarvis is still in the app's own
   *  list (a Disconnect that could not change its file): Remove finishes it. */
  entryLeft: boolean;
  /** v1.538.0 (MS2): connected, but the app's entry names an older or missing
   *  Iron Jarvis program: Connect again. */
  entryStale: boolean;
  /** v1.538.0 (C-OT-2): connected, and the app has not used it yet. */
  waitingForApp: boolean;
  /** Chats it reported, and when it last did. */
  count: number;
  last: string;
}

/** Which tool's list an app's chats appear in. */
export const APP_TOOL: Record<string, string> = {
  "claude-desktop": "claude-chat",
  codex: "codex",
  "claude-code": "claude-code",
};
/** The line drawn for a connected app that has reported nothing yet. */
export const TOOL_LABEL: Record<string, string> = { "claude-chat": "Claude Chat", codex: "Codex", "claude-code": "Claude Code" };

/** v1.530.0: where the part is drawn, and what it shows there. The chat
 *  list holds conversations (Claude desktop's Chat tab); Chat › Code holds
 *  the coding tools running beside the IJ harness, to be watched. */
export type OtherToolsMode = "chat" | "code";
export const MODE_TOOLS: Record<OtherToolsMode, readonly string[]> = {
  chat: ["claude-chat"],
  code: ["claude-code", "codex"],
};
export const MODE_APPS: Record<OtherToolsMode, readonly string[]> = {
  chat: ["claude-desktop"],
  code: ["claude-code", "codex"],
};
/** A session written to within this long cannot be continued here without
 *  the owner saying it is closed there (the daemon's LIVE_ELSEWHERE_S). */
export const LIVE_ELSEWHERE_S = 600;
/** Chat › Code reads the list again this often while a session is working. */
export const WORKING_REREAD_MS = 15_000;
/** The watch view reads its steps again this often while it is open and
 *  the session is working. */
export const WATCH_REREAD_MS = 4_000;
export const stepsPath = (tool: string, id: string) =>
  `/work-record/other-tools/${encodeURIComponent(tool)}/${encodeURIComponent(id)}/steps`;

export interface WatchStep {
  at: string;
  kind: string;
  words: string;
}

export interface Watch {
  working: boolean;
  quietS: number;
  steps: WatchStep[];
}

export function decodeWatch(raw: unknown): Watch | null {
  const o = obj(raw);
  if (!o) return null;
  const steps: WatchStep[] = [];
  for (const s of Array.isArray(o.steps) ? o.steps : []) {
    const so = obj(s);
    const words = text(so?.words, 400).trim();
    if (so && words) steps.push({ at: text(so.at, 60), kind: text(so.kind, 20), words });
  }
  const quiet = typeof o.quiet_s === "number" && Number.isFinite(o.quiet_s) ? o.quiet_s : -1;
  return { working: o.working === true, quietS: quiet, steps };
}

/** "40 s" / "3 min" / "2 h" for a quiet time. */
export function quietWords(seconds: number): string {
  if (seconds < 0) return "";
  if (seconds < 60) return `${Math.floor(seconds)} s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min`;
  return `${Math.floor(seconds / 3600)} h`;
}

export const OTHER_TOOLS_PATH = "/work-record/other-tools";
export const appConnectPath = (app: string) => `/work-record/other-apps/${encodeURIComponent(app)}/connect`;
export const appDisconnectPath = (app: string) => `/work-record/other-apps/${encodeURIComponent(app)}/disconnect`;
/** What Connect does, said before the press that does it. v1.532.0 (fourth
 *  review OT5): honest about what leaves this PC. What a connected app looks up
 *  (titles, folders, file names, short summaries) becomes part of that app's
 *  chat, which goes to its own model; work for a client and Local only work
 *  are never told to another app (the daemon's outside wall, group 24). */
export const CONNECT_WORDS: Record<string, string> = {
  "claude-desktop":
    "Lets Claude desktop record its chats here and look up your earlier work. What it looks up (titles, folders, file names, short summaries) goes to Claude's model with the rest of that chat; client work and Local only work are never shared. Restart Claude afterwards.",
  codex:
    "Lets Codex add a summary to each chat, look up your earlier work and see what is running in Iron Jarvis. What it looks up (titles, folders, file names, short summaries) goes to Codex's model with the rest of that chat; client work and Local only work are never shared.",
  "claude-code":
    "Lets Claude Code add a summary to each session, look up your earlier work and see what is running in Iron Jarvis, through Claude Code's own setting. What it looks up (titles, folders, file names, short summaries) goes to Claude's model with the rest of that session; client work and Local only work are never shared. New Claude Code sessions use it.",
};
export const DISCONNECT_WORDS = "It stops recording here and can no longer look anything up. What it recorded stays.";
/** v1.538.0 (OT4): the Remove press for an entry left in the app's own list,
 *  and what it says once armed (a second press sends). */
export const REMOVE_PRESS = "Remove";
export const REMOVE_AGAIN = "Remove for good? Press again";

/** v1.538.0 (OT4): Iron Jarvis is still in the app's own list, though it is
 *  not connected. */
export function entryLeftWords(label: string): string {
  return `${label} still lists Iron Jarvis in its settings.`;
}

/** v1.538.0 (MS2, C-OT-2): the connected app's status line. An entry naming
 *  an older program comes first (the app cannot reach Iron Jarvis); then an
 *  app that has not used it since Connect; else "Connected". */
export function appStatusWords(app: OtherApp): string {
  if (app.entryStale) return `Connected, but ${app.label} points at an older Iron Jarvis. Press Connect again.`;
  if (app.waitingForApp) return `Connected · ${app.label} has not used it yet. Restart ${app.label} if it was open.`;
  return `Connected${app.count ? ` · ${app.count} recorded` : ""}`;
}
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
    // Only an explicit false: an older daemon's rows can all be opened.
    const openable = o.openable !== false;
    const folders: OtherFolder[] = [];
    for (const f of Array.isArray(o.folders) ? o.folders : []) {
      const fo = obj(f);
      const folder = text(fo?.folder, 1000).trim();
      // A chat that can be opened needs its folder; a Claude Chat chat may
      // have named none ("No folder").
      if (!fo || (!folder && openable)) continue;
      const chats: OtherChat[] = [];
      for (const c of Array.isArray(fo.chats) ? fo.chats : []) {
        const co = obj(c);
        const id = text(co?.id, 200).trim();
        const title = text(co?.title, 300).trim();
        if (!co || !id || !title) continue;
        const link = text(co.link, 500).trim();
        chats.push({
          id,
          title,
          folder: text(co.folder, 1000).trim() || folder,
          started: text(co.started, 60),
          ended: text(co.ended, 60),
          summary: text(co.summary, 1200).trim(),
          // Only a web address is ever drawn as a link.
          link: /^https:\/\/\S+$/.test(link) ? link : "",
          working: co.working === true,
          quietS: typeof co.quiet_s === "number" && Number.isFinite(co.quiet_s) ? co.quiet_s : -1,
          doing: text(co.doing, 300).trim(),
        });
      }
      if (chats.length) folders.push({ folder, name: text(fo.name, 200).trim() || folder || "No folder", chats });
    }
    if (!folders.length) continue;
    out.push({
      tool,
      label: text(o.label, 60).trim() || tool,
      total: folders.reduce((n, f) => n + f.chats.length, 0),
      openable,
      folders,
    });
  }
  return out;
}

/** v1.528.0: the apps that can be connected, from the same answer. */
export function decodeOtherApps(raw: unknown): OtherApp[] {
  const list = obj(raw)?.apps;
  if (!Array.isArray(list)) return [];
  const out: OtherApp[] = [];
  for (const a of list) {
    const o = obj(a);
    const app = text(o?.app, 40).trim();
    if (!o || !(app in APP_TOOL)) continue;
    out.push({
      app,
      label: text(o.label, 60).trim() || app,
      installed: o.installed === true,
      connected: o.connected === true,
      entryMissing: o.entry_missing === true,
      // v1.538.0: an older daemon sends none of these: absent is false.
      entryLeft: o.entry_left === true,
      entryStale: o.entry_stale === true,
      waitingForApp: o.waiting_for_app === true,
      count: typeof o.count === "number" && Number.isFinite(o.count) ? o.count : 0,
      last: text(o.last, 60),
    });
  }
  return out;
}

/** The connectable app whose chats a tool's list holds, if any. */
export function appForTool(apps: OtherApp[], tool: string): OtherApp | null {
  return apps.find((a) => APP_TOOL[a.app] === tool) ?? null;
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

/** v1.531.0 (the owner: "I can't minimize the other tools"): whether the
 *  "Other tools" part is folded to its heading, kept per place (the chat
 *  list, Chat › Code) in this browser. Open until folded. */
export const FOLDED_KEY = "ij_other_tools_folded";

export function readFolded(mode: OtherToolsMode): boolean {
  try {
    return obj(JSON.parse(window.localStorage.getItem(FOLDED_KEY) || "{}"))?.[mode] === true;
  } catch {
    return false;
  }
}

export function writeFolded(mode: OtherToolsMode, folded: boolean): void {
  try {
    const now = obj(JSON.parse(window.localStorage.getItem(FOLDED_KEY) || "{}")) ?? {};
    window.localStorage.setItem(FOLDED_KEY, JSON.stringify({ ...now, [mode]: folded }));
  } catch {
    // Storage is a convenience: without it the part opens each time.
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

/** The request that opens one of these chats as a coding session.
 *  `closedThere` (v1.530.0): the owner said it is closed in its own app,
 *  for a session written to a moment ago. */
export function openSavedBody(tool: string, folder: string, id: string, closedThere = false) {
  return closedThere
    ? { harness: tool, folder, harness_session_id: id, elsewhere_closed: true }
    : { harness: tool, folder, harness_session_id: id };
}

/** Any session in these tools working now. */
export function anyWorking(tools: OtherTool[]): boolean {
  return tools.some((t) => t.folders.some((f) => f.chats.some((c) => c.working)));
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
