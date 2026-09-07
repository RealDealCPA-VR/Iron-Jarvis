// The service worker: the only place the add-on holds a socket.
//
// Everything the add-on does for Iron Jarvis happens here or is called from here.
// The popup and the setup page hold no socket of their own and talk to this worker
// through `chrome.runtime.sendMessage`, for one reason: there is exactly one live
// connection per browser (D08), and a second socket opened by a popup would REPLACE
// the worker's, take the session, and then die when the popup closed — leaving the
// daemon's card connected to a page that no longer exists.
//
// MV3 lifetime is the thing to know before changing this file. A service worker is
// evicted when idle, so every piece of state below is rebuilt from
// `chrome.storage.local` on the next wake, and the file's top level does real work:
// `boot()` runs on load, which is also what `onStartup` and `onInstalled` amount to.
// State that only exists in a module-level variable is state that silently resets,
// so the pairing token and the user's suspend choice live in storage and nothing
// else needs to survive.
//
// Ship 3 registers all fourteen methods: Ship 1's three tab-metadata reads, Ship 2's
// `read_page`, `get_elements` and `screenshot`, and the eight that ACT —
// `activate_tab`, `scroll`, `create_tab`, `close_tab`, `click`, `type_text`,
// `press_key` and `navigate`. An unregistered method is still answered with
// EXTENSION_ERROR naming it, so a daemon built ahead of the add-on learns which half
// is missing instead of timing out.
//
// EVERY ACTING METHOD READS THE HOST GRANT FRESH, exactly as the reads do, and for a
// sharper reason: the user can revoke site access from chrome://extensions between
// two calls, and a cached `true` would send a click at a page the add-on may no
// longer touch — surfacing as Chrome's own wording instead of the remedy naming the
// button to press.
//
// Downloads are the third thing that is NOT in this file: `background/downloads.ts`
// owns what a completed download means — completion only, the origin tab across an
// MV3 eviction, and the absolute path Chromium reports — and is handed one emitter
// so it never learns that a socket exists.
//
// The one thing this file DOES do about downloads is plan 10.3's single agent-facing
// capability: the `click` handler below awaits `awaitDownload` and puts what comes
// back on `ClickResult.download`, so the path of a file a click produced rides the
// answer to that click. An event the model never sees is a path it cannot hand to
// `read_document`, which is the whole point of 10.3 — and this call site is the only
// place the capability exists. Nothing else in the add-on calls `awaitDownload`.
//
// Note where the page work is NOT: none of it is in this file. `read_page` and
// `get_elements` go through `tabs.runInPage`, which injects `content/index.ts` on
// demand and speaks to it; the screenshot is `tabs.captureVisible`, which must live in
// the worker because only an extension page may call `captureVisibleTab`. This file
// stays what it was — the socket, the directives, the events and the popup's
// messages — and the reason is MV3 lifetime: a worker that also held the DOM walk
// would be a worker that gets evicted in the middle of one.

import {
  DIRECTIVE_DISCONNECT,
  DIRECTIVE_REQUEST_HOST_PERMISSIONS,
  EVENT_ID_PREFIX,
  EVENT_NAVIGATION_COMPLETED,
  EVENT_TAB_ACTIVATED,
  METHOD_ACTIVATE_TAB,
  METHOD_ACTIVE_TAB,
  METHOD_CLICK,
  METHOD_CLOSE_TAB,
  METHOD_CREATE_TAB,
  METHOD_GET_ELEMENTS,
  METHOD_LIST_TABS,
  METHOD_NAVIGATE,
  METHOD_PRESS_KEY,
  METHOD_READ_PAGE,
  METHOD_SCREENSHOT,
  METHOD_SCROLL,
  METHOD_STATUS,
  METHOD_TYPE_TEXT,
} from "../protocol";
import { BridgeError } from "../bridge/errors";
import { Dispatcher } from "../bridge/dispatch";
import { BridgeSocket, toggleAction, type BridgeStatus } from "../bridge/socket";
import { awaitDownload, watchDownloads } from "./downloads";
import { hasHostPermission, onHostPermissionChanged, openSetupPage } from "./hostperms";
import {
  activateTab,
  activeTab,
  captureVisible,
  clickInPage,
  closeTab,
  createTab,
  getElements,
  listTabs,
  navigate,
  pressKeyInPage,
  readPage,
  scrollInPage,
  tabRow,
  typeTextInPage,
} from "./tabs";

