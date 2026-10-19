/**
 * v1.352.0 (readiness audit G19): what a helper agent's run DID, in the words
 * a person reads, never the tool ids the ledger keeps.
 *
 * The card under an agent's reply (components/chat/RunResultCard) listed its
 * tools as raw ids ("history_search ×83") and its errors as raw output
 * ("memory_read — missing required: layer…"), and the settled helper-agent
 * row (components/chat/AgentRunRow) said nothing about the work at all. Both
 * now read their lines here:
 *
 *   "Searched your chats 83 times"   (the steps that worked, counted)
 *   "One memory lookup failed"       (the steps that failed, counted apart)
 *
 * THE WORDS COME FROM lib/stepWords FIRST. A tool that table knows by kind
 * (read_file → "Read 2 files", web_search → "Searched the web 3 times") is
 * said exactly as the chat's own "Worked for …" line says it, so the two can
 * never drift apart. Only the agent-side built-ins stepWords does not know
 * (history_search, memory_read, consult, worklist_done …) get a phrase here,
 * each by EXACT name and true to what that tool does. Any other name (a custom
 * tool an agent made, a connected app's tool) is said by its OWN name with the
 * underscores turned to spaces ("Used rename real file"), never described in
 * words the app cannot vouch for; the raw id always stays in the row's title.
 *
 * A failed call is counted once, as failed, and never as work done (the G07
 * rule lib/stepWords keeps): 80 searches that worked and 3 that failed read
 * "Searched your chats 80 times" and "3 chat searches failed".
 *
 * Plain data, no React: imported by the card, the row and the tests.
 */

import { STEP_WORDS } from "@/lib/stepWords";
import { toolKind, type WorkKind } from "@/lib/workTarget";

/** An agent-side built-in the shared step table does not know by kind:
 *  `did` is the phrase for one call, `noun` names one call when it failed
 *  ("One chat search failed"), `nouns` its plural when not just "+s". */
interface AgentToolWords {
  did: string;
  noun: string;
  nouns?: string;
}

/**
 * Built-ins only, by EXACT name (src/iron_jarvis/**: each phrase says only
 * what that tool does). A count reads "<did> 3 times".
 */
const AGENT_TOOLS: Readonly<Record<string, AgentToolWords>> = {
  // search/tools.py: every past conversation (chats, messaging threads, the
  // agent rooms, finished sessions).
  history_search: { did: "Searched your chats", noun: "chat search", nouns: "chat searches" },
  // memory/tools.py: layered memory, one entry by layer + key / by meaning.
  memory_read: { did: "Looked up a memory", noun: "memory lookup" },
  memory_search: { did: "Searched its memory", noun: "memory search", nouns: "memory searches" },
  memory_write: { did: "Saved to its memory", noun: "memory save" },
  memory_propose: { did: "Suggested something to remember", noun: "memory suggestion" },
  // memory/recall.py: everything Jarvis knows, ranked by meaning.
  recall: { did: "Searched everything Jarvis knows", noun: "recall" },
  recall_lessons: { did: "Checked its lessons", noun: "lesson check" },
  // ltm/tools.py: the user's long-term knowledge base.
  ltm_search: { did: "Searched your knowledge base", noun: "knowledge base search", nouns: "knowledge base searches" },
  ltm_append: { did: "Added to your knowledge base", noun: "knowledge base note" },
  // filesearch/tools.py: across the configured roots, broader than grep.
  file_search: { did: "Searched your drives", noun: "drive search", nouns: "drive searches" },
  // computeruse/tools.py: an isolated browser context.
  browse: { did: "Opened a web page", noun: "web page visit" },
  web_extract: { did: "Read part of a web page", noun: "web page read" },
  // documents/*: spreadsheet query, document compare.
  excel_query: { did: "Queried a workbook", noun: "workbook query", nouns: "workbook queries" },
  compare_documents: { did: "Compared documents", noun: "document compare" },
  // agents/*: the team.
  consult: { did: "Asked a teammate for advice", noun: "request for advice", nouns: "requests for advice" },
  delegate: { did: "Handed work to a teammate", noun: "hand-off" },
  spawn_agent: { did: "Started a helper agent", noun: "helper agent start" },
  message_agent: { did: "Messaged a teammate", noun: "message to a teammate", nouns: "messages to a teammate" },
  blackboard_post: { did: "Posted a note for the team", noun: "team note" },
  blackboard_read: { did: "Read the team's notes", noun: "team notes read" },
  worklist_add: { did: "Listed the items to work through", noun: "worklist update" },
  worklist_next: { did: "Picked the next item to work on", noun: "worklist pick" },
  worklist_done: { did: "Finished an item on its list", noun: "worklist item" },
  worklist_status: { did: "Checked its list", noun: "worklist check" },
  // skills/*, learning/*, agents/files.py.
  skill_search: { did: "Looked for a skill", noun: "skill search", nouns: "skill searches" },
  skill_load: { did: "Loaded a skill", noun: "skill load" },
  remember_preference: { did: "Noted a preference", noun: "preference note" },
  notebook: { did: "Updated its notebook", noun: "notebook update" },
  // guide/tools.py: the bundled docs and the user's own setup.
  guide_search: { did: "Searched the Iron Jarvis guide", noun: "guide search", nouns: "guide searches" },
  guide_read: { did: "Read the Iron Jarvis guide", noun: "guide read" },
  app_search: { did: "Searched your Iron Jarvis setup", noun: "setup search", nouns: "setup searches" },
  app_status: { did: "Checked your Iron Jarvis setup", noun: "setup check" },
};

/** One failed call of a KNOWN step kind (lib/stepWords), as a noun. A kind
 *  this table does not name (one a later wave adds) uses the tool's own name. */
