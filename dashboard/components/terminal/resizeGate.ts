/**
 * resizeGate — should THIS attach be the one that resizes the PTY?
 *
 * v1.232.0 (audit Wave 6). A terminal session has ONE PTY and any number of
 * attached clients: the desktop window, a phone over Tailscale, the Build
 * pane's peek strip, a second browser tab. Every attach used to send its own
 * `resize` on open and on every ResizeObserver tick, and the daemon keeps
 * last-writer semantics — so opening the phone reflowed the desktop's running
 * session into 40 columns, and the desktop's next tick reflowed it back.
 *
 * The rule: a resize is sent only by the PRIMARY attach — the pane is actually
 * visible (not a `visibility:hidden` rail box) AND its document has focus (the
 * window the user is looking at). A background tab or a phone lying on the
 * desk never reflows anyone. The daemon side is unchanged on purpose: last
 * writer wins, and the gate decides who writes.
 *
 * Pure and dependency-free so it is unit-testable under jsdom; the pane calls
 * it from `sendResize`.
 */

/** The holder is visible per CSS, defaulting to visible where the platform
 *  API is absent (both spellings of the visibility option — the dictionary
 *  member was renamed checkVisibilityCSS → visibilityProperty). */
export function holderVisible(el: HTMLElement): boolean {
  const check = (
    el as HTMLElement & {
      checkVisibility?: (opts?: Record<string, boolean>) => boolean;
    }
  ).checkVisibility;
  if (typeof check !== "function") return true;
  try {
    return check.call(el, { checkVisibilityCSS: true, visibilityProperty: true });
  } catch {
    return true;
  }
}

/** True when this attach may send a PTY resize: the pane is visible AND the
 *  document has focus. `doc` is injectable for tests; a document without a
 *  `hasFocus` counts as focused (never gate on a missing API). */
export function resizeAllowed(
  el: HTMLElement | null | undefined,
  doc: Document | undefined = typeof document === "undefined" ? undefined : document,
): boolean {
  if (!el) return false;
  if (!holderVisible(el)) return false;
  if (!doc || typeof doc.hasFocus !== "function") return true;
  try {
    return doc.hasFocus();
  } catch {
    return true;
  }
}
