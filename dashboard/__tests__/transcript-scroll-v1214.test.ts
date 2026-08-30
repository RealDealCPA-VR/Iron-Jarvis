import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

/**
 * THE TRANSCRIPT SCROLLS ITSELF, NOT THE PAGE (v1.214.0).
 *
 * MEASURED, in a real browser, against the shipped build:
 *
 *   viewport 430×850, /agents, on arrival
 *     v1.213.0 (packaged, :8788)   main.scrollTop = 0
 *     v1.214.0 before this fix     main.scrollTop = 506
 *     v1.214.0 after               main.scrollTop = 0
 *
 * `RoundTable` pinned its transcript to the newest line with
 * `bottomRef.current.scrollIntoView({ block: "end" })`. `scrollIntoView` walks
 * EVERY scrollable ancestor, not just the nearest one — and the transcript is
 * already its own `max-h-[62vh] overflow-y-auto` box, so the inner scroll was
 * all the effect ever needed. The walk then also scrolled `<main>`, the app's
 * page scroller.
 *
 * That was harmless while the page above the round-table was short. This
 * release makes the left card full height, and the narrow-width layout stacks
 * it ABOVE the conversation — so the walk landed the user 506px down, with the
 * thread list and the agents icon scrolled off the top of the screen on
 * arrival. Scrolling the BOX cannot reach an ancestor by construction.
 *
 * THIS IS A SOURCE PIN, and it is worth saying why rather than pretending it
 * is a behavioural test. jsdom computes no layout: `scrollHeight` and
 * `clientHeight` are 0 for every element, so "did it end up at the bottom"
 * has no meaning there and a render test would pass against either
 * implementation. The behaviour was verified in a real browser (numbers
 * above); what this file defends is the one line that made it true, because
 * `scrollIntoView` is the obvious thing to reach for and would come back
 * silently.
 */

// Resolved off the vitest CWD (the dashboard package) rather than
// import.meta.url: vitest rewrites module URLs, so `new URL(..., import.meta.url)`
// is not a file: URL here.
const SRC = readFileSync(
  resolve(process.cwd(), "components/agents/RoundTable.tsx"),
  "utf-8",
);

describe("RoundTable's auto-scroll", () => {
  it("scrolls the transcript's own box", () => {
    expect(SRC).toContain("const transcriptRef = useRef<HTMLDivElement>(null)");
    expect(SRC).toContain("ref={transcriptRef}");
    expect(SRC).toMatch(/box\.scrollTo\(\{\s*top: box\.scrollHeight/);
  });

  it("never calls scrollIntoView, which would take the page with it", () => {
    // The check is on CODE, not on a comment mentioning the old call — the
    // header above names `scrollIntoView` on purpose and must not trip this.
    const code = SRC.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
    expect(code).not.toContain("scrollIntoView");
  });

  it("keeps the transcript addressable as its own scroll region", () => {
    // Both halves of the guarantee live on one element: the box that scrolls
    // and the box the test harness (and the browser check above) measures.
    expect(SRC).toMatch(
      /ref=\{transcriptRef\}\s*\n\s*data-testid="thread-transcript"/,
    );
    expect(SRC).toContain("max-h-[62vh] flex-col gap-4 overflow-y-auto");
  });
});
