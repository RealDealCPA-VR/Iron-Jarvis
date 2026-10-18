/**
 * Click a step to see what it did (v1.349.0, polish wave B, item 1).
 *
 * The idea is ported from 21st.dev agent-elements' expandable tool rows (MIT;
 * the idea, not the code). The daemon puts ONE new key on every FINISHED
 * `tool_call` frame:
 *
 *   "detail": {"input": "<the call's redacted arguments, short>",
 *              "output": "<what the tool answered, unfenced>"}
 *
 * both already MASKED for secrets (detections/redact.mask) and cut (input at
 * most 300 characters, output at most 1,500). The stream hook keeps it on the
 * step, the step is saved with the message, and a work row whose step has a
 * detail opens beneath itself to show it, in a chat reopened next week too.
 *
 * This file is the pure half (no React), kept outside lib/useChatStream
 * because ~every chat test mocks that module with a fixed export list:
 *   - `cleanStepDetail`: the ONE reader of a detail, used where it arrives
 *     (the frame decode) AND where it is read back (a saved thread crosses a
 *     JSON boundary and may be older, hand-edited or junk). Exactly the two
 *     string keys survive, each masked AGAIN and cut again.
 *   - `capStepDetails`: the per-message budget. At most STEP_DETAILS_MAX steps
 *     keep a detail and at most STEP_DETAILS_CHARS characters across them, in
 *     call order; past that a step keeps its row and drops only its detail.
 *   - `stepInputView` / `outputLines`: what the opened row draws. A command is
 *     one "$ command" line, code is code, and anything else is a short
 *     `key: value` line through wave A's approval formatters
 *     (lib/approvalPreview `approvalPreview` + `argEntry`), so the approval
 *     card and the step row say an argument the same way.
 *
 * WHAT IS NEVER SAVED: the raw `card.output` and `card.args` stay on the live
 * card (this screen only). Only the daemon's masked detail rides the step.
 */

import { approvalPreview, argEntry, type ArgEntry } from "@/lib/approvalPreview";

/** What a finished step did: its input and its output, both masked text. */
export interface StepDetail {
  input: string;
  output: string;
}

/** The longest input kept (the daemon's own cut). */
export const STEP_DETAIL_INPUT_MAX = 300;
/** The longest output kept (the daemon's own cut). */
export const STEP_DETAIL_OUTPUT_MAX = 1_500;
/** At most this many steps of ONE message keep their detail. */
export const STEP_DETAILS_MAX = 40;
/** At most this many characters of detail across ONE message. */
export const STEP_DETAILS_CHARS = 30_000;
/** Output lines shown before "Show all N lines" (the approval card's cap). */
export const STEP_OUTPUT_LINES = 12;

/* ------------------------------------------------------------- the masking */

const MASK = "***";

/**
 * The daemon's masking rules (src/iron_jarvis/detections/redact.py `_RULES`),
 * applied again here: the daemon masks before it sends, so on a working
 * daemon this changes nothing (every rule leaves "***" alone). It is the
 * belt to that brace — a detail is SAVED with the chat, and a secret that
 * slipped through once would sit in the thread file for good.
 *
 * LOCK-STEP with redact.py: tests/test_redact_linear_v1349.py reads this
 * array and fails when a pattern or its case rule differs from the daemon's.
 * v1.349.0: the key=value rule has no `\b[\w.-]*` prefix and the URL rule
 * starts only where a run of scheme characters starts — both used to rescan a
 * long `a.a.a…` run from every word boundary in it (quadratic; a 1,500-char
 * output cost ~200 ms per render of a 40-step message). Same output, linear.
 */
