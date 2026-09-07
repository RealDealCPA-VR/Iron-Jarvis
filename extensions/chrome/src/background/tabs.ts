// Tabs: what the add-on can see of the user's browser, and how it reaches a page.
//
// Fourteen methods now. Ship 1's three are read-only tab METADATA: `status`,
// `list_tabs`, `active_tab`. Ship 2 adds the three that need the page itself —
// `read_page`, `get_elements` and `screenshot` — so this file also owns the two
// bridges into a tab: `runInPage`, which injects the content script on demand and
// speaks to it, and `captureVisible`, which photographs a tab. Ship 3 adds the eight
// that ACT: `activate_tab`, `create_tab`, `close_tab` and `navigate`, which change
// the browser and live at the foot of this file, and `click`, `type_text`,
// `press_key` and `scroll`, which change a page and go through `runInPage` to
// `content/actions.ts`.
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
  METHOD_CLICK,
  METHOD_GET_ELEMENTS,
  METHOD_PRESS_KEY,
  METHOD_READ_PAGE,
  METHOD_SCROLL,
  METHOD_TYPE_TEXT,
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

// --- acting on a tab (Ship 3) -----------------------------------------------
//
// Four methods that change the user's browser rather than a page's contents:
// `activate_tab`, `create_tab`, `close_tab` and `navigate`. They live beside the
// reads because they resolve tabs the same way and refuse the same pages, and a
// second tab module would be a second answer to "which tab did you mean".
//
// THE HOST-GRANT LINE IS DRAWN BY WHAT THE RESULT CLAIMS, not by what the action
// touches. `activate_tab`, `create_tab` and `navigate` all report a `title` and a
// `url`, and without the grant Chrome hands back empty strings — so the honest
// answer would be "I switched you to a tab I cannot name", which is an action taken
// blind on a real browser. They refuse with PERMISSION_DENIED, which names the
// button. `close_tab` reports neither, so it works without the grant: refusing to
// close a tab the user asked to close, because we cannot read its title, would be a
// refusal with no reason a user could act on.

/** `activate_tab`: bring one tab to the front of its window. */
export async function activateTab(
  params: Record<string, unknown>,
  hostPermission: boolean,
): Promise<Record<string, unknown>> {
  if (!hostPermission) {
    throw new BridgeError("PERMISSION_DENIED");
  }
  const tab = await pageTab(params["tab_id"]);
  const tabId = tab.id as number;
  let updated: chrome.tabs.Tab | undefined;
  try {
    updated = await chrome.tabs.update(tabId, { active: true });
    // The WINDOW too. `tabs.update({active:true})` makes the tab current inside its
    // own window and leaves that window behind whichever one is on screen, so the
    // user would be told Iron Jarvis switched to a tab they still cannot see.
    await chrome.windows.update(tab.windowId, { focused: true });
  } catch (err) {
    throw new BridgeError("EXTENSION_ERROR", {
      detail: `tab ${tabId} could not be activated: ${reason(err)}`,
    });
  }
  return {
    tab_id: tabId,
    title: updated?.title ?? tab.title ?? "",
    url: updated?.url ?? tab.url ?? "",
    activated: true,
  };
}

/** `close_tab`: close one tab. Not undoable, which is why it is `IRREVERSIBLE`. */
export async function closeTab(params: Record<string, unknown>): Promise<Record<string, unknown>> {
  const raw = params["tab_id"];
  if (typeof raw !== "number" || !Number.isInteger(raw)) {
    // Deliberately NOT routed through `pageTab`, which falls back to the ACTIVE tab
    // when the id is missing or malformed. That fallback is right for a read and
    // catastrophic here: `close_tab` with a dropped id would close whatever the
    // user is looking at instead of refusing.
    throw new BridgeError("TAB_NOT_FOUND", { tab_id: String(raw ?? "(none)") });
  }
  try {
    await chrome.tabs.remove(raw);
  } catch (err) {
    throw new BridgeError("TAB_NOT_FOUND", { tab_id: `${raw} (${reason(err)})` });
  }
  return { tab_id: raw, closed: true };
}

/** `create_tab`: open a new tab, optionally at a URL. */
export async function createTab(
  params: Record<string, unknown>,
  hostPermission: boolean,
): Promise<Record<string, unknown>> {
  if (!hostPermission) {
    throw new BridgeError("PERMISSION_DENIED");
  }
  const url = String(params["url"] ?? "").trim();
  if (url) {
    const scheme = unsupportedScheme(url);
    if (scheme) {
      throw new BridgeError("UNSUPPORTED_PAGE", { scheme });
    }
  }
  let created: chrome.tabs.Tab;
  try {
    // `url` is omitted entirely when empty rather than sent as `about:blank`:
    // `about:` is on UNSUPPORTED_SCHEMES, so the model would be handed a tab id it
    // can never read or act on.
    created = await chrome.tabs.create({
      active: params["active"] !== false,
      ...(url ? { url } : {}),
    });
  } catch {
    throw new BridgeError("NAVIGATION_FAILED", { url: url || "(a blank tab)" });
  }
  if (created.id === undefined) {
    throw new BridgeError("EXTENSION_ERROR", {
      detail: "Chrome opened a tab without giving it an id, so it cannot be acted on",
    });
  }
  const settled = await settle(created.id);
  return {
    tab_id: created.id,
    url: settled.url ?? created.pendingUrl ?? url,
    title: settled.title ?? "",
  };
}

