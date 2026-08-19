/**
 * v1.190.0 — the scrollback replay lands at the right size.
 *
 * The user's report: leave Build, come back, the terminals return "malformed
 * or pixelated"; dragging the boxes partially fixes them. The mechanism: the
 * server replays a session's ENTIRE scrollback the moment the socket opens,
 * at whatever size the client terminal has right then — and on a RETURN
 * visit the xterm module is cached, so connect used to win the race against
 * the pane's own layout. History wrapped into a default-sized buffer never
 * recovers; dragging re-fits and triggers the server's repaint wiggle, which
 * fixes only the live screen. Remount-shaped, timing-shaped — which is why
 * the ORIGINAL visit always looked right (the module download bought layout
 * its time).
 *
 * jsdom cannot render xterm, so the split is: the waiting primitive is
 * unit-tested for its contract, and the ORDERING (wait → fit → connect) is
 * pinned at the source — the house idiom for seams a rendered test cannot
 * reach (v1.163.0).
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it, vi } from "vitest";

import { waitForStableSize } from "@/lib/layout";

function fakeEl(sizes: Array<[number, number]>): Element {
  let i = 0;
  return {
    getBoundingClientRect: () => {
      const [w, h] = sizes[Math.min(i++, sizes.length - 1)];
      return { width: w, height: h } as DOMRect;
    },
  } as unknown as Element;
}

describe("waitForStableSize", () => {
  it("resolves once the size is nonzero and stable across two frames", async () => {
    // Grows, then holds — resolves on the first repeated nonzero reading.
    const el = fakeEl([[0, 0], [120, 80], [480, 320], [480, 320]]);
    await waitForStableSize(el, { timeoutMs: 5000 });
    // Reaching here IS the assertion; the cap above is deliberately huge so
    // a regression to "just wait for the timeout" fails this test's runtime
    // budget rather than passing by exhaustion.
  });

  it("caps out on an element that never gets a size — proceed, never hang", async () => {
    const el = fakeEl([[0, 0]]);
    const started = Date.now();
    await waitForStableSize(el, { timeoutMs: 120 });
    const took = Date.now() - started;
    expect(took).toBeGreaterThanOrEqual(100);
    expect(took).toBeLessThan(3000); // resolved by the cap, not by luck
  });

  it("never rejects", async () => {
    const el = {
      getBoundingClientRect: () => {
        throw new Error("detached");
      },
    } as unknown as Element;
    // A detached element must not blow up the pane's whole mount path.
    await expect(
      waitForStableSize(el, { timeoutMs: 50 }).catch(() => "rejected"),
    ).resolves.not.toBe("rejected");
  });
});

describe("the pane's ordering (source-pinned)", () => {
  const pane = readFileSync(
    join(process.cwd(), "components", "terminal", "TerminalPane.tsx"),
    "utf8",
  );

  it("waits for layout, then fits, THEN connects", () => {
    const wait = pane.indexOf("await waitForStableSize(holder)");
    const connect = pane.indexOf("connect();");
    expect(wait).toBeGreaterThan(-1);
    expect(connect).toBeGreaterThan(-1);
    // The order IS the fix: a connect that precedes the wait replays the
    // scrollback into an unsized buffer, exactly the reported bug.
    expect(wait).toBeLessThan(connect);
    // …and a fit sits between them, so the replay meets the true cols.
    const between = pane.slice(wait, connect);
    expect(between).toContain("doFit()");
    // The wait respects disposal — a pane unmounted mid-wait must not connect.
    expect(between).toContain("if (disposed) return;");
  });

  it("sweeps the viewport once after the replay window", () => {
    expect(pane).toMatch(/term\?\.refresh\(0, Math\.max\(0, \(term\?\.rows \?\? 1\) - 1\)\)/);
  });
});
