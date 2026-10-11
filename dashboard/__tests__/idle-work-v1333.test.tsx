/**
 * v1.333.0 (lane F) — the dashboard stops doing work nobody sees, and two
 * shared hooks stop going wrong under load.
 *
 *  - F1: the sidebar's chat list (lib/chatList) re-read itself on
 *    `events.length`, and its event window holds 20 frames, so from the 21st
 *    `chat.thread_updated` on the list was never refreshed again. It is now
 *    keyed on the newest frame's id: 25 updates cost 1 + 25 reads.
 *  - F2: against a daemon that accepts but never answers, every poll tick of
 *    `usePolledApi` sent a fresh GET past the hung one (`get` has no
 *    timeout), so the requests piled up. A tick now waits on the hook's own
 *    request while it is younger than POLL_STALL_TICKS intervals and never
 *    has more than MAX_OUTSTANDING_POLLS on the wire.
 *  - F6: the MoodOrb spun "thinking" forever after a daemon restart cut a run
 *    off (no session.completed is ever sent for it). It reads the boot's
 *    `sessions.interrupted`, and on a socket reconnect asks once per run it
 *    still shows as in flight.
 *
 * Counts only (GETs, renders); never wall-clock assertions.
 */
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const H = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 500) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  return {
    FakeApiError,
    gets: [] as string[],
    /** path -> how the GET answers: a body, or "hang" (a pending promise the
     *  test can settle later through `hung`). */
    replies: {} as Record<string, unknown>,
    hung: [] as { path: string; resolve: (v: unknown) => void; reject: (e: unknown) => void }[],
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: H.FakeApiError,
  API_BASE: "",
  ijToken: () => "",
  wsUrl: (p: string) => `ws://daemon${p}`,
  get: (path: string) => {
    H.gets.push(path);
    const r = H.replies[path];
    if (r === "hang") {
      return new Promise((resolve, reject) => H.hung.push({ path, resolve, reject }));
    }
    return Promise.resolve(r === undefined ? {} : JSON.parse(JSON.stringify(r)));
  },
  post: async () => ({}),
}));

import { MoodOrb } from "@/components/MoodOrb";
import { __resetChatList, useChatList } from "@/lib/chatList";
import { MAX_OUTSTANDING_POLLS, POLL_STALL_TICKS, usePolledApi } from "@/lib/useApi";
import { EventsHub, EventsProvider } from "@/lib/useEvents";
import { __resetApiCache } from "@/lib/apiCache";

/* ---- fake /events socket --------------------------------------------------- */

