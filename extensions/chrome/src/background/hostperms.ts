// Site access: whether this add-on may read pages, and how the user grants it.
//
// The add-on installs with NO host permissions (Q02). `optional_host_permissions`
// in the manifest declares that it *may* ask for `<all_urls>` (v1.274.0; it was
// `http://*/*` and `https://*/*`, which screenshots cannot use — see HOST_ORIGINS),
// and nothing more happens until the user says yes.
//
// The grant CANNOT be requested from here. `chrome.permissions.request()` requires
// a user gesture and throws "This function must be called during a user gesture"
// in a service worker — Chrome's own words are "Permissions must be requested from
// inside a user gesture, like a button's click handler." Iron Jarvis is a page on
// another origin and cannot make the call either. So the daemon sends a
// `request_host_permissions` directive, this module opens the bundled setup page,
// and the one button there makes the call (plan section 6, DEVIATION 1).
//
// The silent failure that shape avoids: calling `request()` from the worker
// rejects, the daemon's directive comes back successful-looking or times out, and
// the user is told to press a button that never appears. Opening a real tab with a
// real button is the only path Chrome permits, so it is the path taken.
//
// What a missing grant costs, exactly (plan section 6): `chrome.tabs.query` still
// returns tabs, but with `url` and `title` as empty strings, and
// `chrome.scripting.executeScript` rejects. So tab metadata degrades honestly and
// everything touching a page refuses with PERMISSION_DENIED.

import { focusOrOpen } from "./openpage";

/** The two schemes the add-on ever asks for. Must match `optional_host_permissions`. */
// v1.274.0: `<all_urls>`, not `http://*/*` + `https://*/*`. Chromium's screenshot
// check (`PermissionsData::CanCaptureVisiblePage`) asks whether the granted
// hosts CONTAIN the `<all_urls>` pattern — two scheme patterns do not, so every
// `captureVisibleTab` failed with "Either the '<all_urls>' or 'activeTab'
// permission is required" on a grant that covered every web page. The prompt
// Chrome shows the user is the same sentence for both shapes. An install that
// granted the old pair sees "no site access" once and presses Grant again.
export const HOST_ORIGINS = ["<all_urls>"];

/** The bundled grant surface, relative to the extension root. */
export const SETUP_PAGE = "dist/setup.html";
/** v1.272.0: the microphone grant page — the same mechanism, for the same reason
 * (a prompt the browser shows only for a page in a tab). See src/mic/mic.html. */
export const MIC_PAGE = "dist/mic.html";

/**
 * Whether the user has granted access to both schemes.
 *
 * Both, not either: a grant covering only the https scheme would read as "site
 * access granted" while every plain-http intranet page still refused, and the user
 * would have no way to tell which half was missing.
 */
export async function hasHostPermission(): Promise<boolean> {
  try {
    return await chrome.permissions.contains({ origins: HOST_ORIGINS });
  } catch {
    // `contains` rejects only if the manifest never declared these origins as
    // optional. Reporting `false` is the honest answer: the add-on has no access
    // and cannot obtain any, and the refusal path already names the remedy.
    return false;
  }
}

/**
 * Open the setup page so the user can grant site access, and focus it.
 *
 * Re-uses an already-open setup tab instead of stacking duplicates: a directive
 * that arrives twice (the user pressed Grant site access twice, or a reconnect
 * replayed it) would otherwise leave two identical tabs and no clue which one is
 * live.
 */
export async function openSetupPage(): Promise<{ tab_id: number | null }> {
  return openAddonPage(SETUP_PAGE);
}

/** v1.272.0: open (or focus) the microphone grant page. */
export async function openMicPage(): Promise<{ tab_id: number | null }> {
  return openAddonPage(MIC_PAGE);
}

/** Focus the page if a tab already shows it, else open it in a new active tab
 * (v1.277.0: the one opener in `openpage.ts`, shared with Open Jarvis). */
async function openAddonPage(page: string): Promise<{ tab_id: number | null }> {
  const { tab_id } = await focusOrOpen(chrome.runtime.getURL(page));
  return { tab_id };
}

/**
 * Request the grant. Only ever called from a click handler on the setup page.
 *
 * Lives here rather than in `setup.ts` so the origin list has exactly one
 * definition: a setup page asking for a different pair of schemes than the
 * manifest declares fails with "Permissions are not declared as optional", and the
 * button would appear to do nothing at all.
 */
export async function requestHostPermission(): Promise<boolean> {
  return chrome.permissions.request({ origins: HOST_ORIGINS });
}

/** Run `cb` whenever the grant changes, in either direction. */
export function onHostPermissionChanged(cb: () => void): void {
  chrome.permissions.onAdded.addListener(() => cb());
  chrome.permissions.onRemoved.addListener(() => cb());
}
