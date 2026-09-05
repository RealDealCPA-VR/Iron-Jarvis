/**
 * v1.230.0 (audit Wave 4, FP2 / FP3 / the duplicate /health pollers) — polling
 * that pauses when hidden, ONE shared /health, a light /sessions poll.
 *
 *  - FP2: `usePolledApi` tears its interval down while `document.hidden` and
 *    refetches ONCE on the hidden->visible edge; `DaemonProvider` stretches
 *    its /health cadence to 30 s while hidden (a minimised window polled at
 *    full rate for 6.5 minutes in the audit).
 *  - shared /health: the layout's ModelSwitcher and the Overview read the
 *    DaemonProvider's poll instead of running their own (three pollers of one
 *    endpoint in one window); `useProviderHealth` reads the same context and
 *    polls for itself only outside a provider.
 *  - FP3: the Overview polls `/sessions?limit=50`; once a response carried an
 *    ETag the hook sends `If-None-Match` and keeps its data on a 304.
 *
 * Real `@/lib/api`, real `DaemonProvider`, real hooks; only `fetch` is stubbed.
 * Converted from __audit_20260904__/polling.test.tsx (Q1).
 */
import { act, cleanup, render, renderHook, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: [], connected: true }) }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: () => {}, push: () => {}, refresh: () => {} }),
  useSearchParams: () => new URLSearchParams(""),
  usePathname: () => "/",
}));

import { DaemonProvider, HIDDEN_HEALTH_INTERVAL_MS } from "@/lib/daemon";
import { usePolledApi } from "@/lib/useApi";
import { useProviderHealth } from "@/lib/useProviderHealth";
import { ModelSwitcher } from "@/components/ModelSwitcher";
import OverviewPage from "@/app/page";

type FetchCall = { url: string; init: RequestInit };
const calls: FetchCall[] = [];
const count = (needle: string) => calls.filter((c) => c.url.endsWith(needle)).length;
const headerOf = (call: FetchCall, name: string) =>
  ((call.init.headers || {}) as Record<string, string>)[name];

type Reply = { status?: number; body?: unknown; etag?: string };
type Router = (path: string, init: RequestInit) => Reply | undefined;

const HEALTH = {
  status: "ok",
  version: "1.230.0",
  default_provider: "fleet-custom",
  default_model: "fleet-llama-70b",
  providers: [
    { provider: "anthropic", available: true, class: "cloud" },
    { provider: "fleet-custom", available: false, class: "custom" },
  ],
};

/** The daemon's answers for everything the layout + Overview ask for. */
const daemonRoutes: Router = (path) => {
  if (path === "/health") return { body: HEALTH };
  if (path === "/models")
    return {
      body: { models: [{ provider: "anthropic", model: "claude-sonnet-4-6", name: "Anthropic" }] },
    };
  if (path.startsWith("/sessions")) return { body: { sessions: [] } };
  if (path === "/metrics")
    return {
      body: {
        sessions_evaluated: 1,
        avg_completion: 1,
        avg_tool_success_rate: 1,
        avg_latency_s: 1,
        total_tool_invocations: 1,
        event_count: 1,
      },
    };
  if (path === "/vault") return { body: { providers: [] } };
  if (path.startsWith("/onboarding"))
    return {
      body: { complete: true, done: true, dismissed: true, checklist: [], checks: [], next_step: null },
    };
  if (path.startsWith("/goals") || path.startsWith("/autonomy"))
    return { body: { goals: [], rules: [], enabled: false } };
  if (path === "/templates") return { body: { templates: [] } };
  if (path.startsWith("/reflex")) return { body: { rules: [] } };
  if (path.startsWith("/routing"))
    return { body: { enabled: false, routing_model: "", connected: [], suggested: null, tiers: {} } };
  return { body: {} };
};

function stubFetch(route: Router = daemonRoutes) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init: RequestInit) => {
      calls.push({ url, init });
      const path = url.replace(/^https?:\/\/[^/]+/, "");
      const r = route(path, init) ?? { body: {} };
      const status = r.status ?? 200;
      return {
        ok: status >= 200 && status < 300,
        status,
        statusText: status === 304 ? "Not Modified" : "OK",
        headers: { get: (k: string) => (k.toLowerCase() === "etag" ? (r.etag ?? null) : null) },
        json: async () => r.body ?? {},
      } as unknown as Response;
    }),
  );
}