/** The dashboard page the popup's Open Jarvis button goes to. */
const JARVIS_URL = "http://127.0.0.1:8788/computeruse";

/** Messages the popup and the setup page send this worker. */
export type WorkerMessage =
  | { kind: "status" }
  | { kind: "open_jarvis" }
  | { kind: "toggle_connection" }
  | { kind: "request_host_permission" }
  | { kind: "host_permission_result"; granted: boolean };

const manifest = chrome.runtime.getManifest();
const dispatcher = new Dispatcher();

/** Monotonic within one worker lifetime, which is all an event id has to be. */
let eventSeq = 0;

/**
 * The last status this worker computed, so the popup renders immediately.
 *
 * A popup that had to wait for a round trip would paint an empty panel first and
 * then fill in, which reads as a broken add-on for the ~100ms it lasts.
 */
let lastStatus: BridgeStatus | null = null;

const socket = new BridgeSocket({
  dispatcher,
  hostPermission: hasHostPermission,
  onDirective: handleDirective,
  extensionId: chrome.runtime.id,
  extensionVersion: manifest.version,
  onStatusChange: (status) => {
    lastStatus = status;
  },
});

// --- the three tab-metadata read methods ------------------------------------

dispatcher.register(METHOD_STATUS, async () => {
  const hostPermission = await hasHostPermission();
  const focused = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
  const all = await chrome.tabs.query({});
  const active = focused[0];
  return {
    connected: true,
    host_permission: hostPermission,
    extension_id: chrome.runtime.id,
    extension_version: manifest.version,
    tab_count: all.length,
    active_tab: active ? tabRow(active, hostPermission) : null,
  };
});

dispatcher.register(METHOD_LIST_TABS, async () => {
  // Tab metadata survives a missing grant on purpose (plan section 6): the ids and
  // window ids are real, and each row says `needs_host_permission` so the model
  // asks for the grant rather than reporting that the user has no tabs open.
  return listTabs(await hasHostPermission());
});

dispatcher.register(METHOD_ACTIVE_TAB, async () => {
  // Spread rather than returned directly: a `TabRow` is a named interface and so
  // carries no index signature, which the response frame's `result` needs. The
  // spread keeps every key and its type while making the shape assignable.
  return { ...(await activeTab(await hasHostPermission())) };
});

// --- the three page read methods --------------------------------------------

// The host grant is read fresh on every call, never cached in a module variable: the
// user can revoke it from chrome://extensions between two calls, and a cached `true`
// would send the page reader at a tab it may no longer read — which surfaces as
// Chrome's own wording instead of the remedy naming the button to press.

dispatcher.register(METHOD_READ_PAGE, async (params) => {
  return readPage(params, await hasHostPermission());
});

dispatcher.register(METHOD_GET_ELEMENTS, async (params) => {
  return getElements(params, await hasHostPermission());
});

dispatcher.register(METHOD_SCREENSHOT, async (params) => {
  return captureVisible(params, await hasHostPermission());
});

// --- the eight acting methods (Ship 3) --------------------------------------

// The four that change the BROWSER. Three of them report a title and a URL, so they
// refuse without the site grant rather than name a page they cannot read;
// `close_tab` reports neither and so needs no grant. That line is drawn in tabs.ts,
// beside the code it governs.

dispatcher.register(METHOD_ACTIVATE_TAB, async (params) => {
  return activateTab(params, await hasHostPermission());
});

dispatcher.register(METHOD_CREATE_TAB, async (params) => {
  return createTab(params, await hasHostPermission());
});

dispatcher.register(METHOD_CLOSE_TAB, async (params) => {
  return closeTab(params);
});

dispatcher.register(METHOD_NAVIGATE, async (params) => {
  return navigate(params, await hasHostPermission());
});

// The four that change a PAGE. Each is one line because `runInPage` already holds
// the gate, the unsupported-page refusal and the injection; the work itself is in
// `content/actions.ts`, inside the page, where the live node is.

