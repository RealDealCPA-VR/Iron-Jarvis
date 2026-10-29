/**
 * MCP MEMORY BASE TOOL ROLES (v1.408.0): which of an MCP memory server's tools
 * Jarvis uses for each kind of note work (find, read, save, add to, archive,
 * delete, rename, move), and where each of that tool's arguments comes from.
 *
 * Pure module, no React, NO import from lib/api (about 70 test files mock that
 * module wholesale). The decoders are WHITELISTS: an unknown role, source or
 * field dies here, and a malformed answer reads as "nothing", never a crash.
 *
 * The daemon contract:
 *   GET /ltm/sources/{name}/roles
 *     {tools: [{name, description, params: [{name, required, type}]}],
 *      roles: [...], proposed: {role: Mapping|null}, saved: {role: Mapping}|null,
 *      problems: {role: string[]}, sources: [...], error?}
 *     404 = not an MCP base, or an older Iron Jarvis: nothing is drawn.
 *   PUT /ltm/sources/{name}/roles {roles: {role: Mapping|null}}
 *     -> the same view after saving | 400 {detail}
 * Mapping = {tool, args: {param: source | {const: string}}}.
 * `saved: null` means the user never confirmed: Jarvis may search and read the
 * server but never writes to it.
 */

export const ROLE_IDS = ["search", "read", "create", "append", "archive", "delete", "rename", "move"] as const;
export type RoleId = (typeof ROLE_IDS)[number];

export const SOURCE_IDS = ["query", "ref", "title", "content", "to"] as const;
export type SourceId = (typeof SOURCE_IDS)[number];

export type ArgValue = SourceId | { const: string };

export interface RoleMapping {
  tool: string;
  args: Record<string, ArgValue>;
}

export interface McpToolParam {
  name: string;
  required: boolean;
  type: string;
}

export interface McpTool {
  name: string;
  description: string;
  params: McpToolParam[];
}

export interface McpRolesView {
  tools: McpTool[];
  roles: RoleId[];
  proposed: Partial<Record<RoleId, RoleMapping | null>>;
  /** null = never confirmed (the server is read-only for Jarvis). */
  saved: Partial<Record<RoleId, RoleMapping>> | null;
  problems: Partial<Record<RoleId, string[]>>;
  sources: SourceId[];
  error: string;
}

/** Role words, as the user reads them. */
export const ROLE_WORDS: Record<RoleId, string> = {
  search: "Find notes",
  read: "Read one note",
  create: "Save a new note",
  append: "Add to an existing note",
  archive: "Archive a note",
  delete: "Delete a note",
  rename: "Rename a note",
  move: "Move a note",
};

/** Where an argument's value comes from. */
export const SOURCE_WORDS: Record<SourceId, string> = {
  query: "The search words",
  ref: "The note's reference",
  title: "The note's title",
  content: "The note's text",
  to: "The new name or folder",
};
export const CONST_WORDS = "A fixed value…";
export const EMPTY_WORDS = "Leave empty";
export const NOT_USED_WORDS = "Not used";

/** The one line at the top of the editor. */
export const ROLES_INTRO =
  "Jarvis uses only what you confirm here to save, add to or change notes on this server. " +
  "Until you press Use these, it can search and read but never write.";

export const NOT_SET_UP_WORDS = "Not set up yet";
export const SET_UP_WORDS = "Tools set up";
/** After "Use these": what was saved, said truthfully (v1.408.0 review: with
 *  every role on "Not used" the daemon keeps nothing, and the old line still
 *  said the tools were in use). */
export const SAVED_WORDS = "Saved. Jarvis now uses these tools for this memory base.";
export const SAVED_NOTHING_WORDS =
  "Saved. No tool is chosen, so Jarvis can only search and read this memory base, never write to it.";

const isObj = (v: unknown): v is Record<string, unknown> => !!v && typeof v === "object" && !Array.isArray(v);
const str = (v: unknown) => (typeof v === "string" ? v : "");
const isRole = (v: unknown): v is RoleId => typeof v === "string" && (ROLE_IDS as readonly string[]).includes(v);
const isSource = (v: unknown): v is SourceId =>
  typeof v === "string" && (SOURCE_IDS as readonly string[]).includes(v);

function decodeArg(v: unknown): ArgValue | null {
  if (isSource(v)) return v;
  if (isObj(v) && typeof v.const === "string") return { const: v.const };
  return null;
}

/** A mapping, or null when it is not one. */
export function decodeMapping(v: unknown): RoleMapping | null {
  if (!isObj(v) || typeof v.tool !== "string" || !v.tool) return null;
  const args: Record<string, ArgValue> = {};
  if (isObj(v.args)) {
    for (const [k, raw] of Object.entries(v.args)) {
      const a = decodeArg(raw);
      if (k && a !== null) args[k] = a;
    }
  }
  return { tool: v.tool, args };
}

function decodeTool(v: unknown): McpTool | null {
  if (!isObj(v) || typeof v.name !== "string" || !v.name) return null;
  const params = (Array.isArray(v.params) ? v.params : [])
    .map((p): McpToolParam | null =>
      isObj(p) && typeof p.name === "string" && p.name
        ? { name: p.name, required: p.required === true, type: str(p.type) }
        : null,
    )
    .filter((p): p is McpToolParam => p !== null);
  return { name: v.name, description: str(v.description), params };
}

