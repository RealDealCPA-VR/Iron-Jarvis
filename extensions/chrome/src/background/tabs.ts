// Tabs: what the add-on can see of the user's browser, and how it reaches a page.
//
// Six methods now. Ship 1's three are read-only tab METADATA: `status`, `list_tabs`,
// `active_tab`. Ship 2 adds the three that need the page itself — `read_page`,
// `get_elements` and `screenshot` — so this file also owns the two bridges into a
// tab: `runInPage`, which injects the content script on demand and speaks to it, and
// `captureVisible`, which photographs a tab. Activating, creating, closing and
// navigating a tab land in Ship 3 with the tools that reach them; writing them now
// would be code no caller can run, and this repository has already paid for a
// library that worked while nothing could reach it.
//
// WHY THE SCREENSHOT LIVES HERE AND NOT IN THE CONTENT SCRIPT: only an extension
// page may call `chrome.tabs.captureVisibleTab`, and it photographs whatever is
// VISIBLE in a window — so it takes a window id, not a tab id, and it cannot
// photograph a tab that is not the one on screen. A content script could only
// reproduce it by scrolling and stitching, which is an ACTION on the user's page and
// belongs to Ship 3 at the earliest.
//
// The row shape here is the contract the daemon's tools re-serve to a model, and
// two of its keys exist to prevent a specific lie:
//
//   * `needs_host_permission` with `title` and `url` as NULL when site access is
//     absent. Chrome hands the add-on a tab whose `url` and `title` are empty
//     STRINGS in that case. Passing `""` through reads to a model as "this tab has
//     no title", which is false and unactionable; `null` beside a flag reads as
//     "not readable yet, and here is why".
//   * `supported: false` for a page Chrome closes to add-ons — `chrome://`,
//     `about:`, the Web Store. Such a tab is still LISTED with its real title and
//     URL, because hiding the tab the user is actually looking at would make the
//     answer wrong in a way the user can see; only acting on it is refused.

import {
  MAX_FRAME_BYTES,
  METHOD_GET_ELEMENTS,
  METHOD_READ_PAGE,
  UNSUPPORTED_HOSTS,
  UNSUPPORTED_SCHEMES,
} from "../protocol";
import { BridgeError } from "../bridge/errors";
import { PAGE_CHANNEL, type PageReply, type PageRequest } from "../content/channel";

/** One tab as the daemon and the model see it. */
export interface TabRow {
  id: number;
  title: string | null;
  url: string | null;
  active: boolean;
  window_id: number;
  status: string;
  supported: boolean;
  needs_host_permission: boolean;
}

/**
 * The scheme to name in an UNSUPPORTED_PAGE refusal, or `""` when the page is fine.
 *
 * A mirror of `protocol.unsupported_page_scheme` in Python, deliberately including
 * its choice to report `https:` for a host-closed page: that is what the user sees
 * in the address bar, and naming a scheme the URL does not have reads as a bug in
 * the answer rather than as a policy.
 */
export function unsupportedScheme(url: string): string {
  const lowered = (url || "").trim().toLowerCase();
  for (const scheme of UNSUPPORTED_SCHEMES) {
    if (lowered.startsWith(scheme)) {
      return scheme;
    }
  }
  for (const host of UNSUPPORTED_HOSTS) {
    if (lowered.startsWith(`https://${host}/`) || lowered === `https://${host}`) {
      return "https:";
    }
  }
  return "";
}

/** Build one row from a Chrome tab, honouring the host-permission degradation. */
export function tabRow(tab: chrome.tabs.Tab, hostPermission: boolean): TabRow {
  const url = tab.url ?? "";
  return {
    id: tab.id ?? -1,
    title: hostPermission ? (tab.title ?? "") : null,
    url: hostPermission ? url : null,
    active: tab.active === true,
    window_id: tab.windowId ?? -1,
    status: tab.status ?? "unknown",
    // Classified from the URL, which is only readable with the grant. Without it
    // the honest answer is "assume supported": a tab claimed unsupported on no
    // evidence would be refused for a reason that may not be true.
    supported: hostPermission ? unsupportedScheme(url) === "" : true,
    needs_host_permission: !hostPermission,
  };
}

/** Every open tab, in Chrome's own order. */
export async function listTabs(hostPermission: boolean): Promise<{ tabs: TabRow[]; count: number }> {
  const tabs = await chrome.tabs.query({});
  const rows = tabs.filter((tab) => tab.id !== undefined).map((tab) => tabRow(tab, hostPermission));
  return { tabs: rows, count: rows.length };
}

/**
 * The tab the user is looking at, in the focused window.
 *
 * `lastFocusedWindow` rather than `currentWindow`: a service worker has no window
 * of its own, so `currentWindow` can resolve to whichever window Chrome last
 * associated with the extension — including a popup that has since closed — and the
 * answer would name a tab the user is not looking at, which is worse than no
 * answer at all.
 */
