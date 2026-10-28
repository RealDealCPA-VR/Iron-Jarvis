/**
 * See it before you allow it (v1.348.0, polish wave A).
 *
 * The idea is ported from 21st.dev agent-elements' Bash and Edit approval
 * footers (MIT; the idea, not the code): an approval card shows WHAT WILL
 * HAPPEN, not the raw arguments. This is the pure half (no React); the card
 * draws it with components/chat/ApprovalPreview.tsx inside the existing
 * ApprovalCard, never as a second card.
 *
 * WHAT EACH CALL LOOKS LIKE, read off the arguments the card already holds.
 * Those are the REDACTED arguments the daemon put on the approval frame
 * (`tool.redact_args`): this module draws them and never fetches or reveals
 * anything more.
 *   - A FILE EDIT (edit_file's `old`/`new`, the `old_string`/`new_string` and
 *     `old_str`/`new_str` spellings other agents use, a MultiEdit-style
 *     `edits` list, docx_edit's `replace` operations) is a compact line diff
 *     through the shared lib/diff.ts `diffLines`.
 *   - A FILE WRITE (write_file / write_document with a text `content`) shows
 *     the new text's first lines.
 *   - A COMMAND (any string `command`, and pane_send's `text`, which is a line
 *     typed into a terminal) is ONE terminal-looking line, verbatim and in
 *     full: approving a command you cannot read is not a decision.
 *   - `code` (repl, run_code) is verbatim and in full for the same reason.
 *   - EVERYTHING ELSE is a tidy `key: value` line: a nested value is
 *     summarised ("3 items", "2 fields: a, b"), a long string is cut with an
 *     ellipsis, and the card offers "Show in full". A path is never cut
 *     (v1.314.0: a path cut off at the one moment the user is deciding is a
 *     decision made on half the facts); `isPathKey` says which keys are
 *     paths, including the real tools' `new_path` / `output_path`.
 *   - A swap's other settings (docx_edit's `count` / `match_case`, an edit's
 *     `replace_all`) are drawn under its diff: they decide how much changes.
 *   - A PLAN TO APPROVE (v1.350.0, lane C1): the optional plan ask (setting
 *     `plan_approval`) reaches the chat as an ordinary ask for the tool
 *     `approve_plan` with args `{"steps": [goal, ...]}`; its steps are drawn
 *     as a numbered list, each goal in full (approving a plan you cannot read
 *     is not a decision), capped like a diff with "Show all N steps".
 * The parts keep the arguments' own order, so the block sits where its
 * argument sat (`path`, then the change).
 */

import { diffLines, textLines } from "@/lib/diff";
import { PLAN_TOOL } from "@/lib/toolWords";

/** Diff rows / written lines shown before "Show all N lines". */
export const PREVIEW_LINES = 12;
/** A long string value is cut here (paths and payloads never are). */
export const VALUE_CHARS = 200;
/** …and after this many lines. */
export const VALUE_LINES = 4;

/** One drawn line of a change: the shared diff vocabulary, plus a quiet
 *  separator between two replacements (`gap`) and one line of plain words
 *  for an operation that is not a text swap (`note`). */
export interface PreviewRow {
  kind: "same" | "added" | "removed" | "gap" | "note";
  text: string;
}

/** One argument as a tidy line. */
export interface ArgEntry {
  key: string;
  /** What the line shows at a glance: long text cut, nested values summarised. */
  short: string;
  /** Everything, for "Show in full" (a nested value as indented JSON). */
  full: string;
  /** True when `short` leaves something out. */
  cut: boolean;
  /** True when `full` is structured (drawn monospace). */
  nested: boolean;
  /** Nothing in it: the card says "(empty)". */
  empty: boolean;
}

export type PreviewPart =
  | { type: "arg"; entry: ArgEntry }
  | { type: "diff"; rows: PreviewRow[]; added: number; removed: number }
  | { type: "content"; lines: string[] }
  | { type: "command"; command: string }
  | { type: "code"; code: string }
  | { type: "plan"; steps: string[]; more: number; label?: string };

export type PreviewKind = "edit" | "write" | "command" | "code" | "plan" | "form" | "args";

/** v1.400.9: the browser's one-card form tool, by EXACT name. */
export const FORM_TOOL = "browser_fill_form";

/** The first `n` CHARACTERS (code points, Python's slice), never half an emoji. */
function cutPoints(text: string, n: number): string {
  return Array.from(text).slice(0, n).join("");
}

/** One form step in words — the twin of the daemon's
 *  `browser/tools.describe_form_step` (pinned equal on a shared fixture,
 *  `__tests__/fixtures/form-steps-v1400-9.json`). It reads the card's REDACTED
 *  arguments, so typed text is a count ("type 18 characters into 'Email'"),
 *  never the characters. */
