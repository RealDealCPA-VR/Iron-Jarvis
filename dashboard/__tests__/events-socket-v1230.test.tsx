/**
 * v1.230.0 (audit Wave 4, FP6) — ONE /events socket per window, with backoff.
 *
 * Converted from the 2026-09-04 audit repros (`events.test.tsx`,
 * `useEvents-reconnect-audit.test.tsx`), which pinned the OLD behaviour: one
 * socket per hook (4–7 per window), a flat 2.5 s retry (26 sockets in 62 s
 * per hook during an outage) and a hook that appended a replayed duplicate.
 *
 *  - Four hooks under one EventsProvider share ONE socket; each keeps its
 *    own window; the provider keeps the socket alive across a route change.
 *  - A 62 s outage costs at most 8 attempts: 2.5 s, then doubling to a 30 s
 *    cap, ±20% jitter from the second attempt on, reset by a successful open.
 *  - A replayed duplicate id is appended once; id-less frames still append.
 *  - Reconnect still carries `?since=<last id>` (v1.226.0 contract C1).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, renderHook } from "@testing-library/react";
import {
  EventsProvider,
  RECONNECT_BASE_MS,
  RECONNECT_CAP_MS,
  reconnectDelay,
  useEvents,
  type EventsState,
} from "@/lib/useEvents";

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
  open() {
    this.onopen?.();
  }
  frame(id: string | null, type = "t") {
    const body: Record<string, unknown> = { type, session_id: null, ts: "t", payload: {} };
    if (id) body.id = id;
    this.onmessage?.({ data: JSON.stringify(body) });
  }
  drop() {
    this.onclose?.();
  }
  refuse() {
    this.onerror?.();
    this.onclose?.();
  }
}
const latest = () => FakeWS.instances[FakeWS.instances.length - 1];

beforeEach(() => {
  vi.useFakeTimers();
  FakeWS.instances = [];
  vi.stubGlobal("WebSocket", FakeWS);
  // Jitter multiplier 1.0 → the schedule is exact and readable below.
  vi.spyOn(Math, "random").mockReturnValue(0.5);
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

/** Four consumers with their own windows, like the layout's bell/orb/bridge
 * plus a page, all under the one provider the layout mounts. */
const seen: Record<string, EventsState> = {};
function Probe({ name, max }: { name: string; max: number }) {
  seen[name] = useEvents(max);
  return null;
}
function Tree({ show }: { show: boolean }) {
  return (
    <EventsProvider>
      {show ? (
        <>
          <Probe name="bell" max={100} />
          <Probe name="orb" max={2} />
          <Probe name="bridge" max={50} />
          <Probe name="page" max={5} />
        </>
      ) : null}
    </EventsProvider>
  );
}

describe("one socket per window (EventsProvider)", () => {
  it("four hooks under one provider open ONE socket; each keeps its own window", () => {
    render(<Tree show />);
    expect(FakeWS.instances).toHaveLength(1);
    const ws = latest();
    act(() => {
      ws.open();
      ws.frame("e1");
      ws.frame("e2");
      ws.frame("e3");
    });
    expect(FakeWS.instances).toHaveLength(1); // frames did not spawn sockets
    for (const name of ["bell", "orb", "bridge", "page"]) {
      expect(seen[name].connected).toBe(true);
    }
    expect(seen.bell.events.map((e) => e.id)).toEqual(["e3", "e2", "e1"]);
    expect(seen.orb.events.map((e) => e.id)).toEqual(["e3", "e2"]); // its own max
    expect(seen.page.events.map((e) => e.id)).toEqual(["e3", "e2", "e1"]);
  });

  it("the provider keeps the socket alive across a route change (all hooks unmount, then remount)", () => {
    const { rerender } = render(<Tree show />);
    const ws = latest();
    act(() => {
      ws.open();
      ws.frame("e1");
    });
    rerender(<Tree show={false} />); // page navigation: every consumer gone
    expect(ws.closed).toBe(false);
    expect(FakeWS.instances).toHaveLength(1);

    rerender(<Tree show />);
    expect(FakeWS.instances).toHaveLength(1); // same socket, no reopen
    expect(seen.bell.connected).toBe(true); // a late subscriber learns the state at once
    expect(seen.bell.events).toEqual([]); // and starts with an empty window, as before

    // The cursor survived too: the next reconnect resumes from e1.
    act(() => {
      ws.drop();
      vi.advanceTimersByTime(RECONNECT_BASE_MS);
    });
    expect(FakeWS.instances).toHaveLength(2);
    expect(latest().url).toMatch(/\/events\?since=e1$/);
  });

  it("hooks rendered with no provider share the module hub — still one socket", () => {
    renderHook(() => useEvents(10));
    renderHook(() => useEvents(20));
    renderHook(() => useEvents(30));
    renderHook(() => useEvents(40));
    expect(FakeWS.instances).toHaveLength(1);
  });
});