export async function currentTab(): Promise<chrome.tabs.Tab> {
  const focused = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
  const tab = focused[0] ?? (await chrome.tabs.query({ active: true }))[0];
  if (!tab || tab.id === undefined) {
    throw new BridgeError("TAB_NOT_FOUND", { tab_id: "active" });
  }
  return tab;
}

export async function activeTab(hostPermission: boolean): Promise<TabRow> {
  return tabRow(await currentTab(), hostPermission);
}

// --- reaching into a page ---------------------------------------------------

/** The injected bundle, as `manifest.json` and `esbuild.config.mjs` both name it. */
export const CONTENT_SCRIPT_FILE = "dist/content.js";

/**
 * The tab a page command names, or the active one when it names none.
 *
 * A missing `tab_id` means the active tab throughout the protocol, so the resolution
 * happens HERE rather than in the page: the content script has no way to learn its own
 * tab id, and the id is what the snapshot reports back as the tab it actually read.
 */
async function pageTab(tabId: unknown): Promise<chrome.tabs.Tab> {
  if (typeof tabId !== "number" || !Number.isInteger(tabId)) {
    return currentTab();
  }
  try {
    const tab = await chrome.tabs.get(tabId);
    if (tab.id === undefined) {
      throw new Error("no id");
    }
    return tab;
  } catch {
    throw new BridgeError("TAB_NOT_FOUND", { tab_id: tabId });
  }
}

/**
 * Refuse a page Chrome closes to add-ons, naming the scheme (plan section 9.7).
 *
 * The daemon runs this same check before it sends the command, so this is a BACKSTOP
 * — and it earns its place: without it, `executeScript` against a `chrome://` tab
 * rejects with "Cannot access a chrome:// URL", which this file would otherwise map to
 * PERMISSION_DENIED and send the user off to press a Grant site access button that
 * cannot help.
 */
function refuseUnsupported(tab: chrome.tabs.Tab): void {
  const scheme = unsupportedScheme(tab.url ?? "");
  if (scheme) {
    throw new BridgeError("UNSUPPORTED_PAGE", { scheme });
  }
}

/** A thrown value's message, for a refusal that has to say what Chrome said. */
function reason(err: unknown): string {
  const detail = err instanceof Error ? err.message : String(err);
  return detail || "no reason given";
}

/**
 * Inject the page reader into `tabId`. Cheap to repeat: it installs once per document.
 *
 * `Cannot access contents of the page` is Chrome's wording when the host grant is
 * absent, and mapping it to PERMISSION_DENIED is what puts the right remedy in front
 * of the model — the alternative, EXTENSION_ERROR, tells the user to reload the add-on,
 * which will not help and hides the one button that will.
 */
async function inject(tabId: number): Promise<void> {
  try {
    await chrome.scripting.executeScript({ target: { tabId }, files: [CONTENT_SCRIPT_FILE] });
  } catch (err) {
    const detail = reason(err);
    if (/cannot access|not granted|host permission|extension manifest/i.test(detail)) {
      throw new BridgeError("PERMISSION_DENIED");
    }
    throw new BridgeError("EXTENSION_ERROR", {
      detail: `the page reader could not be injected into tab ${tabId}: ${detail}`,
    });
  }
}

/**
 * Run one operation inside a page and return its result.
 *
 * The host-permission gate is first and unconditional. Plan section 6 draws the line
 * exactly here: tab METADATA survives a missing grant, and everything that touches a
 * page refuses with the remedy naming the button to press. Without the gate,
 * `executeScript` rejects with a message that varies by Chrome version.
 */
export async function runInPage(
  op: string,
  params: Record<string, unknown>,
  hostPermission: boolean,
): Promise<Record<string, unknown>> {
  if (!hostPermission) {
    throw new BridgeError("PERMISSION_DENIED");
  }
  const tab = await pageTab(params["tab_id"]);
  refuseUnsupported(tab);
  const tabId = tab.id as number;
  await inject(tabId);
  const request: PageRequest = { channel: PAGE_CHANNEL, op, params: { ...params, tab_id: tabId } };
  let reply: PageReply | undefined;
  try {
    reply = (await chrome.tabs.sendMessage(tabId, request)) as PageReply | undefined;
  } catch (err) {
    throw new BridgeError("EXTENSION_ERROR", {
      detail: `the page reader in tab ${tabId} did not answer: ${reason(err)}`,
    });
  }
  if (!reply || typeof reply !== "object") {
    // A listener that returned nothing, which is what a page reader unloaded mid-call
    // looks like. Saying so beats waiting out the daemon's timeout.
    throw new BridgeError("EXTENSION_ERROR", {
      detail: `the page reader in tab ${tabId} answered with nothing`,
    });
  }
  if (reply.ok) {
    return reply.result;
  }
  // The code and its arguments, rendered by the one remedy table (`bridge/errors.ts`).
  throw new BridgeError(reply.failure.code, reply.failure.fmt);
}