/** The daemon's view, or null when the answer is not one. */
export function decodeRolesView(raw: unknown): McpRolesView | null {
  if (!isObj(raw) || !Array.isArray(raw.tools) || !Array.isArray(raw.roles)) return null;
  const roles = raw.roles.filter(isRole).filter((r, i, a) => a.indexOf(r) === i);
  const tools = raw.tools.map(decodeTool).filter((t): t is McpTool => t !== null);
  const proposed: McpRolesView["proposed"] = {};
  if (isObj(raw.proposed)) {
    for (const r of roles) if (r in raw.proposed) proposed[r] = decodeMapping(raw.proposed[r]);
  }
  let saved: McpRolesView["saved"] = null;
  if (isObj(raw.saved)) {
    saved = {};
    for (const r of roles) {
      const m = decodeMapping(raw.saved[r]);
      if (m) saved[r] = m;
    }
  }
  const problems: McpRolesView["problems"] = {};
  if (isObj(raw.problems)) {
    for (const r of roles) {
      const list = raw.problems[r];
      if (Array.isArray(list)) {
        const words = list.filter((x): x is string => typeof x === "string" && x.trim() !== "");
        if (words.length) problems[r] = words;
      }
    }
  }
  const sources = (Array.isArray(raw.sources) ? raw.sources : SOURCE_IDS).filter(isSource);
  return { tools, roles, proposed, saved, problems, sources, error: str(raw.error) };
}

/** What the editor starts from: the confirmed choice if there is one, else
 *  the daemon's proposal. Every role of the view has a slot (null = unused). */
export function startingChoice(view: McpRolesView): Record<string, RoleMapping | null> {
  const from = view.saved ?? view.proposed;
  const out: Record<string, RoleMapping | null> = {};
  for (const r of view.roles) {
    const m = from[r];
    out[r] = m ? { tool: m.tool, args: { ...m.args } } : null;
  }
  return out;
}

export function toolNamed(view: McpRolesView, name: string): McpTool | undefined {
  return view.tools.find((t) => t.name === name);
}

/** A newly chosen tool keeps the arguments it shares with the old choice. */
export function chooseTool(prev: RoleMapping | null, tool: McpTool | undefined, name: string): RoleMapping | null {
  if (!name) return null;
  const args: Record<string, ArgValue> = {};
  if (prev && tool) {
    for (const p of tool.params) if (p.name in prev.args) args[p.name] = prev.args[p.name];
  }
  return { tool: name, args };
}

/** The select's value for one argument: a source id, "const", or "". */
export function argSelectValue(v: ArgValue | undefined): string {
  if (v === undefined) return "";
  return typeof v === "string" ? v : "const";
}

/** Set one argument from the select: "" leaves it out, "const" a fixed value. */
export function setArg(m: RoleMapping, param: string, choice: string, constText = ""): RoleMapping {
  const args = { ...m.args };
  if (choice === "") delete args[param];
  else if (choice === "const") args[param] = { const: constText };
  else if (isSource(choice)) args[param] = choice;
  return { ...m, args };
}

/** The first thing stopping a save, in one sentence, or "". A required
 *  argument must come from somewhere — and a fixed value for it must say
 *  something (v1.408.0 review: a blank one was saved as "", and the server
 *  refused every note at save time instead). */
export function missingRequired(view: McpRolesView, choice: Record<string, RoleMapping | null>): string {
  for (const r of view.roles) {
    const m = choice[r];
    if (!m) continue;
    const tool = toolNamed(view, m.tool);
    if (!tool) continue;
    for (const p of tool.params) {
      if (!p.required) continue;
      const a = m.args[p.name];
      if (a === undefined) {
        return `Choose where “${p.name}” comes from for ${ROLE_WORDS[r].toLowerCase()}.`;
      }
      if (typeof a !== "string" && a.const.trim() === "") {
        return `Type the fixed value for “${p.name}” for ${ROLE_WORDS[r].toLowerCase()}.`;
      }
    }
  }
  return "";
}

function sameArg(a: ArgValue | undefined, b: ArgValue | undefined): boolean {
  if (a === undefined || b === undefined) return a === b;
  if (typeof a === "string" || typeof b === "string") return a === b;
  return a.const === b.const;
}

/** Two mappings name the same tool with the same arguments (order aside). */
export function sameMapping(a: RoleMapping | null | undefined, b: RoleMapping | null | undefined): boolean {
  if (!a || !b) return !a && !b;
  if (a.tool !== b.tool) return false;
  const keys = new Set([...Object.keys(a.args), ...Object.keys(b.args)]);
  for (const k of keys) if (!sameArg(a.args[k], b.args[k])) return false;
  return true;
}

/** A role's problems as the daemon said them — only while the role is still
 *  the mapping the daemon judged (`saved`, else `proposed`). Once the user
 *  changes it the daemon's sentences describe something else (v1.408.0
 *  review: "'type' is required" stayed beside the fixed type just typed);
 *  "Use these" asks the daemon again. */
export function shownProblems(
  view: McpRolesView,
  choice: Record<string, RoleMapping | null>,
  role: RoleId,
): string[] {
  const judged = (view.saved ?? view.proposed)[role] ?? null;
  return sameMapping(choice[role] ?? null, judged) ? (view.problems[role] ?? []) : [];
}

/** The PUT body: every role of the view, a mapping or null. Arguments the
 *  chosen tool no longer lists are left out (a known tool only). */
export function rolesBody(view: McpRolesView, choice: Record<string, RoleMapping | null>): {
  roles: Record<string, RoleMapping | null>;
} {
  const roles: Record<string, RoleMapping | null> = {};
  for (const r of view.roles) {
    const m = choice[r] ?? null;
    if (!m) {
      roles[r] = null;
      continue;
    }
    const tool = toolNamed(view, m.tool);
    const args: Record<string, ArgValue> = {};
    for (const [k, v] of Object.entries(m.args)) {
      if (!tool || tool.params.some((p) => p.name === k)) args[k] = v;
    }
    roles[r] = { tool: m.tool, args };
  }
  return { roles };
}
