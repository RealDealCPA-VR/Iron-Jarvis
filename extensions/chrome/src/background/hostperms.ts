// Site access: whether this add-on may read pages, and how the user grants it.
//
// The add-on installs with NO host permissions (Q02). `optional_host_permissions`
// in the manifest declares that it *may* ask for `http://*/*` and `https://*/*`,
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

/** The two schemes the add-on ever asks for. Must match `optional_host_permissions`. */
export const HOST_ORIGINS = ["http://*/*", "https://*/*"];

/** The bundled grant surface, relative to the extension root. */
export const SETUP_PAGE = "dist/setup.html";

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
  const url = chrome.runtime.getURL(SETUP_PAGE);
  const open = await chrome.tabs.query({ url });
  const existing = open[0];
  if (existing?.id !== undefined) {
    await chrome.tabs.update(existing.id, { active: true });
    if (existing.windowId !== undefined) {
      await chrome.windows.update(existing.windowId, { focused: true });
    }
    return { tab_id: existing.id };
  }
  const created = await chrome.tabs.create({ url, active: true });
  return { tab_id: created.id ?? null };
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
