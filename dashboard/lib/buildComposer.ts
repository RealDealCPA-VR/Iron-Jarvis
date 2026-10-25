/**
 * "/" and "@" in a Build session's box (v1.373.0, IJ harness part 2 Phase 2).
 *
 * The chat page's pieces, pointed at a session: the token rule is
 * `lib/slash` (a "/" or "@" opens only at the start of a word), the open
 * rule `lib/composerMenus`, the folder's files `lib/atMenuFiles` (GET
 * /fs/files on the SESSION's folder — never outside it). What a session adds:
 *
 * - "/" lists the session's own commands — `/plan` (Plan first, the mode
 *   switch the box already has), `/compact` (v1.374.0: the meter's Compact —
 *   summarize the older turns), `/rewind` (the list of messages to go back
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
import { modelRowName } from "@/lib/onboarding";
import type { BuildMode } from "@/lib/buildSessions";

/** The session's own "/" commands. v1.374.0 (part 2, Phase 3): "/compact"
 *  presses the same Compact the meter offers (it says in words when there is
 *  not enough conversation to summarize yet). v1.379.0: Claude Code's own
 *  built-in commands that have an IJ harness equivalent, under Claude Code's
 *  names (and its aliases): /clear starts a new session on the same folder,
 *  /resume opens the session search, /usage (/cost) says what the session
 *  used, /init asks for a CLAUDE.md — each row says what it really does. */
export type CommandId = "plan" | "compact" | "rewind" | "model" | "clear" | "resume" | "usage" | "init";

export interface SessionCommand {
  id: CommandId;
  /** What follows the "/" ("plan"). */
  name: string;
  description: string;
  /** Other names Claude Code knows it by ("cost" for /usage). */
  aliases?: string[];
  /** What may follow the name ("[what to plan]"). */
  argumentHint?: string;
}

/** The message "/init" sends (an ordinary message: its writes ask as usual). */
export const INIT_PROMPT =
  "Look through this folder and write a CLAUDE.md that explains it to an AI assistant working here: " +
  "what the project is, how it is laid out, how to build, run and test it, and the conventions to follow. " +
  "If a CLAUDE.md or AGENTS.md already exists, improve that file instead of starting over. " +
  "Keep it short and specific to this folder.";

export function sessionCommands(mode: BuildMode, opts: { hasFolder?: boolean } = {}): SessionCommand[] {
  const hasFolder = opts.hasFolder !== false;
  const out: SessionCommand[] = [
    {
      id: "plan",
      name: "plan",
      description:
        mode === "plan"
          ? "Stop planning: let it make changes from your next message"
          : "Plan first: it reads and writes a plan for you to approve before it changes anything",
      argumentHint: "[what to plan]",
    },
    {
      id: "compact",
      name: "compact",
      description: "Summarize the earlier turns so the conversation stays light (the full conversation is kept)",
      // v1.379.0 review: Claude Code's "/compact <instructions>".
      argumentHint: "[what to keep in view]",
    },
    {
      id: "rewind",
      name: "rewind",
      description: "Go back to before one of your messages",
      aliases: ["checkpoint", "undo"],
    },
    { id: "model", name: "model", description: "Choose the model this session uses", argumentHint: "[model]" },
    {
      id: "clear",
      name: "clear",
      description: hasFolder
        ? "Start a new session on this folder (this one stays in the list)"
        : "Start a new session with no folder (this one stays in the list)",
      aliases: ["reset", "new"],
    },
    {
      id: "resume",
      name: "resume",
      description: "Find another session to continue (the search above your sessions)",
      aliases: ["continue"],
    },
    {
      id: "usage",
      name: "usage",
      description: "What this session has used so far: tokens, and the cost when there is one",
      aliases: ["cost", "stats"],
    },
  ];
  if (hasFolder) {
    out.push({
      id: "init",
      name: "init",
      description: "Ask it to write a CLAUDE.md that explains this folder (an ordinary message)",
    });
  }
  return out;
}

/** Where a skill comes from: this session's folder, or the user's own. */
export type SkillSource = "folder" | "yours";

