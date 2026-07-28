import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createElement } from "react";
import { act, render } from "@testing-library/react";
import { useFocusRef } from "@/lib/useFocusRef";

/**
 * Contract pins for the `?focus=<key>` deep-link hook (lib/useFocusRef.ts).
 *
 * Harness notes:
 *  - The URL is driven with `history.replaceState`, NOT `vi.stubGlobal("location")`.
 *    jsdom's `window.location` is a non-configurable accessor, so stubbing it
 *    throws; replaceState mutates the real Location the hook actually reads.
 *  - `vi.useFakeTimers` is given an explicit `toFake` list including
 *    requestAnimationFrame — Vitest's default list is timers + Date only, and
 *    this hook defers its work by one frame.
 *  - jsdom does not implement Element#scrollIntoView at all, so every test
 *    installs its own (spy or throwing) implementation.
 *  - No JSX: the probe component is built with `createElement` so this stays a
 *    .ts file, matching the hook it covers.
 */

const RING = ["ring-2", "ring-accent/70", "rounded-2xl", "transition-shadow"];

/** Minimal host: one div carrying the hook's ref plus a stable base class. */
function Probe({ focusKey, base = "" }: { focusKey: string; base?: string }) {
  const ref = useFocusRef<HTMLDivElement>(focusKey);
  return createElement("div", {
    ref,
    "data-testid": `probe-${focusKey || "inert"}`,
    className: base,
  });
}

/** Point the (fake) browser at a URL without navigating. */
function setUrl(search: string) {
  window.history.replaceState({}, "", `/documents${search}`);
}

/** Run the deferred frame the hook schedules. */
function flushFrame() {
  act(() => {
    vi.advanceTimersByTime(20);
  });
}

let scrollSpy: ReturnType<typeof vi.fn>;

beforeEach(() => {
  vi.useFakeTimers({
    toFake: [
      "setTimeout",
      "clearTimeout",
      "requestAnimationFrame",
      "cancelAnimationFrame",
      "Date",
    ],
  });
  scrollSpy = vi.fn();
  // jsdom ships no scrollIntoView; define one per test.
  Object.defineProperty(Element.prototype, "scrollIntoView", {
    value: scrollSpy,
    writable: true,
    configurable: true,
  });
  setUrl("");
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  setUrl("");
});

describe("useFocusRef — matching key", () => {
  it("scrolls the element into view centered and flashes the ring", () => {
    setUrl("?focus=me");
    const { getByTestId } = render(createElement(Probe, { focusKey: "me" }));
    const el = getByTestId("probe-me");

    // Nothing happens until the deferred frame runs.
    expect(scrollSpy).not.toHaveBeenCalled();

    flushFrame();

    expect(scrollSpy).toHaveBeenCalledTimes(1);
    expect(scrollSpy).toHaveBeenCalledWith({ block: "center", behavior: "smooth" });
    for (const cls of RING) expect(el.classList.contains(cls)).toBe(true);
    // The ruling: the ring must be rounded so it doesn't box a rounded card.
    expect(el.classList.contains("rounded-2xl")).toBe(true);
  });

  it("drops the ring after the 2.5s flash", () => {
    setUrl("?focus=me");
    const { getByTestId } = render(
      createElement(Probe, { focusKey: "me", base: "lg:col-span-1" }),
    );
    const el = getByTestId("probe-me");
    flushFrame();
    expect(el.classList.contains("ring-2")).toBe(true);

    act(() => {
      vi.advanceTimersByTime(2500);
    });

    for (const cls of RING) expect(el.classList.contains(cls)).toBe(false);
    // The element's own styling survives the flash untouched.
    expect(el.className).toBe("lg:col-span-1");
  });

  it("honors prefers-reduced-motion by scrolling without smoothing", () => {
    setUrl("?focus=me");
    vi.stubGlobal(
      "matchMedia",
      vi.fn(() => ({ matches: true })),
    );
    render(createElement(Probe, { focusKey: "me" }));
    flushFrame();

    expect(scrollSpy).toHaveBeenCalledWith({ block: "center", behavior: "auto" });
  });

  it("survives a scrollIntoView that rejects the options object", () => {
    setUrl("?focus=me");
    const strict = vi.fn((arg?: unknown) => {
      if (arg !== undefined) throw new TypeError("options unsupported");
    });
    Object.defineProperty(Element.prototype, "scrollIntoView", {
      value: strict,
      writable: true,
      configurable: true,
    });

    const { getByTestId } = render(createElement(Probe, { focusKey: "me" }));
    expect(() => flushFrame()).not.toThrow();

    // Fell back to the no-arg form, and still flashed.
    expect(strict).toHaveBeenCalledTimes(2);
    expect(strict).toHaveBeenLastCalledWith();
    expect(getByTestId("probe-me").classList.contains("ring-2")).toBe(true);
  });

  it("does not strip a highlight class the element already owned", () => {
    setUrl("?focus=me");
    const { getByTestId } = render(
      createElement(Probe, { focusKey: "me", base: "rounded-2xl p-5" }),
    );
    const el = getByTestId("probe-me");
    flushFrame();
    act(() => {
      vi.advanceTimersByTime(2500);
    });

    // ring-2 was ours to remove; rounded-2xl came from className and must stay.
    expect(el.classList.contains("ring-2")).toBe(false);
    expect(el.classList.contains("rounded-2xl")).toBe(true);
    expect(el.classList.contains("p-5")).toBe(true);
  });
});

