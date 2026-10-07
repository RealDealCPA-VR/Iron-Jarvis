/**
 * v1.311.0 (UX wave 3, SPEED, track C, daemon-ctx-rerender-storm) — the
 * DaemonProvider's context value is MEMOISED: the provider re-rendering for a
 * reason that changes none of its fields (a refresh() that re-polls an
 * unchanged /health, the window's visibility edge) hands consumers the SAME
 * object, so they do not re-render. The equality skip on /health is pinned in
 * hooks-wave3-v1311; this pins the memo, which that file cannot see (an
 * unchanged poll never re-renders the provider at all).
 *
 * Counts, never time: renders of a useDaemon() consumer, plus the /health GET
 * that proves the re-poll really ran. Real `@/lib/api` with `fetch` stubbed
 * and a FRESH object per response.
 */
import { act, cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DaemonProvider, useDaemon } from "@/lib/daemon";

const HEALTH = { status: "ok", version: "1.311.0", default_provider: "anthropic", default_model: "m" };
let healthGets = 0;

beforeEach(() => {
  healthGets = 0;
  Object.defineProperty(document, "visibilityState", { configurable: true, get: () => "visible" });
  Object.defineProperty(document, "hidden", { configurable: true, get: () => false });
  vi.useFakeTimers();
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (String(url).endsWith("/health")) healthGets += 1;
      return {
        ok: true,
        status: 200,
        statusText: "OK",
        headers: { get: () => null },
        json: async () => JSON.parse(JSON.stringify(HEALTH)),
      } as unknown as Response;
    }),
  );
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  delete (document as unknown as Record<string, unknown>).visibilityState;
  delete (document as unknown as Record<string, unknown>).hidden;
});

const settle = async () => {
  for (let i = 0; i < 5; i += 1) {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10);
    });
  }
};

describe("DaemonProvider hands consumers one stable value", () => {
  it("a refresh() that re-polls an unchanged /health re-renders no consumer", async () => {
    const box = { renders: 0, refresh: (() => {}) as () => void };
    function Probe() {
      box.renders += 1;
      box.refresh = useDaemon().refresh;
      return null;
    }
    render(
      <DaemonProvider>
        <Probe />
      </DaemonProvider>,
    );
    await settle();
    const gets0 = healthGets;
    const base = box.renders;
    await act(async () => box.refresh());
    await settle();
    expect(healthGets - gets0).toBe(1); // anti-vacuity: the re-poll really ran
    expect(box.renders - base).toBe(0);
  });
});