const KIND_NOUN: Partial<Record<WorkKind, { noun: string; nouns?: string }>> = {
  read: { noun: "file read" },
  make: { noun: "file save" },
  change: { noun: "file change" },
  folder: { noun: "folder listing" },
  search: { noun: "file search", nouns: "file searches" },
  web: { noun: "web search", nouns: "web searches" },
  page: { noun: "web page read" },
  command: { noun: "command" },
};

function has<T extends object>(table: T, key: string): boolean {
  return Object.prototype.hasOwnProperty.call(table, key);
}

function capFirst(s: string): string {
  return s ? s.charAt(0).toUpperCase() + s.slice(1) : s;
}

function count(n: unknown): number {
  return typeof n === "number" && Number.isFinite(n) && n > 0 ? Math.floor(n) : 0;
}

/**
 * A tool id said by its own name: underscores to spaces, a connected app's
 * `mcp__<app>__<tool>` as "<app>: <tool>". Never an invented description.
 * Empty / non-string → "a tool".
 */
export function toolOwnName(tool: unknown): string {
  const id = typeof tool === "string" ? tool.trim() : "";
  if (!id) return "a tool";
  const app = APP_TOOL.exec(id);
  if (app) {
    const server = app[1].replace(/_/g, " ").replace(/\s+/g, " ").trim();
    const name = app[2].replace(/_/g, " ").replace(/\s+/g, " ").trim();
    return server && name ? `${server}: ${name}` : id;
  }
  return id.replace(/_/g, " ").replace(/\s+/g, " ").trim() || id;
}

/** A connected app's tool id (`mcp__<app>__<tool>`). Said by its OWN name
 *  here, app and tool both ("gmail: search messages"): a per-run count of
 *  "used an app" would hide which app ran. */
const APP_TOOL = /^mcp__(.+?)__(.+)$/;

/** The kind lib/stepWords says a tool in its own words, or null: the
 *  catch-all "tool", and a connected app's tool (said by its own name). */
function sharedKind(tool: string): WorkKind | null {
  if (APP_TOOL.test(tool)) return null;
  const kind = toolKind(tool);
  return kind !== "tool" && STEP_WORDS[kind] ? kind : null;
}

/**
 * The calls of `tool` that WORKED, counted: "Searched your chats 83 times",
 * "Read 2 files", "Used rename real file". Null for no calls.
 */
export function toolDidWords(tool: string, n: number): string | null {
  const c = count(n);
  if (!c) return null;
  const id = typeof tool === "string" ? tool.trim() : "";
  if (has(AGENT_TOOLS, id)) {
    const w = AGENT_TOOLS[id];
    return c === 1 ? w.did : `${w.did} ${c} times`;
  }
  const kind = sharedKind(id);
  if (kind) return capFirst(STEP_WORDS[kind].count(c));
  const own = `Used ${toolOwnName(id)}`;
  return c === 1 ? own : `${own} ${c} times`;
}

/**
 * The calls of `tool` that FAILED, counted: "One memory lookup failed",
 * "3 chat searches failed", "One rename real file call failed". Null for none.
 */
export function toolFailedWords(tool: string, n: number): string | null {
  const c = count(n);
  if (!c) return null;
  const { noun, nouns } = failedNouns(tool);
  if (c === 1) return `One ${noun} failed`;
  return `${c} ${nouns ?? `${noun}s`} failed`;
}

/** One failed call of `tool` as a noun ("memory lookup", "file read",
 *  "rename real file call"), with its plural when not just "+s". */
function failedNouns(tool: unknown): { noun: string; nouns?: string } {
  const id = typeof tool === "string" ? tool.trim() : "";
  if (has(AGENT_TOOLS, id)) return AGENT_TOOLS[id];
  const kind = sharedKind(id);
  const k = kind ? KIND_NOUN[kind] : undefined;
  return k ?? { noun: `${toolOwnName(id)} call` };
}

/** ONE failed call, said first on its error row: "Memory lookup failed",
 *  "File read failed", "Rename real file call failed". */
export function stepFailedWords(tool: unknown): string {
  return `${capFirst(failedNouns(tool).noun)} failed`;
}

/** One line of what a run did, for the card's chips and the settled row. */
export interface RunActionLine {
  /** The raw tool id: the record, kept in the row's title. */
  tool: string;
  text: string;
  failed: boolean;
}

/**
 * The run's work as lines, in the order the tools were first used: for each
 * tool, the calls that worked ("Searched your chats 80 times"), then the ones
 * that failed ("3 chat searches failed"). `used` counts every call of a tool
 * (the ledger's `tools_used`), `failed` the failed ones (`tools_failed`), so a
 * failed call is said once, as failed. Junk entries are skipped.
 */
export function runActionLines(
  used: ReadonlyArray<{ tool?: unknown; count?: unknown }> | null | undefined,
  failed?: ReadonlyArray<{ tool?: unknown; count?: unknown }> | null,
): RunActionLine[] {
  const bad = new Map<string, number>();
  for (const f of Array.isArray(failed) ? failed : []) {
    if (f && typeof f.tool === "string" && f.tool.trim()) bad.set(f.tool, count(f.count));
  }
  const out: RunActionLine[] = [];
  const seen = new Set<string>();
  for (const u of Array.isArray(used) ? used : []) {
    if (!u || typeof u.tool !== "string" || !u.tool.trim() || seen.has(u.tool)) continue;
    seen.add(u.tool);
    const total = count(u.count);
    const nBad = Math.min(bad.get(u.tool) ?? 0, total);
    const ok = toolDidWords(u.tool, total - nBad);
    if (ok) out.push({ tool: u.tool, text: ok, failed: false });
    const no = toolFailedWords(u.tool, nBad);
    if (no) out.push({ tool: u.tool, text: no, failed: true });
  }
  return out;
}