describe("useFocusRef — no-op paths", () => {
  it("ignores a focus param aimed at a different card", () => {
    setUrl("?focus=packs");
    const { getByTestId } = render(createElement(Probe, { focusKey: "me" }));
    flushFrame();

    expect(scrollSpy).not.toHaveBeenCalled();
    expect(getByTestId("probe-me").className).toBe("");
    expect(vi.getTimerCount()).toBe(0);
  });

  it("ignores a URL with no focus param at all", () => {
    setUrl("?tab=history");
    const { getByTestId } = render(createElement(Probe, { focusKey: "me" }));
    flushFrame();

    expect(scrollSpy).not.toHaveBeenCalled();
    expect(getByTestId("probe-me").className).toBe("");
  });

  it("an empty key opts out BEFORE reading the URL", () => {
    // `?focus=` parses to the empty string. If the hook compared the param
    // instead of short-circuiting on the key, "" === "" would match and this
    // inert instance would light up. Nothing may be scheduled either.
    setUrl("?focus=");
    const { getByTestId } = render(createElement(Probe, { focusKey: "" }));
    flushFrame();

    expect(scrollSpy).not.toHaveBeenCalled();
    expect(getByTestId("probe-inert").className).toBe("");
    expect(vi.getTimerCount()).toBe(0);
  });
});

describe("useFocusRef — many instances (the ConnectionCard shape)", () => {
  it("lights only the keyed instance, leaving the inert siblings alone", () => {
    setUrl("?focus=endpoints");
    const { container } = render(
      createElement(
        "div",
        null,
        createElement(Probe, { key: "a", focusKey: "" }),
        createElement(Probe, { key: "b", focusKey: "endpoints" }),
        createElement(Probe, { key: "c", focusKey: "" }),
      ),
    );
    flushFrame();

    const divs = Array.from(container.querySelectorAll("[data-testid]"));
    expect(divs).toHaveLength(3);
    expect(scrollSpy).toHaveBeenCalledTimes(1);
    expect(divs.filter((d) => d.classList.contains("ring-2"))).toHaveLength(1);
    expect(divs[1].classList.contains("ring-2")).toBe(true);
  });
});

describe("useFocusRef — throttled frames (backgrounded tab)", () => {
  it("times the flash from when it is drawn, not from mount", () => {
    // A hidden tab freezes requestAnimationFrame but keeps setTimeout running.
    // If the removal timer were a sibling of the rAF it would expire before the
    // ring existed, and the ring would then be painted permanently. Drive the
    // frame by hand to reproduce that ordering.
    setUrl("?focus=me");
    let frame: FrameRequestCallback | null = null;
    vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => {
      frame = cb;
      return 1;
    });
    vi.stubGlobal("cancelAnimationFrame", () => {
      frame = null;
    });

    const { getByTestId } = render(createElement(Probe, { focusKey: "me" }));
    const el = getByTestId("probe-me");

    // Tab stays hidden well past the flash window: no frame has run yet.
    act(() => {
      vi.advanceTimersByTime(6000);
    });
    expect(el.className).toBe("");

    // Tab comes back; the deferred frame finally runs and draws the ring.
    const run = frame as unknown as FrameRequestCallback;
    act(() => {
      run(0);
    });
    expect(el.classList.contains("ring-2")).toBe(true);

    // …and it still clears 2.5s later rather than sticking forever.
    act(() => {
      vi.advanceTimersByTime(2500);
    });
    expect(el.className).toBe("");
    expect(vi.getTimerCount()).toBe(0);
  });
});

describe("useFocusRef — teardown", () => {
  it("unmounting mid-flash strips the ring and leaves no live timer", () => {
    setUrl("?focus=me");
    const { getByTestId, unmount } = render(
      createElement(Probe, { focusKey: "me", base: "lg:col-span-1" }),
    );
    const el = getByTestId("probe-me");
    flushFrame();
    expect(el.classList.contains("ring-2")).toBe(true);
    expect(vi.getTimerCount()).toBeGreaterThan(0);

    unmount();

    for (const cls of RING) expect(el.classList.contains(cls)).toBe(false);
    expect(el.className).toBe("lg:col-span-1");
    expect(vi.getTimerCount()).toBe(0);
  });

  it("unmounting before the frame runs cancels the scroll entirely", () => {
    setUrl("?focus=me");
    const { getByTestId, unmount } = render(createElement(Probe, { focusKey: "me" }));
    const el = getByTestId("probe-me");

    unmount();
    act(() => {
      vi.advanceTimersByTime(5000);
    });

    expect(scrollSpy).not.toHaveBeenCalled();
    expect(el.className).toBe("");
    expect(vi.getTimerCount()).toBe(0);
  });
});
