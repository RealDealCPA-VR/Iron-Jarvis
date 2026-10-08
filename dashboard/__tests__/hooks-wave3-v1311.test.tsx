/**
 * v1.311.0 (UX wave 3, SPEED, track C) — the shared dashboard hooks stop
 * re-rendering the app for work that changed nothing.
 *
 *  - daemon-ctx-rerender-storm: a /health poll whose answer did not change
 *    re-renders NO consumer; a useApi host depends on the daemon `epoch` only,
 *    so even a CHANGED /health leaves it alone.
 *  - useapi-poll-double-render: a background poll tick costs ZERO renders when
 *    the answer is unchanged (fresh-but-equal JSON, or a 304), exactly ONE when
 *    it changed, never flips `loading`, and keeps the held `data` reference.
 *  - duplicate-pollers-no-dedupe: identical GETs share one in-flight request;
 *    a job handled from one surface (the Overview note) leaves the other (the
 *    bell) at once.
 *  - events-unfiltered-page-subscribers: `useEvents(max, { types })` drops a
 *    frame the caller does not read BEFORE any state update; the bell, the
 *    MoodOrb, the desktop bridge and the Overview each pass their own set.
 *
 * Perf is pinned by COUNTS (renders, commits, GETs) and by equality against a
 * baseline measured in the same test — never by wall-clock time. Real
 * `@/lib/api` with only `fetch` stubbed, real DaemonProvider / useApi /
 * EventsHub (a fake WebSocket feeds frames), real components.
 */
import { Profiler, useEffect, useState, type ReactNode } from "react";
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: () => {}, push: () => {}, refresh: () => {} }),
  useSearchParams: () => new URLSearchParams(""),
  usePathname: () => "/",
}));
// The Overview's live feed is unfiltered BY DESIGN (it shows everything); it
// is stubbed so the Overview's Profiler counts the page, not that feed.
vi.mock("@/components/EventStream", () => ({ EventStream: () => null }));

import { DaemonProvider, useDaemon } from "@/lib/daemon";
import { useApi, usePolledApi, type ApiState } from "@/lib/useApi";
import { EventsHub, EventsProvider, useEvents, type EventsState } from "@/lib/useEvents";
import { useInterruptedJobs, INTERRUPTED_PATH } from "@/components/InterruptedJobs";
import { MoodOrb } from "@/components/MoodOrb";
import { DesktopNotifyBridge } from "@/components/DesktopNotifyBridge";
import { NotificationBell } from "@/components/NotificationBell";
import OverviewPage from "@/components/overview/StatusOverview";

/* ---- fetch stub: a tiny daemon ------------------------------------------ */

type Reply = { status?: number; body?: unknown; etag?: string; delayMs?: number; hang?: boolean };
type Route = (path: string, init: RequestInit) => Reply | undefined;
type Call = { path: string; init: RequestInit };

const calls: Call[] = [];
const gets = (path: string) => calls.filter((c) => c.path === path).length;
const healthGets = () => gets("/health");

let HEALTH: Record<string, unknown>;
let route: Route;

const OVERVIEW_ROUTES: Route = (path) => {
  if (path === "/health") return { body: HEALTH };
  if (path === "/models")
    return { body: { models: [{ provider: "anthropic", model: "claude-sonnet-4-6", name: "Anthropic" }] } };
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
    return { body: { complete: true, done: true, dismissed: true, checklist: [], checks: [], next_step: null } };
  if (path.startsWith("/goals") || path.startsWith("/autonomy"))
    return { body: { goals: [], rules: [], enabled: false } };
  if (path === "/templates") return { body: { templates: [] } };
  if (path.startsWith("/reflex")) return { body: { rules: [] } };
  if (path.startsWith("/routing"))
    return { body: { enabled: false, routing_model: "", connected: [], suggested: null, tiers: {} } };
  return { body: {} };
};