export interface SkillOption {
  name: string;
  description: string;
  source: SkillSource;
  /** v1.379.0: where the app found one of YOUR skills (GET /skills `source`:
   *  builtin, user, claude, codex, custom) — the row says it in words. */
  origin?: string;
  /** v1.379.0: a plugin's skill listed under Claude Code's
   *  `<plugin>:<skill>` name — the app's own skill it runs, and its plugin. */
  invoke?: string;
  plugin?: string;
}

export interface PickedSkill {
  name: string;
  source: SkillSource;
}

/** v1.379.0: one of Claude Code's own custom commands
 *  (GET /build/sessions/{id}/commands) — a command file, or a plugin's
 *  skill under Claude Code's name (`kind: "skill"`, run as `skill`). */
export interface CustomCommand {
  name: string;
  description: string;
  argumentHint: string;
  source: "personal" | "project" | "plugin";
  plugin: string;
  kind: "command" | "skill";
  skill: string;
}

export type SlashRow =
  | { kind: "command"; key: string; command: SessionCommand }
  | { kind: "skill"; key: string; skill: SkillOption }
  | { kind: "custom"; key: string; command: CustomCommand };

function holds(text: string, q: string): boolean {
  return text.toLowerCase().includes(q);
}

/** The commands list, by whitelist (an older Iron Jarvis has none: []). */
export function decodeCommands(raw: unknown): CustomCommand[] {
  const list = raw && typeof raw === "object" ? (raw as { commands?: unknown }).commands : null;
  if (!Array.isArray(list)) return [];
  const out: CustomCommand[] = [];
  for (const c of list.slice(0, 600)) {
    if (!c || typeof c !== "object") continue;
    const r = c as Record<string, unknown>;
    const name = typeof r.name === "string" ? r.name.trim() : "";
    if (!name || /\s/.test(name)) continue;
    const source = r.source === "personal" || r.source === "project" || r.source === "plugin" ? r.source : null;
    if (!source) continue;
    const kind = r.kind === "skill" ? "skill" : "command";
    const skill = typeof r.skill === "string" ? r.skill.trim() : "";
    if (kind === "skill" && !skill) continue;
    out.push({
      name,
      description: typeof r.description === "string" ? r.description.slice(0, 300) : "",
      argumentHint: typeof r.argument_hint === "string" ? r.argument_hint.slice(0, 120) : "",
      source,
      plugin: typeof r.plugin === "string" ? r.plugin.slice(0, 80) : "",
      kind,
      skill,
    });
  }
  return out;
}

/** Where a row comes from, in quiet words ("" = the session's own command). */
export function sourceWords(row: SlashRow): string {
  if (row.kind === "command") return "";
  if (row.kind === "custom") {
    const c = row.command;
    if (c.source === "plugin") return c.plugin ? `from ${c.plugin}` : "from a plugin";
    return c.source === "project" ? "this folder" : "from your commands";
  }
  const s = row.skill;
  if (s.source === "folder") return "this folder";
  if (s.plugin) return `from ${s.plugin}`;
  switch (s.origin) {
    case "claude":
      return "from Claude Code";
    case "codex":
      return "from Codex";
    case "builtin":
      return "built in";
    case "custom":
      return "from your skill folders";
    default:
      return "yours";
  }
}

/** The custom commands as "/" rows: command files as such; a plugin's skill
 *  under Claude Code's name joins your skills in place of the app's own row
 *  for it (one row per skill). */
