/**
 * v1.326.0 — Calm chat W1-2 fix rounds: a transcript that is following its
 * newest line stays at the bottom when its content grows on its own, and the
 * chat page has ONE bottom target.
 *
 * Round 1: "Jump to latest" showed over a saved chat nobody had scrolled: the
 * open scroll was aimed at the bottom, then a chart laid out and the
 * conversation grew (71px short on a desk, 150px on a phone), so the next
 * scroll event read "the reader left the bottom". `useRepinOnGrowth` scrolls
 * back to the bottom when the content got taller while the reader follows.
 *
 * Round 2: that re-pin set `scrollTop = scrollHeight - clientHeight` (the
 * true bottom) while every other bottom scroll on the page used the
 * sentinel's `scrollIntoView({block: "end"})`, which stopped 32px short (the
 * scroller's bottom padding). A streaming reply then bounced on every line:
 * the observer moved it to one bottom before paint, onGrow moved it to the
 * other after. Both now go through `scrollToLatest(sentinel)`, and the
 * sentinel's scroll margin equals the padding so that target is the true
 * bottom.
 *
 * jsdom has no layout, no ResizeObserver and no real scrollIntoView, so the
 * observer is a fake the test fires, heights are set by hand, and
 * scrollIntoView is a small stand-in layout: the sentinel sits at the end of
 * the content, `PAD` px above the scroller's bottom, and "block: end" lines
 * the sentinel's bottom (plus its scroll margin) up with the scroller's
 * bottom edge, clamped to the scroll range, the way a browser does.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { useRef } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render } from "@testing-library/react";

import { scrollToLatest, useRepinOnGrowth } from "@/lib/transcriptRepin";

type Fired = () => void;
const RO = { observed: new Set<Element>(), fire: (() => {}) as Fired };

class FakeResizeObserver {
  constructor(cb: ResizeObserverCallback) {
    RO.fire = () => cb([], this as unknown as ResizeObserver);
  }
  observe(el: Element) {
    RO.observed.add(el);
  }
  unobserve(el: Element) {
    RO.observed.delete(el);
  }
  disconnect() {
    RO.observed.clear();
  }
}

const state = { following: true };

function Transcript({ rows }: { rows: string[] }) {
  const ref = useRef<HTMLDivElement>(null);
  const bottom = useRef<HTMLDivElement>(null);
  useRepinOnGrowth(ref, bottom, () => state.following);
  return (
    <div ref={ref} data-testid="scroller">
      <div data-testid="list" style={{ display: "contents" }}>
        {rows.map((r) => (
          <div key={r} data-testid={`row-${r}`}>
            {r}
          </div>
        ))}
      </div>
      <div ref={bottom} data-testid="sentinel" />
    </div>
  );
}

function sizes(el: HTMLElement, scrollHeight: number, clientHeight = 500) {
  Object.defineProperty(el, "scrollHeight", { configurable: true, value: scrollHeight });
  Object.defineProperty(el, "clientHeight", { configurable: true, value: clientHeight });
}

function scroller(container: HTMLElement): HTMLElement {
  return container.querySelector('[data-testid="scroller"]') as HTMLElement;
}

function sentinel(container: HTMLElement): HTMLElement {
  return container.querySelector('[data-testid="sentinel"]') as HTMLElement;
}

// The stand-in layout. `layout.margin` is the sentinel's scroll-margin-bottom.
const PAD = 32;
const layout = { margin: 0, calls: [] as ScrollIntoViewOptions[] };
const realScrollIntoView = Element.prototype.scrollIntoView;

function fakeScrollIntoView(this: Element, opts?: boolean | ScrollIntoViewOptions) {
  const o = (typeof opts === "object" ? opts : {}) as ScrollIntoViewOptions;
  layout.calls.push(o);
  const sc = this.parentElement as HTMLElement;
  const max = Math.max(0, sc.scrollHeight - sc.clientHeight);
  // Only the sentinel (the scroller's last child) sits at the end of the
  // content; anything else is somewhere near the top.
  const isSentinel = this === sc.lastElementChild;
  const bottom = isSentinel ? sc.scrollHeight - PAD : 100;
  const target = bottom + (isSentinel ? layout.margin : 0) - sc.clientHeight;
  if (o.block === "end") sc.scrollTop = Math.min(max, Math.max(0, target));
}

beforeEach(() => {
  RO.observed.clear();
  RO.fire = () => {};
  state.following = true;
  layout.margin = 0;
  layout.calls = [];
  vi.stubGlobal("ResizeObserver", FakeResizeObserver);
  Element.prototype.scrollIntoView = fakeScrollIntoView;
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  Element.prototype.scrollIntoView = realScrollIntoView;
});

describe("useRepinOnGrowth", () => {
  it("content that grows under a reader who is following lands them at the NEW bottom", () => {
    layout.margin = PAD; // the page's sentinel: margin == padding
    const { container } = render(<Transcript rows={["a", "b"]} />);
    const el = scroller(container);
    sizes(el, 1000);
    el.scrollTop = 500; // at the old bottom
    // A chart lays out: the conversation is 150px taller.
    sizes(el, 1150);
    act(() => RO.fire());
    expect(el.scrollTop).toBe(1150 - 500);
    // Instantly: a smooth scroll queued per growth never settles.
    expect(layout.calls.at(-1)).toEqual({ behavior: "auto", block: "end" });
  });

  it("the re-pin and the page's other bottom scrolls land on the SAME scrollTop (one target, whatever the sentinel's margin)", () => {
    for (const margin of [0, PAD]) {
      layout.margin = margin;
      const { container, unmount } = render(<Transcript rows={["a", "b"]} />);
      const el = scroller(container);
      sizes(el, 1000);
      el.scrollTop = 500;
      sizes(el, 1031); // one more streamed line
      act(() => RO.fire());
      const byObserver = el.scrollTop;
      // What scrollLiveIntoView / the scroll effect / Jump to latest do next.
      el.scrollTop = 0;
      scrollToLatest(sentinel(container), "auto");
      const byOnGrow = el.scrollTop;
      expect({ margin, byObserver }).toEqual({ margin, byObserver: byOnGrow });
      unmount();
    }
  });

  it("never moves a reader who scrolled up", () => {
    const { container } = render(<Transcript rows={["a", "b"]} />);
    const el = scroller(container);
    sizes(el, 1000);
    el.scrollTop = 120;
    state.following = false;
    sizes(el, 1150);
    act(() => RO.fire());
    expect(el.scrollTop).toBe(120);
    expect(layout.calls).toEqual([]);
  });

  it("content that SHRANK (or did not change) is left where it is", () => {
    const { container } = render(<Transcript rows={["a", "b"]} />);
    const el = scroller(container);
    sizes(el, 1000);
    act(() => RO.fire()); // first report: the baseline height
    el.scrollTop = 300;
    sizes(el, 900);
    act(() => RO.fire());
    expect(el.scrollTop).toBe(300);
    act(() => RO.fire()); // same height again
    expect(el.scrollTop).toBe(300);
  });

  it("watches the ROWS inside a display:contents list (the list has no box), and rows added later", async () => {
    const { container, rerender, getByTestId } = render(<Transcript rows={["a"]} />);
    expect(RO.observed.has(getByTestId("list"))).toBe(false);
    expect(RO.observed.has(getByTestId("row-a"))).toBe(true);
    expect(RO.observed.has(getByTestId("sentinel"))).toBe(true);
    rerender(<Transcript rows={["a", "b"]} />);
    // The MutationObserver reports in a microtask.
    await act(async () => {
      await Promise.resolve();
    });
    expect(RO.observed.has(getByTestId("row-b"))).toBe(true);
    expect(scroller(container)).toBeTruthy();
  });

  it("does nothing (and does not throw) where ResizeObserver does not exist", () => {
    vi.stubGlobal("ResizeObserver", undefined);
    const { container } = render(<Transcript rows={["a"]} />);
    const el = scroller(container);
    sizes(el, 1000);
    el.scrollTop = 10;
    expect(el.scrollTop).toBe(10);
  });
});

// The chat page's side of the agreement, read from its source (normalised:
// CI checks files out with CRLF).
const PAGE = readFileSync(join(__dirname, "..", "app", "chat", "page.tsx"), "utf8").replace(/\r\n/g, "\n");

describe("the chat page has one bottom target", () => {
  it("every bottom scroll goes through scrollToLatest on the sentinel; none aims anywhere else", () => {
    expect(PAGE).not.toMatch(/bottomRef\.current\?\.scrollIntoView/);
    // The scroll effect, scrollLiveIntoView and Jump to latest.
    expect(PAGE.match(/scrollToLatest\(bottomRef\.current,/g)?.length ?? 0).toBeGreaterThanOrEqual(3);
    // The growth re-pin is handed the same sentinel.
    expect(PAGE).toMatch(/useRepinOnGrowth\(\s*scrollRef,\s*bottomRef,/);
  });

  it("the sentinel's scroll margin equals the scroller's bottom padding, so its target is the true bottom", () => {
    const sentinelTag = PAGE.match(/<div ref=\{bottomRef\}[^>]*\/>/)?.[0] ?? "";
    expect(sentinelTag).not.toBe("");
    const margin = sentinelTag.match(/\bscroll-mb-(\d+)\b/)?.[1];
    expect(margin, "the sentinel carries a scroll-mb-* class").toBeTruthy();
    const scrollerTag = PAGE.match(/ref=\{scrollRef\}[\s\S]*?className="([^"]*)"/)?.[1] ?? "";
    const pads = Array.from(scrollerTag.matchAll(/(?:^|\s)(?:sm:)?pb-(\d+)\b/g)).map((m) => m[1]);
    expect(pads.length).toBeGreaterThan(0);
    for (const pad of pads) expect(pad).toBe(margin);
  });
});