describe("reconnect backoff (2.5 s → 30 s cap, ±20% jitter, reset on open)", () => {
  it("reconnectDelay: flat first retry, doubling, capped, jittered ±20%", () => {
    expect(reconnectDelay(0, () => 1)).toBe(2_500); // first retry: exact
    expect(reconnectDelay(1, () => 0.5)).toBe(5_000);
    expect(reconnectDelay(2, () => 0.5)).toBe(10_000);
    expect(reconnectDelay(3, () => 0.5)).toBe(20_000);
    expect(reconnectDelay(4, () => 0.5)).toBe(RECONNECT_CAP_MS);
    expect(reconnectDelay(10, () => 0.5)).toBe(RECONNECT_CAP_MS); // stays capped
    expect(reconnectDelay(2, () => 0)).toBe(8_000); // −20%
    expect(reconnectDelay(2, () => 1)).toBe(12_000); // +20%
    expect(reconnectDelay(9, () => 1)).toBe(36_000); // jitter applies to the cap too
  });

  it("a 62 s outage costs at most 8 attempts (2.5, 7.5, 17.5, 37.5 s), then holds at 30 s", () => {
    const { result } = renderHook(() => useEvents(100));
    const ws1 = latest();
    act(() => {
      ws1.open();
      ws1.frame("e1");
      ws1.drop();
    });
    expect(result.current.connected).toBe(false);

    // The daemon is away: every new socket is refused at once.
    const attemptsAt: number[] = [];
    let t = 0;
    let known = FakeWS.instances.length;
    while (t < 62_000) {
      act(() => {
        vi.advanceTimersByTime(500);
      });
      t += 500;
      if (FakeWS.instances.length > known) {
        known = FakeWS.instances.length;
        attemptsAt.push(t);
        act(() => latest().refuse());
      }
    }
    expect(attemptsAt.length).toBeLessThanOrEqual(8);
    expect(attemptsAt).toEqual([2_500, 7_500, 17_500, 37_500]);
    expect(latest().url).toMatch(/\/events\?since=e1$/); // still resumes from the cursor

    // Beyond that it holds at the 30 s cap: next at 67.5 s, then 97.5 s.
    act(() => {
      vi.advanceTimersByTime(67_500 - t);
    });
    expect(FakeWS.instances).toHaveLength(6);
    act(() => latest().refuse());
    act(() => {
      vi.advanceTimersByTime(30_000 - 1);
    });
    expect(FakeWS.instances).toHaveLength(6);
    act(() => {
      vi.advanceTimersByTime(1);
    });
    expect(FakeWS.instances).toHaveLength(7);

    // A successful open RESETS the ladder: the next blip retries at 2.5 s.
    act(() => {
      latest().open();
    });
    expect(result.current.connected).toBe(true);
    act(() => {
      latest().drop();
      vi.advanceTimersByTime(RECONNECT_BASE_MS - 1);
    });
    expect(FakeWS.instances).toHaveLength(7);
    act(() => {
      vi.advanceTimersByTime(1);
    });
    expect(FakeWS.instances).toHaveLength(8);
  });

  it("jitter reaches the live schedule: the second retry lands at 4 s (−20%) not 5 s", () => {
    vi.spyOn(Math, "random").mockReturnValue(0);
    renderHook(() => useEvents(10));
    act(() => {
      latest().drop(); // retry 1 at 2.5 s (exact)
      vi.advanceTimersByTime(2_500);
    });
    expect(FakeWS.instances).toHaveLength(2);
    act(() => {
      latest().refuse(); // retry 2 at 5 s × 0.8 = 4 s
      vi.advanceTimersByTime(3_999);
    });
    expect(FakeWS.instances).toHaveLength(2);
    act(() => {
      vi.advanceTimersByTime(1);
    });
    expect(FakeWS.instances).toHaveLength(3);
  });
});

describe("idempotent replay", () => {
  it("a replayed duplicate id is appended once; id-less frames still append", () => {
    const { result } = renderHook(() => useEvents(100));
    const ws1 = latest();
    act(() => {
      ws1.open();
      ws1.frame("e1");
      ws1.frame("e2");
      ws1.frame("e3");
      ws1.drop();
      vi.advanceTimersByTime(RECONNECT_BASE_MS);
    });
    const ws2 = latest();
    expect(ws2.url).toMatch(/\/events\?since=e3$/);
    act(() => {
      ws2.open();
      ws2.frame("e3"); // the daemon replays the cursor frame itself
      ws2.frame("e4");
      ws2.frame("e4"); // and a live duplicate
      ws2.frame(null);
      ws2.frame(null);
    });
    const ids = result.current.events.map((e) => e.id);
    expect(ids).toEqual([undefined, undefined, "e4", "e3", "e2", "e1"]);
    expect(result.current.connected).toBe(true);
  });

  it("a daemon RESTART forgets the cursor: replay is empty and live resumes silently (known, docs/TODO.md)", () => {
    const { result } = renderHook(() => useEvents(100));
    const ws1 = latest();
    act(() => {
      ws1.open();
      ws1.frame("e1");
      ws1.drop();
      vi.advanceTimersByTime(RECONNECT_BASE_MS);
    });
    const ws2 = latest();
    expect(ws2.url).toContain("since=e1");
    act(() => {
      ws2.open();
      ws2.frame("e9"); // live only — nothing between e1 and e9 is replayed
    });
    expect(result.current.events.map((e) => e.id)).toEqual(["e9", "e1"]);
  });
});
