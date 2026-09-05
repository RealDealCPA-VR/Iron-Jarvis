/**
 * v1.230.0 (audit Wave 4, FP1/FP4/FP5) — the fetch seam.
 *
 *  - FP1: lib/api.ts no longer sends `cache: "no-store"` (it defeated
 *    Chromium's preflight cache: one OPTIONS per GET). Freshness is the
 *    daemon's `Cache-Control: no-store` header now (tests/test_no_store_v1230.py).
 *  - FP4: DaemonProvider has an in-flight guard + a sequence number, and the
 *    banner needs TWO missed polls. One slow /health (>8 s) on a healthy daemon
 *    never commits an offline render and never bumps the epoch.
 *  - FP5: a CALLER-aborted request rejects with ApiError("cancelled", 0,
 *    cancelled=true) and fires NO network signal, so the command palette's
 *    per-keystroke abort no longer restarts the poll loop.
 *
 * Real `@/lib/api` + real `DaemonProvider`; only `fetch` is stubbed.
 * Converted from __audit_20260904__/polling.test.tsx (Q2, Q3).
 */
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api, ApiError, onNetworkError } from "@/lib/api";
import { DaemonProvider, useDaemon } from "@/lib/daemon";
import { DaemonBanner } from "@/components/DaemonBanner";

type FetchCall = { url: string; init: RequestInit };
const calls: FetchCall[] = [];

const okResponse = (body: unknown) =>
  ({ ok: true, status: 200, statusText: "OK", json: async () => body }) as Response;

/** A fetch that never answers until its AbortSignal fires (a slow daemon). */
function hangUntilAbort(init: RequestInit): Promise<Response> {
  return new Promise((_, reject) => {
    if (init.signal?.aborted) {
      reject(new DOMException("The user aborted a request.", "AbortError"));
      return;
    }
    init.signal?.addEventListener("abort", () =>
      reject(new DOMException("The user aborted a request.", "AbortError")),
    );
  });
}

const commits: string[] = [];
function Probe() {
  const d = useDaemon();
  commits.push(d.checking ? "checking" : d.online ? "online" : "offline");
  return (
    <div data-testid="probe">
      {d.checking ? "checking" : d.online ? "online" : "offline"} epoch={d.epoch}
    </div>
  );
}

beforeEach(() => {
  calls.length = 0;
  commits.length = 0;
  vi.useFakeTimers();
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("FP1 — the fetch init lets Chromium use its preflight cache", () => {
  it("lib/api.ts sends NO `cache` key on a GET (the daemon's no-store header owns freshness)", async () => {
    window.localStorage.setItem("ij_token", "tok");
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init: RequestInit) => {
        calls.push({ url, init });
        return okResponse({});
      }),
    );
    await api("/metrics");
    const init = calls[0].init;
    expect("cache" in init).toBe(false); // measured in Edge: cache:"no-store" forced OPTIONS per GET
    const h = init.headers as Record<string, string>;
    expect(h.Authorization).toBe("Bearer tok"); // the auth path is untouched
    window.localStorage.removeItem("ij_token");
  });
});

describe("FP5 — an abort is not an outage", () => {
  it("a CALLER-aborted request rejects as cancelled (status 0, cancelled=true) and fires no network signal", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init: RequestInit) => {
        calls.push({ url, init });
        return hangUntilAbort(init);
      }),
    );
    const seen = vi.fn();
    const off = onNetworkError(seen);
    const ctrl = new AbortController();
    const p = api("/search/history?q=x", { signal: ctrl.signal });
    ctrl.abort();
    await expect(p).rejects.toMatchObject({ status: 0, message: "cancelled", cancelled: true });
    await expect(p).rejects.toBeInstanceOf(ApiError);
    expect(seen).not.toHaveBeenCalled();
    off();
  });

  it("the opt-in TIMEOUT abort is still an outage: status 0, not cancelled, network signal fired", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init: RequestInit) => {
        calls.push({ url, init });
        return hangUntilAbort(init);
      }),
    );
    const seen = vi.fn();
    const off = onNetworkError(seen);
    const p = api("/metrics", { timeoutMs: 1000 });
    const settled = expect(p).rejects.toMatchObject({
      status: 0,
      message: "daemon offline",
      cancelled: false,
    });
    await vi.advanceTimersByTimeAsync(1000);
    await settled;
    expect(seen).toHaveBeenCalledTimes(1);
    off();
  });

  it("the palette's per-keystroke abort does not restart DaemonProvider's poll loop", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init: RequestInit) => {
        calls.push({ url, init });
        if (url.includes("/search/history")) return hangUntilAbort(init);
        return okResponse({ status: "ok", providers: [] });
      }),
    );
    render(
      <DaemonProvider>
        <Probe />
      </DaemonProvider>,
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(screen.getByTestId("probe").textContent).toContain("online epoch=1");
    const healthCalls = () => calls.filter((c) => c.url.endsWith("/health")).length;
    expect(healthCalls()).toBe(1);
    for (const q of ["a", "ab", "abc"]) {
      const ctrl = new AbortController();
      const p = api("/search/history?q=" + q, { signal: ctrl.signal }).catch((e) => e);
      ctrl.abort();
      await act(async () => {
        await p;
        await vi.advanceTimersByTimeAsync(0);
      });
    }
    // No refresh(): the loop was not restarted, so no extra /health was issued
    // and the epoch did not move.
    expect(healthCalls()).toBe(1);
    expect(screen.getByTestId("probe").textContent).toContain("online epoch=1");
  });
});