/**
 * `navigate`: point one tab at a URL and wait for it to finish loading.
 *
 * `page_version` is reported as 0, and the zero is the point. The document that
 * loads is BRAND NEW: the previous content script died with the previous document,
 * and no snapshot of the new one exists. Reporting the old version, or inventing a
 * 1, would let the daemon compare equal against a registry that describes a page
 * that is gone — and the next `browser_click` would resolve an element id against
 * it. Zero matches no real version, so every id from before this call answers
 * STALE_ELEMENT, which is the truth.
 *
 * NAVIGATION_FAILED is reported only where it can honestly be detected: Chrome
 * rejects a malformed URL, and that rejection is a real failure. A DNS error or a
 * 404 is NOT a rejection — Chrome loads its own error page and reports `complete` —
 * so those come back as a successful navigation whose title says what went wrong,
 * which is what the user's own browser shows them too.
 */
export async function navigate(
  params: Record<string, unknown>,
  hostPermission: boolean,
): Promise<Record<string, unknown>> {
  if (!hostPermission) {
    throw new BridgeError("PERMISSION_DENIED");
  }
  const url = String(params["url"] ?? "").trim();
  if (!url) {
    throw new BridgeError("NAVIGATION_FAILED", { url: "(no address)" });
  }
  const scheme = unsupportedScheme(url);
  if (scheme) {
    throw new BridgeError("UNSUPPORTED_PAGE", { scheme });
  }
  const tab = await pageTab(params["tab_id"]);
  const tabId = tab.id as number;
  try {
    await chrome.tabs.update(tabId, { url });
  } catch (err) {
    throw new BridgeError("NAVIGATION_FAILED", { url: `${url} (${reason(err)})` });
  }
  const settled = await settle(tabId);
  return {
    tab_id: tabId,
    url: settled.url ?? url,
    title: settled.title ?? "",
    page_version: 0,
    // "complete" or "loading". A load that outran the wait is reported as still
    // loading rather than as complete: the model then calls browser_read_page,
    // which refuses with PAGE_NOT_READY until the document is actually there.
    status: settled.status ?? "unknown",
  };
}

/** How long a create or a navigate waits for the tab to report `complete`. */
export const SETTLE_TIMEOUT_MS = 20000;

/**
 * The three things a settled tab is asked for, and nothing else.
 *
 * Narrower than `chrome.tabs.Tab` on purpose: the closed-tab path has no real tab to
 * return, and a hand-built `Tab` would have to invent `index`, `pinned`,
 * `highlighted` and eight more fields that would then travel to the daemon as though
 * Chrome had said them.
 */
interface SettledTab {
  url?: string;
  title?: string;
  status?: string;
}

/**
 * Wait for `tabId` to finish loading, and return it however it ends up.
 *
 * NEVER REJECTS AND NEVER HANGS. A page that loads slowly, a page that never
 * finishes, and a tab the user closes mid-load all resolve — with whatever the tab
 * last said. A wait that could hang would burn the daemon's whole command timeout
 * and report ACTION_TIMEOUT ("your browser did not answer"), which is false: the
 * browser answered, the page is just still loading, and the two need different
 * remedies.
 *
 * The listener is removed on every exit path, including the timeout. A service
 * worker accumulates listeners across calls and is evicted while holding them, so a
 * leaked one is a leak that survives nothing and confuses everything in between.
 */
async function settle(tabId: number): Promise<SettledTab> {
  const current = async (): Promise<SettledTab> => {
    try {
      const tab = await chrome.tabs.get(tabId);
      return { url: tab.url, title: tab.title, status: tab.status };
    } catch {
      // The tab closed. Reporting an unknown status is honest; throwing here would
      // turn "the user closed the tab" into an extension error, and inventing a
      // half-built `chrome.tabs.Tab` would put fabricated fields on the wire.
      return { status: "unknown" };
    }
  };
  const already = await current();
  if (already.status === "complete") {
    return already;
  }
  return new Promise<SettledTab>((resolve) => {
    let done = false;
    const finish = (tab: SettledTab): void => {
      if (done) {
        return;
      }
      done = true;
      chrome.tabs.onUpdated.removeListener(onUpdated);
      clearTimeout(timer);
      resolve(tab);
    };
    const onUpdated = (
      id: number,
      change: chrome.tabs.OnUpdatedInfo,
      tab: chrome.tabs.Tab,
    ): void => {
      if (id === tabId && change.status === "complete") {
        finish({ url: tab.url, title: tab.title, status: tab.status });
      }
    };
    const timer = setTimeout(() => {
      void current().then(finish);
    }, SETTLE_TIMEOUT_MS);
    chrome.tabs.onUpdated.addListener(onUpdated);
  });
}

// --- acting inside a page (Ship 3) ------------------------------------------
//
// The four page actions go through `runInPage`, the same door the two reads use,
// and so inherit its host-permission gate, its unsupported-page refusal and its
// on-demand injection with nothing restated. `scroll` is here rather than in the
// worker for the same reason `read_page` is: the scroll position belongs to the
// document, and only the injected half can see one.

/** `click`: press one element in a page. */
export async function clickInPage(
  params: Record<string, unknown>,
  hostPermission: boolean,
): Promise<Record<string, unknown>> {
  return runInPage(METHOD_CLICK, params, hostPermission);
}

/** `type_text`: fill one field. The result never carries what was typed. */
export async function typeTextInPage(
  params: Record<string, unknown>,
  hostPermission: boolean,
): Promise<Record<string, unknown>> {
  return runInPage(METHOD_TYPE_TEXT, params, hostPermission);
}

/** `press_key`: send one key to a page. */
export async function pressKeyInPage(
  params: Record<string, unknown>,
  hostPermission: boolean,
): Promise<Record<string, unknown>> {
  return runInPage(METHOD_PRESS_KEY, params, hostPermission);
}

/** `scroll`: move the user's view of one page. */
export async function scrollInPage(
  params: Record<string, unknown>,
  hostPermission: boolean,
): Promise<Record<string, unknown>> {
  return runInPage(METHOD_SCROLL, params, hostPermission);
}
