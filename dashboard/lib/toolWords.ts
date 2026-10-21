/**
 * v1.314.0 (UX wave 2, approval-card-tool-ids): what a tool DOES, in the few
 * plain words a busy person can decide on — "save a file", not `write_file`.
 *
 * Used where the app asks for consent (the mid-turn approval card), so the
 * words must stay TRUE to the tool: each entry describes only what that tool
 * id does. An id this map does not know returns null and the caller falls
 * back to the exact id — never an invented description. The exact id always
 * stays on the card too (a <code> chip, the aria-label): it is the record of
 * what will run, and what a "this conversation" grant is keyed on.
 *
 * Phrases complete "The assistant wants to … — your call."
 */

const WORDS: Record<string, string> = {
  write_file: "save a file",
  edit_file: "change a file",
  // rename_file renames OR moves one file (tools/builtins.py: a `new_path`
  // that is a path puts it in another folder) — the consent lead must stay
  // true for a move too (v1.314.0 review).
  rename_file: "rename or move a file",
  // Built-ins ONLY. An agent-authored custom tool (tools/dynamic.py — e.g. a
  // `rename_real_file` CommandTool) runs whatever argv template its author
  // wrote, so the app cannot vouch for it in its own words: it falls back to
  // "run <its id>" on purpose (v1.314.0 review).
  shell: "run a command",
  repl: "run some code",
  run_code: "run some code",
  // web_fetch fetches a page and hands its TEXT back (tools/webfetch.py) —
  // nothing opens on the user's screen, so "read", not "open".
  web_fetch: "read a web page",
  web_search: "search the web",
  // v1.357.0 (G84): one call to a web service the user saved in Settings ›
  // Connections (integrations/tools.RestCallTool); the card's own arguments
  // name the service, the method and the path. (REST_CALL_TOOL below.)
  rest_call: "call one of your saved web services",
  // Calm UI redesign S3: the chat's settings tools (settings/tools.py). Only
  // the two ask-tier ones ever reach a card; both change one setting, and
  // the card's own arguments name which one.
  config_change: "change a setting",
  config_change_protected: "change a protected setting",
  // Redesign S5: the user's records from chat (settings/records.py); each
  // has an Undo on the reply.
  schedule_update: "change a schedule",
  schedule_delete: "remove a schedule",
  workflow_update: "change a saved workflow",
  workflow_delete: "remove a saved workflow",
  workflow_schedule: "put a saved workflow on a schedule",
  channel_toggle: "turn a channel's two-way messages on or off",
  channel_connect: "add a notification channel",
  app_connect: "connect an app",
  // AUDIT §6.3: the rest of the records, and "undo that".
  webhook_update: "change a webhook",
  webhook_delete: "remove a webhook",
  sentinel_update: "pause or resume a watcher",
  sentinel_delete: "remove a watcher",
  goal_update: "change a goal",
  goal_delete: "drop a goal",
  reflex_create: "add a rule that acts on its own",
  reflex_update: "change a rule that acts on its own",
  reflex_delete: "remove a rule that acts on its own",
  config_undo: "undo your last change",
  // v1.350.0: the daemon's own plan approval (agents/runtime.py
  // PLAN_APPROVAL_TOOL), not a registry tool: its arguments are the plan's
  // steps. No custom tool may take this name (tools/dynamic.py
  // RESERVED_TOOL_NAMES), so these words stay true.
  approve_plan: "follow this plan",
  // v1.358.0 (integrate, re-audit L7 + G41): the document writers and the
  // media maker read "run write_document" / "run generate_media" here while
  // the desktop toast already said these words. ONE table: desktop/main.js
  // TOOL_WORDS and fixtures/desktop-tool-words-v13580.json say the same, and
  // agents/mission.ASK_WORDS (the activity log + chat refusal footnote)
  // repeats them (tests/test_receipt_seams_v13520 pins it equal).
  write_document: "create a document",
  excel_edit: "edit a workbook",
  generate_media: "make an image, video or sound",
  // v1.364.0 (IJ harness Phase 1): the two that ask (a long-running program
  // and typing into it), and an app's resource read, which a local-only turn
  // cards. desktop/main.js TOOL_WORDS and agents/mission.ASK_WORDS say the
  // same (pinned equal).
  process_start: "start a program that keeps running",
  process_write: "type into a running program",
  mcp_read_resource: "read something from one of your apps",
};

// The 14 built-in browser tools, by EXACT name (src/iron_jarvis/browser/
// tools.py). Never a `browser_` prefix: an agent may create a custom
// CommandTool under any name the built-ins do not already use
// (tools/dynamic.py), so `browser_backup` would be a command of its author's
// choosing — described as "act in a browser tab" on a consent card, that is
// a lie (v1.314.0 review). An unknown name falls back to "run <its id>".
//
// v1.352.0 (G22): exported, so the work line (lib/workTarget's `browser`
// kind) reads the SAME fourteen names the approval card does.
export const BROWSER_TOOLS: ReadonlySet<string> = new Set([
  "browser_get_status",
  "browser_list_tabs",
  "browser_get_active_tab",
  "browser_read_page",
  "browser_get_elements",
  "browser_screenshot",
  "browser_activate_tab",
  "browser_scroll",
  "browser_create_tab",
  "browser_close_tab",
  "browser_click",
  "browser_type",
  "browser_press_key",
  "browser_navigate",
]);

/** v1.315.0: true only for the 14 BUILT-IN browser tools, by exact name —
 *  never a `browser_` prefix and never trimmed or case-folded (`Browser_click`
 *  is not one). The approval card offers "Allow for this tab" only for these:
 *  the daemon grants tabs to the real acting browser tools alone, so the
 *  button on any other tool would promise something that cannot happen. */