dispatcher.register(METHOD_CLICK, async (params) => {
  // BEFORE the click, not after: `awaitDownload` will only accept a completion
  // recorded at or after this moment, which is what stops a click that downloaded
  // nothing from claiming a file the user downloaded themselves earlier.
  const startedAt = Date.now();
  const result = await clickInPage(params, await hasHostPermission());
  // Plan 10.3. `clickInPage` has already resolved the real tab, so the result's
  // `tab_id` attributes the download better than the caller's params could — a
  // click with no `tab_id` acts on the active tab, and `params` would say `null`.
  const tabId = typeof result["tab_id"] === "number" ? (result["tab_id"] as number) : null;
  const download = await awaitDownload(tabId, startedAt);
  if (download === null) {
    // By far the common case: the click pressed a button. The result is returned
    // unchanged, WITHOUT a `download` key — an absent key reads downstream as "no
    // file", where a null one would have to be special-cased in three places.
    return result;
  }
  return { ...result, download: download as unknown as Record<string, unknown> };
});

dispatcher.register(METHOD_TYPE_TEXT, async (params) => {
  return typeTextInPage(params, await hasHostPermission());
});

dispatcher.register(METHOD_PRESS_KEY, async (params) => {
  return pressKeyInPage(params, await hasHostPermission());
});

dispatcher.register(METHOD_SCROLL, async (params) => {
  return scrollInPage(params, await hasHostPermission());
});

// --- directives -------------------------------------------------------------

/**
 * Handle one `browser.directive` from the daemon.
 *
 * `request_host_permissions` cannot grant anything by itself: the API needs a user
 * gesture (plan section 6, DEVIATION 1). So the honest result is `opened: true`
 * plus the grant's CURRENT state, never `granted: true` — a directive that claimed
 * a grant it merely asked for would make the card go green while every page read
 * still refused.
 */
async function handleDirective(
  action: string,
  _params: Record<string, unknown>,
): Promise<Record<string, unknown>> {
  if (action === DIRECTIVE_REQUEST_HOST_PERMISSIONS) {
    const already = await hasHostPermission();
    if (already) {
      return { opened: false, granted: true };
    }
    const opened = await openSetupPage();
    return { opened: true, granted: false, tab_id: opened.tab_id };
  }
  if (action === DIRECTIVE_DISCONNECT) {
    // Suspend, not just close: the daemon asked this browser to stop, and a socket
    // that reconnected two seconds later through normal backoff would undo the
    // user's action with no trace of why.
    await socket.suspend();
    return { disconnecting: true };
  }
  throw new BridgeError("EXTENSION_ERROR", { detail: `unknown directive ${action || "(none)"}` });
}

// --- browser events ---------------------------------------------------------

chrome.tabs.onActivated.addListener((info) => {
  void (async () => {
    const hostPermission = await hasHostPermission();
    let title: string | null = null;
    let url: string | null = null;
    if (hostPermission) {
      try {
        const tab = await chrome.tabs.get(info.tabId);
        title = tab.title ?? "";
        url = tab.url ?? "";
      } catch {
        // The tab closed between the event and the lookup. Emitting the id alone is
        // still true and still useful; inventing a title would not be.
      }
    }
    eventSeq += 1;
    socket.emitEvent(`${EVENT_ID_PREFIX}${eventSeq}`, EVENT_TAB_ACTIVATED, {
      tab_id: info.tabId,
      title,
      url,
    });
  })();
});

chrome.tabs.onUpdated.addListener((tabId, change, tab) => {
  // SAME-TAB NAVIGATION. Without this the daemon only ever hears onActivated,
  // which fires when the user SWITCHES tabs — so following a link in the tab you
  // are already in left Jarvis asserting the previous page's title and URL in the
  // ambient block, and left a snapshot of the old document cached as current.
  // Both are wrong in the most confident possible way: specific, plausible, and
  // about the one page the user is actually looking at.
  //
  // `status === "complete"` is the settle signal: onUpdated also fires for
  // "loading", for favicon changes and for title changes, and emitting on those
  // would report a document that is still moving. A title-only update (a
  // single-page app rewriting document.title after the URL changed) still
  // arrives, because it lands after the load completed and carries the new title.
  if (change.status !== "complete" && change.title === undefined) {
    return;
  }
  void (async () => {
    // Only the tab the user is looking at. A background tab finishing its load is
    // true but not what the ambient block is about, and the daemon merges this
    // onto its active-tab cache — so reporting a background tab here would
    // overwrite the active one with a page the user cannot see.
    let active = false;
    try {
      const [current] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
      active = current?.id === tabId;
    } catch {
      // Query failed (no focused window). Say nothing rather than guess.
      return;
    }
    if (!active) {
      return;
    }
    const hostPermission = await hasHostPermission();
    eventSeq += 1;
    socket.emitEvent(`${EVENT_ID_PREFIX}${eventSeq}`, EVENT_NAVIGATION_COMPLETED, {
      tab_id: tabId,
      // Without the site grant Chrome hands back empty strings, and an empty
      // title reads to a model as "this page has no title" — a lie it repeats to
      // the user. null says "not known", which is the truth.
      title: hostPermission ? (change.title ?? tab.title ?? "") : null,
      url: hostPermission ? (tab.url ?? "") : null,
    });
  })();
});

