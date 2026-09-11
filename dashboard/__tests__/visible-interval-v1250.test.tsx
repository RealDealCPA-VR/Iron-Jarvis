/**
 * Polling pauses while the window is hidden (v1.250.0, S-09).
 *
 * Nine timers around the app kept firing into a minimised or background window
 * — panels every 5 s, file lists every 4–8 s, the workflows page every 5 s —
 * so a machine the user had walked away from went on asking the daemon for
 * answers nobody could read. `useVisibleInterval` is the one definition of
 * "poll while visible, and catch up once on return".
 *
 * What these pin, and why each one matters:
 *   - while hidden the interval is TORN DOWN, not merely ignored. A guard that
 *     returns early inside the callback still wakes the timer, still runs
 *     React, and on a laptop still costs battery;
 *   - coming back fires EXACTLY ONE catch-up, so the page is current the moment
 *     it is looked at — but a window that has been hidden for an hour does not
 *     replay an hour of missed ticks;
 *   - mounting hidden fires nothing (the first fetch is the caller's own), and
 *     mounting visible does not fire a spurious catch-up;
 *   - `enabled: false` is silent, and the callback is always the LATEST one, so
 *     a stale closure cannot re-fetch with yesterday's arguments.
 *
 * Counts and edges only — never a wall-clock assertion, which would measure the
 * runner rather than the wiring.
 */
import { render } from "@testing-library/react";
import { act } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useVisibleInterval } from "@/lib/useVisibleInterval";

/**
 * Hide/show the document the way a browser does.
 *
 * `document.hidden` is the lever that matters: useDocumentVisible reads THAT,
 * not `visibilityState`. Stubbing only the latter leaves the hook permanently
 * visible and every assertion below passes for the wrong reason — which is
 * exactly what the first cut of this file did (61 polls into a "hidden"
 * window). Both are set so the fake document stays self-consistent.
 */
function setVisibility(state: "visible" | "hidden") {
  const hidden = state === "hidden";
  Object.defineProperty(document, "hidden", {
    configurable: true,
    get: () => hidden,
  });
  Object.defineProperty(document, "visibilityState", {
    configurable: true,
    get: () => state,
  });
  document.dispatchEvent(new Event("visibilitychange"));
}

function Poller({
  fn,
  ms = 1000,
  enabled = true,
}: {
  fn: () => void;
  ms?: number;
  enabled?: boolean;
}) {
  useVisibleInterval(fn, ms, enabled);
  return null;
}

beforeEach(() => {
  vi.useFakeTimers();
  setVisibility("visible");
});

afterEach(() => {
  vi.useRealTimers();
  setVisibility("visible");
});

describe("useVisibleInterval", () => {
  it("polls on its interval while visible", () => {
    const fn = vi.fn();
    render(<Poller fn={fn} />);
    expect(fn).not.toHaveBeenCalled(); // no tick at mount
    act(() => void vi.advanceTimersByTime(3000));
    expect(fn).toHaveBeenCalledTimes(3);
  });

  it("issues NOTHING while hidden — the interval is gone, not guarded", () => {
    const fn = vi.fn();
    render(<Poller fn={fn} />);
    act(() => void vi.advanceTimersByTime(1000));
    expect(fn).toHaveBeenCalledTimes(1);

    act(() => setVisibility("hidden"));
    const atHide = fn.mock.calls.length;
    act(() => void vi.advanceTimersByTime(60_000)); // a minute in the background
    expect(fn).toHaveBeenCalledTimes(atHide);
  });

  it("fires EXACTLY ONE catch-up on the hidden→visible edge", () => {
    const fn = vi.fn();
    render(<Poller fn={fn} />);
    act(() => setVisibility("hidden"));
    act(() => void vi.advanceTimersByTime(60_000));
    fn.mockClear();

    act(() => setVisibility("visible"));
    expect(fn).toHaveBeenCalledTimes(1); // current at once…
    act(() => void vi.advanceTimersByTime(999));
    expect(fn).toHaveBeenCalledTimes(1); // …and no replay of missed ticks
    act(() => void vi.advanceTimersByTime(1));
    expect(fn).toHaveBeenCalledTimes(2); // ordinary polling resumes
  });

  it("a window that mounts VISIBLE gets no spurious catch-up", () => {
    const fn = vi.fn();
    render(<Poller fn={fn} />);
    act(() => setVisibility("visible")); // already visible
    expect(fn).not.toHaveBeenCalled();
  });

  it("a window that mounts HIDDEN stays silent until it is looked at", () => {
    setVisibility("hidden");
    const fn = vi.fn();
    render(<Poller fn={fn} />);
    act(() => void vi.advanceTimersByTime(10_000));
    expect(fn).not.toHaveBeenCalled();

    act(() => setVisibility("visible"));
    expect(fn).toHaveBeenCalledTimes(1);
  });

  it("enabled: false polls not at all, visible or not", () => {
    const fn = vi.fn();
    render(<Poller fn={fn} enabled={false} />);
    act(() => void vi.advanceTimersByTime(10_000));
    act(() => setVisibility("hidden"));
    act(() => setVisibility("visible"));
    act(() => void vi.advanceTimersByTime(10_000));
    expect(fn).not.toHaveBeenCalled();
  });

  it("calls the LATEST callback — no stale closure re-fetching old arguments", () => {
    const first = vi.fn();
    const second = vi.fn();
    const view = render(<Poller fn={first} />);
    act(() => void vi.advanceTimersByTime(1000));
    expect(first).toHaveBeenCalledTimes(1);

    view.rerender(<Poller fn={second} />);
    act(() => void vi.advanceTimersByTime(1000));
    expect(second).toHaveBeenCalledTimes(1);
    expect(first).toHaveBeenCalledTimes(1); // not called again
  });

  it("unmounting stops it", () => {
    const fn = vi.fn();
    const view = render(<Poller fn={fn} />);
    act(() => void vi.advanceTimersByTime(1000));
    view.unmount();
    act(() => void vi.advanceTimersByTime(10_000));
    expect(fn).toHaveBeenCalledTimes(1);
  });
});
