// Downloads: what Chrome saved, where it really put it, and nothing else.
//
// D23 draws the whole flow, and the important half of it is what the add-on does
// NOT do:
//
//   page starts a download
//     -> Chrome owns it; the user's own download settings apply
//     -> chrome.downloads.onCreated + onChanged fire in this service worker
//     -> on state "complete" this module sends one browser.event download_completed
//     -> the daemon publishes browser.download_completed and the EXISTING file
//        tools reach the file
//
// The add-on never chooses a directory, never renames anything, never reads a
// byte of the file. It reports. Plan section 10.3 states the tradeoff plainly and
// this file must not paper over it: the file lands wherever Chrome puts it,
// normally ~/Downloads, and Iron Jarvis cannot silently redirect that.
//
// THE §32 REALITY CHECK, ANSWERED. Chromium documents `DownloadItem.filename` as
// an absolute local path, readable with the "downloads" permission alone — which
// manifest.json already declares. So there is no native messaging host here, and
// no filesystem access invented inside the extension, which is exactly what D23
// forbids. What the add-on sends is still only a CLAIM: the daemon verifies the
// string is absolute before it publishes `local_path`, the key the file tools
// read. This side says what the browser reported; the daemon says what it
// verified.
//
// Four silent failures this file is shaped around.
//
//   * COMPLETION ONLY. An interrupted, cancelled or paused download publishes
//     nothing. A "download_completed" event for a file that is not there is the
//     worst available answer: the model hands the path to read_document, gets a
//     missing-file error, and reports a failure the user cannot reproduce because
//     their own downloads list shows the transfer as cancelled.
//   * THE DELTA IS NOT THE ITEM. `onChanged` hands over a delta whose only
//     reliable field on completion is `state`; `filename`, `mime` and `fileSize`
//     are absent unless they changed in that same tick. Building the payload from
//     the delta therefore produces an event with an empty filename most of the
//     time, which reads downstream as "Chrome gave us no path" rather than as a
//     bug here. So the item is re-read with `chrome.downloads.search`.
//   * MV3 EVICTION BETWEEN onCreated AND onChanged. A download that takes thirty
//     seconds outlives an idle service worker, and a Map at module scope is empty
//     when the worker wakes for the completion. That is how `tab_id` silently
//     became absent on every download big enough to matter. The origin tab is
//     therefore written to `chrome.storage.session`, which survives worker
//     eviction, and the payload OMITS `tab_id` when it genuinely cannot be
//     determined rather than inventing the currently-active tab at completion
//     time — by then the user has usually switched tabs, and a confident wrong
//     tab id is worse than an absent one.
//   * DownloadItem HAS NO TAB. Chromium simply does not report which tab started
//     a download. The attribution here is the tab that was active when
//     `onCreated` fired, which is right for the case that matters (the model
//     clicked something in a tab and a file appeared) and is a best guess for a
//     background download the user started elsewhere. Named here because the
//     daemon treats `tab_id` as evidence when it decides which tool result may
//     claim the file.
//
// `awaitDownload` IS CALLED, and by exactly one caller: the `click` handler in
// `background/index.ts`. Plan 10.3's one agent-facing capability is that the
// completed download's absolute path appears in the RESULT of whichever browser
// action triggered it, because a model that asked for a file needs the path in the
// answer to the call it made — an event it never sees is a path it cannot hand to
// read_document. `clickInPage` returns, the handler awaits this, and what comes
// back rides `ClickResult.download`. If that call ever disappears the capability
// disappears with it and nothing else notices, so a pin in
// `tests/test_browser_actions_page_v1237.py` reads the call site.
//
// CLICK ONLY, and deliberately. `ClickResult` is the one result shape with a
// `download` field, and a click is the action that starts a transfer in practice.
// Taxing every `type_text`, `press_key` and `scroll` with a wait would spend real
// time on calls that have never produced a download.
//
// TWO BOUNDS, NOT ONE, because the ordinary click starts nothing at all. Waiting
// DOWNLOAD_SETTLE_MS on every click would add five seconds to every button press in
// the product. So the handler first waits DOWNLOAD_START_MS to learn whether a
// download was even CREATED — `chrome.downloads.onCreated` fires within a network
// round trip of the click that caused it — and only then waits out the settle bound
// for the completion. A click that started nothing pays the short bound and no more.
//
// AND A DOWNLOAD IS ONLY EVER MATCHED TO AN ACTION THAT COULD HAVE CAUSED IT. Every
// completion is stamped with the moment it was recorded and every claim carries the
// moment its action began; a file that finished BEFORE the click is not that click's
// file. Without that, an unclaimed download from ten minutes ago would be handed to
// the next click, and `BrowserClickTool.render` would tell the user "It downloaded a
// file to ..." about a click that downloaded nothing.

