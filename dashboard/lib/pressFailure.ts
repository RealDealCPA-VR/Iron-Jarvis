/**
 * v1.455.0 (readiness Wave F, item 8): a server error on a PRESS is said
 * plainly, once.
 *
 * Before, a button whose request answered the daemon's 500 envelope printed
 * it word for word ("internal error [err_1a2b3c4d]: KeyError: 'agent_type'"),
 * because ~80 press handlers on Fleet, Skills, Artifacts, Updates, Browser,
 * Creative and Workflows show `ApiError.message`. Now `lib/api.ts` turns that
 * envelope (or a 500 that carried no words at all) into PRESS_PROBLEM_WORDS
 * and keeps the original text as the error's `technical`, and here, for
 * `components/ui.ErrorNote`, which draws it under a "Details" fold so the
 * err id can still be quoted.
 *
 * No imports: `lib/api.ts` is mocked wholesale by hundreds of test files, so
 * the words and the store live apart from it (the lib/etag.ts lesson).
 */

/** What a press that hit an unexpected server error says. */
export const PRESS_PROBLEM_WORDS = "Iron Jarvis hit a problem doing that. Try again.";

/** The fold's press (the same word as `lib/failureActions.DETAILS_WORDS`;
 *  a test keeps the two equal). */
export const PRESS_DETAILS_WORDS = "Details";

/** The daemon's one 500 envelope (`daemon/auth.unhandled_error_response`):
 *  "internal error [err_<8 hex>]: <Class>: <message>". */
const ENVELOPE = /^internal error \[err_[0-9a-f]{4,32}\]/i;

/** True for the daemon's unhandled-error envelope. */
export function isServerEnvelope(detail: unknown): boolean {
  return typeof detail === "string" && ENVELOPE.test(detail.trim());
}

/** The words a failed request should carry, and the raw text kept behind
 *  them. Only a 500 is reworded, and only when its words are the envelope
 *  or there were no words at all ("500 Internal Server Error"): a 5xx the
 *  daemon wrote itself ("Iron-Proxy is not answering", a 503 while a pack
 *  starts) is a sentence for the user and is kept. */
export function pressWords(
  status: number,
  detail: string,
  hadDetail: boolean,
): { message: string; technical?: string } {
  if (status === 500 && (isServerEnvelope(detail) || !hadDetail)) {
    return { message: PRESS_PROBLEM_WORDS, technical: detail };
  }
  return { message: detail };
}

let last: { seq: number; technical: string } | null = null;
let seq = 0;

/** Called by `lib/api.ts` when it rewords a 500: the newest raw text, so a
 *  note that shows PRESS_PROBLEM_WORDS can fold it under Details. */
export function rememberServerProblem(technical: string): void {
  seq += 1;
  last = { seq, technical };
}

/** The newest reworded 500's raw text, or "" when there was none. */
export function lastServerProblem(): string {
  return last?.technical ?? "";
}

/** True when a note's words are the press-problem sentence. */
export function saysPressProblem(text: string): boolean {
  return text.includes(PRESS_PROBLEM_WORDS);
}

/** Tests only. */
export function _resetServerProblem(): void {
  last = null;
  seq = 0;
}