function stubFetch() {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init: RequestInit) => {
      const path = url.replace(/^https?:\/\/[^/]+/, "");
      calls.push({ path, init });
      const r = route(path, init) ?? { body: {} };
      if (r.hang) return new Promise<Response>(() => {});
      if (r.delayMs) await new Promise((res) => setTimeout(res, r.delayMs));
      const status = r.status ?? 200;
      return {
        ok: status >= 200 && status < 300,
        status,
        statusText: status === 304 ? "Not Modified" : "OK",
        headers: { get: (k: string) => (k.toLowerCase() === "etag" ? (r.etag ?? null) : null) },
        // A FRESH object per response, exactly like a real fetch: a stub that
        // handed back the same reference would let React bail out on its own
        // and every "no re-render" pin below would pass vacuously.
        json: async () => JSON.parse(JSON.stringify(r.body ?? {})),
      } as unknown as Response;
    }),
  );
}

const advance = (ms: number) =>
  act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
/** `times` intervals of `stepMs`, EACH in its own act(): one act around
 *  several ticks would let React batch them into one render and the counts
 *  below would under-report (a real browser renders per macrotask). */
const advanceEach = async (stepMs: number, times: number) => {
  for (let i = 0; i < times; i += 1) await advance(stepMs);
};
/** Let mount fetches resolve (several microtask/timer turns, no poll fires). */
const settle = async () => {
  for (let i = 0; i < 5; i += 1) await advance(10);
};

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
    this.onmessage?.({ data: JSON.stringify({ id, type, session_id, ts: "2026-10-07T00:00:00Z", payload }) });
  }
  drop() {
    this.onclose?.();
  }
}
const socket = () => FakeWS.instances[FakeWS.instances.length - 1];
let seq = 0;
/** 20 frames of types no Track-C surface reads (add-on tab noise, router
 *  noise) — each frame in its OWN act(), as each socket message is its own
 *  task in a browser (one act would batch them into one render). */
function pushNoise(ws: FakeWS, n = 20) {
  for (let i = 0; i < n; i += 1) {
    act(() => ws.frame(`noise-${(seq += 1)}`, i % 2 ? "browser.tab_activated" : "provider.routed", { i }));
  }
}

/* ---- visibility (jsdom defaults to hidden) -------------------------------- */

beforeEach(() => {
  calls.length = 0;
  HEALTH = {
    status: "ok",
    version: "1.311.0",
    default_provider: "anthropic",
    default_model: "claude-sonnet-4-6",
    providers: [{ provider: "anthropic", available: true, class: "cloud" }],
  };
  route = (path) => (path === "/health" ? { body: HEALTH } : { body: {} });
  Object.defineProperty(document, "visibilityState", { configurable: true, get: () => "visible" });
  Object.defineProperty(document, "hidden", { configurable: true, get: () => false });
  FakeWS.instances = [];
  vi.useFakeTimers();
  stubFetch();
  vi.stubGlobal("WebSocket", FakeWS);
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  delete (document as unknown as Record<string, unknown>).visibilityState;
  delete (document as unknown as Record<string, unknown>).hidden;
  delete (window as unknown as Record<string, unknown>).ironjarvis;
});

/* ========================================================================== */
/* daemon-ctx-rerender-storm                                                  */
/* ========================================================================== */