let hidden = false;
function setHidden(v: boolean) {
  hidden = v;
  document.dispatchEvent(new Event("visibilitychange"));
}
const advance = (ms: number) =>
  act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });

beforeEach(() => {
  calls.length = 0;
  hidden = false;
  Object.defineProperty(document, "visibilityState", {
    configurable: true,
    get: () => (hidden ? "hidden" : "visible"),
  });
  Object.defineProperty(document, "hidden", { configurable: true, get: () => hidden });
  vi.useFakeTimers();
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  delete (document as unknown as Record<string, unknown>).visibilityState;
  delete (document as unknown as Record<string, unknown>).hidden;
});

const wrapper = ({ children }: { children: React.ReactNode }) => (
  <DaemonProvider>{children}</DaemonProvider>
);

describe("FP2 — polling pauses while the document is hidden", () => {
  it("hidden: usePolledApi issues at most ONE request in 60 s and /health slows to every 30 s", async () => {
    stubFetch();
    setHidden(true);
    renderHook(() => usePolledApi<{ ok: boolean }>("/metrics", 5000), { wrapper });
    for (let i = 0; i < 12; i++) await advance(5_000);
    // The mount fetch (the page must still render SOMETHING when it is next
    // looked at) and nothing else — no 5 s ticks while nobody is watching.
    expect(count("/metrics")).toBeLessThanOrEqual(1);
    // DaemonProvider: the first poll, then one every HIDDEN_HEALTH_INTERVAL_MS
    // (30 s and 60 s) — a hidden window still learns about an outage.
    expect(HIDDEN_HEALTH_INTERVAL_MS).toBe(30_000);
    expect(count("/health")).toBe(3);
    expect(document.visibilityState).toBe("hidden");
  });

  it("visible: the usual cadence — 13 requests in 60 s for both the page poll and /health", async () => {
    stubFetch();
    renderHook(() => usePolledApi<{ ok: boolean }>("/metrics", 5000), { wrapper });
    for (let i = 0; i < 12; i++) await advance(5_000);
    expect(count("/metrics")).toBe(13);
    expect(count("/health")).toBe(13);
  });

  it("regain: exactly ONE refetch on the hidden->visible edge, then the 5 s cadence resumes", async () => {
    stubFetch();
    const { result } = renderHook(() => usePolledApi<{ ok: boolean }>("/metrics", 5000), {
      wrapper,
    });
    await advance(5_000);
    expect(count("/metrics")).toBe(2);
    expect(count("/health")).toBe(2);

    await act(async () => setHidden(true));
    const metricsAtHide = count("/metrics");
    const healthAtHide = count("/health");
    await advance(30_000);
    // Going hidden cost nothing; 30 s later /health checked once, the page not at all.
    expect(count("/metrics")).toBe(metricsAtHide);
    expect(count("/health")).toBe(healthAtHide + 1);

    await act(async () => setHidden(false));
    // Both refetch at once so the page is current the moment it is looked at...
    expect(count("/metrics")).toBe(metricsAtHide + 1);
    expect(count("/health")).toBe(healthAtHide + 2);
    expect(result.current.data).toEqual(expect.objectContaining({}));
    // ...and only once: no doubled tick from the edge.
    await advance(4_999);
    expect(count("/metrics")).toBe(metricsAtHide + 1);
    await advance(1);
    expect(count("/metrics")).toBe(metricsAtHide + 2);
    expect(count("/health")).toBe(healthAtHide + 3);
  });
});

