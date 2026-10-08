/**
 * How the next page was reached (calm UI redesign S0): a press on a menu row,
 * a palette row or a tile marks the navigation as "nav" for a moment; the
 * route beacon (components/RouteVisitBeacon.tsx) reads it when the page
 * changes. Anything else — a link inside a page, a deep link, Back — is
 * "link". Kept outside lib/api.ts and the components so recordOpen can call
 * it without importing React.
 */

const WINDOW_MS = 3000;
let markedAt = 0;

/** A navigation press is about to change the page. */
export function markNavPress(): void {
  markedAt = Date.now();
}

/** "nav" when a press marked this navigation; consumed on read. */
export function takeVia(): "nav" | "link" {
  const via = Date.now() - markedAt <= WINDOW_MS ? "nav" : "link";
  markedAt = 0;
  return via;
}