describe("FP4 — a steady offline banner", () => {
  const mount = () =>
    render(
      <DaemonProvider>
        <DaemonBanner />
        <Probe />
      </DaemonProvider>,
    );
  const step = async (ms: number) => {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(ms);
    });
  };
  const healthCalls = () => calls.filter((c) => c.url.endsWith("/health")).length;
  const probe = () => screen.getByTestId("probe").textContent ?? "";
  const planFetch = (plan: Record<number, "hang">) => {
    let n = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init: RequestInit) => {
        calls.push({ url, init });
        n += 1;
        if (plan[n] === "hang") return hangUntilAbort(init);
        return okResponse({ status: "ok", providers: [] });
      }),
    );
  };

  it("ONE slow /health (>8 s) on a healthy daemon: no overlapping poll, no offline commit, no banner, no epoch bump", async () => {
    planFetch({ 2: "hang" }); // #2 (t=5) hangs until its 8 s abort
    mount();
    await step(0);
    expect(probe()).toContain("online epoch=1");
    await step(5_000); // #2 issued, hangs
    await step(5_000); // t=10: the tick is SKIPPED — #2 is still in flight
    expect(healthCalls()).toBe(2);
    await step(3_500); // t=13: #2 aborted -> first miss -> immediate confirmation #3, answered
    expect(healthCalls()).toBe(3);
    await step(0);
    expect(probe()).toContain("online epoch=1"); // never left online, epoch untouched
    expect(commits).not.toContain("offline"); // no offline render was ever committed...
    expect(screen.queryByText("Daemon offline.")).toBeNull(); // ...so the banner never mounted
    await step(5_000); // and the regular cadence resumes
    expect(healthCalls()).toBe(4);
    expect(probe()).toContain("online epoch=1");
  });

  it("a REAL outage: banner after TWO missed polls, recovery on the first good poll", async () => {
    // Daemon stops answering after t=0: #2(t5) hangs -> abort t13 (miss 1) ->
    // #3 confirmation at t13 hangs -> abort t21 (miss 2) -> offline. #4(t25)
    // and #5(t35) hang; #6(t45) answers -> online, epoch bumped.
    planFetch({ 2: "hang", 3: "hang", 4: "hang", 5: "hang" });
    mount();
    await step(0);
    await step(5_000);
    await step(5_000);
    expect(screen.queryByText("Daemon offline.")).toBeNull(); // t=10: nothing yet
    await step(3_100); // t=13.1: miss 1 -> still online, confirming
    expect(probe()).toContain("online epoch=1");
    expect(screen.queryByText("Daemon offline.")).toBeNull();
    expect(healthCalls()).toBe(3);
    await step(8_000); // t=21.1: confirmation aborted -> miss 2 -> offline
    expect(probe()).toContain("offline");
    expect(screen.getByText("Daemon offline.")).toBeInTheDocument();
    await step(5_000); // t=26.1: #4 (t25) hangs; still offline
    expect(probe()).toContain("offline");
    await step(10_000); // t=36.1: #4 aborted at 33; #5 (t35) hangs
    expect(probe()).toContain("offline");
    await step(10_000); // t=46.1: #5 aborted at 43; #6 (t45) answers -> online
    await step(0);
    expect(probe()).toContain("online epoch=2");
    expect(healthCalls()).toBe(6);
  });

  it("a 500 on /health does NOT flip offline (reachable-but-erroring stays online)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init: RequestInit) => {
        calls.push({ url, init });
        return { ok: false, status: 500, statusText: "Internal", json: async () => ({}) } as Response;
      }),
    );
    render(
      <DaemonProvider>
        <Probe />
      </DaemonProvider>,
    );
    await step(0);
    expect(screen.getByTestId("probe").textContent).toContain("online");
  });
});