describe("DaemonProvider: an unchanged /health renders nothing", () => {
  const r = { daemon: 0, api: 0 };
  function DaemonProbe() {
    r.daemon += 1;
    const { health } = useDaemon();
    return <span data-testid="model">{String(health?.default_model ?? "")}</span>;
  }
  function ApiProbe() {
    r.api += 1;
    const s = useApi<{ ok?: boolean }>("/x");
    return <span data-testid="x">{s.data ? "ok" : "-"}</span>;
  }
  beforeEach(() => {
    r.daemon = 0;
    r.api = 0;
    route = (path) => (path === "/health" ? { body: HEALTH } : { body: { ok: true } });
  });

  it("three identical /health polls re-render a useDaemon() consumer ZERO times", async () => {
    render(
      <DaemonProvider>
        <DaemonProbe />
      </DaemonProvider>,
    );
    await settle();
    expect(screen.getByTestId("model").textContent).toBe("claude-sonnet-4-6");
    const polls0 = healthGets();
    const base = r.daemon;
    await advanceEach(5000, 3);
    expect(healthGets() - polls0).toBe(3); // anti-vacuity: the polls really ran
    expect(r.daemon - base).toBe(0);
  });

  it("CONTROL: a changed /health re-renders the useDaemon() consumer exactly once and shows the change", async () => {
    render(
      <DaemonProvider>
        <DaemonProbe />
      </DaemonProvider>,
    );
    await settle();
    const base = r.daemon;
    HEALTH = { ...HEALTH, default_model: "claude-opus-4-8" };
    await advance(5000);
    expect(screen.getByTestId("model").textContent).toBe("claude-opus-4-8");
    expect(r.daemon - base).toBe(1);
  });

  it("three identical /health polls re-render a useApi() host ZERO times", async () => {
    render(
      <DaemonProvider>
        <ApiProbe />
      </DaemonProvider>,
    );
    await settle();
    expect(screen.getByTestId("x").textContent).toBe("ok");
    const polls0 = healthGets();
    const base = r.api;
    await advanceEach(5000, 3);
    expect(healthGets() - polls0).toBe(3);
    expect(r.api - base).toBe(0);
  });

  it("a CHANGED /health re-renders a useDaemon() reader but NOT a useApi() host (useApi reads only the epoch)", async () => {
    render(
      <DaemonProvider>
        <DaemonProbe />
        <ApiProbe />
      </DaemonProvider>,
    );
    await settle();
    const baseApi = r.api;
    const baseDaemon = r.daemon;
    HEALTH = { ...HEALTH, default_model: "claude-opus-4-8", version: "1.311.1" };
    await advance(5000);
    expect(r.daemon - baseDaemon).toBe(1); // in-test control: the change was seen
    expect(r.api - baseApi).toBe(0);
  });
});

/* ========================================================================== */
/* useapi-poll-double-render                                                  */
/* ========================================================================== */