function withPluginSkills(yours: readonly SkillOption[], custom: readonly CustomCommand[]): SkillOption[] {
  const aliasOf = new Map<string, CustomCommand>();
  for (const c of custom) if (c.kind === "skill" && !aliasOf.has(c.skill)) aliasOf.set(c.skill, c);
  return yours.map((s) => {
    const alias = aliasOf.get(s.name);
    return alias ? { ...s, name: alias.name, invoke: s.name, plugin: alias.plugin } : s;
  });
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
  /** v1.379.0: Claude Code's own custom commands — offered only where
   *  Claude Code recognizes one: at the START of the message. */
  custom: readonly CustomCommand[] | null = null,
): SlashRow[] {
  const q = query.trim().toLowerCase();
  // v1.379.0 review: a row whose NAME is exactly what was typed leads, so
  // Enter on "/review" runs /review — not /review-pr, which sorted first and
  // ran in its place.
  const exact: SlashRow[] = [];
  const named: SlashRow[] = [];
  const described: SlashRow[] = [];
  const place = (row: SlashRow, names: readonly string[], description: string) => {
    if (q && names.some((n) => n.toLowerCase() === q)) exact.push(row);
    else if (!q || names.some((n) => holds(n, q))) named.push(row);
    else if (holds(description, q)) described.push(row);
  };
  // v1.379.0 review: a custom command that a skill (or one of the session's
  // own commands) shares a name with is never reached by typing it — Claude
  // Code's rule: "A skill and a file in .claude/commands/: The skill" — so
  // it is not offered either (its row ran the command the typed name never
  // would).
  const yours = withPluginSkills(yourSkills ?? [], custom ?? []);
  const taken = new Set<string>([
    ...commands.flatMap((c) => [c.name, ...(c.aliases ?? [])]),
    ...(folderSkills ?? []).map((s) => s.name),
    ...yours.flatMap((s) => [s.name, ...(s.invoke ? [s.invoke] : [])]),
  ].map((n) => n.toLowerCase()));
  for (const c of commands) {
    place({ kind: "command", key: `cmd:${c.id}`, command: c }, [c.name, ...(c.aliases ?? [])], c.description);
  }
  // The order the "/" menu reads (v1.379.0): the session's commands, the
  // folder's skills, Claude Code's custom commands, then your skills.
  for (const s of folderSkills ?? []) {
    if (picked && picked.name === s.name && picked.source === s.source) continue;
    place({ kind: "skill", key: `skill:${s.source}:${s.name}`, skill: s }, [s.name], s.description);
  }
  for (const c of custom ?? []) {
    if (c.kind !== "command" || taken.has(c.name.toLowerCase())) continue;
    place({ kind: "custom", key: `custom:${c.name}`, command: c }, [c.name], c.description);
  }
  for (const s of yours) {
    const runs = s.invoke ?? s.name;
    if (picked && picked.name === runs && picked.source === s.source) continue;
    place({ kind: "skill", key: `skill:${s.source}:${s.name}`, skill: s }, [s.name], s.description);
  }
  return [...exact, ...named, ...described];
}

/** v1.379.0 review: "/model <name>" — the one model in the menu called
 *  `name` (its id, "provider::id", or the name the menu shows; any case),
 *  as a choice ("provider::model"); null when none or more than one — then
 *  the menu opens. Two rows of the same name: the one on the session's own
 *  `provider` wins. Only a row that can answer now (`available`). */
export function modelByName(
  rows: readonly { provider: string; model: string; name?: string; label?: string; available?: boolean }[],
  name: string,
  provider = "",
): string | null {
  const q = name.trim().replace(/\s+/g, " ").toLowerCase();
  if (!q) return null;
  const hits = new Map<string, string>();
  for (const r of rows) {
    if (r.available === false || !r.provider || !r.model) continue;
    const said = [r.model, `${r.provider}::${r.model}`, r.label ?? "", modelRowName(r, rows)]
      .map((x) => x.trim().replace(/\s+/g, " ").toLowerCase());
    if (said.includes(q)) hits.set(`${r.provider}::${r.model}`, r.provider);
  }
  if (hits.size === 1) return [...hits.keys()][0];
  const own = [...hits].filter(([, p]) => p === provider);
  return own.length === 1 ? own[0][0] : null;
}

/** A skills list (`GET /skills` or the session's own) by whitelist. */
export function decodeSkills(raw: unknown, source: SkillSource): SkillOption[] {
  const list = raw && typeof raw === "object" ? (raw as { skills?: unknown }).skills : null;
  if (!Array.isArray(list)) return [];
  const out: SkillOption[] = [];
  for (const s of list) {
    if (!s || typeof s !== "object") continue;
    const { name, description, source: origin } = s as { name?: unknown; description?: unknown; source?: unknown };
    if (typeof name !== "string" || !name.trim()) continue;
    out.push({
      name,
      description: typeof description === "string" ? description : "",
      source,
      ...(source === "yours" && typeof origin === "string" && origin ? { origin } : {}),
    });
  }
  return out;
}

/** The skill a pick or a typed name runs (a plugin's skill listed under
 *  Claude Code's name runs the app's own skill of that name). */
