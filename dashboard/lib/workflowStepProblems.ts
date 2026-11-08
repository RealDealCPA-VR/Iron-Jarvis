// The workflow step editor's problems, in words, beside the step (v1.449.0,
// Wave D item 42). Pure: no React.
//
//   * jsonProblem — the Arguments (JSON) box: why the text is not JSON, with
//     the line and column when the parser says where (it used to keep the
//     last valid value in silence);
//   * stepProblemOf — a save the daemon refused for ONE step
//     (`steps[i] (label): …`, routes/workflows._validate_step_shapes): which
//     step, and the daemon's own words without the prefix.

export interface JsonCheck {
  ok: boolean;
  /** The parsed object when ok. */
  value?: Record<string, unknown>;
  /** One sentence when not ok. */
  words: string;
}

/** Line and column (1-based) of a character offset in `text`. */
export function lineColumnOf(text: string, offset: number): { line: number; column: number } {
  const before = text.slice(0, Math.max(0, Math.min(offset, text.length)));
  const lines = before.split("\n");
  return { line: lines.length, column: lines[lines.length - 1].length + 1 };
}

/** Where the parser says it stopped: "(line 2 column 5)" (newer engines) or
 *  "at position 12" (older ones, turned into a line and column), else null. */
export function parserPlace(message: string, text: string): { line: number; column: number } | null {
  const lc = /line (\d+) column (\d+)/i.exec(message);
  if (lc) return { line: Number(lc[1]), column: Number(lc[2]) };
  const pos = /position (\d+)/i.exec(message);
  if (pos) return lineColumnOf(text, Number(pos[1]));
  return null;
}

/** The parser's own reason, without its place and without the "JSON" noise
 *  ("Unexpected token '}', ..."). */
function reasonOf(message: string): string {
  return message
    .replace(/\s*\(line \d+ column \d+\)/i, "")
    .replace(/\s*in JSON at position \d+/i, "")
    .replace(/^JSON\.parse:\s*/i, "")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 160);
}

/** Check the Arguments box. Empty = `{}`. An array or a plain value is not
 *  arguments (a tool takes named arguments). */
export function jsonProblem(text: string): JsonCheck {
  const raw = (text ?? "").trim() ? text : "{}";
  try {
    const parsed: unknown = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      return { ok: false, words: "The arguments must be an object in curly braces, like {\"path\": \".\"}. Nothing was changed." };
    }
    return { ok: true, value: parsed as Record<string, unknown>, words: "" };
  } catch (e) {
    const message = e instanceof Error ? e.message : String(e);
    const place = parserPlace(message, raw);
    const where = place ? ` at line ${place.line}, column ${place.column}` : "";
    const why = reasonOf(message);
    return {
      ok: false,
      words: `This isn't valid JSON${where}${why ? ` (${why})` : ""}. The step keeps its last good arguments until this is fixed.`,
    };
  }
}

export interface StepProblem {
  /** The step's place in the saved order (0-based). */
  index: number;
  /** The daemon's words, without `steps[i] (label): `. */
  words: string;
}

/** A save refusal about one step, or null (a refusal about the whole
 *  workflow, or another shape, stays where it was shown before). */
export function stepProblemOf(detail: string): StepProblem | null {
  const m = /^steps\[(\d+)\](?:\.[a-z_]+)?(?: \((?:[^()]|\([^()]*\))*\))?:\s*([\s\S]+)$/.exec((detail ?? "").trim());
  if (!m) return null;
  return { index: Number(m[1]), words: m[2].trim() };
}