/** `read_page`: the bounded semantic snapshot of plan section 9.1. */
export async function readPage(
  params: Record<string, unknown>,
  hostPermission: boolean,
): Promise<Record<string, unknown>> {
  return runInPage(METHOD_READ_PAGE, params, hostPermission);
}

/** `get_elements`: the same snapshot, filtered to what was asked for. */
export async function getElements(
  params: Record<string, unknown>,
  hostPermission: boolean,
): Promise<Record<string, unknown>> {
  return runInPage(METHOD_GET_ELEMENTS, params, hostPermission);
}

// --- screenshots (D14) ------------------------------------------------------

/**
 * The capture attempts, in order, and why there is more than one.
 *
 * A response frame over `MAX_FRAME_BYTES` is dropped by the daemon BEFORE it is
 * parsed, so its request id is unknowable and the screenshot call burns its whole
 * timeout to report ACTION_TIMEOUT — "your browser did not answer in time" — when the
 * browser answered in 200 ms with a picture that was too big. A full-window PNG passes
 * 512 KB routinely, so a PNG-only add-on would fail that way on any large monitor.
 *
 * PNG is therefore tried first and kept when it fits; otherwise the same frame is
 * re-encoded as JPEG at falling quality. `media_type` on the result says which one the
 * bytes actually are, so the tool that saves the artifact must name the file from
 * `media_type` and not assume `.png`.
 */
const CAPTURE_ATTEMPTS: chrome.extensionTypes.ImageDetails[] = [
  { format: "png" },
  { format: "jpeg", quality: 80 },
  { format: "jpeg", quality: 60 },
  { format: "jpeg", quality: 40 },
];

/** How many bytes of a response frame the image itself may use. */
export const IMAGE_BUDGET_BYTES = MAX_FRAME_BYTES - 8192;

/**
 * `screenshot`: the visible area of one tab, as base64 image bytes (D14).
 *
 * Two honest refusals rather than a wrong picture:
 *
 *   * a tab that is not the one on screen cannot be photographed at all, and
 *     activating it to try would change what the user is looking at inside a READ
 *     tool. So it refuses and says which tab is in the way.
 *   * `full_page` cannot be honoured. `captureVisibleTab` is the viewport, and a
 *     full-page capture means scrolling the user's page and stitching the frames —
 *     an action, and Ship 3 at the earliest. The result therefore carries
 *     `full_page: false` whatever was asked for, so the tool can tell the model it is
 *     looking at the viewport instead of asserting a full page it never received.
 */
export async function captureVisible(
  params: Record<string, unknown>,
  hostPermission: boolean,
): Promise<Record<string, unknown>> {
  if (!hostPermission) {
    throw new BridgeError("PERMISSION_DENIED");
  }
  const tab = await pageTab(params["tab_id"]);
  refuseUnsupported(tab);
  if (tab.active !== true) {
    throw new BridgeError("EXTENSION_ERROR", {
      detail:
        `Chrome can only photograph the tab that is on screen, and tab ${tab.id} is not ` +
        "the active tab in its window. Ask the user to switch to it, or take the " +
        "screenshot of the active tab instead",
    });
  }
  const windowId = tab.windowId;
  let largest = 0;
  for (const attempt of CAPTURE_ATTEMPTS) {
    let dataUrl: string;
    try {
      dataUrl = await chrome.tabs.captureVisibleTab(windowId, attempt);
    } catch (err) {
      throw new BridgeError("EXTENSION_ERROR", {
        detail: `the screenshot of tab ${tab.id} failed: ${reason(err)}`,
      });
    }
    const parsed = /^data:([^;,]+);base64,(.*)$/.exec(dataUrl ?? "");
    if (!parsed || !parsed[1] || !parsed[2]) {
      throw new BridgeError("EXTENSION_ERROR", {
        detail: `the screenshot of tab ${tab.id} came back in a form the add-on cannot read`,
      });
    }
    const [, mediaType, data] = parsed;
    largest = Math.max(largest, data.length);
    if (data.length <= IMAGE_BUDGET_BYTES) {
      return {
        tab_id: tab.id ?? -1,
        media_type: mediaType,
        data_b64: data,
        bytes: data.length,
        full_page: false,
      };
    }
  }
  throw new BridgeError("EXTENSION_ERROR", {
    detail:
      `this screen's picture is ${largest} base64 bytes even as a compressed JPEG, over the ` +
      `${MAX_FRAME_BYTES} byte frame limit, so it was not sent. Ask the user to make the ` +
      "browser window smaller and try again",
  });
}