describe("usePolledApi: a background tick that changed nothing costs nothing", () => {
  const log: { renders: number; loading: boolean[]; datas: unknown[]; last: ApiState<unknown> | null } = {
    renders: 0,
    loading: [],
    datas: [],
    last: null,
  };
  function Polled({ path, every }: { path: string; every: number }) {
    const s = usePolledApi<{ n: number }>(path, every);
    log.renders += 1;
    log.loading.push(s.loading);
    log.datas.push(s.data);
    log.last = s as ApiState<unknown>;
    return <span data-testid="n">{s.data ? String(s.data.n) : "-"}</span>;
  }
  let payload: { n: number; rows: string[] };
  beforeEach(() => {
    log.renders = 0;
    log.loading = [];
    log.datas = [];
    log.last = null;
    payload = { n: 1, rows: ["a", "b"] };
  });

  it("five ticks with an identical (fresh-object) answer: ZERO renders after the first load", async () => {
    route = (path) => (path === "/m" ? { body: payload } : { body: {} });
    render(<Polled path="/m" every={1000} />);
    await settle();
    expect(screen.getByTestId("n").textContent).toBe("1");
    const fetched = gets("/m");
    const base = log.renders;
    await advanceEach(1000, 5);
    expect(gets("/m") - fetched).toBe(5); // anti-vacuity: five real fetches
    expect(log.renders - base).toBe(0);
  });

  it("five ticks answered 304 (ETag): ZERO renders", async () => {
    route = (path, init) => {
      if (path !== "/m") return { body: {} };
      const inm = ((init.headers || {}) as Record<string, string>)["If-None-Match"];
      return inm === '"v1"' ? { status: 304 } : { body: payload, etag: '"v1"' };
    };
    render(<Polled path="/m" every={1000} />);
    await settle();
    const base = log.renders;
    const fetched = gets("/m");
    await advanceEach(1000, 5);
    const conditional = calls
      .filter((c) => c.path === "/m")
      .slice(fetched)
      .filter((c) => ((c.init.headers || {}) as Record<string, string>)["If-None-Match"] === '"v1"');
    expect(conditional).toHaveLength(5); // anti-vacuity: five 304 round trips
    expect(log.renders - base).toBe(0);
  });

  it("a tick whose answer CHANGED renders exactly ONCE, with the new data", async () => {
    route = (path) => (path === "/m" ? { body: payload } : { body: {} });
    render(<Polled path="/m" every={1000} />);
    await settle();
    const base = log.renders;
    payload = { n: 2, rows: ["a", "b", "c"] };
    await advance(1000);
    expect(screen.getByTestId("n").textContent).toBe("2");
    expect(log.renders - base).toBe(1);
  });

  it("a background tick never flips `loading` to true (no spinner flicker over held data)", async () => {
    route = (path) => (path === "/m" ? { body: payload } : { body: {} });
    render(<Polled path="/m" every={1000} />);
    await settle();
    expect(log.last?.loading).toBe(false);
    const from = log.loading.length;
    payload = { n: 3, rows: [] }; // a changed tick still renders — but never as "loading"
    await advanceEach(1000, 3);
    expect(log.loading.length).toBeGreaterThan(from); // anti-vacuity: it did render
    expect(log.loading.slice(from)).not.toContain(true);
  });

  it("an unchanged tick keeps the SAME data reference (dependent useMemos do not recompute)", async () => {
    route = (path) => (path === "/m" ? { body: payload } : { body: {} });
    render(<Polled path="/m" every={1000} />);
    await settle();
    const held = log.last?.data;
    expect(held).toBeTruthy();
    await advanceEach(1000, 2);
    expect(log.last?.data).toBe(held);
  });

  it("GUARD (passes today): switching PATH still reports loading=true while the new path is pending", async () => {
    // The verifier's trap: gating setLoading(true) on `data === null` would
    // show the OLD path's data as settled. Only BACKGROUND ticks are quiet.
    const seenBox: { s: ApiState<{ n: number }> | null } = { s: null };
    function Switch({ p }: { p: string }) {
      seenBox.s = useApi<{ n: number }>(p);
      return null;
    }
    route = (path) => (path === "/a" ? { body: { n: 1 } } : path === "/b" ? { hang: true } : { body: {} });
    const { rerender } = render(<Switch p="/a" />);
    await settle();
    expect(seenBox.s!.loading).toBe(false);
    rerender(<Switch p="/b" />);
    await settle();
    expect(seenBox.s!.loading).toBe(true);
  });

  it("GUARD (passes today): an explicit reload() still issues a GET", async () => {
    route = (path) => (path === "/m" ? { body: payload } : { body: {} });
    render(<Polled path="/m" every={60_000} />);
    await settle();
    const before = gets("/m");
    await act(async () => log.last!.reload());
    await settle();
    expect(gets("/m") - before).toBe(1);
  });
});

/* ========================================================================== */
/* duplicate-pollers-no-dedupe                                                */
/* ========================================================================== */