export function pickedOf(s: SkillOption): PickedSkill {
  return { name: s.invoke ?? s.name, source: s.source };
}

// -------------------------------------------------- a typed "/name words"

/** A message that STARTS with "/name" (Claude Code recognizes a command only
 *  at the start of a message): the name, the words after it (its
 *  arguments) and whether a space already follows the name. Null when the
 *  message does not start with "/", or the "name" holds another "/" (a path
 *  such as /etc/hosts is ordinary text). */
export function leadingSlash(text: string): { name: string; args: string; hasSpace: boolean } | null {
  const m = /^\s*\/([^\s/]+)(\s+([\s\S]*))?$/.exec(text);
  if (!m) return null;
  return { name: m[1], args: (m[3] ?? "").trim(), hasSpace: m[2] !== undefined };
}

export type TypedTarget =
  | { kind: "command"; command: SessionCommand; args: string }
  | { kind: "skill"; skill: SkillOption; args: string }
  | { kind: "custom"; command: CustomCommand; args: string };

/** What a typed "/name words" runs, by its EXACT name (case-insensitive):
 *  the session's own commands (and Claude Code's aliases for them), then the
 *  folder's skills, then your skills (a plugin's skill also by Claude Code's
 *  `<plugin>:<skill>` name), then Claude Code's custom commands — a skill
 *  wins over a command file of the same name, as in Claude Code. Null for an
 *  unknown name: the message goes as ordinary text. */
export function resolveTyped(
  text: string,
  commands: readonly SessionCommand[],
  folderSkills: readonly SkillOption[] | null,
  yourSkills: readonly SkillOption[] | null,
  custom: readonly CustomCommand[] | null,
): TypedTarget | null {
  const lead = leadingSlash(text);
  if (!lead) return null;
  const name = lead.name.toLowerCase();
  const args = lead.args;
  const cmd = commands.find((c) => c.name === name || (c.aliases ?? []).includes(name));
  if (cmd) return { kind: "command", command: cmd, args };
  const folder = (folderSkills ?? []).find((s) => s.name.toLowerCase() === name);
  if (folder) return { kind: "skill", skill: folder, args };
  const yours = withPluginSkills(yourSkills ?? [], custom ?? []);
  const skill =
    yours.find((s) => s.name.toLowerCase() === name) ?? yours.find((s) => (s.invoke ?? "").toLowerCase() === name);
  if (skill) return { kind: "skill", skill, args };
  const own = (custom ?? []).find((c) => c.kind === "command" && c.name.toLowerCase() === name);
  if (own) return { kind: "custom", command: own, args };
  return null;
}

/** v1.379.0: "/usage" — what this session used, in words ("" = nothing yet).
 *  A flat-subscription figure is a list-price equivalent and says so. */
export function sessionUsageWords(u: unknown): string {
  const r = u && typeof u === "object" ? (u as Record<string, unknown>) : null;
  const num = (v: unknown) => (typeof v === "number" && Number.isFinite(v) ? v : 0);
  const turns = num(r?.turns);
  if (!r || !(turns > 0)) return "This session has not used anything yet.";
  const tokens = num(r.input_tokens) + num(r.output_tokens);
  const dollars = (n: number) => (n < 0.01 ? "under $0.01" : `$${n.toFixed(2)}`);
  const parts = [
    `This session used ${tokens.toLocaleString("en-US")} tokens over ${turns === 1 ? "1 reply" : `${turns} replies`}`,
  ];
  if (num(r.cost_usd) > 0) parts.push(dollars(num(r.cost_usd)));
  if (num(r.list_price_equivalent_usd) > 0) {
    parts.push(`≈${dollars(num(r.list_price_equivalent_usd))} at list price (subscription)`);
  }
  return `${parts.join(" · ")}.`;
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
  /** Harness gaps review: a message SENT once before and sent again by Retry
   *  (lib/buildRetry). Its words go as they are — never read for a "/"
   *  command again (a skill's words may start with "/model") — and
   *  `command` names the Claude Code command it ran ("" = none), even when
   *  the session's command list cannot be read. Never saved in the queue
   *  (`decodeQueuedItem` reads it back as words). */
  resend?: { command: string };
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
