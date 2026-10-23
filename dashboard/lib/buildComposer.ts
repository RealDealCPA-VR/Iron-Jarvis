/**
 * "/" and "@" in a Build session's box (v1.373.0, IJ harness part 2 Phase 2).
 *
 * The chat page's pieces, pointed at a session: the token rule is
 * `lib/slash` (a "/" or "@" opens only at the start of a word), the open
 * rule `lib/composerMenus`, the folder's files `lib/atMenuFiles` (GET
 * /fs/files on the SESSION's folder — never outside it). What a session adds:
 *
 * - "/" lists the session's own commands — `/plan` (Plan first, the mode
 *   switch the box already has), `/rewind` (the list of messages to go back
 *   to), `/model` (the model menu) — then the folder's own harness skills
 *   (`GET /build/sessions/{id}/skills`) and the user's skills (`GET /skills`,
 *   the chat's list). A picked skill is a chip, sent as `skill` (the user's)
 *   or `folder_skill` (the folder's) with the next message only.
 * - "@" lists the folder's files as you type (the chat's ranking, plus a
 *   loose in-order match). A picked file is a chip, sent as `files`; the
 *   daemon checks each one is inside the folder and reads it like an
 *   attachment.
 * - A waiting message (Ctrl+Enter) keeps its own skill and files.
 *
 * Pure, so the rules are tested on their own.
 */

import { matchProjectEntries, type ProjectListing } from "@/lib/atMenuFiles";
import type { BuildMode } from "@/lib/buildSessions";

/** The session's own "/" commands. No "/compact" yet: Compact comes with a
 *  later phase, and a command that does nothing would be a lie. */
export type CommandId = "plan" | "rewind" | "model";

export interface SessionCommand {
  id: CommandId;
  /** What follows the "/" ("plan"). */
  name: string;
  description: string;
}

export function sessionCommands(mode: BuildMode): SessionCommand[] {
  return [
    {
      id: "plan",
      name: "plan",
      description:
        mode === "plan"
          ? "Stop planning: let it make changes from your next message"
          : "Plan first: it reads and writes a plan for you to approve before it changes anything",
    },
    { id: "rewind", name: "rewind", description: "Go back to before one of your messages" },
    { id: "model", name: "model", description: "Choose the model this session uses" },
  ];
}

/** Where a skill comes from: this session's folder, or the user's own. */
export type SkillSource = "folder" | "yours";

export interface SkillOption {
  name: string;
  description: string;
  source: SkillSource;
}

export interface PickedSkill {
  name: string;
  source: SkillSource;
}

export type SlashRow =
  | { kind: "command"; key: string; command: SessionCommand }
  | { kind: "skill"; key: string; skill: SkillOption };

function holds(text: string, q: string): boolean {
  return text.toLowerCase().includes(q);
}

/**
 * The "/" menu's rows for what is typed after it (`query`, already lower
 * case): the commands, then the folder's skills, then the user's — each kept
 * when its name or description holds the query (the chat's filter). A
 * picked skill is not offered again.
 *
 * NAME matches come first, then rows that match only by their description,
 * each group in that same order (v1.373.0): the commands always led, so a
 * one-letter "/b" + Enter switched Plan first on because its description
 * holds a "b", while a skill NAMED with a "b" sat below it.
 */
export function slashRows(
  query: string,
  commands: readonly SessionCommand[],
  folderSkills: readonly SkillOption[] | null,
  yourSkills: readonly SkillOption[] | null,
  picked: PickedSkill | null = null,
): SlashRow[] {
  const q = query.trim().toLowerCase();
  const named: SlashRow[] = [];
  const described: SlashRow[] = [];
  const place = (row: SlashRow, name: string, description: string) => {
    if (!q || holds(name, q)) named.push(row);
    else if (holds(description, q)) described.push(row);
  };
  for (const c of commands) {
    place({ kind: "command", key: `cmd:${c.id}`, command: c }, c.name, c.description);
  }
  for (const s of [...(folderSkills ?? []), ...(yourSkills ?? [])]) {
    if (picked && picked.name === s.name && picked.source === s.source) continue;
    place({ kind: "skill", key: `skill:${s.source}:${s.name}`, skill: s }, s.name, s.description);
  }
  return [...named, ...described];
}

/** A skills list (`GET /skills` or the session's own) by whitelist. */
export function decodeSkills(raw: unknown, source: SkillSource): SkillOption[] {
  const list = raw && typeof raw === "object" ? (raw as { skills?: unknown }).skills : null;
  if (!Array.isArray(list)) return [];
  const out: SkillOption[] = [];
  for (const s of list) {
    if (!s || typeof s !== "object") continue;
    const { name, description } = s as { name?: unknown; description?: unknown };
    if (typeof name !== "string" || !name.trim()) continue;
    out.push({ name, description: typeof description === "string" ? description : "", source });
  }
  return out;
}

/** The turn body's fields for a picked skill (none when nothing is picked). */
export function skillFields(p: PickedSkill | null | undefined): { skill?: string; folder_skill?: string } {
  if (!p) return {};
  return p.source === "folder" ? { folder_skill: p.name } : { skill: p.name };
}

/** A file picked with "@" from the session's folder. */
export interface AtFile {
  /** Absolute path (what the turn sends). */
  path: string;
  /** Path inside the folder, "/"-separated (what the chip's title says). */
  rel: string;
  name: string;
}

