// v1.277.0: OPEN A PAGE ONCE.
//
// Two buttons open pages from the add-on: the sidebar's Open Jarvis and the
// worker's own grant pages (site access, the microphone). The grant pages already
// re-used an open tab instead of stacking duplicates (`openAddonPage`); Open
// Jarvis did not, so every press was one more dashboard tab. This is the one
// implementation both now use: find a tab whose URL matches, bring it and its
// window to the front, else open a new active tab.
//
// The match is a Chrome URL PATTERN (`chrome.tabs.query({url})`), so a page's
// own address matches exactly and `sitePattern(url)` matches every page on that
// site — the dashboard tab the user already has open on ANY route is the one to
// focus, not only the route the button would open.

/** `scheme://host/*` for a URL: the pattern that matches every page on its site.
 * Built from the parts, not `URL.origin`: an extension URL's origin is "null"
 * under Node (an opaque origin), and a pattern of `null/*` matches nothing. */
export function sitePattern(url: string): string {
  try {
    const u = new URL(url);
    if (!u.host) return url;
    return `${u.protocol}//${u.host}/*`;
  } catch {
    return url;
  }
}

/**
 * Focus the first tab matching `pattern` (default: the URL itself), else open `url`.
 *
 * `reused` says which happened, so a caller can tell the user "brought it to the
 * front" rather than "opened" when nothing was opened.
 */
export async function focusOrOpen(
  url: string,
  pattern: string = url,
): Promise<{ tab_id: number | null; reused: boolean }> {
  const open = await chrome.tabs.query({ url: pattern });
  const existing = open[0];
  if (existing?.id !== undefined) {
    await chrome.tabs.update(existing.id, { active: true });
    if (existing.windowId !== undefined) {
      await chrome.windows.update(existing.windowId, { focused: true });
    }
    return { tab_id: existing.id, reused: true };
  }
  const created = await chrome.tabs.create({ url, active: true });
  return { tab_id: created.id ?? null, reused: false };
}
