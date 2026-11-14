/**
 * The work record (v1.491.0, vision plan leap 3): what Iron Jarvis did, in
 * which folder, and the files it wrote. The Memory page's "Your work, by
 * folder" section reads it from `GET /work-record/folders` and
 * `GET /work-record/entries`.
 *
 * Decoders only keep what the page shows, typed; anything else the daemon
 * sends is dropped, and a row that is not a row is left out. No imports from
 * lib/api (many tests mock that module whole).
 */

export interface WorkFolder {
  folder: string;
  name: string;
  count: number;
  lastAt: string | null;
}

export interface WorkFile {
  name: string;
  path: string;
}

/** v1.492.0: a recap of a finished conversation, written by a model after
 *  the work went quiet and checked against the work's own record. */
export interface WorkRecap {
  lines: string[];
  writtenAt: string | null;
  /** False when the conversation changed after the recap was written. */
  current: boolean;
}

/** v1.492.0: a folder the user told Iron Jarvis to forget. */
export interface ForgottenFolder {
  folder: string;
  name: string;
}

export interface WorkItem {
  id: string;
  kind: string;
  title: string;
  tool: string;
  outcome: string;
  localOnly: boolean;
  archived: boolean;
  files: WorkFile[];
  filesTotal: number;
  note: string;
  updatedAt: string | null;
  /** An address inside the app, or "" when the daemon sent anything else. */
  link: string;
  recap: WorkRecap | null;
}

export const WORK_FOLDERS_PATH = "/work-record/folders";
export const WORK_FORGET_PATH = "/work-record/forget";
export const WORK_REMEMBER_PATH = "/work-record/remember";
/** Recap lines one piece of work shows, at most. */
export const RECAP_LINES_SHOWN = 8;

export function workEntriesPath(folder: string): string {
  return `/work-record/entries?folder=${encodeURIComponent(folder)}&limit=100`;
}

function text(v: unknown): string {
  return typeof v === "string" ? v : "";
}

function count(v: unknown): number {
  return typeof v === "number" && Number.isFinite(v) && v > 0 ? Math.floor(v) : 0;
}

/** Only an address inside the app: one leading slash, never "//host". */
export function insideLink(v: unknown): string {
  const s = text(v);
  return s.startsWith("/") && !s.startsWith("//") && !s.includes("\\") ? s : "";
}

export function decodeRecap(raw: unknown): WorkRecap | null {
  if (!raw || typeof raw !== "object") return null;
  const o = raw as Record<string, unknown>;
  const lines = (Array.isArray(o.lines) ? o.lines : [])
    .filter((l): l is string => typeof l === "string" && l.trim().length > 0)
    .map((l) => l.trim().slice(0, 400))
    .slice(0, RECAP_LINES_SHOWN);
  if (lines.length === 0) return null;
  return { lines, writtenAt: text(o.written_at) || null, current: o.current === true };
}

export function decodeFolders(raw: unknown): {
  folders: WorkFolder[];
  foldersTotal: number;
  total: number;
  building: boolean;
  forgotten: ForgottenFolder[];
} {
  const data = (raw && typeof raw === "object" ? raw : {}) as Record<string, unknown>;
  const rows = Array.isArray(data.folders) ? data.folders : [];
  const folders: WorkFolder[] = [];
  for (const r of rows) {
    if (!r || typeof r !== "object") continue;
    const o = r as Record<string, unknown>;
    const folder = text(o.folder);
    if (!folder) continue;
    folders.push({
      folder,
      name: text(o.name) || folder,
      count: count(o.count),
      lastAt: text(o.last_at) || null,
    });
  }
  const forgotten: ForgottenFolder[] = [];
  for (const r of Array.isArray(data.forgotten) ? data.forgotten : []) {
    if (!r || typeof r !== "object") continue;
    const folder = text((r as Record<string, unknown>).folder);
    if (folder) forgotten.push({ folder, name: text((r as Record<string, unknown>).name) || folder });
  }
  return {
    folders,
    foldersTotal: Math.max(count(data.folders_total), folders.length),
    total: count(data.total),
    building: data.building === true,
    forgotten,
  };
}

