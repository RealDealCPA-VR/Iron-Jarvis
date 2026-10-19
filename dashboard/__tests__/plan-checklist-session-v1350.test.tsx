/**
 * v1.350.0 (polish wave C, lane C1): the session page shows the same plan
 * checklist as the chat's helper-agent row, in its Live run card under the
 * phase line.
 *
 * Plain words: open a run that made a plan (the decomposed lane) on its
 * session page and the plan is there, ticking off as the run's events land.
 * A page opened after the plan was made reads the daemon's copy (GET
 * /sessions/{id}/plan); a run with no plan (or an older daemon, a 404) shows
 * the card exactly as before. A finished session reads nothing.
 *
 * Harness: trust-posture-v1298's (the real page, its data hooks mocked), with
 * the run stream live and useEvents a small external store.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";

const H = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
    }
  }
  return {
    FakeApiError,
    responses: {} as Record<string, unknown>,
    gets: [] as string[],
    run: { active: true, phase: null as null | { phase: string; detail: string } },
    ev: { list: [] as Record<string, unknown>[], listeners: new Set<() => void>(), n: 0 },
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: H.FakeApiError,
  API_BASE: "http://api.test",
  ijToken: () => "tok",
  get: (path: string) => {
    H.gets.push(path);
    const r = H.responses[path];
    if (r === undefined) return Promise.reject(new H.FakeApiError(`unmocked GET ${path}`, 404));
    return Promise.resolve(JSON.parse(JSON.stringify(r)));
  },
  post: () => Promise.resolve({}),
  put: () => Promise.resolve({}),
  del: () => Promise.resolve({}),
}));
vi.mock("@/lib/useApi", () => ({
  useApi: (path: string | null) => ({
    data: path ? H.responses[path] ?? null : null,
    error: null,
    loading: false,
    reload: () => {},
  }),
  usePolledApi: (path: string | null) => ({
    data: path ? H.responses[path] ?? null : null,
    error: null,
    loading: false,
    reload: () => {},
  }),
}));
vi.mock("@/lib/useRunStream", () => ({
  useRunStream: () => ({
    text: "",
    tools: [],
    phase: H.run.phase,
    active: H.run.active,
    start: () => {},
    stop: () => {},
  }),
}));
vi.mock("@/lib/useEvents", async () => {
  const React = await import("react");
  const subscribe = (cb: () => void) => {
    H.ev.listeners.add(cb);
    return () => {
      H.ev.listeners.delete(cb);
    };
  };
  return {
    useEvents: () => ({ events: React.useSyncExternalStore(subscribe, () => H.ev.list), connected: true }),
  };
});
vi.mock("@/lib/useTTS", () => ({
  useTTS: () => ({ enabled: false, supported: false, toggle: () => {}, speak: () => {}, stop: () => {} }),
}));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: () => {} }) }));
vi.mock("@/components/ReviewPanel", () => ({ ReviewPanel: () => null }));
vi.mock("@/components/TracesPanel", () => ({ TracesPanel: () => null }));
vi.mock("@/components/SessionFeedback", () => ({ SessionFeedback: () => null }));
vi.mock("@/components/TimeTravelFeed", () => ({ TimeTravelFeed: () => null }));
vi.mock("@/components/chat/DocPreview", () => ({
  DocPreview: ({ path }: { path: string }) => <div data-testid="doc-preview">{path}</div>,
  appLabelFor: () => "app",
}));
vi.mock("next/link", async () => {
  const { createElement } = await import("react");
  return {
    default: ({ href, children, ...rest }: { href: string; children?: React.ReactNode }) =>
      createElement("a", { href, ...rest }, children),
  };
});
vi.mock("framer-motion", async () => {
  const { createElement, Fragment } = await import("react");
  const MOTION_ONLY = new Set(["initial", "animate", "exit", "transition", "variants", "layout", "whileHover", "whileTap", "whileInView", "viewport"]);
  const tagFor = (tag: string) => (props: Record<string, unknown>) => {
    const rest: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(props)) if (!MOTION_ONLY.has(k)) rest[k] = v;
    return createElement(tag, rest);
  };
  const cache = new Map<string, unknown>();
  const motion = new Proxy({} as Record<string, unknown>, {
    get: (_t, tag) => {
      const key = String(tag);
      if (!cache.has(key)) cache.set(key, tagFor(key));
      return cache.get(key);
    },
  });
  return {
    m: motion,
    motion,
    AnimatePresence: ({ children }: { children?: React.ReactNode }) => createElement(Fragment, null, children),
  };
});

import SessionDetailPage from "@/app/sessions/[id]/page";

const GOALS = ["read the ledger", "draft the summary", "check the totals"];
const PLAN = "/sessions/s-1/plan";

function ev(type: string, payload: Record<string, unknown>, session = "s-1") {
  H.ev.n += 1;
  return { id: `e${H.ev.n}`, type, session_id: session, ts: "", payload };
}
async function land(...oldestFirst: Record<string, unknown>[]) {
  await act(async () => {
    H.ev.list = [...[...oldestFirst].reverse(), ...H.ev.list];
    for (const cb of [...H.ev.listeners]) cb();
  });
}

function fakeParams(id: string): Promise<{ id: string }> {
  const p = Promise.resolve({ id });
  Object.assign(p as object, { status: "fulfilled", value: { id } });
  return p;
}

function renderPage(status: string) {
  H.responses["/sessions/s-1"] = {
    session: {
      id: "s-1",
      task: "summarise the ledger",
      agent_type: "researcher",
      provider: "ollama",
      model: "qwen",
      status,
      workspace_path: "C:/w",
      summary: "",
      created_at: "2026-10-17T10:00:00Z",
      finished_at: null,
    },
    transcript: { runs: [], tools: [] },
  };
  return render(<SessionDetailPage params={fakeParams("s-1")} />);
}

const states = () =>
  Array.from(document.querySelectorAll('[data-testid="plan-step"]')).map((li) => li.getAttribute("data-state"));

beforeEach(() => {
  H.responses = {};
  H.gets.length = 0;
  H.run.active = true;
  H.run.phase = { phase: "running", detail: "step 2 of 3: draft the summary" };
  H.ev.list = [];
  H.ev.n = 0;
});
afterEach(() => cleanup());

describe("sessions/[id]: the plan checklist in the Live run card", () => {
  it("a page opened mid-run reads the daemon's copy once, shows it under the phase line, and ticks with the events", async () => {
    H.responses[PLAN] = {
      steps: [
        { goal: GOALS[0], state: "done" },
        { goal: GOALS[1], state: "running" },
        { goal: GOALS[2], state: "waiting" },
      ],
    };
    renderPage("running");
    await waitFor(() => expect(states()).toEqual(["done", "running", "waiting"]));
    // It sits in the Live run card, the one with the phase line.
    const list = screen.getByTestId("plan-checklist");
    const phase = screen.getByText(/step 2 of 3: draft the summary/);
    let card: HTMLElement | null = list;
    while (card && !card.contains(phase)) card = card.parentElement;
    expect(card).not.toBeNull();
    expect(within(card!).getByText("Live run")).toBeTruthy();
    await land(
      ev("plan.step_completed", { run_id: "r1", index: 1, ok: true }),
      ev("plan.step_started", { run_id: "r1", index: 2, goal: GOALS[2] }),
    );
    await waitFor(() => expect(states()).toEqual(["done", "done", "running"]));
    expect(H.gets.filter((g) => g === PLAN)).toHaveLength(1);
  });

  it("opened before any phase frame (the stream does not replay one): the daemon's copy is read at once", async () => {
    H.run.phase = null;
    H.responses[PLAN] = { steps: GOALS.map((goal, i) => ({ goal, state: i === 0 ? "running" : "waiting" })) };
    renderPage("running");
    await waitFor(() => expect(states()).toEqual(["running", "waiting", "waiting"]));
    // anti-vacuity: no phase line yet. v1.354.0 (G20): before the daemon names
    // a phase the card says "Working…" (it said the engine word "streaming").
    expect(screen.getByTestId("live-run-phase").textContent).toBe("Working…");
  });

  it("the events alone make the checklist when the page saw the plan made (no daemon copy needed)", async () => {
    H.ev.list = [
      ev("plan.created", { run_id: "r1", steps: GOALS }),
      ev("plan.step_started", { run_id: "r1", index: 0, goal: GOALS[0] }),
      ev("plan.created", { run_id: "rx", steps: ["another session's plan"] }, "s-2"),
    ].reverse();
    renderPage("running");
    await waitFor(() => expect(states()).toEqual(["running", "waiting", "waiting"]));
    expect(screen.queryByText("another session's plan")).toBeNull();
    expect(H.gets).not.toContain(PLAN);
  });

  it("no plan (a 404, or an older daemon): the Live run card is as it was; a finished session reads nothing", async () => {
    renderPage("running");
    await waitFor(() => expect(H.gets).toContain(PLAN));
    await act(async () => {});
    expect(screen.queryByTestId("plan-checklist")).toBeNull();
    expect(screen.getByText("Live run")).toBeTruthy();
    cleanup();
    H.gets.length = 0;
    H.run.active = false;
    renderPage("completed");
    await act(async () => {});
    expect(H.gets).not.toContain(PLAN);
    expect(screen.queryByTestId("plan-checklist")).toBeNull();
  });

  /* v1.350.0 lane C1 review: a session that goes live AGAIN while this page
   * stays mounted on it (a queued run promoted, a run restarted under its own
   * id) must not inherit the last run's plan (its daemon copy, its spent
   * reads, or its events still in the window). ("Send a follow-up" itself
   * opens the NEW session's page: POST /continue mints a new session.) */
  const setStatus = (status: string) => {
    (H.responses["/sessions/s-1"] as { session: { status: string } }).session.status = status;
  };

  it("a follow-up run on the same page starts afresh: the last run's daemon copy is not its plan", async () => {
    H.responses[PLAN] = { steps: GOALS.map((goal) => ({ goal, state: "running" })) };
    const page = renderPage("running");
    await waitFor(() => expect(states()).toEqual(["running", "running", "running"]));
    // The run ends: the Live run card goes.
    H.run.active = false;
    H.run.phase = null;
    setStatus("completed");
    page.rerender(<SessionDetailPage params={fakeParams("s-1")} />);
    await waitFor(() => expect(screen.queryByTestId("plan-checklist")).toBeNull());
    // A follow-up starts a new run on the same session, with no plan of its own.
    delete H.responses[PLAN];
    H.run.active = true;
    H.run.phase = { phase: "running", detail: "working the task" };
    setStatus("running");
    page.rerender(<SessionDetailPage params={fakeParams("s-1")} />);
    // The new run asks the daemon again (a 404 now) and shows no plan.
    await waitFor(() => expect(H.gets.filter((g) => g === PLAN)).toHaveLength(2));
    await act(async () => {});
    expect(screen.getByText("Live run")).toBeTruthy();
    expect(screen.queryByTestId("plan-checklist")).toBeNull();
  });

  it("a follow-up run on the same page starts afresh: the last run's plan events are not its plan, its own are", async () => {
    H.ev.list = [
      ev("plan.created", { run_id: "r1", steps: GOALS }),
      ev("plan.step_started", { run_id: "r1", index: 0, goal: GOALS[0] }),
      ev("plan.step_completed", { run_id: "r1", index: 0, ok: true }),
    ].reverse();
    const page = renderPage("running");
    await waitFor(() => expect(states()).toEqual(["done", "waiting", "waiting"]));
    H.run.active = false;
    setStatus("completed");
    page.rerender(<SessionDetailPage params={fakeParams("s-1")} />);
    await land(ev("session.completed", {}));
    H.run.active = true;
    H.run.phase = { phase: "running", detail: "working the task" };
    setStatus("running");
    page.rerender(<SessionDetailPage params={fakeParams("s-1")} />);
    await waitFor(() => expect(H.gets).toContain(PLAN)); // the new run's own read (404)
    await act(async () => {});
    expect(screen.getByText("Live run")).toBeTruthy();
    expect(screen.queryByTestId("plan-checklist")).toBeNull();
    // The new run's own plan shows, made from its own events.
    await land(ev("plan.created", { run_id: "r2", steps: ["sort the receipts", "file them"] }));
    await waitFor(() => expect(states()).toEqual(["waiting", "waiting"]));
    expect(screen.getByText("sort the receipts")).toBeTruthy();
  });
});