import { EVENT_DOWNLOAD_COMPLETED, type DownloadPayload } from "../protocol";

/**
 * How long an acting handler may wait for a download to finish before answering
 * without one.
 *
 * A BOUND, not a promise about speed. The click has already happened by the time
 * anything waits here, so the only question is whether the answer names the file;
 * waiting forever would turn "the click started no download" — by far the common
 * case — into a tool call that never returns.
 */
export const DOWNLOAD_SETTLE_MS = 5000;

/**
 * How long an acting handler waits to learn whether it started a download AT ALL.
 *
 * The short bound of the two, and the one that keeps the feature affordable: a
 * click that downloads nothing — nearly all of them — pays this and returns.
 * Chrome fires `chrome.downloads.onCreated` as soon as it decides a response is a
 * download, which is one network round trip after the click, so a few hundred
 * milliseconds is the honest window. A BOUND, not a promise about speed; no test
 * asserts an elapsed duration against it.
 */
export const DOWNLOAD_START_MS = 600;

/**
 * How many completed downloads and remembered origins are kept.
 *
 * A bound, not a cache policy. Each entry holds an absolute path to a file in the
 * user's own Downloads folder, and an unbounded map of those in a long browsing
 * session is both a leak and a pile of private paths with no reason to exist.
 */
const MAX_TRACKED = 16;

/** The `chrome.storage.session` key holding `download id -> origin tab id`. */
const ORIGIN_KEY = "ij_download_origins";

/** What `watchDownloads` is given so this module never touches the socket. */
export type EmitEvent = (event: string, payload: Record<string, unknown>) => void;

/** One completed download, when it landed, and whether a result already named it. */
interface Completed {
  payload: DownloadPayload;
  /** `Date.now()` at the moment the completion was recorded. See `takeDownload`. */
  at: number;
  claimed: boolean;
}

/** Someone awaiting the next completion, optionally for one tab. */
interface Waiter {
  tabId: number | null;
  /** Only a completion recorded at or after this moment may settle this waiter. */
  since: number;
  settle: (payload: DownloadPayload | null) => void;
}

const completed: Completed[] = [];
const waiters: Waiter[] = [];

/** `Date.now()` of each `onCreated`, newest last. Bounded by `MAX_TRACKED`. */
const creations: number[] = [];

/** Anyone waiting to hear whether a download STARTED. Woken by `noteCreation`. */
const startWaiters: Array<(started: boolean) => void> = [];

/**
 * Record that Chrome began a download, and wake anyone asking whether one had.
 *
 * Called from the `onCreated` listener SYNCHRONOUSLY, before the async origin
 * write. The origin lookup awaits `chrome.storage.session`, and a handler that
 * learned about the creation only after that round trip could miss its own
 * download inside the start bound.
 */
function noteCreation(): void {
  creations.push(Date.now());
  creations.splice(0, Math.max(0, creations.length - MAX_TRACKED));
  for (const settle of startWaiters.splice(0, startWaiters.length)) {
    settle(true);
  }
}

/** Whether Chrome began any download at or after `since`. */
function createdSince(since: number): boolean {
  return creations.some((at) => at >= since);
}

/**
 * Wait up to `timeoutMs` for a download to START, and say whether one did.
 *
 * `false` is the ordinary answer and is not a failure: it means the click pressed
 * a button rather than a download link, which is what almost every click does.
 */
function awaitStart(since: number, timeoutMs: number): Promise<boolean> {
  if (createdSince(since)) {
    return Promise.resolve(true);
  }
  return new Promise((resolve) => {
    const settle = (started: boolean): void => {
      resolve(started);
    };
    startWaiters.push(settle);
    setTimeout(() => {
      const index = startWaiters.indexOf(settle);
      if (index !== -1) {
        startWaiters.splice(index, 1);
        resolve(false);
      }
    }, timeoutMs);
  });
}

/**
 * Remember which tab a download came from, across a service-worker eviction.
 *
 * `chrome.storage.session` and not `local`: the attribution is worth nothing
 * after the browser restarts, and writing it to disk would leave a map of the
 * user's download ids in their profile for no benefit.
 */