export function formStepWords(step: unknown): string {
  const s = step && typeof step === "object" && !Array.isArray(step) ? (step as Record<string, unknown>) : {};
  const action = String(s.action ?? "");
  const t =
    s.target && typeof s.target === "object" && !Array.isArray(s.target)
      ? (s.target as Record<string, unknown>)
      : {};
  const elementId = t.element_id;
  const rawLabel =
    (t.name as string) ||
    (t.css as string) ||
    (elementId !== undefined && elementId !== null && elementId !== "" ? `element ${String(elementId)}` : "") ||
    "";
  const label = cutPoints(String(rawLabel).split(/\s+/).filter(Boolean).join(" "), 60);
  const where = label ? `'${label}'` : "";
  if (action === "type") {
    let count: number | null = typeof s.chars === "number" && Number.isInteger(s.chars) ? (s.chars as number) : null;
    if (count === null && typeof s.text === "string" && s.text !== "***REDACTED***") count = s.text.length;
    const what = count === null ? "type some text" : `type ${count} character${count === 1 ? "" : "s"}`;
    const clear = s.clear ? " (clearing it first)" : "";
    return where ? `${what} into ${where}${clear}` : `${what}${clear}`;
  }
  if (action === "click") return where ? `click ${where}` : "click on the page";
  if (action === "press_key") {
    const key = cutPoints(String(s.key ?? "").split(/\s+/).filter(Boolean).join(" "), 30);
    const on = where ? ` on ${where}` : "";
    return key ? `press ${key}${on}` : `press a key${on}`;
  }
  return "do a step";
}

/** The plan ask's tool (lane C2's optional `plan_approval` ask), defined
 *  once in lib/toolWords (dependency-free, so the bell can read it too). */
export { PLAN_TOOL };
/** No real plan has more steps (agents/decompose.py MAX_PLAN_STEPS_CEILING);
 *  a longer list is cut here, and the card says how many more there were. */
export const PLAN_STEPS_MAX = 20;

/** The plan ask's steps as goal texts (a goal string, or a `{goal}` record),
 *  or null when `value` is not a list of them. Each goal keeps its words; a
 *  run of spaces or line breaks reads as one space. */
export function planSteps(value: unknown): string[] | null {
  if (!Array.isArray(value) || value.length === 0) return null;
  const out: string[] = [];
  for (const s of value) {
    const raw =
      typeof s === "string"
        ? s
        : s && typeof s === "object" && !Array.isArray(s) && typeof (s as Record<string, unknown>).goal === "string"
          ? ((s as Record<string, unknown>).goal as string)
          : null;
    if (raw === null) return null;
    out.push(raw.replace(/\s+/g, " ").trim());
  }
  return out;
}

export interface ApprovalPreviewModel {
  kind: PreviewKind;
  parts: PreviewPart[];
}

/** The before/after spellings of a text swap, most specific first. */
const EDIT_PAIRS: ReadonlyArray<readonly [string, string]> = [
  ["old_string", "new_string"],
  ["old_str", "new_str"],
  ["old_text", "new_text"],
  ["old", "new"],
];

/** Tools whose text `content` is a whole file's new text. */
const WRITE_TOOLS = new Set(["write_file", "write_document", "write", "create_file"]);
/** The keys a written file's text rides on. */
const CONTENT_KEYS = ["content", "contents", "file_text"];
/** The keys that name the file being written (a shape-only write needs one). */
const FILE_KEYS = ["path", "file_path", "filepath", "filePath", "filename", "notebook_path", "file"];
/** Keys whose value is a location: never cut, whatever its length. */
const PATH_KEYS = new Set([
  ...FILE_KEYS,
  "source",
  "src",
  "dest",
  "destination",
  "target",
  "output",
  "folder",
  "dir",
  "directory",
  "cwd",
  "url",
  "uri",
  "href",
]);
/** A key that ENDS in a location word is a location too: the real tools'
 *  own spellings (rename_file's `new_path`, redact_pii's `output_path`, a
 *  worklist's `result_path`) and any app's `target_dir` / `source_url`. */
const PATH_SUFFIX = /(?:^|_)(?:path|paths|file|files|filename|dir|dirs|directory|folder|folders|url|urls|uri|href)$/i;
const PATH_CAMEL = /[a-z](?:Path|Paths|File|Filename|Dir|Directory|Folder|Url|Uri)$/;

/** True for an argument whose value is a location (never cut). */
export function isPathKey(key: string): boolean {
  return PATH_KEYS.has(key) || PATH_SUFFIX.test(key) || PATH_CAMEL.test(key);
}

/** A text as the lines a diff compares: "" is no lines at all (not one empty
 *  line), and one trailing newline does not add an empty last line. */
export function linesOf(text: string): string[] {
  if (text === "") return [];
  const lines = textLines(text);
  if (lines.length > 1 && lines[lines.length - 1] === "") lines.pop();
  return lines;
}