describe("identical GETs share one request", () => {
  const states: Record<string, ApiState<{ v: number }>> = {};
  function One({ name, path }: { name: string; path: string }) {
    states[name] = useApi<{ v: number }>(path);
    return null;
  }
  function Poll({ name, path, every }: { name: string; path: string; every: number }) {
    states[name] = usePolledApi<{ v: number }>(path, every);
    return null;
  }
  let version: number;
  beforeEach(() => {
    version = 1;
    for (const k of Object.keys(states)) delete states[k];
  });

  it("two useApi('/diagnostics') hosts mounted together issue ONE GET and both get the data", async () => {
    route = (path) => (path === "/diagnostics" ? { body: { v: version }, delayMs: 20 } : { body: {} });
    render(
      <>
        <One name="bell" path="/diagnostics" />
        <One name="page" path="/diagnostics" />
      </>,
    );
    await settle();
    expect(gets("/diagnostics")).toBe(1);
    expect(states.bell.data?.v).toBe(1);
    expect(states.page.data?.v).toBe(1);
  });

  it("two pollers of one path at one interval: 1 + 3 GETs over three ticks, not 2 + 6", async () => {
    route = (path) => (path === "/diagnostics" ? { body: { v: version }, delayMs: 20 } : { body: {} });
    render(
      <>
        <Poll name="bell" path="/diagnostics" every={15_000} />
        <Poll name="page" path="/diagnostics" every={15_000} />
      </>,
    );
    await settle();
    await advanceEach(15_000, 3);
    await settle();
    expect(gets("/diagnostics")).toBe(4);
  });

  it("GUARD (passes today): different paths are never merged", async () => {
    route = (path) => ({ body: { v: path === "/a" ? 1 : 2 }, delayMs: 20 });
    render(
      <>
        <One name="a" path="/a" />
        <One name="b" path="/b" />
      </>,
    );
    await settle();
    expect(gets("/a")).toBe(1);
    expect(gets("/b")).toBe(1);
    expect(states.a.data?.v).toBe(1);
    expect(states.b.data?.v).toBe(2);
  });

  it("GUARD (passes today): a reload() after a mutation never joins a request issued BEFORE it (no stale answer)", async () => {
    // Request 1 is answered with the state at the time it was SENT.
    route = (path) => {
      if (path !== "/p") return { body: {} };
      const v = version;
      return { body: { v }, delayMs: 50 };
    };
    render(
      <>
        <One name="bell" path="/p" />
        <One name="note" path="/p" />
      </>,
    );
    // Request 1 is in flight; the user mutates, then the note reloads.
    await advance(5);
    version = 2;
    await act(async () => states.note.reload());
    await advance(200);
    await settle();
    expect(states.note.data?.v).toBe(2);
  });

  it("a job dismissed from the Overview note leaves the bell AT ONCE (before any refetch answers)", async () => {
    const J1 = "sess_w3_dismiss_1";
    const J2 = "sess_w3_keep_2";
    let hangNow = false;
    route = (path) => {
      if (path !== INTERRUPTED_PATH) return { body: {} };
      if (hangNow) return { hang: true }; // every GET after the press stays in flight
      return {
        body: {
          sessions: [
            { id: J1, task: "one", agent_type: "coder", interrupted_at: "2026-10-07T00:00:00Z" },
            { id: J2, task: "two", agent_type: "coder", interrupted_at: "2026-10-07T00:00:00Z" },
          ],
        },
      };
    };
    const lists: Record<string, ReturnType<typeof useInterruptedJobs>> = {};
    function Surface({ name, every }: { name: string; every: number }) {
      lists[name] = useInterruptedJobs(every);
      return null;
    }
    render(
      <>
        <Surface name="bell" every={15_000} />
        <Surface name="note" every={30_000} />
      </>,
    );
    await settle();
    const ids = (k: string) => lists[k].jobs.map((j) => j.id);
    // Precondition: both surfaces hold the list.
    expect(ids("bell")).toEqual([J1, J2]);
    expect(ids("note")).toEqual([J1, J2]);
    hangNow = true;
    await act(async () => lists.note.forget(J1));
    await advance(100); // far below the bell's 15 s tick
    expect(ids("note")).toEqual([J2]); // control: the acting surface drops it
    expect(ids("bell")).toEqual([J2]);
  });
});

/* ========================================================================== */
/* events-unfiltered-page-subscribers                                         */
/* ========================================================================== */

type Opts = { types?: readonly string[] };
const useEventsFiltered = useEvents as unknown as (max?: number, opts?: Opts) => EventsState;