export function isBuiltinBrowserTool(tool: string): boolean {
  return typeof tool === "string" && BROWSER_TOOLS.has(tool);
}

/** v1.350.0: the daemon's own plan approval (agents/runtime.py
 *  PLAN_APPROVAL_TOOL): approved or declined, nothing wider, and a name no
 *  custom tool may take. The one definition the card, the chat page and the
 *  bell share. */
export const PLAN_TOOL = "approve_plan";

/** v1.358.0 (wave 8, lane 8C): the call to one of the user's saved web
 *  services (integrations/tools.RestCallTool, v1.357.0 G84). The ONE place
 *  its name is written on the dashboard: the approval card's words above, the
 *  work line's `service` kind (lib/workTarget) and its words (lib/stepWords:
 *  "Called crm", counted "called a web service") all key on it, so it never
 *  reads "Ran rest_call" / "ran 1 tool" again. */
export const REST_CALL_TOOL = "rest_call";

/** v1.358.0 (lane 8C): the saved web service a REST call named, as words:
 *  the tool takes the service's name or the "<name> (<id>)" form its own
 *  spec lists, and the row says only the name ("crm"). One line, no control
 *  characters; "" when nothing usable was given. The caller still makes it
 *  safe to show (lib/workTarget cleanTarget). */
export function serviceNameWords(raw: unknown): string {
  if (typeof raw !== "string") return "";
  const flat = raw.replace(/[\u0000-\u001f\u007f]+/g, " ").replace(/\s+/g, " ").trim();
  // "<name> (<id>)": the id is the record, the name is what the user typed.
  const named = /^(.+?)\s*\([^()]*\)$/.exec(flat);
  return (named ? named[1] : flat).trim();
}

/** v1.352.0 (G22): what the browser tool did, said after "Used your
 *  browser" — its own name without the `browser_` prefix ("click", "read
 *  page"). Built-ins only (exact names); null for anything else. */
export function browserActionWords(tool: string): string | null {
  if (!isBuiltinBrowserTool(tool)) return null;
  return tool.slice("browser_".length).replace(/_/g, " ");
}

/* ------------------------------------------------- connected apps (v1.352.0) */

/** The longest pack name / tool phrase a row shows (the full id is in the
 *  title). */
const APP_NAME_MAX = 40;
const APP_TOOL_MAX = 60;

function cutWords(s: string, max: number): string {
  return s.length > max ? `${s.slice(0, max - 1).trimEnd()}…` : s;
}

/** A control character (a line break, a NUL, DEL) anywhere in the text. */
function hasControlChar(s: string): boolean {
  for (let i = 0; i < s.length; i += 1) {
    const c = s.charCodeAt(i);
    if (c < 0x20 || c === 0x7f) return true;
  }
  return false;
}

/**
 * A connected app's tool, read from its registry name `mcp__<pack>__<tool>`
 * (mcp/tools.py: `f"mcp__{server_name}__{remote_name}"`): the pack's name as
 * the Tools page shows it, and the tool's own name. Null for any other name
 * — a built-in, a custom tool, or a malformed id (no pack, no tool, or
 * anything that is not one line of plain characters). The split is at the
 * FIRST `__` after the prefix, as the registry joins them.
 */
export function appToolParts(tool: string): { app: string; tool: string } | null {
  if (typeof tool !== "string" || !tool.startsWith("mcp__")) return null;
  const rest = tool.slice("mcp__".length);
  const cut = rest.indexOf("__");
  if (cut <= 0) return null;
  const app = rest.slice(0, cut);
  const name = rest.slice(cut + 2);
  if (!/^[A-Za-z0-9][A-Za-z0-9_.-]*$/.test(app)) return null;
  if (!name.trim() || hasControlChar(name)) return null;
  return { app, tool: name };
}

/** A tool's own name as words: "search_messages" → "search messages",
 *  "listOpenIssues" → "list open issues", "get-file" → "get file"; an
 *  acronym keeps its case ("get_PR" → "get PR"). Cut to one short line. */
export function appToolNameWords(name: string): string {
  const words = name
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
    .split(/[\s_-]+/)
    .filter(Boolean)
    .map((w) => (/^[A-Z][a-z]+$/.test(w) ? w.toLowerCase() : w));
  return cutWords(words.join(" ") || name, APP_TOOL_MAX);
}

/** The pack's name for a row, one short line. v1.358.0 (lane 8C): an
 *  underscore in the pack's id reads as a space ("google_drive" →
 *  "google drive"), as the run card always said it; agents/mission.py
 *  `app_name_words` is the Python twin (pinned equal). */
export function appNameWords(app: string): string {
  const words = app.replace(/_/g, " ").replace(/\s+/g, " ").trim();
  return cutWords(words || app, APP_NAME_MAX);
}

/** Plain words for a tool id, or null when this map does not know it. */
export function toolWords(tool: string): string | null {
  const id = (tool || "").trim();
  if (!id) return null;
  if (Object.prototype.hasOwnProperty.call(WORDS, id)) return WORDS[id];
  // The built-in browser tools act in a tab of the browser the user
  // connected; the card's tab answer and note say the rest.
  if (isBuiltinBrowserTool(id)) return "act in a browser tab";
  // v1.352.0 (G22): a connected app's tool is named by its app and its own
  // words — "use gmail (search messages)" — read straight off its id, so
  // nothing is invented; the exact id stays on the card beside it.
  const app = appToolParts(id);
  if (app) return `use ${appNameWords(app.app)} (${appToolNameWords(app.tool)})`;
  return null;
}