export const DETAIL_MASK_RULES: ReadonlyArray<readonly [RegExp, string]> = [
  // Authorization: Bearer <v> / Basic <v> / Token <v> / <v>
  [/(\bauthorization["']?\s*[:=]\s*["']?(?:(?:bearer|basic|token)\s+)?)[^\s"',;]+/gi, `$1${MASK}`],
  // key = value / "key": "value" for secret-named keys (refresh_token=…, pwd:…)
  [
    /((?:api[_-]?key|access[_-]?key|client[_-]?secret|token|password|passwd|secret|pwd)["']?\s*[=:]\s*["']?)(?![*]{3})[^\s"'&,;}]+/gi,
    `$1${MASK}`,
  ],
  // curl -u user:pass / --user user:pass
  [/((?:^|\s)(?:-u|--user)[\s=]+["']?[^\s:"']+:)[^\s"']+/g, `$1${MASK}`],
  // scheme://user:pass@host
  [/((?<![a-z0-9+.-])(?=[a-z0-9+.-]*?\b[a-z])[a-z0-9+.-]*:\/\/[^/\s:@]+:)[^@\s/]+(@)/gi, `$1${MASK}$2`],
  // known token shapes
  [/\b(ghp_|gho_|ghu_|ghs_|ghr_|github_pat_|sk-ant-|sk-|xox[abprs]-)[A-Za-z0-9_-]{6,}/g, `$1${MASK}`],
  [/\b(AKIA|ASIA)[0-9A-Z]{16}\b/g, `$1${MASK}`],
];

/** *text* with every secret-looking VALUE replaced by "***" (the shape kept:
 *  "Authorization: Bearer ***", "sk-ant-***"). */
export function maskDetailText(text: string): string {
  let out = text;
  for (const [rx, repl] of DETAIL_MASK_RULES) out = out.replace(rx, repl);
  return out;
}

/* --------------------------------------------------------------- the reader */

/**
 * The first `max` CHARACTERS of *s*, counted the daemon's way (Python counts
 * code points; a JS string counts UTF-16 units, so an emoji is two). A plain
 * `slice` cut an emoji-heavy output short and could end it on half a
 * surrogate pair. Never longer than `slice(0, max * 2)`.
 */
export function cutCodePoints(s: string, max: number): string {
  if (s.length <= max) return s; // fewer units than `max` = fewer code points
  let i = 0;
  let n = 0;
  while (i < s.length && n < max) {
    const c = s.charCodeAt(i);
    const pair =
      c >= 0xd800 && c <= 0xdbff && i + 1 < s.length && (s.charCodeAt(i + 1) & 0xfc00) === 0xdc00;
    i += pair ? 2 : 1;
    n += 1;
  }
  return s.slice(0, i);
}

function field(v: unknown, max: number): string {
  if (typeof v !== "string" || !v) return "";
  // Masked BEFORE the cut, so a cut can never leave half a secret unmasked.
  return cutCodePoints(maskDetailText(v), max);
}

/**
 * A detail made safe to keep and show, or null: an object whose `input` /
 * `output` are strings (anything else reads as ""), masked and cut; nothing
 * else on it survives. Null when both are empty — a step with nothing to show
 * stays a plain row.
 */
export function cleanStepDetail(v: unknown): StepDetail | null {
  if (!v || typeof v !== "object" || Array.isArray(v)) return null;
  const rec = v as Record<string, unknown>;
  const input = field(rec.input, STEP_DETAIL_INPUT_MAX);
  const output = field(rec.output, STEP_DETAIL_OUTPUT_MAX);
  if (!input.trim() && !output.trim()) return null;
  return { input, output };
}

/**
 * The per-message budget, in call order: each step's detail is cleaned
 * (cleanStepDetail) and kept while fewer than STEP_DETAILS_MAX are kept and
 * the running total stays within STEP_DETAILS_CHARS; once one does not fit,
 * it and every later step drop their detail (their rows stay). A step with no
 * `detail` key is returned AS IS (same object, same keys), so older steps are
 * untouched. Junk entries pass through for the reader downstream to skip.
 */
export function capStepDetails<T>(steps: readonly T[]): T[] {
  let kept = 0;
  let chars = 0;
  let spent = false;
  return steps.map((st) => {
    if (!st || typeof st !== "object" || !Object.prototype.hasOwnProperty.call(st, "detail")) return st;
    const rec = st as T & { detail?: unknown };
    const d = cleanStepDetail(rec.detail);
    const size = d ? d.input.length + d.output.length : 0;
    if (d && !spent && kept < STEP_DETAILS_MAX && chars + size <= STEP_DETAILS_CHARS) {
      kept += 1;
      chars += size;
      // Spread keeps `detail` where it was (last on a step from the hook).
      return { ...rec, detail: d };
    }
    if (d) spent = true;
    const { detail: _dropped, ...rest } = rec;
    void _dropped;
    return rest as T;
  });
}

/* ------------------------------------------------------------- the drawing */

/** What an opened row draws for the input. */
export type StepInputView =
  /** One terminal line ("$ git status"), then the call's other settings. */
  | { kind: "command"; command: string; rest: ArgEntry[] }
  /** Code that ran (repl, run_code), then the call's other settings. */
  | { kind: "code"; code: string; rest: ArgEntry[] }
  /** Short `key: value` lines (an edit, a read, any other tool). */
  | { kind: "args"; entries: ArgEntry[] }
  /** Text that is not a whole argument object (the daemon cut it): shown
   *  verbatim in monospace. */
  | { kind: "text"; text: string };

/** The daemon cut a long command's JSON mid-string: the command it starts
 *  with, unescaped, or null. Only the leading `"command"` key (the daemon
 *  sorts keys, and a shell call's other keys sort after it). */
function cutCommand(text: string): string | null {
  const m = /^\{\s*"command"\s*:\s*"((?:[^"\\]|\\.)*)/.exec(text);
  if (!m || !m[1].trim()) return null;
  // The string closed before the cut: the command is whole (a later key was
  // cut). Else it was cut inside the command: say so with an ellipsis.
  const whole = text.charAt(m[0].length) === '"';
  // Drop a \u escape the cut may have split, then unescape as JSON.
  const body = whole ? m[1] : m[1].replace(/\\u[0-9a-fA-F]{0,3}$/, "");
  try {
    const command = JSON.parse(`"${body}"`) as string;
    return whole ? command : `${command}…`;
  } catch {
    return null;
  }
}

