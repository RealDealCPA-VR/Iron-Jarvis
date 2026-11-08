/**
 * v1.445.0 — open a Settings section or setting while Settings is ALREADY open.
 *
 * Settings read `?section=` / `?focus=` / `#appearance` only when it first
 * opened, and Next keeps a page's state when only its query changes, so a
 * Ctrl K "Default model" press (or a link from one Settings section to
 * another) changed the address and nothing else. SettingsHome now listens
 * for this event, for `popstate`, and for the browser's own
 * "address changed" signal; the palette announces its own presses.
 *
 * No imports: the palette and the Settings pages both use it.
 */

export const SETTINGS_OPEN_EVENT = "ij:settings-open";

/** Wave D item 32: `#settings-<key>` names one setting's row, like
 *  `?focus=<key>`; a folded (advanced) row's fold opens for it. */
export const SETTINGS_HASH = "#settings-";

export interface SettingsTarget {
  section: string;
  focus: string;
}

/** The section/setting a `/settings…` address names, or null for any other
 *  address (and for a bare `/settings`, which names nothing). */
export function settingsTarget(href: string): SettingsTarget | null {
  try {
    const url = new URL(href, "http://x");
    if (url.pathname !== "/settings" && url.pathname !== "/settings/") return null;
    let section = url.searchParams.get("section") || "";
    if (!section && url.hash === "#appearance") section = "appearance";
    let focus = url.searchParams.get("focus") || "";
    if (!focus && url.hash.startsWith(SETTINGS_HASH)) focus = decodeURIComponent(url.hash.slice(SETTINGS_HASH.length));
    return section || focus ? { section, focus } : null;
  } catch {
    return null;
  }
}

/** Tell an open Settings page to go to `href`'s section or setting. Does
 *  nothing unless this window is on Settings and `href` names somewhere. */
export function announceSettingsLink(href: string): void {
  if (typeof window === "undefined") return;
  if (!window.location.pathname.startsWith("/settings")) return;
  const target = settingsTarget(href);
  if (!target) return;
  window.dispatchEvent(new CustomEvent<SettingsTarget>(SETTINGS_OPEN_EVENT, { detail: target }));
}
