// The one message shape that crosses from the service worker into a page.
//
// This file exists so the constant and the two types have ONE definition. The
// service worker cannot import `content/index.ts` — importing it would bundle the
// whole page reader into the worker AND run its top-level installer there — and the
// page cannot import `background/tabs.ts` for the same reason in reverse. A shared
// string duplicated in both halves is the classic drift: one side is renamed, the
// other keeps listening on the old name, and every read then fails with "the page
// reader did not answer" while both files read as correct. So the shared vocabulary
// lives here, in a module with no imports and no side effects, and both halves
// import it.
//
// Why the OP names are the wire method names (`read_page`, `get_elements`) rather
// than a second vocabulary: a model reads `read_page` in the error detail when the
// injection fails, and inventing an internal alias would mean the message a user
// sees names something that appears nowhere in the protocol.
//
// Why a failure crosses as a CODE plus its format arguments and not as a rendered
// message: `bridge/errors.ts` owns every remedy string, generated from Python. A
// page that rendered its own wording would be a second copy of the vocabulary, and
// a model taught to act on one wording would meet the other.

/** The discriminant on every message the worker sends into a page. */
export const PAGE_CHANNEL = "ironjarvis.page.v1";

/** Worker -> page: run one page operation. `params` is the command's own params. */
export interface PageRequest {
  channel: string;
  op: string;
  params: Record<string, unknown>;
}

/** Page -> worker: a refusal, as a code the daemon already has a remedy for. */
export interface PageFailure {
  code: string;
  fmt: Record<string, string | number | boolean | null>;
}

/** Page -> worker: the answer to one page operation. */
export type PageReply =
  | { ok: true; result: Record<string, unknown> }
  | { ok: false; failure: PageFailure };

/**
 * Whether `value` is one of our requests.
 *
 * Narrow, and deliberately so: a page reader shares `chrome.runtime.onMessage`
 * with the popup's status messages and with anything a future surface sends, and a
 * listener that answered every message would resolve the popup's `status` call with
 * a page snapshot.
 */
export function isPageRequest(value: unknown): value is PageRequest {
  if (typeof value !== "object" || value === null) {
    return false;
  }
  const candidate = value as { channel?: unknown; op?: unknown };
  return candidate.channel === PAGE_CHANNEL && typeof candidate.op === "string";
}