/** How many file rows the "@" menu shows (the rest: "N more, type to narrow"). */
export const AT_FILE_ROWS = 8;

/** Files one message's turn READS — uploads and "@" files together (the
 *  daemon's `chat_turn._MAX_ATTACHMENTS`; it refuses a message with "@"
 *  files past it rather than drop some without a word). A test reads both. */
export const READ_PER_MESSAGE = 4;

/** The sentence when one more file would not be read. */
export const READ_LIMIT_WORDS = `One message reads at most ${READ_PER_MESSAGE} files. Remove one to add another.`;

/** The "@" menu's file rows: the folder's FILES (a session's folder is fixed,
 *  so a folder is never offered) matching `query`, best first, already-picked
 *  ones left out. `more` = how many matched past the rows shown. */
export function atFileRows(
  listing: ProjectListing | null,
  query: string,
  picked: readonly string[],
  max: number = AT_FILE_ROWS,
): { rows: AtFile[]; more: number } {
  const all = matchProjectEntries(listing, query, picked, Infinity, { fuzzy: true }).filter((e) => e.kind === "file");
  return {
    rows: all.slice(0, max).map((e) => ({ path: e.path, rel: e.rel, name: e.name })),
    more: Math.max(0, all.length - max),
  };
}

/** An upload a waiting message keeps (lib's HarnessFile, by value). */
export interface QueuedUpload {
  name: string;
  path: string;
  bytes: number;
  label: string;
}

/** A message waiting for the running reply, with ITS OWN skill and files. */
export interface QueuedItem {
  text: string;
  skill?: PickedSkill | null;
  files?: AtFile[];
  attachments?: QueuedUpload[];
}

function str(v: unknown, cap: number): string {
  return typeof v === "string" ? v.slice(0, cap) : "";
}

/** One waiting message from the session view (`queued`), by whitelist: an
 *  older Iron Jarvis's `{text}` reads as words only. Null when it carries
 *  nothing to send. */
export function decodeQueuedItem(raw: unknown): QueuedItem | null {
  if (!raw || typeof raw !== "object") return null;
  const r = raw as Record<string, unknown>;
  const text = str(r.text, 20_000);
  const item: QueuedItem = { text };
  const sk = r.skill && typeof r.skill === "object" ? (r.skill as Record<string, unknown>) : null;
  if (sk && typeof sk.name === "string" && sk.name.trim()) {
    item.skill = { name: sk.name, source: sk.source === "folder" ? "folder" : "yours" };
  }
  const files: AtFile[] = [];
  for (const f of Array.isArray(r.files) ? r.files : []) {
    const o = f && typeof f === "object" ? (f as Record<string, unknown>) : null;
    const path = str(o?.path, 2000);
    if (!path) continue;
    files.push({ path, rel: str(o?.rel, 2000) || path, name: str(o?.name, 400) || path.split(/[\\/]/).pop() || path });
  }
  if (files.length) item.files = files;
  const ups: QueuedUpload[] = [];
  for (const a of Array.isArray(r.attachments) ? r.attachments : []) {
    const o = a && typeof a === "object" ? (a as Record<string, unknown>) : null;
    const path = str(o?.path, 2000);
    if (!path) continue;
    const name = str(o?.name, 400) || path.split(/[\\/]/).pop() || path;
    const bytes = typeof o?.bytes === "number" && Number.isFinite(o.bytes) && o.bytes >= 0 ? o.bytes : 0;
    ups.push({ name, path, bytes, label: str(o?.label, 400) || name });
  }
  if (ups.length) item.attachments = ups;
  if (!text.trim() && !files.length && !ups.length) return null;
  return item;
}

/** What `PUT /chat/threads/{id}/queue` keeps for one waiting message. */
export function queuedItemBody(item: QueuedItem): Record<string, unknown> {
  return {
    text: item.text,
    ...(item.skill ? { skill: { name: item.skill.name, source: item.skill.source } } : {}),
    ...(item.files?.length ? { files: item.files.map((f) => ({ path: f.path, rel: f.rel, name: f.name })) } : {}),
    ...(item.attachments?.length
      ? { attachments: item.attachments.map((a) => ({ name: a.name, path: a.path, bytes: a.bytes, label: a.label })) }
      : {}),
  };
}

/** The words a waiting message shows: its text, then what rides with it. */
export function queuedWords(item: QueuedItem): string {
  const parts: string[] = [];
  if (item.text.trim()) parts.push(item.text.trim());
  const n = (item.files?.length ?? 0) + (item.attachments?.length ?? 0);
  if (n) parts.push(n === 1 ? "1 file" : `${n} files`);
  if (item.skill) parts.push(`skill ${item.skill.name}`);
  return parts.join(" · ");
}

/** A message that never started goes back in the box BEFORE what the box
 *  holds now (it was written first), never instead of it (v1.373.0 review:
 *  a waiting message refused at send lost its words when the next message
 *  was already being typed). The same words twice are kept once. */
export function withMessageRestored(box: string, text: string): string {
  const back = text.trim();
  const cur = box.trim();
  if (!back || back === cur) return box;
  if (!cur) return text;
  return `${back}\n\n${cur}`;
}

/** The "@" files of a message that never started, beside those the box holds
 *  now: each once, the box's first. */
export function withFilesRestored(box: readonly AtFile[], back: readonly AtFile[]): AtFile[] {
  return [...box, ...back.filter((b) => !box.some((f) => f.path === b.path))];
}
