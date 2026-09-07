// Command dispatch: one `browser.command` frame in, one `browser.response` out,
// keyed by request id.
//
// CONCURRENT IN-FLIGHT COMMANDS ARE REQUIRED (plan section 5.3). The daemon mints
// `req_<n>`, registers a pending future and does not wait for the previous answer,
// so a `read_page` on a slow tab must not delay a `status` behind it. Nothing here
// serialises: `dispatch` is called and its promise is not awaited by the caller,
// and the request id on the way out is copied from the frame on the way in.
//
// The silent failure this file exists to catch is a dropped or swapped answer.
// A handler that resolved without the dispatcher echoing the SAME id would resolve
// the daemon's future for a different call, and the model would receive one page's
// snapshot as the answer to another page's read — a wrong answer that looks
// completely well-formed. So the id is never regenerated, and a duplicate id is
// refused rather than allowed to overwrite a live entry.

import { FRAME_RESPONSE, type CommandFrame, type ResponseFrame } from "../protocol";
import { browserError, envelopeFor } from "./errors";

/** One method's implementation. Rejects with a `BridgeError` to refuse. */
export type CommandHandler = (params: Record<string, unknown>) => Promise<Record<string, unknown>>;

export class Dispatcher {
  private readonly handlers = new Map<string, CommandHandler>();
  private readonly inFlight = new Map<string, string>();

  /** Register `method`. Registering twice is a programming error, not a silent win. */
  register(method: string, handler: CommandHandler): void {
    if (this.handlers.has(method)) {
      throw new Error(`dispatch: ${method} is already registered`);
    }
    this.handlers.set(method, handler);
  }

  /** Method names this add-on can answer, for the README and for a status reply. */
  methods(): string[] {
    return [...this.handlers.keys()].sort();
  }

  /** How many commands are being worked on right now. Read by tests and the popup. */
  get pending(): number {
    return this.inFlight.size;
  }

  /**
   * Answer one command frame.
   *
   * Always resolves — never rejects — because the caller sends whatever comes back
   * and a rejection would mean no frame at all, which the daemon can only report as
   * ACTION_TIMEOUT after its 15-second deadline. A refusal that arrives in
   * milliseconds and names its remedy is the whole point of the error vocabulary.
   */
  async dispatch(frame: CommandFrame): Promise<ResponseFrame> {
    const id = String(frame.id ?? "");
    const method = String(frame.method ?? "");
    if (!id) {
      // No id means no future to resolve on the daemon side. Answering an empty
      // id would resolve nothing and hide the malformed frame; naming it does not.
      return fail("", browserError("EXTENSION_ERROR", { detail: "command frame carried no id" }));
    }
    if (this.inFlight.has(id)) {
      return fail(
        id,
        browserError("EXTENSION_ERROR", {
          detail: `request id ${id} is already in flight for ${this.inFlight.get(id)}`,
        }),
      );
    }
    const handler = this.handlers.get(method);
    if (!handler) {
      // An honest "not in this version" beats a silent no-op. Ship 1 registers the
      // three read methods; the page-reading and acting methods land in Ship 2 and
      // a daemon built ahead of the add-on must be told which half is missing.
      return fail(
        id,
        browserError("EXTENSION_ERROR", {
          detail: `the Iron Jarvis browser add-on does not implement ${method || "(no method)"} yet`,
        }),
      );
    }
    this.inFlight.set(id, method);
    try {
      const result = await handler({ ...(frame.params ?? {}) });
      return { id, type: FRAME_RESPONSE, success: true, result };
    } catch (err) {
      return fail(id, envelopeFor(err));
    } finally {
      this.inFlight.delete(id);
    }
  }
}

function fail(id: string, error: { code: string; message: string }): ResponseFrame {
  return { id, type: FRAME_RESPONSE, success: false, error };
}
