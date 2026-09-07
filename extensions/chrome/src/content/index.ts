// The injected half: the only Iron Jarvis code that ever touches a page.
//
// THERE IS NO `content_scripts` BLOCK IN THE MANIFEST AND THERE MUST NOT BE ONE
// (plan section 6). A declared content script runs in every tab the user opens, for
// the life of the browser, whether or not Iron Jarvis is doing anything — which is
// exactly the ambient presence the pairing model exists to avoid. This file is
// injected by `chrome.scripting.executeScript` at the moment a read tool runs, and
// it is gone when the tab navigates.
//
// INSTALLS EXACTLY ONCE PER DOCUMENT, and the guard is load-bearing rather than
// tidy. `executeScript` re-runs the bundle on every call, so without the flag each
// read would install a second `onMessage` listener — two listeners answering one
// message, one of them winning by timing — and, worse, would build a second
// `ElementRegistry` starting at `page_version` 1. A genuinely changed page would then
// present as unchanged and a stale element id would resolve to whatever node now
// holds that slot. The flag lives on `globalThis` because each execution gets its own
// module scope but shares the isolated world's global object.
//
// The listener is deliberately NARROW (`isPageRequest`): the popup's `status`
// message and anything a later surface sends travel the same channel, and a listener
// that answered everything would resolve the popup's call with a page snapshot.
//
// A refusal leaves here as a CODE plus its format arguments, never as a rendered
// sentence — `bridge/errors.ts` owns the remedy vocabulary, generated from Python, so
// the model reads one wording no matter which half of the add-on refused.

import {
  METHOD_CLICK,
  METHOD_GET_ELEMENTS,
  METHOD_PRESS_KEY,
  METHOD_READ_PAGE,
  METHOD_SCROLL,
  METHOD_TYPE_TEXT,
} from "../protocol";
import { BridgeError } from "../bridge/errors";
import { click, pressKey, scroll, typeText } from "./actions";
import { isPageRequest, type PageFailure, type PageReply, type PageRequest } from "./channel";
import { registry } from "./elements";
import { getElements, readPage } from "./snapshot";

/** The one-install flag, on the isolated world's global object. */
const INSTALL_FLAG = "__ironJarvisPageReader";

type Installable = Record<string, unknown>;

/** Run one page operation, or throw a `BridgeError` a model can act on. */
function handle(request: PageRequest): Record<string, unknown> {
  const params = { ...(request.params ?? {}) };
  if (request.op === METHOD_READ_PAGE) {
    return { ...readPage(params) };
  }
  if (request.op === METHOD_GET_ELEMENTS) {
    return { ...getElements(params) };
  }
  // The four ACTING operations (Ship 3). They are dispatched from the same table as
  // the two reads on purpose: one listener, one refusal shape, one place a reviewer
  // can see everything this add-on will do inside a page. Every one of them is
  // SYNCHRONOUS — see the listener below for why that is load-bearing rather than
  // stylistic.
  if (request.op === METHOD_CLICK) {
    return { ...click(params) };
  }
  if (request.op === METHOD_TYPE_TEXT) {
    return { ...typeText(params) };
  }
  if (request.op === METHOD_PRESS_KEY) {
    return { ...pressKey(params) };
  }
  if (request.op === METHOD_SCROLL) {
    return { ...scroll(params) };
  }
  // Anything else. An honest "not in this version, and here is the name" beats a
  // silent no-op: a daemon built ahead of the add-on learns which half is missing
  // instead of waiting out its timeout and reporting that the browser never
  // answered.
  throw new BridgeError("EXTENSION_ERROR", {
    detail: `the Iron Jarvis browser add-on's page reader does not implement ${
      request.op || "(no operation)"
    } yet`,
  });
}

/** Any thrown value as the two-field failure the worker turns back into a remedy. */
function failureOf(err: unknown): PageFailure {
  if (err instanceof BridgeError) {
    return { code: err.code, fmt: { ...err.fmt } as PageFailure["fmt"] };
  }
  const detail = err instanceof Error ? err.message : String(err);
  return {
    code: "EXTENSION_ERROR",
    fmt: { detail: detail || "the page reader failed without saying why" },
  };
}

function install(): void {
  const world = globalThis as unknown as Installable;
  if (world[INSTALL_FLAG] === true) {
    return;
  }
  world[INSTALL_FLAG] = true;

  // Watch the document from the first injection, not from the first snapshot: a page
  // that mutates between the injection and the read has already moved, and
  // `page_version` has to say so.
  registry.observe();

  chrome.runtime.onMessage.addListener((message: unknown, _sender, respond) => {
    if (!isPageRequest(message)) {
      // Not ours. Returning false leaves the message to whichever listener it was
      // meant for instead of closing the port under it.
      return false;
    }
    let reply: PageReply;
    try {
      reply = { ok: true, result: handle(message) };
    } catch (err) {
      reply = { ok: false, failure: failureOf(err) };
    }
    respond(reply);
    // The whole walk is synchronous, so the answer is already sent. Returning true
    // here would hold the port open for an async reply that will never come, and the
    // worker would wait out its timeout on a call that has already answered.
    //
    // SYNCHRONOUS ALSO MATTERS FOR THE ACTIONS, and for a harder reason than tidiness:
    // a click can navigate the document, and a navigating document tears this script
    // down. An action that awaited anything before responding would be killed
    // mid-await on exactly the clicks that worked, and the model would be told the
    // browser never answered a call that in fact signed the user in.
    return false;
  });
}

install();