/** The non-empty arguments as tidy lines, in their own order. */
function entriesOf(args: Record<string, unknown>, skip: readonly string[] = []): ArgEntry[] {
  return Object.entries(args)
    .filter(([k]) => !skip.includes(k))
    .map(([k, v]) => argEntry(k, v));
}

/**
 * The input as the opened row draws it. The daemon sends the redacted
 * arguments as one JSON line; a whole object is read through wave A's
 * approval model (a `command` — or pane_send's `text` — is a command, `code`
 * is code), and everything else becomes `key: value` lines (an edit too: a
 * diff of a cut-down edit would claim lines it never saw). Text that is not a
 * JSON object is shown as it is. Null for an empty input.
 */
export function stepInputView(tool: string, input: string): StepInputView | null {
  const text = input.trim();
  if (!text) return null;
  let parsed: unknown = undefined;
  try {
    parsed = JSON.parse(text);
  } catch {
    parsed = undefined;
  }
  if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
    const args = parsed as Record<string, unknown>;
    if (Object.keys(args).length === 0) return null;
    const model = approvalPreview(tool, args);
    if (model.kind === "command") {
      const key = typeof args.command === "string" ? "command" : "text";
      const command = String(args[key]);
      if (command.trim()) return { kind: "command", command, rest: entriesOf(args, [key]) };
    }
    if (model.kind === "code" && typeof args.code === "string") {
      return { kind: "code", code: args.code, rest: entriesOf(args, ["code"]) };
    }
    return { kind: "args", entries: entriesOf(args) };
  }
  const command = cutCommand(text);
  if (command) return { kind: "command", command, rest: [] };
  return { kind: "text", text };
}

/** The output as lines: one trailing newline adds no empty last line, and
 *  an output that is only whitespace has none. */
export function outputLines(output: string): string[] {
  if (!output.trim()) return [];
  const lines = output.replace(/\r\n?/g, "\n").split("\n");
  while (lines.length > 1 && lines[lines.length - 1].trim() === "") lines.pop();
  return lines;
}