/** Cut a text for one glance: at most `lines` lines and `chars` characters,
 *  with an ellipsis where it was cut. */
export function clipText(text: string, chars = VALUE_CHARS, lines = VALUE_LINES): string {
  const all = text.split(/\r?\n/);
  let out = all.slice(0, lines).join("\n");
  let cut = all.length > lines;
  if (out.length > chars) {
    out = out.slice(0, chars).trimEnd();
    cut = true;
  }
  return cut ? `${out}…` : text;
}

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;

function isPrimitive(v: unknown): v is string | number | boolean {
  return typeof v === "string" || typeof v === "number" || typeof v === "boolean";
}

function pretty(value: unknown): string {
  try {
    return JSON.stringify(value, null, 2) ?? String(value);
  } catch {
    return String(value);
  }
}

/** One argument as a tidy `key: value` line. */
export function argEntry(key: string, value: unknown): ArgEntry {
  const line = (short: string, full: string, nested = false): ArgEntry => ({
    key,
    short,
    full,
    cut: short !== full,
    nested,
    empty: false,
  });
  // A summary always has more behind it, and that full form is structured.
  const summary = (short: string): ArgEntry => ({
    key,
    short,
    full: pretty(value),
    cut: true,
    nested: true,
    empty: false,
  });
  const empty: ArgEntry = { key, short: "", full: "", cut: false, nested: false, empty: true };
  if (value === null || value === undefined) return empty;
  if (typeof value === "string") {
    if (value === "") return empty;
    return line(isPathKey(key) ? value : clipText(value), value);
  }
  if (typeof value === "number" || typeof value === "boolean" || typeof value === "bigint") {
    return line(String(value), String(value));
  }
  if (Array.isArray(value)) {
    if (value.length === 0) return empty;
    if (value.every(isPrimitive)) {
      const joined = value.map(String).join(", ");
      // A list of locations is never cut either.
      const short = isPathKey(key) ? joined : clipText(joined);
      // Cut, the full form is the exact list (a comma inside an item would
      // make the joined line ambiguous).
      return short === joined ? line(joined, joined) : summary(short);
    }
    return summary(plural(value.length, "item", "items"));
  }
  if (typeof value === "object") {
    const entries = Object.entries(value as Record<string, unknown>);
    if (entries.length === 0) return empty;
    if (entries.every(([, v]) => isPrimitive(v))) {
      const joined = entries.map(([k, v]) => `${k}: ${String(v)}`).join(", ");
      const short = clipText(joined);
      return short === joined ? line(joined, joined) : summary(short);
    }
    const names = entries.slice(0, 4).map(([k]) => k).join(", ");
    const more = entries.length > 4 ? ", …" : "";
    return summary(`${plural(entries.length, "field", "fields")}: ${names}${more}`);
  }
  return line(String(value), String(value));
}

/** The one-line form a batch example uses: strings verbatim (a path is the
 *  record), anything else summarised instead of raw JSON. */
export function exampleValue(value: unknown): string {
  if (typeof value === "string") return value;
  const e = argEntry("", value);
  return e.empty ? "(empty)" : e.short;
}

/** A text swap, plus `extra`: the swap's other settings in words
 *  ("count: all, match_case: false", "replace_all: true"), which change how
 *  much of the file it touches and so are drawn under it, never dropped. */
type Segment = { before: string; after: string; extra?: string } | { note: string };

/** The other keys of a swap's record, in words ("" when there are none). */
function extraWords(rec: Record<string, unknown>, used: readonly string[]): string {
  const rest = Object.fromEntries(Object.entries(rec).filter(([k]) => !used.includes(k)));
  const words = argEntry("", rest);
  return words.empty ? "" : words.short;
}

/** The diff part for one or more text swaps (and plain-word notes). */
function diffPart(segments: Segment[]): PreviewPart {
  const rows: PreviewRow[] = [];
  let added = 0;
  let removed = 0;
  segments.forEach((s, i) => {
    if (i > 0) rows.push({ kind: "gap", text: "" });
    if ("note" in s) {
      rows.push({ kind: "note", text: s.note });
      return;
    }
    for (const l of diffLines(linesOf(s.before), linesOf(s.after))) {
      rows.push({ kind: l.kind, text: l.text });
      if (l.kind === "added") added++;
      else if (l.kind === "removed") removed++;
    }
    if (s.extra) rows.push({ kind: "note", text: s.extra });
  });
  return { type: "diff", rows, added, removed };
}