describe("one /health poll per window", () => {
  it("layout (DaemonProvider + ModelSwitcher) + Overview: 12/min after the first, i.e. 13 in 60 s", async () => {
    stubFetch();
    localStorage.setItem("ij_nav_advanced", "1"); // the Advanced health tile reads /health too
    render(
      <DaemonProvider>
        <ModelSwitcher />
        <OverviewPage />
      </DaemonProvider>,
    );
    await advance(10);
    // Both consumers rendered from the SHARED payload: the switcher's trigger
    // (it renders null until /health is known) and the Overview's version chip.
    expect(screen.getByRole("button", { name: /switch the active model/i })).toBeTruthy();
    expect(screen.getAllByText(/v1\.230\.0/).length).toBeGreaterThan(0);
    // FP3: the Overview's sessions poll asks for 50 rows, not the default 200.
    expect(calls.some((c) => c.url.endsWith("/sessions?limit=50"))).toBe(true);
    expect(calls.some((c) => /\/sessions(\?|$)/.test(c.url) && !c.url.includes("limit=50"))).toBe(
      false,
    );

    for (let i = 0; i < 12; i++) await advance(5_000);
    // Used to be 39 (three 5 s pollers). One poll: 1 + 12 ticks.
    expect(count("/health")).toBe(13);
  });

  it("useProviderHealth inside the provider reads the shared poll and adds NO request of its own", async () => {
    stubFetch();
    const { result } = renderHook(() => useProviderHealth(), { wrapper });
    await advance(10_000);
    expect(count("/health")).toBe(3); // the provider's t=0, 5 s, 10 s — nothing extra
    expect(result.current.loading).toBe(false);
    expect(result.current.byProvider).toEqual({ anthropic: true, "fleet-custom": false });
    expect(result.current.defaultProvider).toBe("fleet-custom");
    expect(result.current.stale).toBe(false);
  });

  it("useProviderHealth OUTSIDE any provider still polls for itself (the fallback)", async () => {
    stubFetch();
    const { result } = renderHook(() => useProviderHealth());
    await advance(10_000);
    expect(count("/health")).toBe(3); // its own t=0, 5 s, 10 s
    expect(result.current.byProvider).toEqual({ anthropic: true, "fleet-custom": false });
  });
});

describe("FP3 — the hook sends If-None-Match and keeps its data on a 304", () => {
  it("first 200 carries an ETag; the next poll sends it and a 304 keeps the data; a change lands with a new tag", async () => {
    const A = { sessions: [{ id: "s1", task: "one" }] };
    const C = { sessions: [{ id: "s2", task: "two" }, { id: "s1", task: "one" }] };
    let n = 0;
    stubFetch((path, init) => {
      if (!path.startsWith("/sessions")) return daemonRoutes(path, init);
      const tag = ((init.headers || {}) as Record<string, string>)["If-None-Match"];
      n += 1;
      if (n === 1) return { body: A, etag: 'W/"aaa"' };
      if (n === 2) return tag === 'W/"aaa"' ? { status: 304 } : { body: A, etag: 'W/"aaa"' };
      if (n === 3) return { body: C, etag: 'W/"ccc"' }; // the list changed
      return tag === 'W/"ccc"' ? { status: 304 } : { body: C, etag: 'W/"ccc"' };
    });
    const { result } = renderHook(
      () => usePolledApi<typeof A>("/sessions?limit=50", 5000),
      { wrapper },
    );
    await advance(10);
    const sessionCalls = () => calls.filter((c) => c.url.endsWith("/sessions?limit=50"));
    expect(headerOf(sessionCalls()[0], "If-None-Match")).toBeUndefined(); // nothing held yet
    expect(result.current.data).toEqual(A);

    await advance(5_000);
    expect(sessionCalls()).toHaveLength(2);
    expect(headerOf(sessionCalls()[1], "If-None-Match")).toBe('W/"aaa"');
    // 304: the data is kept, and it is a success — no error, not loading.
    expect(result.current.data).toEqual(A);
    expect(result.current.error).toBeNull();
    expect(result.current.loading).toBe(false);

    await advance(5_000);
    expect(headerOf(sessionCalls()[2], "If-None-Match")).toBe('W/"aaa"');
    expect(result.current.data).toEqual(C); // the change landed

    await advance(5_000);
    expect(headerOf(sessionCalls()[3], "If-None-Match")).toBe('W/"ccc"'); // and its tag is now held
    expect(result.current.data).toEqual(C);
  });

  it("a path whose responses carry no ETag keeps the one-argument fetch (no If-None-Match ever)", async () => {
    stubFetch();
    renderHook(() => usePolledApi<{ ok: boolean }>("/metrics", 5000), { wrapper });
    // One tick per act: React batches two setTick calls inside a single act
    // into one render (a browser renders each tick), so step 5 s at a time.
    await advance(5_000);
    await advance(5_000);
    const metrics = calls.filter((c) => c.url.endsWith("/metrics"));
    expect(metrics).toHaveLength(3);
    for (const c of metrics) expect(headerOf(c, "If-None-Match")).toBeUndefined();
  });
});