watchDownloads((event, payload) => {
  // Downloads are reported through the SAME event frame as everything else (D22:
  // no second bus, and no second transport either). `downloads.ts` owns what a
  // completion means and never touches the socket; this closure owns the event id,
  // because `eventSeq` is this worker's counter and two owners would mint the same
  // id for two different events.
  //
  // The site grant is NOT consulted here, and that is deliberate rather than an
  // omission. A download is a fact about the user's own filesystem reported by
  // `chrome.downloads`, which the "downloads" permission alone covers; gating it on
  // host permissions would silence the one event that works without them, on a page
  // Iron Jarvis was never allowed to read in the first place.
  eventSeq += 1;
  socket.emitEvent(`${EVENT_ID_PREFIX}${eventSeq}`, event, payload);
});

onHostPermissionChanged(() => {
  // Tell the daemon at once. Without this the card keeps saying "no site access"
  // until some other frame happens to be sent, and the user who just pressed the
  // button concludes it failed and presses it again.
  void socket.announce();
});

// --- messages from the popup and the setup page -----------------------------

chrome.runtime.onMessage.addListener((message: WorkerMessage, _sender, respond) => {
  // Read the discriminant into a plain string first. A message arrives from a page,
  // so it is untrusted input rather than a value TypeScript has already narrowed:
  // switching on the union directly leaves the default branch typed `never` and
  // there would be no way to report an unrecognised kind at all.
  const kind: string = (message as { kind?: string } | null)?.kind ?? "";
  void (async () => {
    switch (kind) {
      case "status":
        respond(lastStatus ?? socket.status());
        return;
      case "open_jarvis":
        await chrome.tabs.create({ url: JARVIS_URL, active: true });
        respond({ opened: true });
        return;
      case "toggle_connection": {
        // One button, both directions, and the DIRECTION comes from the same
        // function that writes the button's word (`toggleAction`), so the press can
        // never do the opposite of what the label said. It used to switch on
        // `state === "suspended"` alone, which meant the "Reconnect" offered after
        // another browser took over actually SUSPENDED this one, and a press in the
        // merely-offline state persisted a suspend flag nobody asked for.
        const action = toggleAction(socket.status().state);
        if (action === "connect") {
          await socket.resume();
        } else if (action === "disconnect") {
          await socket.suspend();
        }
        // `"none"`: the bridge is offline and already retrying. Doing nothing is the
        // honest answer, and the popup shows no button in that state anyway.
        respond(socket.status());
        return;
      }
      case "request_host_permission":
        await openSetupPage();
        respond({ opened: true });
        return;
      case "host_permission_result":
        // The setup page reports the outcome of its own click. `announce()` re-reads
        // the grant from Chrome rather than trusting the message, because the page
        // and the browser can disagree — the user may revoke it from
        // chrome://extensions a second later.
        await socket.announce();
        respond(socket.status());
        return;
      default:
        respond({ error: `unknown message ${kind || "(none)"}` });
        return;
    }
  })();
  // `true` keeps the message channel open for the async `respond` above. Returning
  // nothing here makes every reply arrive after the port closed, and the popup then
  // renders its "could not reach the add-on" state forever.
  return true;
});

// --- boot -------------------------------------------------------------------

function boot(): void {
  void socket.start();
}

chrome.runtime.onStartup.addListener(boot);
chrome.runtime.onInstalled.addListener(boot);

// And on plain worker wake-up, which is neither of the above and is by far the most
// common case: Chrome evicts an idle worker and re-runs this file when anything
// needs it again.
boot();