class FakeWS {
  static instances: FakeWS[] = [];
  url: string;
  onopen: (() => void) | null = null;
  onmessage: ((ev: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  constructor(url: string) {
    this.url = url;
    FakeWS.instances.push(this);
  }
  close() {}
  open() {
    this.onopen?.();
  }
  frame(id: string, type: string, payload: Record<string, unknown> = {}, session_id: string | null = null) {
    this.onmessage?.({ data: JSON.stringify({ id, type, session_id, ts: "2026-10-11T00:00:00Z", payload }) });
  }
  drop() {
    this.onclose?.();
  }
}
const socket = () => FakeWS.instances[FakeWS.instances.length - 1];
const count = (path: string) => H.gets.filter((g) => g === path).length;

beforeEach(() => {
  H.gets.length = 0;
  H.replies = {};
  H.hung = [];
  FakeWS.instances = [];
  __resetChatList();
  __resetApiCache();
  Object.defineProperty(document, "visibilityState", { configurable: true, get: () => "visible" });
  Object.defineProperty(document, "hidden", { configurable: true, get: () => false });
  vi.stubGlobal("WebSocket", FakeWS);
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  delete (document as unknown as Record<string, unknown>).visibilityState;
  delete (document as unknown as Record<string, unknown>).hidden;
});

/* ========================================================================== */
/* F1 — the sidebar chat list re-reads on EVERY thread update                 */
/* ========================================================================== */

describe("useChatList: a thread update re-reads the list, however many came before", () => {
  function Probe() {
    useChatList();
    return null;
  }

  it("25 chat.thread_updated frames cost 1 + 25 reads of /chat/threads (the window holds only 20)", async () => {
    H.replies["/chat/threads"] = { threads: [] };
    H.replies["/projects"] = { projects: [] };
    render(
      <EventsProvider hub={new EventsHub()}>
        <Probe />
      </EventsProvider>,
    );
    act(() => socket().open());
    await act(async () => {});
    expect(count("/chat/threads")).toBe(1);
    for (let i = 1; i <= 25; i += 1) {
      act(() => socket().frame(`t-${i}`, "chat.thread_updated", { thread_id: "c1" }));
      await act(async () => {}); // the read lands before the next update
    }
    expect(count("/chat/threads")).toBe(1 + 25);
  });

  it("CONTROL: frames of other types read nothing", async () => {
    H.replies["/chat/threads"] = { threads: [] };
    render(
      <EventsProvider hub={new EventsHub()}>
        <Probe />
      </EventsProvider>,
    );
    act(() => socket().open());
    await act(async () => {});
    for (let i = 1; i <= 5; i += 1) {
      act(() => socket().frame(`n-${i}`, "provider.routed"));
      await act(async () => {});
    }
    expect(count("/chat/threads")).toBe(1);
  });
});

/* ========================================================================== */
/* F2 — a stalled daemon does not collect a pile of hung polls                */
/* ========================================================================== */

describe("usePolledApi: poll ticks never pile up hung requests", () => {
  const PATH = "/terminals";
  const EVERY = 2000;
  function Probe() {
    usePolledApi(PATH, EVERY);
    return null;
  }
  const advanceEach = async (stepMs: number, times: number) => {
    for (let i = 0; i < times; i += 1) {
      await act(async () => {
        await vi.advanceTimersByTimeAsync(stepMs);
      });
    }
  };

  it("a daemon that never answers: one minute of 2 s ticks sends MAX_OUTSTANDING_POLLS requests, not 31", async () => {
    vi.useFakeTimers();
    H.replies[PATH] = "hang";
    render(<Probe />);
    await act(async () => {});
    expect(count(PATH)).toBe(1);
    await advanceEach(EVERY, 30);
    expect(MAX_OUTSTANDING_POLLS).toBe(2);
    expect(count(PATH)).toBe(MAX_OUTSTANDING_POLLS);
  });

  it("a tick waits on its own request while it is young, then sends one fresh request (the old recovery)", async () => {
    vi.useFakeTimers();
    H.replies[PATH] = "hang";
    render(<Probe />);
    await act(async () => {});
    // Ticks before POLL_STALL_TICKS intervals have passed send nothing new.
    await advanceEach(EVERY, POLL_STALL_TICKS - 1);
    expect(count(PATH)).toBe(1);
    // The tick at POLL_STALL_TICKS intervals sends ONE fresh request.
    await advanceEach(EVERY, 1);
    expect(count(PATH)).toBe(2);
  });

  it("when the hung requests finally answer, polling resumes at the old cadence", async () => {
    vi.useFakeTimers();
    H.replies[PATH] = "hang";
    render(<Probe />);
    await act(async () => {});
    await advanceEach(EVERY, 10);
    expect(count(PATH)).toBe(2);
    // The daemon recovers: both hung GETs settle and later ones answer.
    H.replies[PATH] = { panes: [] };
    await act(async () => {
      for (const h of H.hung) h.resolve({ panes: [] });
    });
    await advanceEach(EVERY, 5);
    expect(count(PATH)).toBe(2 + 5);
  });

  it("a slow daemon that still answers keeps refreshing: every answer lands and frees its slot", async () => {
    vi.useFakeTimers();
    // Each GET answers 5 s after it is sent (2.5 intervals): slow, not stalled.
    let n = 0;
    H.replies[PATH] = "hang";
    function SlowProbe() {
      const { data } = usePolledApi<{ n: number }>(PATH, EVERY);
      return <span data-testid="n">{data ? String(data.n) : "-"}</span>;
    }
    render(<SlowProbe />);
    await act(async () => {});
    const answerDue = async () => {
      // Settle every request that has been on the wire for 5 s.
      for (const h of H.hung.splice(0)) {
        n += 1;
        h.resolve({ n });
      }
    };
    for (let t = 0; t < 12; t += 1) {
      await advanceEach(2500, 2); // 5 s
      await act(answerDue);
    }
    // A minute of a 5 s daemon: it was asked again and again, and each answer
    // reached the screen (never more than 2 on the wire at once).
    expect(count(PATH)).toBeGreaterThanOrEqual(10);
    expect(screen.getByTestId("n").textContent).toBe(String(n));
  });

  it("a new path is polled at its own cadence even while the old path's requests are hung", async () => {
    vi.useFakeTimers();
    const OLD = "/terminals/stuck";
    const NEW = "/terminals/fine";
    H.replies[OLD] = "hang";
    H.replies[NEW] = { panes: [] };
    function Switch({ path }: { path: string }) {
      usePolledApi(path, EVERY);
      return null;
    }
    const { rerender } = render(<Switch path={OLD} />);
    await act(async () => {});
    await advanceEach(EVERY, 10);
    expect(count(OLD)).toBe(MAX_OUTSTANDING_POLLS); // the old path is capped…
    rerender(<Switch path={NEW} />);
    await act(async () => {});
    expect(count(NEW)).toBe(1); // the path switch's own fetch
    await advanceEach(EVERY, 5);
    // …and its hung requests do not hold the NEW path's polls back.
    expect(count(NEW)).toBe(1 + 5);
  });

  it("CONTROL: a responsive daemon is polled exactly as before (1 + 30 GETs in a minute)", async () => {
    vi.useFakeTimers();
    H.replies[PATH] = { panes: [] };
    render(<Probe />);
    await act(async () => {});
    await advanceEach(EVERY, 30);
    expect(count(PATH)).toBe(1 + 30);
  });
});

/* ========================================================================== */
/* F6 — the MoodOrb stops spinning for runs a daemon restart ended            */
/* ========================================================================== */

describe("MoodOrb: a run a restart cut off stops reading as in flight", () => {
  const mood = () => screen.getByRole("img").getAttribute("aria-label") ?? "";
  function mountOrb() {
    render(
      <EventsProvider hub={new EventsHub()}>
        <MoodOrb />
      </EventsProvider>,
    );
    act(() => socket().open());
  }

  it("a sessions.interrupted frame naming the run turns the orb back to idle", () => {
    mountOrb();
    act(() => socket().frame("e1", "session.created", {}, "s1"));
    expect(mood()).toMatch(/^Thinking/);
    act(() => socket().frame("e2", "sessions.interrupted", { count: 1, session_ids: ["s1"] }));
    expect(mood()).toMatch(/^Idle/);
  });

  it("on a socket RECONNECT it asks once per in-flight run (slim read) and a run that ended stops the spin", async () => {
    vi.useFakeTimers();
    H.replies["/sessions/s2?transcript=0"] = { session: { id: "s2", status: "failed" }, transcript: null };
    mountOrb();
    act(() => socket().frame("e1", "session.created", {}, "s2"));
    expect(mood()).toMatch(/^Thinking/);
    expect(H.gets).toEqual([]); // nothing is read while connected
    act(() => socket().drop()); // the daemon restarts…
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000); // …the hub reconnects
    });
    act(() => socket().open());
    await act(async () => {});
    expect(H.gets).toEqual(["/sessions/s2?transcript=0"]);
    expect(mood()).toMatch(/^Idle/);
  });

