/**
 * v1.226.0 — useEvents reconnects with `?since=<last id>` (contract C1) and
 * cannot orphan a socket under a StrictMode remount (F-D-7).
 *
 *  - First connect: plain `/events` (nothing to resume).
 *  - After a frame with id "e1" and a close: the retry connects to
 *    `/events?since=e1`, so the daemon replays what happened in the gap.
 *  - StrictMode: the effect runs, is cleaned up (ws1 closed), and runs
 *    again (ws2). ws1's async onclose then fires; before the per-connection
 *    guard it scheduled a retry that opened ws3 and orphaned ws2. Now ws1's
 *    close is ignored: still exactly two sockets, ws2 live.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { StrictMode } from "react";
import { act, cleanup, renderHook } from "@testing-library/react";
import { useEvents } from "@/lib/useEvents";

class FakeWS {
  static instances: FakeWS[] = [];
  url: string;
  onopen: (() => void) | null = null;
  onmessage: ((ev: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  closed = false;
  constructor(url: string) {
    this.url = url;
    FakeWS.instances.push(this);
  }
  close() {
    this.closed = true; // the real close event is async — tests fire it by hand
  }
}

beforeEach(() => {
  vi.useFakeTimers();
  FakeWS.instances = [];
  vi.stubGlobal("WebSocket", FakeWS);
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("useEvents reconnect cursor (v1.226.0)", () => {
  it("first connect has no since; the reconnect after a frame carries ?since=<id>", () => {
    const { result } = renderHook(() => useEvents(10));
    expect(FakeWS.instances).toHaveLength(1);
    const ws1 = FakeWS.instances[0];
    expect(ws1.url).toMatch(/\/events$/);
    expect(ws1.url).not.toContain("since");

    act(() => {
      ws1.onopen?.();
      ws1.onmessage?.({
        data: JSON.stringify({ id: "e1", type: "x", session_id: null, ts: "t", payload: {} }),
      });
    });
    expect(result.current.events.map((e) => e.id)).toEqual(["e1"]);
    expect(result.current.connected).toBe(true);

    act(() => {
      ws1.onclose?.();
    });
    expect(result.current.connected).toBe(false);
    act(() => {
      vi.advanceTimersByTime(2500);
    });
    expect(FakeWS.instances).toHaveLength(2);
    expect(FakeWS.instances[1].url).toMatch(/\/events\?since=e1$/);
  });

  it("a frame with no id leaves the cursor alone (reconnect still plain)", () => {
    renderHook(() => useEvents(10));
    const ws1 = FakeWS.instances[0];
    act(() => {
      ws1.onmessage?.({ data: JSON.stringify({ type: "x", payload: {} }) });
      ws1.onclose?.();
      vi.advanceTimersByTime(2500);
    });
    expect(FakeWS.instances[1].url).not.toContain("since");
  });

  it("StrictMode remount: the closed first socket's late onclose does not open a third", () => {
    const { result } = renderHook(() => useEvents(10), { wrapper: StrictMode });
    // Effect ran twice: ws1 (cleaned up -> close()) and ws2 (live).
    expect(FakeWS.instances).toHaveLength(2);
    const [ws1, ws2] = FakeWS.instances;
    expect(ws1.closed).toBe(true);
    expect(ws2.closed).toBe(false);

    act(() => {
      ws2.onopen?.();
    });
    expect(result.current.connected).toBe(true);

    // The browser delivers ws1's close event asynchronously, AFTER the remount.
    act(() => {
      ws1.onclose?.();
      ws1.onmessage?.({ data: JSON.stringify({ id: "stale", type: "x", payload: {} }) });
      vi.advanceTimersByTime(5000);
    });
    expect(FakeWS.instances).toHaveLength(2); // no ws3
    expect(result.current.connected).toBe(true); // ws1's close did not flip the flag
    expect(result.current.events).toHaveLength(0); // ws1's frame was not ingested
  });
});