async function rememberOrigin(downloadId: number, tabId: number | null): Promise<void> {
  if (tabId === null) {
    return;
  }
  try {
    const stored = await chrome.storage.session.get(ORIGIN_KEY);
    const raw = stored[ORIGIN_KEY];
    const origins: Record<string, number> =
      raw && typeof raw === "object" ? { ...(raw as Record<string, number>) } : {};
    origins[String(downloadId)] = tabId;
    const keys = Object.keys(origins);
    // Oldest first: download ids increase monotonically within a profile, so a
    // numeric sort is a real age order and not an insertion-order guess.
    keys.sort((a, b) => Number(a) - Number(b));
    for (const key of keys.slice(0, Math.max(0, keys.length - MAX_TRACKED))) {
      delete origins[key];
    }
    await chrome.storage.session.set({ [ORIGIN_KEY]: origins });
  } catch {
    // Storage is unavailable or full. The download still completes and is still
    // reported; it simply arrives without `tab_id`, which the payload allows.
    // Failing the report over a lost attribution would lose the file's path.
  }
}

/** The tab a download came from, or `null` when it cannot be determined. */
async function originOf(downloadId: number): Promise<number | null> {
  try {
    const stored = await chrome.storage.session.get(ORIGIN_KEY);
    const raw = stored[ORIGIN_KEY];
    if (!raw || typeof raw !== "object") {
      return null;
    }
    const value = (raw as Record<string, unknown>)[String(downloadId)];
    return typeof value === "number" ? value : null;
  } catch {
    return null;
  }
}

/** Forget one remembered origin — the download finished, or it never will. */
async function forgetOrigin(downloadId: number): Promise<void> {
  try {
    const stored = await chrome.storage.session.get(ORIGIN_KEY);
    const raw = stored[ORIGIN_KEY];
    if (!raw || typeof raw !== "object") {
      return;
    }
    const origins = { ...(raw as Record<string, number>) };
    delete origins[String(downloadId)];
    await chrome.storage.session.set({ [ORIGIN_KEY]: origins });
  } catch {
    // Nothing to do: a stale entry is bounded by MAX_TRACKED and costs a number.
  }
}

/** The tab the user is looking at right now, or `null` if there isn't one. */
async function activeTabId(): Promise<number | null> {
  try {
    const [tab] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
    return typeof tab?.id === "number" ? tab.id : null;
  } catch {
    return null;
  }
}

/**
 * Build the wire payload from a real `DownloadItem`.
 *
 * `final_url` is reported beside `source_url` because a download link bounces
 * through a CDN more often than not, and the two being different is the ordinary
 * case rather than an anomaly. `bytes` prefers `fileSize`, which is the size on
 * disk after any decompression; `bytesReceived` is what came over the wire and is
 * the honest fallback when the server sent no length.
 */
export function downloadPayload(
  item: chrome.downloads.DownloadItem,
  tabId: number | null,
): DownloadPayload {
  const payload: DownloadPayload = {
    download_id: item.id,
    filename: item.filename ?? "",
    source_url: item.url ?? "",
    final_url: item.finalUrl ?? item.url ?? "",
    bytes: item.fileSize > 0 ? item.fileSize : item.bytesReceived,
    mime: item.mime ?? "",
    timestamp: item.endTime ?? new Date().toISOString(),
  };
  if (tabId !== null) {
    payload.tab_id = tabId;
  }
  return payload;
}

/** Remember a completion and wake anyone waiting for it. */
function publishLocally(payload: DownloadPayload): void {
  const at = Date.now();
  completed.push({ payload, at, claimed: false });
  completed.splice(0, Math.max(0, completed.length - MAX_TRACKED));
  // Newest waiters last, but any waiter for this tab may take it. Exactly ONE
  // does: a file reported in two tool results is a file the model tells the user
  // about twice, and then copies into the project twice.
  for (let index = 0; index < waiters.length; index += 1) {
    const waiter = waiters[index];
    if (!waiter) {
      continue;
    }
    if (waiter.tabId !== null && payload.tab_id !== undefined && waiter.tabId !== payload.tab_id) {
      continue;
    }
    if (at < waiter.since) {
      // The same guard `takeDownload` applies, stated in both places so a
      // completion can never be attributed to an action that had already begun
      // after it. Cheap, and the alternative is a claim nobody can check later.
      continue;
    }
    waiters.splice(index, 1);
    const record = completed[completed.length - 1];
    if (record) {
      record.claimed = true;
    }
    waiter.settle(payload);
    return;
  }
}

/**
 * The newest completed download nothing has claimed yet, or `null`.
 *
 * CLAIMED rather than read: see `publishLocally`. A download whose origin tab is
 * unknown matches any request, because it is still a real file the user just
 * downloaded and refusing to name it would lose the path entirely.
 *
 * `since` is the moment the asking action began. It is not an optimisation: it is
 * what stops a click that downloaded nothing from claiming a file the user
 * downloaded themselves ten minutes earlier.
 */