  it("a run settled after a reconnect that is then CONTINUED (a new start, same id) spins again", async () => {
    vi.useFakeTimers();
    H.replies["/sessions/s4?transcript=0"] = { session: { id: "s4", status: "completed" }, transcript: null };
    mountOrb();
    act(() => socket().frame("e1", "session.created", {}, "s4"));
    act(() => socket().drop());
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });
    act(() => socket().open());
    await act(async () => {});
    expect(mood()).toMatch(/^Idle/);
    // The user continues it: a continue re-publishes session.created under the
    // SAME session id. The answer the orb got for the OLD start must not
    // silence the new one.
    act(() => socket().frame("e7", "session.created", {}, "s4"));
    expect(mood()).toMatch(/^Thinking/);
    expect(H.gets).toEqual(["/sessions/s4?transcript=0"]); // and still read nothing while connected
  });

  it("CONTROL: a run the daemon still reports active keeps the orb thinking", async () => {
    vi.useFakeTimers();
    H.replies["/sessions/s3?transcript=0"] = { session: { id: "s3", status: "active" }, transcript: null };
    mountOrb();
    act(() => socket().frame("e1", "session.created", {}, "s3"));
    act(() => socket().drop());
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });
    act(() => socket().open());
    await act(async () => {});
    expect(H.gets).toEqual(["/sessions/s3?transcript=0"]);
    expect(mood()).toMatch(/^Thinking/);
  });
});
