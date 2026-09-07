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
// Ship 1 registers the three READ methods. `read_page`, `get_elements`, the
// screenshot and every acting method arrive in Ship 2 with the content script and
// the tools that reach them. Until then the dispatcher answers an unregistered
// method with EXTENSION_ERROR naming it, so a daemon built ahead of the add-on
// learns which half is missing instead of timing out.

import {
  DIRECTIVE_DISCONNECT,
  DIRECTIVE_REQUEST_HOST_PERMISSIONS,
  EVENT_ID_PREFIX,
  EVENT_TAB_ACTIVATED,
  METHOD_ACTIVE_TAB,
  METHOD_LIST_TABS,
  METHOD_STATUS,
} from "../protocol";
import { BridgeError } from "../bridge/errors";
import { Dispatcher } from "../bridge/dispatch";
import { BridgeSocket, toggleAction, type BridgeStatus } from "../bridge/socket";
import { hasHostPermission, onHostPermissionChanged, openSetupPage } from "./hostperms";
import { activeTab, listTabs, tabRow } from "./tabs";

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

// --- the three read methods -------------------------------------------------

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
