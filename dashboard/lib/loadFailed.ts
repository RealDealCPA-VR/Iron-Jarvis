/**
 * v1.354.0 (G59): what a panel or a press says when Iron Jarvis did not answer
 * the way it should — in the user's words, never the engine's.
 *
 * Before, a project's Board, Media and Recent activity, the saved-workflows
 * list and a project's Schedules each printed "the daemon returned an error
 * (HTTP 500)" or "the daemon looks offline", and Creative said "Daemon offline
 * — could not upload." A new user does not know what a daemon is, and an HTTP
 * code is not a next step. Now one sentence shape everywhere:
 *
 *   - it answered with an error:  "The board couldn't load. Try again."
 *   - nothing answered at all:    "The board couldn't load. Iron Jarvis isn't responding."
 *
 * The code and the server's own message stay available, but only as a hover
 * title (`failureDetail`) for whoever is troubleshooting — never in the text.
 *
 * Pure strings; the one import is lib/offlineWords.ts, which is itself
 * dependency-free. `lib/api.ts` is mocked wholesale by dozens of test files,
 * so these live apart from it (and the component that draws the line lives in
 * components/LoadFailedLine.tsx).
 */

import { OFFLINE_TITLE } from "@/lib/offlineWords";

/** The words for "nothing answered" (status 0). It IS the shell's offline
 *  banner headline (G30, lib/offlineWords.ts), imported rather than copied, so
 *  a panel and the banner can never disagree. */
export const NOT_RESPONDING = OFFLINE_TITLE;

/** The words a failed read asks the user to do (the press, and the sentence). */
export const TRY_AGAIN_PRESS = "Try again";
export const TRY_AGAIN = `${TRY_AGAIN_PRESS}.`;

/** The shape every failed read here carries: `ApiError` (status 0 = no answer,
 *  else the HTTP status) or any `{ status, message }` look-alike. */
export interface FailureLike {
  status: number;
  message?: string;
  /** v1.455.0 (Wave F, item 8): the server's raw text when `message` is the
   *  plain press-problem sentence (`ApiError.technical`). */
  technical?: string;
}

/** True when nothing answered at all (no error in hand counts as that too —
 *  there is nothing better to say). */
export function noAnswer(error: FailureLike | null | undefined): boolean {
  return !error || !error.status;
}

/** "<Thing> couldn't load." + the next step. `thing` starts the sentence
 *  ("The board", "Saved workflows"). */
export function couldntLoad(thing: string, error: FailureLike | null | undefined): string {
  return `${thing} couldn't load. ${noAnswer(error) ? NOT_RESPONDING : TRY_AGAIN}`;
}

/** The technical detail behind a failed read, for a `title` (hover) only:
 *  "HTTP 500: Internal Server Error". Undefined when nothing answered (there
 *  is no code to show) — the visible sentence already says so. */
export function failureDetail(error: FailureLike | null | undefined): string | undefined {
  if (noAnswer(error)) return undefined;
  // The raw envelope (with its err_ id) wins over the plain words it became.
  const msg = (error!.technical ?? error!.message ?? "").trim();
  return msg ? `HTTP ${error!.status}: ${msg}` : `HTTP ${error!.status}`;
}

/** A press that could not reach Iron Jarvis: "Couldn't upload. Iron Jarvis
 *  isn't responding." `action` is the verb phrase ("upload", "start the
 *  session"). */
export function couldntWhileNoAnswer(action: string): string {
  return couldnt(action, null);
}

/** v1.449.0 (Wave B, item 17): a press that failed, in the same shape as a
 *  failed read — "Couldn't end the session. Try again." / "Couldn't end the
 *  session. Iron Jarvis isn't responding." The server's own 4xx sentence is
 *  the caller's to keep (that is a sentence meant for the user); this is for
 *  the 5xx and the no-answer cases, whose raw text names the engine. */
export function couldnt(action: string, error: FailureLike | null | undefined): string {
  return `Couldn't ${action}. ${noAnswer(error) ? NOT_RESPONDING : TRY_AGAIN}`;
}

/** v1.449.0 (Wave B, item 16): the `FailureLike` behind a caught value —
 *  an ApiError (or any `{status, message}` look-alike) keeps its status and
 *  message; anything else (a thrown string, a TypeError) reads as "nothing
 *  answered", which is the honest default for a read that never came back.
 *  Duck-typed on purpose: this module is read by stores that must not import
 *  lib/api (tests mock that module wholesale, and `instanceof` against a
 *  mocked class is only as good as the mock). */
export function failureOf(e: unknown): FailureLike {
  if (e && typeof e === "object") {
    const status = (e as { status?: unknown }).status;
    const message = (e as { message?: unknown }).message;
    const technical = (e as { technical?: unknown }).technical;
    return {
      status: typeof status === "number" && Number.isFinite(status) ? status : 0,
      message: typeof message === "string" ? message : undefined,
      ...(typeof technical === "string" ? { technical } : {}),
    };
  }
  return { status: 0, message: typeof e === "string" ? e : undefined };
}

/** True when the server's own words are the ones to show: a 4xx carries a
 *  `detail` written for the user (a refused value, a missing record), where
 *  a 5xx or no answer carries engine text (a traceback, a socket error). */
export function serverSentence(error: FailureLike | null | undefined): boolean {
  return !!error && error.status >= 400 && error.status < 500 && !!(error.message ?? "").trim();
}