describe("useEvents(max, { types }) — a frame nobody reads costs no render", () => {
  const r = { n: 0, last: null as EventsState | null };
  function Probe({ max, types }: { max: number; types?: readonly string[] }) {
    r.n += 1;
    r.last = useEventsFiltered(max, types ? { types } : undefined);
    return null;
  }
  const APPROVALS = ["approval.requested"] as const;
  function mount(node: ReactNode, hub = new EventsHub()) {
    const out = render(<EventsProvider hub={hub}>{node}</EventsProvider>);
    act(() => socket().open());
    return out;
  }
  beforeEach(() => {
    r.n = 0;
    r.last = null;
  });

  it("20 unread frames: ZERO renders; one matching frame: exactly ONE render, and it is the only event held", () => {
    mount(<Probe max={50} types={APPROVALS} />);
    const base = r.n;
    pushNoise(socket());
    expect(r.n - base).toBe(0);
    act(() => socket().frame("ap-1", "approval.requested", { id: "a1" }));
    expect(r.n - base).toBe(1);
    expect(r.last!.events.map((e) => e.id)).toEqual(["ap-1"]);
  });

  it("an entry ending in '.*' matches by prefix ('review.*' takes review.requested and review.approved, not reviewer.x)", () => {
    mount(<Probe max={50} types={["review.*"]} />);
    const base = r.n;
    act(() => socket().frame("x-1", "reviewer.assigned"));
    act(() => socket().frame("x-2", "browser.tab_activated"));
    expect(r.n - base).toBe(0);
    act(() => socket().frame("rv-1", "review.requested"));
    act(() => socket().frame("rv-2", "review.approved"));
    expect(r.last!.events.map((e) => e.id)).toEqual(["rv-2", "rv-1"]);
  });

  it("`max` bounds the MATCHING frames: noise never pushes a wanted event out of the window", () => {
    mount(<Probe max={2} types={["agent.started"]} />);
    act(() => socket().frame("a-1", "agent.started"));
    pushNoise(socket(), 5);
    act(() => socket().frame("a-2", "agent.started"));
    expect(r.last!.events.map((e) => e.id)).toEqual(["a-2", "a-1"]);
  });

  it("GUARD (passes today): a filtered subscriber still re-renders when the socket drops (connected flips)", () => {
    mount(<Probe max={50} types={APPROVALS} />);
    expect(r.last!.connected).toBe(true);
    const base = r.n;
    act(() => socket().drop());
    expect(r.last!.connected).toBe(false);
    expect(r.n - base).toBe(1);
  });

  it("GUARD (passes today): the no-argument form still receives every frame (EventStream/TimeTravelFeed)", () => {
    mount(<Probe max={50} />);
    pushNoise(socket(), 3);
    expect(r.last!.events).toHaveLength(3);
  });

  it("GUARD (passes today): an INLINE `types` literal does not resubscribe on every render", () => {
    const hub = new EventsHub();
    const sub = vi.spyOn(hub, "subscribe");
    function Parent() {
      const [, setN] = useState(0);
      useEffect(() => {
        for (let i = 0; i < 5; i += 1) setTimeout(() => setN((n) => n + 1), i);
      }, []);
      // A fresh array every render — the hook must key on its CONTENT.
      return <Probe max={50} types={["approval.requested", "approval.resolved"]} />;
    }
    mount(<Parent />, hub);
    const subs0 = sub.mock.calls.length;
    for (let i = 0; i < 6; i += 1) {
      act(() => {
        vi.advanceTimersByTime(1);
      });
    }
    expect(r.n).toBeGreaterThanOrEqual(5); // anti-vacuity: it really re-rendered
    expect(sub.mock.calls.length - subs0).toBe(0);
  });
});

