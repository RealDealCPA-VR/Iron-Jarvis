/**
 * The intelligence plan, release 5: WHAT IRON JARVIS CHECKED on the files a
 * reply or a task made. The daemon builds the whole thing from its one
 * record of checks (`checks/record.payload`): the line a person reads
 * ("Checked by Iron Jarvis: the spreadsheet opens, no error cells, 3 totals
 * add up."), its tone, and the list a press opens (each check, its result,
 * who checked). This module only decodes it, by whitelist; nothing here
 * composes a word of the line, and a reply with no `checked` draws nothing.
 * The ONE thing it does not take from the payload is the word beside each
 * check ("Problem", "Worth a look"): that is read from the check's `result`
 * (`RESULT_WORDS`, the daemon's own four words), so a payload that says
 * `look` under the word "Checked" can never draw so.
 *
 * Rides the chat reply (the done frame's and the POST answer's `checked`,
 * saved on the message) and a run's result (`GET /sessions/{id}/result`).
 *
 * FOUR results. `failed` ("Problem") is a short list of facts (a file that
 * is not a valid file of its kind, a cell whose saved value is an error, a
 * test that failed, a required file that is not there). `look` ("Worth a
 * look") is everything Iron Jarvis inferred, read out of the reply or was
 * told by a model: no verdict. Both read in the warning colour; which
 * RESULT a check gets is the daemon's decision, never the page's.
 */

export type CheckedResult = "passed" | "failed" | "look" | "not_checked";

const RESULTS: readonly CheckedResult[] = ["passed", "failed", "look", "not_checked"];

/** The word beside a check, by its result: the daemon's own four words
 *  (`checks/record._RESULT_WORDS`; a shared fixture holds the two equal).
 *  Never taken from the payload. */
export const RESULT_WORDS: Record<CheckedResult, string> = {
  passed: "Checked",
  failed: "Problem",
  look: "Worth a look",
  not_checked: "Not checked",
};

/** The results that read in the warning colour, in the list as on the line. */
export function isWarnResult(result: CheckedResult): boolean {
  return result === "failed" || result === "look";
}

/** How many checks the list shows at most (the daemon sends at most 30). */
export const CHECKED_LIST_MAX = 30;

export interface CheckedItem {
  /** What the check is about: a file's name, "The reply", "Step 2". */
  label: string;
  /** What it found, in the daemon's words ("C7 holds #DIV/0!"). */
  said: string;
  result: CheckedResult;
  /** "Checked" | "Problem" | "Worth a look" | "Not checked": read from
   *  `result` (`RESULT_WORDS`), never from the payload. */
  resultWords: string;
  /** "by rule", or "by <the model's name>". */
  who: string;
}

export interface CheckedMark {
  /** The one line, whole, as the daemon wrote it. */
  line: string;
  /** `warn` when there is a problem or something worth a look, else
   *  `quiet`. */
  tone: "warn" | "quiet";
  checks: CheckedItem[];
  /** How many more checks the record holds than `checks` lists. */
  more: number;
  /** The daemon's words for that count ("and 3 more checks"; "" = none). */
  moreWords: string;
}

/** Characters nobody sees and no line needs: the control characters (NUL,
 *  ESC ...) and the ones that turn text round (the bidi marks, embeddings,
 *  overrides and isolates). The daemon strips the same set
 *  (`checks/record.seen_text`); a shared fixture holds the two to the same
 *  inputs. A tab or a line break becomes a space. */
const UNSEEN = /[\u0000-\u001f\u007f-\u009f\u061c\u200e\u200f\u202a-\u202e\u2066-\u2069]/g;

export function seenText(v: string): string {
  return v.replace(/[\t\r\n]+/g, " ").replace(UNSEEN, "");
}

function text(v: unknown, max: number): string {
  return typeof v === "string" ? seenText(v).trim().slice(0, max) : "";
}

/** The daemon's `checked`, whitelisted; null when it says nothing (so a
 *  reply that made no file, and anything malformed, draws no line). */
export function decodeChecked(raw: unknown): CheckedMark | null {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  const o = raw as Record<string, unknown>;
  const line = text(o.line, 1_200);
  if (!line) return null;
  const checks: CheckedItem[] = [];
  for (const it of Array.isArray(o.checks) ? o.checks : []) {
    if (!it || typeof it !== "object") continue;
    const c = it as Record<string, unknown>;
    const result = RESULTS.find((r) => r === c.result);
    const label = text(c.label, 240);
    if (!result || !label) continue;
    checks.push({
      label,
      said: text(c.said, 400),
      result,
      resultWords: RESULT_WORDS[result],
      who: text(c.who, 140),
    });
    if (checks.length >= CHECKED_LIST_MAX) break;
  }
  const more = typeof o.more === "number" && Number.isFinite(o.more) && o.more > 0 ? Math.floor(o.more) : 0;
  return {
    line,
    tone: o.tone === "warn" ? "warn" : "quiet",
    checks,
    more,
    moreWords: more > 0 ? text(o.more_words ?? o.moreWords, 80) : "",
  };
}

/** The mark as it is saved on a message (the daemon's own key names, so a
 *  reopened chat decodes it the same way). */
export function checkedForSave(mark: CheckedMark): Record<string, unknown> {
  return {
    line: mark.line,
    tone: mark.tone,
    checks: mark.checks.map((c) => ({
      label: c.label,
      said: c.said,
      result: c.result,
      result_words: c.resultWords,
      who: c.who,
    })),
    more: mark.more,
    more_words: mark.moreWords,
  };
}

/** The label of the press that opens and closes the list. */
export const CHECKED_OPEN = "Show what was checked";
export const CHECKED_CLOSE = "Hide what was checked";
