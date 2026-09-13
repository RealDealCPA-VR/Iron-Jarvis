/**
 * Which browser this add-on is running in — so the daemon's card can say so.
 *
 * v1.259.0. Chrome and Edge load the same add-on with the same pinned id, and
 * until now the daemon could not tell them apart: the card said "Connected" and
 * a user on Edge read setup steps written for Chrome. The service worker knows:
 * `navigator.userAgentData.brands` names the real browser ("Microsoft Edge",
 * "Google Chrome") beside the "Chromium" and "Not A;Brand" entries every
 * Chromium browser also lists. Engines without that API fall back to the UA
 * string, where Edge is the `Edg/` token and Chrome the `Chrome/` one.
 *
 * Returns undefined when nothing can be said. The daemon treats an absent field
 * as "unknown" — never as Chrome, never as a guess.
 */

export interface BrowserIdentity {
  name: string;
  version: string;
}

interface UABrand {
  brand: string;
  version: string;
}

/** The subset of `navigator` this reads; typed here because lib.dom omits userAgentData. */
export interface NavigatorLike {
  userAgentData?: { brands?: UABrand[] };
  userAgent?: string;
}

/** Brands every Chromium browser lists that name no product. */
const NOISE = /chromium|not.?a.?brand/i;

export function describeBrowser(nav: NavigatorLike | undefined): BrowserIdentity | undefined {
  const brands = nav?.userAgentData?.brands ?? [];
  const real = brands.find((b) => typeof b?.brand === "string" && b.brand && !NOISE.test(b.brand));
  if (real) {
    return { name: real.brand.trim(), version: String(real.version ?? "").trim() };
  }
  const ua = nav?.userAgent ?? "";
  const edge = /\bEdg\/(\d+[\d.]*)/.exec(ua);
  if (edge) return { name: "Microsoft Edge", version: edge[1] ?? "" };
  const chrome = /\bChrome\/(\d+[\d.]*)/.exec(ua);
  if (chrome) return { name: "Google Chrome", version: chrome[1] ?? "" };
  return undefined;
}

/** The running browser, read from the global navigator (absent outside a browser). */
export function currentBrowser(): BrowserIdentity | undefined {
  if (typeof navigator === "undefined") return undefined;
  return describeBrowser(navigator as unknown as NavigatorLike);
}
