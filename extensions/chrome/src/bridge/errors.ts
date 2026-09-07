// Error envelopes for the browser half of the Browser Bridge.
//
// The daemon and the add-on must speak the SAME failure vocabulary, because a
// model reads the message and acts on it. So the codes and the remedy strings
// are not written here: they are imported from `../protocol`, which is generated
// from `src/iron_jarvis/browser/errors.py`. This module holds only the two
// things TypeScript needs that Python already has — placeholder substitution and
// a throwable carrier.
//
// The silent failure this file prevents: an add-on that invents its own wording
// ("could not click element") produces a message the model has never been taught
// to act on, so it retries the identical call forever instead of calling
// browser_read_page. Every refusal that leaves this add-on therefore carries a
// remedy that names the next call or the button to press.

import { REMEDIES, type BrowserErrorCode, type ErrorEnvelope } from "../protocol";

/** Values a remedy placeholder may be filled with. */
export type RemedyFormat = Record<string, string | number | boolean | null | undefined>;

/**
 * The remedy for `code`, with `{placeholder}` spans filled from `fmt`.
 *
 * Never throws and never returns an empty string. An unknown code yields a
 * message naming the code itself, and a placeholder with no value is left as
 * written rather than replaced with `undefined`: a refusal that reads
 * "No element undefined in snapshot undefined" is worse than one that shows the
 * template, because the first looks like a real answer.
 */
export function remedy(code: BrowserErrorCode | string, fmt: RemedyFormat = {}): string {
  const template = REMEDIES[code as BrowserErrorCode];
  if (!template) {
    return `Your browser reported ${code}, which Iron Jarvis does not have a remedy for. Call browser_get_status.`;
  }
  return template.replace(/\{(\w+)\}/g, (whole, name: string) => {
    const value = fmt[name];
    return value === undefined || value === null ? whole : String(value);
  });
}

/** The two-key envelope that rides `browser.response` when `success` is false. */
export function browserError(code: BrowserErrorCode | string, fmt: RemedyFormat = {}): ErrorEnvelope {
  return { code: String(code), message: remedy(code, fmt) };
}

/**
 * A failure a command handler can throw, carrying the code the daemon must see.
 *
 * Handlers throw this instead of returning a half-built result, so the dispatcher
 * has exactly one place that turns a failure into a response frame. A handler
 * that returned `{ error: ... }` on one path and threw on another would leak one
 * of the two shapes onto the wire, and the daemon's `success` flag would then
 * disagree with the body.
 */
export class BridgeError extends Error {
  readonly code: string;
  readonly fmt: RemedyFormat;

  constructor(code: BrowserErrorCode | string, fmt: RemedyFormat = {}) {
    super(remedy(code, fmt));
    this.name = "BridgeError";
    this.code = String(code);
    this.fmt = fmt;
  }

  envelope(): ErrorEnvelope {
    return { code: this.code, message: this.message };
  }
}

/**
 * Any thrown value as an envelope, so an unexpected exception is still actionable.
 *
 * A `chrome.*` API rejection is a plain `Error` with a message like
 * "Cannot access contents of the page" and no code. Left unmapped it would reach
 * the daemon as a hang (no response frame at all) and surface as ACTION_TIMEOUT
 * fifteen seconds later, which tells the model to retry the call that can never
 * work. Mapping it to EXTENSION_ERROR with the real detail says what happened.
 */
export function envelopeFor(err: unknown): ErrorEnvelope {
  if (err instanceof BridgeError) {
    return err.envelope();
  }
  const detail = err instanceof Error ? err.message : String(err);
  return browserError("EXTENSION_ERROR", { detail: detail || "unknown add-on failure" });
}