export function decodeEntries(raw: unknown): { entries: WorkItem[]; total: number } {
  const data = (raw && typeof raw === "object" ? raw : {}) as Record<string, unknown>;
  const rows = Array.isArray(data.entries) ? data.entries : [];
  const entries: WorkItem[] = [];
  for (const r of rows) {
    if (!r || typeof r !== "object") continue;
    const o = r as Record<string, unknown>;
    const id = text(o.id);
    if (!id) continue;
    const files: WorkFile[] = [];
    for (const f of Array.isArray(o.files) ? o.files : []) {
      if (!f || typeof f !== "object") continue;
      const name = text((f as Record<string, unknown>).name);
      if (name) files.push({ name, path: text((f as Record<string, unknown>).path) });
    }
    entries.push({
      id,
      kind: text(o.kind),
      title: text(o.title) || "(no title)",
      tool: text(o.tool),
      outcome: text(o.outcome),
      localOnly: o.local_only === true,
      archived: o.archived === true,
      files,
      filesTotal: Math.max(count(o.files_total), files.length),
      note: text(o.note),
      updatedAt: text(o.updated_at) || null,
      link: insideLink(o.link),
      recap: decodeRecap(o.recap),
    });
  }
  return { entries, total: Math.max(count(data.total), entries.length) };
}

/** What kind of work a row is, in the app's own words. */
export function kindWords(kind: string, tool: string): string {
  if (kind === "chat") return "Chat";
  if (kind === "run") return "Agent run";
  if (kind === "code" || kind === "tool_code") return tool ? `Coding session (${tool})` : "Coding session";
  return "Work";
}

export function countWords(n: number): string {
  return n === 1 ? "1 piece of work" : `${n} pieces of work`;
}

/** How many file names a row shows before "and N more". */
export const FILES_SHOWN = 5;

export function filesWords(item: WorkItem): string {
  if (item.files.length === 0) return "";
  const shown = item.files.slice(0, FILES_SHOWN).map((f) => f.name);
  const more = item.filesTotal - shown.length;
  return `Files: ${shown.join(", ")}${more > 0 ? `, and ${more} more` : ""}`;
}

export const WORK_HEADING = "Your work, by folder";
export const WORK_ABOUT =
  "Iron Jarvis keeps its own record of what it did in each folder: the chats, coding sessions and agent runs, and the files they wrote. A chat that works in a folder is told what was done there before, so the model answering that chat sees those titles and file names. Work switched to Local only is only told to chats that stay on your network.";
export const WORK_EMPTY =
  "Nothing on record yet. Work in a folder (pick one in a chat or start a coding session) and it shows up here.";
export const WORK_BUILDING = "Still reading your earlier work. This list will grow.";

// v1.492.0: recaps and forgetting a folder.
export const RECAP_ABOUT =
  "When a chat or coding session here goes quiet for 10 minutes, Iron Jarvis writes a short recap of it and checks every line against that conversation before keeping it. Later chats in this same folder are told the recap; chats in other folders never are. You can turn this off in Settings › Memory & you, under Work recaps.";
export const RECAP_STALE = "Written before the latest messages. It is not told to chats until it is written again.";
export const FORGET_LABEL = "Forget this folder";
export const FORGET_CONFIRM = "Forget it?";
export const FORGET_TITLE =
  "Takes this folder, and the folders inside it, out of the work record: the record no longer tells chats what was done here, and no recaps are written. Your chats, sessions and files are not touched. You can undo it below.";
export const REMEMBER_LABEL = "Remember again";
export const FORGOTTEN_HEADING = "Forgotten folders";
export const FORGOTTEN_ABOUT =
  "Iron Jarvis keeps no record of work in these folders and tells no chat about it.";

export function forgotWords(name: string, removed: number): string {
  return removed > 0
    ? `Forgot ${name}: ${countWords(removed)} left the record.`
    : `Forgot ${name}. Nothing was on record there.`;
}