describe("layout + Overview subscribers ignore the event types they never read", () => {
  /** Commits of everything inside `<Profiler>` — counts renders of the
   *  component AND its children, without reaching into its internals. */
  function profiled(node: ReactNode) {
    const box = { commits: 0 };
    const onRender = () => {
      box.commits += 1;
    };
    return { box, el: <Profiler id="p" onRender={onRender}>{node}</Profiler> };
  }
  /** Commits caused by `fn` (which brings its own act()s). */
  function delta(box: { commits: number }, fn: () => void) {
    const before = box.commits;
    fn();
    return box.commits - before;
  }
  /** The baseline: the same number of empty act()s as the noise uses. */
  const idleWindow = () => {
    for (let i = 0; i < 20; i += 1) act(() => {});
  };

  it("MoodOrb: 20 unread frames commit nothing; CONTROL: review.requested turns it to alert", () => {
    const { box, el } = profiled(<MoodOrb />);
    render(<EventsProvider hub={new EventsHub()}>{el}</EventsProvider>);
    act(() => socket().open());
    const idle = delta(box, idleWindow);
    expect(delta(box, () => pushNoise(socket()))).toBe(idle);
    act(() => socket().frame("rv-orb", "review.requested", {}, "sess_orb"));
    expect(screen.getByRole("img").getAttribute("aria-label")).toMatch(/Attention needed/);
  });

  it("DesktopNotifyBridge: 20 unread frames commit nothing; CONTROL: comm.desktop raises one toast", () => {
    const notify = vi.fn(async () => true);
    (window as unknown as { ironjarvis: unknown }).ironjarvis = { notify };
    const { box, el } = profiled(<DesktopNotifyBridge />);
    render(<EventsProvider hub={new EventsHub()}>{el}</EventsProvider>);
    act(() => socket().open());
    const idle = delta(box, idleWindow);
    expect(delta(box, () => pushNoise(socket()))).toBe(idle);
    act(() => socket().frame("cd-1", "comm.desktop", { title: "Hi", message: "there" }));
    expect(notify).toHaveBeenCalledTimes(1);
    expect(notify).toHaveBeenCalledWith("Hi", "there");
  });

  it("NotificationBell: 20 unread frames commit nothing; CONTROL: review.requested raises the count", async () => {
    const { box, el } = profiled(<NotificationBell />);
    render(
      <DaemonProvider>
        <EventsProvider hub={new EventsHub()}>{el}</EventsProvider>
      </DaemonProvider>,
    );
    act(() => socket().open());
    await settle();
    const idle = delta(box, idleWindow);
    expect(delta(box, () => pushNoise(socket()))).toBe(idle);
    act(() => socket().frame("rv-bell", "review.requested", {}, "sess_bell"));
    expect(document.title).toMatch(/^\(1\)/);
  });

  it("GUARD (passes today): the bell runs no /health poll of its own (it reads nothing DaemonProvider already polls)", async () => {
    const alone = render(<DaemonProvider>{null}</DaemonProvider>);
    await settle();
    await advance(30_000);
    const baseline = healthGets();
    alone.unmount();
    calls.length = 0;
    render(
      <DaemonProvider>
        <EventsProvider hub={new EventsHub()}>
          <NotificationBell />
        </EventsProvider>
      </DaemonProvider>,
    );
    await settle();
    await advance(30_000);
    expect(healthGets()).toBe(baseline);
  });

  it("Overview: 20 unread frames commit nothing; CONTROL: agent.started re-renders it", async () => {
    route = OVERVIEW_ROUTES;
    const { box, el } = profiled(<OverviewPage />);
    render(
      <DaemonProvider>
        <EventsProvider hub={new EventsHub()}>{el}</EventsProvider>
      </DaemonProvider>,
    );
    act(() => socket().open());
    await settle();
    const idle = delta(box, idleWindow);
    expect(delta(box, () => pushNoise(socket()))).toBe(idle);
    expect(
      delta(box, () => act(() => socket().frame("as-1", "agent.started", { run_id: "r1", task: "x" }, "sess_ov"))),
    ).toBeGreaterThan(idle);
  });
});
