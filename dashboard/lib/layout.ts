// Layout-settling helpers (v1.190.0).

/**
 * Resolve once *el* has a real, stable size — nonzero and unchanged across two
 * consecutive animation frames — or once *timeoutMs* passes, whichever is
 * first. NEVER rejects and never waits forever: a hidden or zero-size element
 * resolves at the cap and the caller proceeds with what it has.
 *
 * THE RACE THIS EXISTS FOR (measured on the Build page): a terminal pane that
 * connects its WebSocket before the pane is laid out receives the session's
 * ENTIRE scrollback replay into a default-sized (80×24) xterm buffer. The
 * history wraps at the wrong width and never recovers — xterm's reflow cannot
 * faithfully re-wrap replayed prompt/TUI sequences — so the pane comes back
 * "malformed", and dragging it (which re-fits and triggers the server's
 * repaint wiggle) fixes only the live screen, never the history. The race is
 * REMOUNT-SHAPED: on the first visit the xterm module download gives layout
 * time to settle; on a return visit the module is cached and the connect wins
 * the race. Waiting for a stable size before connecting removes the ordering
 * from luck.
 */
export function waitForStableSize(
  el: Element,
  { timeoutMs = 600 }: { timeoutMs?: number } = {},
): Promise<void> {
  return new Promise((resolve) => {
    const started = Date.now();
    let last = { w: -1, h: -1 };
    const tick = () => {
      // A detached/degenerate element reads as size 0 and falls to the cap —
      // this helper sits on the pane's MOUNT path, where a throw would take
      // the whole terminal down to fix a measurement.
      let w = 0;
      let h = 0;
      try {
        const r = el.getBoundingClientRect();
        w = Math.round(r.width);
        h = Math.round(r.height);
      } catch {
        /* unmeasurable this frame */
      }
      if (w > 0 && h > 0 && w === last.w && h === last.h) {
        resolve();
        return;
      }
      if (Date.now() - started >= timeoutMs) {
        resolve(); // capped: proceed with what we have, never hang
        return;
      }
      last = { w, h };
      requestAnimationFrame(tick);
    };
    tick();
  });
}
