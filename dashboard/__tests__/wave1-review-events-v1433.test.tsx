/**
 * v1.433.0 (Wave 1, A10) — answering a review clears the bell and the orb.
 *
 * POST /reviews/{id}/approve|reject published nothing, and both readers wait
 * for a later `review.*` event for the same session before they let a
 * `review.requested` go: the bell kept its badge, the orb its rose "alert".
 * The daemon now publishes `review.approved` / `review.rejected` tagged with
 * the session's id. The bell also re-reads /diagnostics at once on one — its
 * badge is the LARGER of the live count and the polled `pending_reviews`, so
 * a stale poll would have held the badge up to 15 s.
 *
 * Real lib/api (only fetch stubbed), real DaemonProvider and EventsHub (a
 * fake WebSocket feeds frames), real components.
 */
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: () => {}, push: () => {}, refresh: () => {} }),
  useSearchParams: () => new URLSearchParams(""),
  usePathname: () => "/",
}));

import { DaemonProvider } from "@/lib/daemon";
import { EventsHub, EventsProvider } from "@/lib/useEvents";
import { MoodOrb } from "@/components/MoodOrb";
import { NotificationBell } from "@/components/NotificationBell";

const calls: string[] = [];
const S = { pending: 0 };

function stubFetch() {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      const p = url.replace(/^https?:\/\/[^/]+/, "");
      calls.push(p);
      let body: unknown = {};
      if (p === "/health")
        body = { status: "ok", version: "1.433.0", default_provider: "anthropic", default_model: "x", providers: [] };
      if (p === "/diagnostics") body = { pending_reviews: S.pending, background_loops: {} };
      return {
        ok: true,
        status: 200,
        statusText: "OK",
        headers: { get: () => null },
        json: async () => JSON.parse(JSON.stringify(body)),
      } as unknown as Response;
    }),
  );
}

class FakeWS {
  static instances: FakeWS[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((ev: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  constructor(public url: string) {
    FakeWS.instances.push(this);
  }
  close() {}
  open() {
    this.onopen?.();
  }
  frame(id: string, type: string, session_id: string | null, payload: Record<string, unknown> = {}) {
    this.onmessage?.({ data: JSON.stringify({ id, type, session_id, ts: "2026-11-04T00:00:00Z", payload }) });
  }
}
const socket = () => FakeWS.instances[FakeWS.instances.length - 1];

const advance = (ms: number) =>
  act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
const settle = async () => {
  for (let i = 0; i < 5; i += 1) await advance(10);
};
const diagGets = () => calls.filter((c) => c === "/diagnostics").length;

beforeEach(() => {
  calls.length = 0;
  S.pending = 0;
  FakeWS.instances = [];
  Object.defineProperty(document, "visibilityState", { configurable: true, get: () => "visible" });
  Object.defineProperty(document, "hidden", { configurable: true, get: () => false });
  vi.useFakeTimers();
  stubFetch();
  vi.stubGlobal("WebSocket", FakeWS);
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("the mood orb", () => {
  it("leaves 'attention' once the review is answered — for THAT session only", () => {
    render(
      <EventsProvider hub={new EventsHub()}>
        <MoodOrb />
      </EventsProvider>,
    );
    act(() => socket().open());
    const label = () => screen.getByRole("img").getAttribute("aria-label") ?? "";
    act(() => socket().frame("rq-1", "review.requested", "sess_a"));
    expect(label()).toMatch(/Attention needed/);
    // CONTROL: an answer for ANOTHER session clears nothing.
    act(() => socket().frame("ap-b", "review.approved", "sess_b"));
    expect(label()).toMatch(/Attention needed/);
    act(() => socket().frame("ap-a", "review.approved", "sess_a"));
    expect(label()).not.toMatch(/Attention needed/);
  });

  it("a rejection clears it too", () => {
    render(
      <EventsProvider hub={new EventsHub()}>
        <MoodOrb />
      </EventsProvider>,
    );
    act(() => socket().open());
    act(() => socket().frame("rq-1", "review.requested", "sess_a"));
    expect(screen.getByRole("img").getAttribute("aria-label")).toMatch(/Attention needed/);
    act(() => socket().frame("rj-a", "review.rejected", "sess_a"));
    expect(screen.getByRole("img").getAttribute("aria-label")).not.toMatch(/Attention needed/);
  });
});

describe("the bell", () => {
  function mountBell() {
    render(
      <DaemonProvider>
        <EventsProvider hub={new EventsHub()}>
          <NotificationBell />
        </EventsProvider>
      </DaemonProvider>,
    );
  }

  it("drops a requested review's badge when the review is rejected", async () => {
    mountBell();
    act(() => socket().open());
    await settle();
    act(() => socket().frame("rq-1", "review.requested", "sess_a"));
    expect(document.title).toMatch(/^\(1\)/);
    act(() => socket().frame("rj-a", "review.rejected", "sess_a"));
    await settle();
    expect(document.title).not.toMatch(/^\(\d+\)/);
  });

  it("re-reads /diagnostics at once on an answer, so a stale poll cannot hold the badge", async () => {
    S.pending = 1; // the daemon's count before the answer
    mountBell();
    act(() => socket().open());
    await settle();
    expect(document.title).toMatch(/^\(1\)/);
    act(() => socket().frame("rq-1", "review.requested", "sess_a"));
    await settle();
    const before = diagGets();
    S.pending = 0; // the answer landed
    act(() => socket().frame("ap-a", "review.approved", "sess_a"));
    await settle(); // far short of the 15 s poll
    expect(diagGets()).toBe(before + 1);
    expect(document.title).not.toMatch(/^\(\d+\)/);
  });
});