export function takeDownload(tabId: number | null, since: number): DownloadPayload | null {
  for (let index = completed.length - 1; index >= 0; index -= 1) {
    const record = completed[index];
    if (!record || record.claimed) {
      continue;
    }
    if (record.at < since) {
      // A file that finished BEFORE this action began is not this action's file.
      // Without this line an unclaimed download from earlier in the session would
      // be handed to the next click, and BrowserClickTool.render would tell the
      // user that click downloaded something. Records are in age order, so the
      // rest are older still and the search is over.
      return null;
    }
    const owner = record.payload.tab_id;
    if (tabId !== null && owner !== undefined && owner !== tabId) {
      continue;
    }
    record.claimed = true;
    return record.payload;
  }
  return null;
}

/** Wait up to `timeoutMs` for a completion for `tabId` no older than `since`. */
function awaitCompletion(
  tabId: number | null,
  since: number,
  timeoutMs: number,
): Promise<DownloadPayload | null> {
  const already = takeDownload(tabId, since);
  if (already) {
    return Promise.resolve(already);
  }
  return new Promise((resolve) => {
    const waiter: Waiter = { tabId, since, settle: resolve };
    waiters.push(waiter);
    setTimeout(() => {
      const index = waiters.indexOf(waiter);
      if (index !== -1) {
        waiters.splice(index, 1);
        resolve(null);
      }
    }, timeoutMs);
  });
}

/**
 * The download the action that began at `since` produced, or `null`.
 *
 * THE ONE FUNCTION PLAN 10.3 IS BUILT ON, and the `click` handler in
 * `background/index.ts` is its caller. Returns `null` rather than throwing when
 * nothing arrives: no download is the ordinary outcome of a click, not a failure,
 * and an error here would turn every ordinary click into a failed tool call.
 *
 * Two bounds, for the reason the file header gives: the short one asks whether a
 * download was created at all and is what an ordinary click pays; the long one is
 * only ever paid by a click that really did start a transfer.
 *
 * `since` is captured by the caller BEFORE the click is dispatched, and is what
 * stops a stale completion from being reported as this click's file.
 */
export async function awaitDownload(
  tabId: number | null,
  since: number,
  startMs: number = DOWNLOAD_START_MS,
  settleMs: number = DOWNLOAD_SETTLE_MS,
): Promise<DownloadPayload | null> {
  const already = takeDownload(tabId, since);
  if (already) {
    return already;
  }
  if (!(await awaitStart(since, startMs))) {
    return null;
  }
  return awaitCompletion(tabId, since, settleMs);
}

/**
 * Start reporting completed downloads through `emit`.
 *
 * Registered at the top level of the service worker, like every other listener
 * here: MV3 re-runs the worker's top level on every wake, and a listener attached
 * inside an async callback is a listener Chrome may never see, which reads as
 * "downloads stopped being reported" with nothing in any log.
 */
export function watchDownloads(emit: EmitEvent): void {
  chrome.downloads.onCreated.addListener((item) => {
    // SYNCHRONOUS, before the await below: a click waiting on the start bound has
    // to hear about its own download without waiting for a storage round trip.
    noteCreation();
    void (async () => {
      // The origin tab is captured HERE, at creation, because it is the only
      // moment Chromium's model can answer the question at all — a DownloadItem
      // carries no tab. By completion the user has usually moved on.
      await rememberOrigin(item.id, await activeTabId());
    })();
  });

  chrome.downloads.onChanged.addListener((delta) => {
    void (async () => {
      const state = delta.state?.current;
      if (state === "interrupted") {
        // Nothing is published. The transfer failed or the user cancelled it, and
        // an event naming a file that is not on disk sends the model to read a
        // path that does not exist.
        await forgetOrigin(delta.id);
        return;
      }
      if (state !== "complete") {
        return;
      }
      // The delta says the state changed and little else; the item says where the
      // file is. Re-reading is the difference between a real path and an empty one.
      let item: chrome.downloads.DownloadItem | undefined;
      try {
        [item] = await chrome.downloads.search({ id: delta.id });
      } catch {
        item = undefined;
      }
      if (!item || item.state !== "complete" || !item.filename) {
        // Chrome no longer has the item, or has it without a path. Reporting a
        // completion with an empty filename would reach the daemon as a download
        // whose path could not be verified, which is a louder version of silence.
        await forgetOrigin(delta.id);
        return;
      }
      const tabId = await originOf(delta.id);
      await forgetOrigin(delta.id);
      const payload = downloadPayload(item, tabId);
      publishLocally(payload);
      emit(EVENT_DOWNLOAD_COMPLETED, payload as unknown as Record<string, unknown>);
    })();
  });
}
