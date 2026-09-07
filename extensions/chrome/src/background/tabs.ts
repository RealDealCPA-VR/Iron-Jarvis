// Tabs: the whole of what Ship 1 can see of the user's browser.
//
// Three methods, all read-only: `status`, `list_tabs`, `active_tab`. Activating,
// creating, closing and navigating a tab land in Ship 2 with the tools that reach
// them; writing them now would be code no caller can run, and this repository has
// already paid for a library that worked while nothing could reach it.
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

import { UNSUPPORTED_HOSTS, UNSUPPORTED_SCHEMES } from "../protocol";
import { BridgeError } from "../bridge/errors";

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
export async function activeTab(hostPermission: boolean): Promise<TabRow> {
  const focused = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
  const tab = focused[0] ?? (await chrome.tabs.query({ active: true }))[0];
  if (!tab || tab.id === undefined) {
    throw new BridgeError("TAB_NOT_FOUND", { tab_id: "active" });
  }
  return tabRow(tab, hostPermission);
}