/** A list of `{old_string, new_string}`-shaped edits (MultiEdit), or null. */
function editList(value: unknown): Segment[] | null {
  if (!Array.isArray(value) || value.length === 0) return null;
  const out: Segment[] = [];
  for (const e of value) {
    if (!e || typeof e !== "object") return null;
    const rec = e as Record<string, unknown>;
    const pair = EDIT_PAIRS.find(([o, n]) => typeof rec[o] === "string" && typeof rec[n] === "string");
    if (!pair) return null;
    out.push({
      before: rec[pair[0]] as string,
      after: rec[pair[1]] as string,
      extra: extraWords(rec, pair),
    });
  }
  return out;
}

/** docx_edit's operations: each `replace` is a text swap, every other
 *  operation one line of plain words. Null when nothing in it is a swap.
 *  Read the way the daemon reads them (documents/docx_edit.py): the op name
 *  is trimmed and lower-cased, and a missing or null `replace` deletes. */
function docxOperations(value: unknown): Segment[] | null {
  if (!Array.isArray(value) || value.length === 0) return null;
  const out: Segment[] = [];
  let swaps = 0;
  for (const op of value) {
    if (!op || typeof op !== "object") continue;
    const rec = op as Record<string, unknown>;
    const kind = typeof rec.op === "string" ? rec.op.trim().toLowerCase() : "";
    const replacement = rec.replace === undefined || rec.replace === null ? "" : rec.replace;
    if (kind === "replace" && typeof rec.find === "string" && rec.find !== "" && typeof replacement === "string") {
      out.push({ before: rec.find, after: replacement, extra: extraWords(rec, ["op", "find", "replace"]) });
      swaps++;
    } else {
      const { op: name, ...rest } = rec;
      const words = argEntry("", rest);
      out.push({ note: `${typeof name === "string" && name ? name : "operation"}${words.empty ? "" : `: ${words.short}`}` });
    }
  }
  return swaps > 0 ? out : null;
}

/** What the approval card shows for this call, in the arguments' order. */
export function approvalPreview(tool: string, args: Record<string, unknown>): ApprovalPreviewModel {
  const name = (tool || "").toLowerCase();
  const special = new Map<string, PreviewPart>();
  const consumed = new Set<string>();
  let kind: PreviewKind = "args";

  const pair = EDIT_PAIRS.find(([o, n]) => typeof args[o] === "string" && typeof args[n] === "string");
  const multi = pair ? null : editList(args.edits);
  const docx = pair || multi ? null : docxOperations(args.operations);
  if (pair) {
    special.set(pair[0], diffPart([{ before: args[pair[0]] as string, after: args[pair[1]] as string }]));
    consumed.add(pair[1]);
    kind = "edit";
  } else if (multi) {
    special.set("edits", diffPart(multi));
    kind = "edit";
  } else if (docx) {
    special.set("operations", diffPart(docx));
    kind = "edit";
  } else {
    const contentKey = CONTENT_KEYS.find((k) => typeof args[k] === "string");
    const writes = WRITE_TOOLS.has(name) || FILE_KEYS.some((k) => typeof args[k] === "string");
    if (contentKey && writes) {
      special.set(contentKey, { type: "content", lines: linesOf(args[contentKey] as string) });
      kind = "write";
    } else if (typeof args.command === "string") {
      special.set("command", { type: "command", command: args.command });
      kind = "command";
    } else if (name === "pane_send" && typeof args.text === "string") {
      // A line typed into a terminal pane and run: it IS a command.
      special.set("text", { type: "command", command: args.text });
      kind = "command";
    } else if (typeof args.code === "string") {
      special.set("code", { type: "code", code: args.code });
      kind = "code";
    } else if (name === FORM_TOOL && Array.isArray(args.steps) && args.steps.length > 0) {
      // v1.400.9: a form in one call — every step on its own numbered line,
      // so the one Allow covers a list the user could read in full.
      const steps = (args.steps as unknown[]).map(formStepWords);
      special.set("steps", {
        type: "plan",
        steps: steps.slice(0, PLAN_STEPS_MAX),
        more: Math.max(0, steps.length - PLAN_STEPS_MAX),
        label: "form",
      });
      kind = "form";
    } else if (name === PLAN_TOOL) {
      // v1.350.0: the plan the agent asks to follow, as a numbered list.
      const steps = planSteps(args.steps);
      if (steps) {
        special.set("steps", {
          type: "plan",
          steps: steps.slice(0, PLAN_STEPS_MAX),
          more: Math.max(0, steps.length - PLAN_STEPS_MAX),
        });
        kind = "plan";
      }
    }
  }

  const parts: PreviewPart[] = [];
  for (const [key, value] of Object.entries(args)) {
    const part = special.get(key);
    if (part) parts.push(part);
    else if (!consumed.has(key)) parts.push({ type: "arg", entry: argEntry(key, value) });
  }
  return { kind, parts };
}

/** Rows that are lines of the change (the quiet separators are not). */
export function countedRows(rows: readonly PreviewRow[]): number {
  return rows.filter((r) => r.kind !== "gap").length;
}
