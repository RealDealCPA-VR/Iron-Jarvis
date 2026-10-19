/**
 * v1.350.0 (polish wave C, lane C1): the live plan checklist.
 *
 * Plain words: when an agent that chat handed work to MAKES A PLAN (the
 * decomposed lane), the helper-agent row shows the plan as a quiet checklist
 * that ticks off while it works (waiting ○, running →, done ✓ struck through,
 * failed ×, not attempted –). Closed, the row keeps "Step 2 of 3: …" and adds
 * "1 of 3 done". When the run ends, finalize saves the checklist as it ENDED
 * on the hand-off message, so a reopened chat shows it settled. A page that
 * joins a run already under way reads the daemon's copy (GET
 * /sessions/{id}/plan; a 404 means no plan, or an older daemon). And the
 * optional plan ask (`approve_plan`, args {steps}) shows its steps as a
 * numbered list on the ordinary approval card.
 *
 * What carries weight:
 *  - the fold (lib/planChecklist): every state from the real event shapes;
 *    re-folding on what was known keeps a plan.created that left the event
 *    window; a resumed run's re-sent plan keeps its ticks; junk never blanks;
 *  - the live row on the REAL chat page, fed by a live event store: ticks as
 *    events land, the plan's own progress lines are not said twice, the
 *    collapsed count, the daemon's copy on a reload mid-run (and nothing but
 *    the events on a 404);
 *  - finalize saves the ended plan on BOTH the live path and a reopened
 *    chat's, asks the daemon when a step is still open (the poll can beat the
 *    last event), and writes nothing when the user left while that read was
 *    out;
 *  - the settled row opens to the saved plan from the thread alone;
 *  - calm: no box, nothing animates, every state said in words;
 *  - the plan ask's numbered list, buttons unchanged.
 *
 * The page is rendered for real; only the transport hooks are mocked, at the
 * same seams agent-run-row-v1349 uses. useEvents is a small external store
 * so the test can land events the way the socket would.
 */

import { readFileSync } from "node:fs";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const H = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 500) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  type Held = { resolve: (v: unknown) => void; reject: (e: unknown) => void };
  return {
    FakeApiError,
    api: {
      gets: [] as string[],
      posts: [] as { path: string; body: Record<string, unknown> }[],
      puts: [] as { path: string; body: Record<string, unknown> }[],
      /** A value, an Error, or a function of the call count (1-based). */
      getResponses: {} as Record<string, unknown>,
      getCounts: {} as Record<string, number>,
      /** Paths whose NEXT read is held until the test releases it. */
      holds: {} as Record<string, { armed: boolean; used: boolean; held?: Held }>,
    },
    /** What the run stream shows (useRunStream's fields the page reads). */
    run: {
      text: "",
      tools: [] as unknown[],
      phase: null as null | { phase: string; detail: string },
    },
    /** The live event window (newest first), as useEvents hands it out. */
    ev: {
      list: [] as Record<string, unknown>[],
      listeners: new Set<() => void>(),
      n: 0,
    },
  };
});

function answer(path: string): unknown {
  H.api.getCounts[path] = (H.api.getCounts[path] ?? 0) + 1;
  let r = H.api.getResponses[path];
  if (typeof r === "function") r = (r as (n: number) => unknown)(H.api.getCounts[path]);
  if (r === undefined) throw new H.FakeApiError(`unmocked GET ${path}`, 404);
  if (r instanceof Error) throw r;
  return JSON.parse(JSON.stringify(r));
}

vi.mock("@/lib/api", () => ({
  ApiError: H.FakeApiError,
  API_BASE: "",
  ijToken: () => "",
  sseUrl: (p: string) => `http://localhost/stub${p}`,
  get: async (path: string) => {
    H.api.gets.push(path);
    const hold = H.api.holds[path];
    if (hold && hold.armed && !hold.used) {
      hold.used = true;
      return new Promise((resolve, reject) => {
        hold.held = { resolve, reject };
      });
    }
    return answer(path);
  },
  post: async (path: string, body: Record<string, unknown>) => {
    H.api.posts.push({ path, body });
    return {};
  },
  put: async (path: string, body: Record<string, unknown>) => {
    H.api.puts.push({ path, body: JSON.parse(JSON.stringify(body)) });
    const m = /^\/chat\/threads\/(.+)$/.exec(path);
    return { id: m && m[1] !== "new" ? m[1] : "t1", title: "t" };
  },
  del: async () => ({}),
}));

vi.mock("@/lib/useChatStream", () => {
  class StreamError extends Error {
    status = 0;
    committed = false;
    offline = false;
    partial = "";
  }
  return {
    StreamError,
    useLiveText: (s: { text?: string }) => s?.text ?? "",
    useChatStream: () => ({
      streaming: false,
      text: "",
      tools: [],
      approval: null,
      run: async () => ({ reply: "" }),
      abort: () => {},
    }),
  };
});

vi.mock("@/lib/useRunStream", () => ({
  useRunStream: () => ({
    text: H.run.text,
    tools: H.run.tools,
    phase: H.run.phase,
    active: true,
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
    useEvents: () => ({
      events: React.useSyncExternalStore(subscribe, () => H.ev.list),
      connected: true,
    }),
  };
});
vi.mock("@/lib/useDictation", () => ({
  useDictation: () => ({
    supported: false,
    reason: null,
    engine: null,
    listening: false,
    processing: false,
    transcript: "",
    interim: "",
    error: null,
    start: () => {},
    stop: () => {},
    reset: () => {},
  }),
}));
vi.mock("@/lib/useTTS", () => ({
  useTTS: () => ({
    supported: false,
    enabled: false,
    speaking: false,
    enable: () => {},
    disable: () => {},
    toggle: () => {},
    speak: () => {},
    resetStream: () => {},
    speakMore: () => {},
    cancel: () => {},
  }),
}));
vi.mock("@/lib/useProviderHealth", () => ({
  useProviderHealth: () => ({
    byProvider: {},
    defaultProvider: "",
    loading: false,
    stale: false,
    refresh: () => {},
  }),
}));
vi.mock("react-markdown", () => ({
  default: ({ children }: { children?: string }) => <div>{children}</div>,
}));
vi.mock("remark-gfm", () => ({ default: () => {} }));

import ChatPage from "@/app/chat/page";
import { AgentRunLive, AgentRunSettled } from "@/components/chat/AgentRunRow";
import { PlanChecklist, readSessionPlan, useLivePlan } from "@/components/chat/PlanChecklist";
import { ApprovalCard } from "@/components/chat/ApprovalCard";
import { stepLabel } from "@/components/chat/stepLabel";
import { makeAgentRunStamp, readAgentRun, settleHandOff, type HandOffLike } from "@/lib/agentRun";
import { approvalPreview, PLAN_STEPS_MAX } from "@/lib/approvalPreview";
import {
  foldPlanEvents,
  isPlanProgressLine,
  MAX_GOAL_CHARS,
  MAX_PLAN_STEPS,
  phaseSaysPlan,
  planCountWords,
  planEventsFor,
  readPlanResponse,
  readSavedPlan,
  settlePlan,
  STATE_WORDS,
  toSavedPlan,
  type PlanEventLike,
  type PlanView,
} from "@/lib/planChecklist";
import type { IJEvent } from "@/lib/types";

type Msg = Record<string, unknown>;

const GOALS = ["read the ledger", "draft the summary", "check the totals"];

/* ------------------------------------------------------------ the events */

/** One daemon event, as /events frames it. */
function ev(type: string, payload: Record<string, unknown>, session = "s1"): IJEvent {
  H.ev.n += 1;
  return { id: `e${H.ev.n}`, type, session_id: session, ts: "", payload };
}
const created = (goals = GOALS, session = "s1") => ev("plan.created", { run_id: "r1", steps: goals }, session);
const started = (index: number, goal = GOALS[index], session = "s1") =>
  ev("plan.step_started", { run_id: "r1", index, goal }, session);
const completed = (index: number, extra: Record<string, unknown> = {}, session = "s1") =>
  ev("plan.step_completed", { run_id: "r1", index, ok: true, ...extra }, session);

function notify() {
  for (const cb of [...H.ev.listeners]) cb();
}
/** Replace the window with these events (given OLDEST first). */
function setEvents(oldestFirst: IJEvent[]) {
  H.ev.list = [...oldestFirst].reverse() as unknown as Record<string, unknown>[];
  notify();
}
/** Land events on the socket, oldest first (each becomes the newest). */
async function land(...oldestFirst: IJEvent[]) {
  await act(async () => {
    H.ev.list = [...[...oldestFirst].reverse(), ...H.ev.list] as unknown as Record<string, unknown>[];
    notify();
  });
}

/* ------------------------------------------------------------- the page */

const SENTENCE = "Handing this to the researcher: needs the agent.";

/** A naive-UTC ISO stamp (the daemon's own shape) `ago` ms in the past. */
const isoAgo = (ago: number, from = Date.now()) => new Date(from - ago).toISOString().replace("Z", "");

const RESULT = {
  found: true,
  session_id: "s1",
  status: "completed",
  task: "draft the summary",
  summary: "",
  steps: 6,
  tools_used: [{ tool: "read_file", count: 2 }],
  tools_failed: [],
  files_created: [],
  files_changed: [],
  errors: [],
  revertable: 0,
  duration_s: 125,
};

function resetApi() {
  H.api.gets.length = 0;
  H.api.posts.length = 0;
  H.api.puts.length = 0;
  for (const k of Object.keys(H.api.getResponses)) delete H.api.getResponses[k];
  for (const k of Object.keys(H.api.getCounts)) delete H.api.getCounts[k];
  for (const k of Object.keys(H.api.holds)) delete H.api.holds[k];
  H.api.getResponses["/models"] = { models: [] };
  H.api.getResponses["/chat/personas"] = { personas: [] };
  H.api.getResponses["/chat/threads"] = { threads: [] };
  H.api.getResponses["/settings"] = { settings: {} };
  H.api.getResponses["/projects"] = { projects: [] };
  H.api.getResponses["/agents/mentionable"] = { agents: [] };
  H.api.getResponses["/skills"] = { skills: [] };
  H.api.getResponses["/workflows"] = { workflows: [] };
  H.api.getResponses["/tools"] = { tools: [] };
  H.api.getResponses["/undo?session_id=chat"] = { actions: [] };
  H.run.text = "";
  H.run.tools = [];
  H.run.phase = null;
  H.ev.list = [];
  H.ev.n = 0;
}

/** Hold the next read of `p` until the test releases (or fails) it. */
function holdNext(p: string) {
  H.api.holds[p] = { armed: true, used: false };
  return {
    used: () => H.api.holds[p]?.used === true,
    release: (v: unknown) => H.api.holds[p]!.held!.resolve(v),
    fail: (e: unknown) => H.api.holds[p]!.held!.reject(e),
  };
}

/** A saved thread the page opens from ?thread=t7. */
function storeThread(messages: Msg[] | ((n: number) => unknown)) {
  H.api.getResponses["/chat/threads/t7"] =
    typeof messages === "function"
      ? messages
      : { id: "t7", title: "saved", updated_at: "S1", messages };
  window.history.replaceState({}, "", "/chat?thread=t7");
}

const WAITING: Msg[] = [
  { role: "user", content: "summarise the ledger" },
  {
    role: "assistant",
    content: "",
    escalated: "needs the agent",
    escalatedTo: "the researcher",
    awaitingSession: "s1",
  },
];

const SESSION = "/sessions/s1?transcript=0";
const PLAN = "/sessions/s1/plan";

/** The session row: running until `done()` says the run is over. */
function sessionRow(status: () => string) {
  const end = Date.now();
  H.api.getResponses[SESSION] = () => {
    const s = status();
    return {
      session: {
        id: "s1",
        status: s,
        summary: s === "running" ? "" : "The summary is ready.",
        created_at: isoAgo(125_000, end),
        ...(s === "running" ? {} : { finished_at: isoAgo(0, end) }),
      },
    };
  };
}

const putsTo = (p: string) => H.api.puts.filter((x) => x.path === p);
const lastSavedTo = (p: string) => putsTo(p).at(-1)?.body.messages as Msg[] | undefined;

/** The checklist's rows: [state, goal] in order. */
function checklist(root: ParentNode = document): [string, string][] {
  const list = root.querySelector('[data-testid="plan-checklist"]');
  if (!list) return [];
  return Array.from(list.querySelectorAll('[data-testid="plan-step"]')).map((li) => [
    li.getAttribute("data-state") ?? "",
    li.querySelector('[data-testid="plan-step-goal"]')?.textContent ?? "",
  ]);
}
const states = (root?: ParentNode) => checklist(root).map(([s]) => s);

/** A row's class tokens. */
const classes = (el: Element | null | undefined) =>
  (el?.getAttribute("class") ?? "").split(/\s+/).filter(Boolean);

/** Box-drawing classes: a border, a fill, rounded corners or a shadow. */
function boxes(root: Element): string[] {
  const out: string[] = [];
  for (const el of [root, ...Array.from(root.querySelectorAll("*"))]) {
    if (el.getAttribute("aria-hidden") === "true" && el.tagName === "SPAN") continue; // the 3 px dot
    for (const c of classes(el)) {
      if (
        /^(border|border-[0-9])$/.test(c) ||
        /^border-[xytlr]-?[0-9]*$/.test(c) ||
        /^bg-/.test(c) ||
        /^rounded/.test(c) ||
        /^shadow/.test(c)
      )
        out.push(c);
    }
  }
  return out;
}

beforeEach(() => {
  resetApi();
  window.history.replaceState({}, "", "/chat");
  window.localStorage.clear();
  Element.prototype.scrollIntoView = vi.fn();
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.clearAllMocks();
});

/* ================================================================ the fold */

describe("the fold: the run's plan.* events into one checklist (the real event shapes)", () => {
  it("says every state: waiting, running, done, failed, not attempted", () => {
    const g = ["a", "b", "c", "d", "e"];
    const events = [
      created(g),
      started(0, "a"),
      completed(0),
      started(1, "b"),
      completed(1, { ok: false }),
      started(2, "c"),
      completed(2, { attempts: 2 }), // a retried step is still done
      completed(3, { ok: false, attempted: false }), // the budget-spent branch
      started(4, "e"),
    ];
    const view = foldPlanEvents(null, events)!;
    expect(view.total).toBe(5);
    expect(view.steps.map((s) => [s.goal, s.state])).toEqual([
      ["a", "done"],
      ["b", "failed"],
      ["c", "done"],
      ["d", "skipped"],
      ["e", "running"],
    ]);
    expect(planCountWords(view)).toBe("2 of 5 done, 1 failed");
    expect(planCountWords(foldPlanEvents(null, [created(g), started(0, "a")])!)).toBe("0 of 5 done");
  });

  it("re-folding the window on what was already known keeps a plan.created that LEFT the window", () => {
    const all = [created(), started(0), completed(0), started(1)];
    const first = foldPlanEvents(null, all)!;
    // Same window again: same checklist (each step's newest event wins).
    expect(foldPlanEvents(first, all)).toEqual(first);
    // The oldest events scroll out of the bounded window; a new one lands.
    const later = [started(1), completed(1), started(2)];
    const kept = foldPlanEvents(first, later)!;
    expect(kept.total).toBe(3);
    expect(kept.steps.map((s) => [s.goal, s.state])).toEqual([
      ["read the ledger", "done"],
      ["draft the summary", "done"],
      ["check the totals", "running"],
    ]);
    // Anti-vacuity: without what was known, that window alone cannot say it.
    expect(foldPlanEvents(null, later)!.steps.map((s) => s.goal)).toEqual(["draft the summary", "check the totals"]);
    expect(foldPlanEvents(null, later)!.total).toBeNull();
  });

  it("a resumed run re-sends its plan (decompose does after a daemon restart): the steps it finished keep their ticks", () => {
    const before = foldPlanEvents(null, [created(), started(0), completed(0), started(1), completed(1)])!;
    const resumed = foldPlanEvents(before, [created(), started(2)])!;
    expect(resumed.steps.map((s) => s.state)).toEqual(["done", "done", "running"]);
    // A DIFFERENT plan is a new checklist.
    const other = foldPlanEvents(before, [created(["x", "y"])])!;
    expect(other.steps.map((s) => [s.goal, s.state])).toEqual([
      ["x", "waiting"],
      ["y", "waiting"],
    ]);
  });

  it("junk never blanks or throws: a malformed plan, a bad index, an index past the plan", () => {
    const view = foldPlanEvents(null, [created(), started(0)])!;
    const junk: PlanEventLike[] = [
      { type: "plan.created", session_id: "s1", payload: { steps: "not a list" } },
      { type: "plan.created", session_id: "s1", payload: { steps: [] } },
      { type: "plan.step_started", session_id: "s1", payload: { index: -1, goal: "x" } },
      { type: "plan.step_started", session_id: "s1", payload: { index: 1.5 } },
      { type: "plan.step_completed", session_id: "s1", payload: { index: "2", ok: true } },
      { type: "plan.step_started", session_id: "s1", payload: null },
    ];
    expect(foldPlanEvents(view, junk)).toEqual(view);
    expect(foldPlanEvents(null, junk)).toBeNull();
    // An index past the plan this checklist knows belongs to no step of it.
    const past: PlanEventLike = { type: "plan.step_completed", session_id: "s1", payload: { index: 7, ok: true } };
    expect(foldPlanEvents(view, [past])).toEqual(view);
    // A model-written goal is one line, cut at 200 characters.
    const long = foldPlanEvents(null, [created([`line one\n\nline   two ${"x".repeat(400)}`])])!;
    expect(long.steps[0].goal.startsWith("line one line two x")).toBe(true);
    expect(Array.from(long.steps[0].goal).length).toBe(MAX_GOAL_CHARS);
    expect(long.steps[0].goal.endsWith("…")).toBe(true);
  });

  it("reads only this session's events after the turn boundary (newest-first window in, oldest-first out)", () => {
    const old = created(["old plan"]);
    const boundary = ev("agent.started", {});
    const mine = [created(), started(0)];
    const theirs = started(1, "someone else's", "s2");
    const win = [old, boundary, ...mine, theirs].reverse();
    const picked = planEventsFor(win, "s1", boundary.id);
    expect(picked.map((e) => e.id)).toEqual(mine.map((e) => e.id));
    expect(planEventsFor(win, null)).toEqual([]);
  });

  it("the plan's own progress lines are recognised from stepLabel's REAL words (so the row never says them twice)", () => {
    const plan = [
      created(),
      started(0),
      completed(0),
      completed(1, { ok: false }),
      completed(2, { ok: false, attempted: false }),
      ev("plan.step_started", { run_id: "unknown-run", index: 3, goal: "joined late" }),
      ev("plan.step_completed", { run_id: "unknown-run", index: 3, ok: true }),
    ];
    const said = plan.map((e) => stepLabel(e)).filter((l): l is string => !!l);
    expect(said).toEqual([
      "Planned 3 steps",
      "Step 1 of 3: read the ledger",
      "Step 1 of 3 done",
      "Step 2 of 3 failed",
      "Step 3 of 3 not attempted (step budget spent)",
      "Step 4: joined late",
      "Step 4 done",
    ]);
    for (const l of said) expect(isPlanProgressLine(l), l).toBe(true);
    // Every other line stays: a tool, a state, a failure, an adaptation.
    const others = [
      ev("tool.executed", { tool: "read_file" }),
      ev("agent.state_changed", { to: "reviewing" }),
      ev("provider.failed", { provider: "ollama", error: "Step 2 timed out" }),
      ev("envelope.adapted", { model: "qwen", adaptations: ["decomposed"] }),
      ev("agent.started", {}),
    ]
      .map((e) => stepLabel(e))
      .filter((l): l is string => !!l);
    expect(others.length).toBe(5);
    for (const l of others) expect(isPlanProgressLine(l), l).toBe(false);
  });

  it("knows the daemon's phase words for a plan", () => {
    expect(phaseSaysPlan("step 2 of 4: draft the summary")).toBe(true);
    expect(phaseSaysPlan("4 steps planned")).toBe(true);
    expect(phaseSaysPlan("1 step planned")).toBe(true);
    expect(phaseSaysPlan("working out the steps")).toBe(false);
    expect(phaseSaysPlan(null)).toBe(false);
  });
});

describe("the daemon's copy and the saved form", () => {
  it("reads GET /sessions/{id}/plan defensively: an unknown state is dropped, never guessed", () => {
    expect(
      readPlanResponse({
        steps: [
          { goal: "a", state: "done" },
          { goal: "b", state: "running" },
          { goal: "c", state: "finished-ish" },
          { goal: "d", state: "waiting" },
        ],
      }),
    ).toEqual({
      total: 4,
      steps: [
        { index: 0, goal: "a", state: "done" },
        { index: 1, goal: "b", state: "running" },
        { index: 3, goal: "d", state: "waiting" },
      ],
    });
    for (const junk of [null, "x", [], {}, { steps: [] }, { steps: "a" }, { steps: [{ goal: "a" }] }]) {
      expect(readPlanResponse(junk)).toBeNull();
    }
  });

  it("saves at most 20 steps and 200 characters a goal, and reads a saved plan as untrusted", () => {
    const big: PlanView = {
      total: 25,
      steps: Array.from({ length: 25 }, (_, index) => ({ index, goal: `g${index} ${"y".repeat(300)}`, state: "done" })),
    };
    const saved = toSavedPlan(big)!;
    expect(saved.steps).toHaveLength(MAX_PLAN_STEPS);
    expect(saved.total).toBe(25);
    for (const s of saved.steps) expect(Array.from(s.goal).length).toBeLessThanOrEqual(MAX_GOAL_CHARS);
    expect(toSavedPlan(null)).toBeNull();
    // Read back: junk steps drop, a lying total is no total.
    expect(
      readSavedPlan({ steps: [{ goal: "a", state: "done" }, "x", { goal: "b", state: "nope" }, { state: "failed" }], total: 1 }),
    ).toEqual({
      total: null,
      steps: [
        { index: 0, goal: "a", state: "done" },
        { index: 1, goal: "", state: "failed" },
      ],
    });
    expect(readSavedPlan({ steps: [] })).toBeNull();
    expect(readSavedPlan([{ goal: "a", state: "done" }])).toBeNull();
  });

  it("v1.350.0 integrate: a step known only by its place is saved by its place, so a reopen never renumbers it", () => {
    // An older daemon (no GET /plan) and a plan.created that was never seen:
    // only step 3 is known, by its index.
    const gap = foldPlanEvents(null, [completed(2)])!;
    expect(gap.steps).toEqual([{ index: 2, goal: "", state: "done" }]);
    const saved = toSavedPlan(gap)!;
    expect(saved.steps).toEqual([{ goal: "Step 3", state: "done" }]);
    const back = readSavedPlan(JSON.parse(JSON.stringify(saved)))!;
    render(<PlanChecklist plan={back} />);
    expect(screen.getByTestId("plan-step-goal").textContent).toBe("Step 3");
  });

  it("settles a plan when the run ends: nothing is left waiting or spinning", () => {
    const live = foldPlanEvents(null, [created(), started(0), completed(0), started(1)])!;
    expect(settlePlan(live, "cancelled")!.steps.map((s) => s.state)).toEqual(["done", "skipped", "skipped"]);
    expect(settlePlan(live, "failed")!.steps.map((s) => s.state)).toEqual(["done", "failed", "skipped"]);
    expect(settlePlan(null, "completed")).toBeNull();
  });

  it("the stamp carries the plan only when there is one; an old stamp reads exactly as before", () => {
    type Line = HandOffLike & { role: string; content: string };
    const hand: Line = { role: "assistant", content: "", escalated: "needs the agent", awaitingSession: "s1" };
    const plan = settlePlan(foldPlanEvents(null, [created(), started(0), completed(0)]), "completed");
    const out = settleHandOff([hand], "s1", { created_at: "2026-10-17T10:00:00", finished_at: "2026-10-17T10:01:00" }, 4, plan);
    expect(readAgentRun(out[0])!.plan).toEqual({
      total: 3,
      steps: [
        { goal: "read the ledger", state: "done" },
        { goal: "draft the summary", state: "skipped" },
        { goal: "check the totals", state: "skipped" },
      ],
    });
    // No plan: the stamp keeps its v1.349.0 shape exactly (no `plan` key).
    expect(Object.keys(makeAgentRunStamp({ session: "s1" }))).toEqual(["session", "startedAt", "endedAt", "steps"]);
    const old = { session: "s1", startedAt: 1_000, endedAt: 5_000, steps: 3 };
    expect(readAgentRun({ agentRun: old })).toEqual(old);
    expect("plan" in readAgentRun({ agentRun: old })!).toBe(false);
    // A broken plan on a good stamp: the stamp still reads, without it.
    expect(readAgentRun({ agentRun: { ...old, plan: { steps: "x" } } })).toEqual(old);
  });
});

/* =========================================================== the checklist */

const VIEW: PlanView = {
  total: 5,
  steps: [
    { index: 0, goal: "read the ledger", state: "done" },
    { index: 1, goal: "draft the summary", state: "running" },
    { index: 2, goal: "check the totals", state: "waiting" },
    { index: 3, goal: "send it", state: "failed" },
    { index: 4, goal: "file it", state: "skipped" },
  ],
};

describe("the checklist draws each state calmly, and says it in words", () => {
  it("one plain list: a word for every state, the running step current, done struck through, failed in the danger tone", () => {
    render(<PlanChecklist plan={VIEW} />);
    const list = screen.getByRole("list", { name: "The plan: 1 of 5 done, 1 failed" });
    const rows = within(list).getAllByRole("listitem");
    expect(rows.map((r) => r.textContent)).toEqual([
      "Done: read the ledger",
      "Working on: draft the summary",
      "Not started: check the totals",
      "Failed: send it",
      "Not attempted: file it",
    ]);
    for (const [i, s] of VIEW.steps.entries()) expect(rows[i].textContent!.startsWith(`${STATE_WORDS[s.state]}: `)).toBe(true);
    // Only the running step is the current one.
    expect(rows.map((r) => r.getAttribute("aria-current"))).toEqual([null, "step", null, null, null]);
    const goal = (i: number) => rows[i].querySelector('[data-testid="plan-step-goal"]')!;
    expect(classes(goal(0))).toContain("line-through");
    for (const i of [1, 2, 3, 4]) expect(classes(goal(i))).not.toContain("line-through");
    // Each row has ONE icon, hidden from a reader (the words say it).
    for (const r of rows) {
      const icons = r.querySelectorAll("svg");
      expect(icons).toHaveLength(1);
      expect(icons[0].getAttribute("aria-hidden")).toBe("true");
    }
    expect(classes(rows[3].querySelector("svg"))).toContain("text-tone-danger");
    expect(classes(rows[1].querySelector("svg"))).toContain("text-accent-soft");
    // Calm: no box anywhere, nothing that moves.
    const root = screen.getByTestId("plan-checklist");
    expect(boxes(root)).toEqual([]);
    for (const el of [root, ...Array.from(root.querySelectorAll("*"))]) {
      expect(classes(el).filter((c) => /^animate-|^transition|ij-shimmer/.test(c)), el.outerHTML.slice(0, 80)).toEqual([]);
    }
  });

  it("a plan longer than it keeps says how many more steps there were", () => {
    render(<PlanChecklist plan={{ total: 23, steps: VIEW.steps }} />);
    expect(screen.getByTestId("plan-more").textContent).toBe("18 more steps");
  });
});

/* ============================================================= the rows */

describe("the live helper-agent row with a plan", () => {
  const live = (plan: PlanView | null) => (
    <AgentRunLive
      session="s1"
      handOff={{ escalated: "needs the agent", escalatedTo: "the researcher" }}
      since={Date.now() - 84_000}
      progress={["Using read_file…", "Step 2 of 3: draft the summary", "Step 1 of 3 done", "Using list_files…", "Step 1 of 3: read the ledger", "Planned 3 steps"]}
      tools={[]}
      phaseTitle="Working…"
      phaseDetail="step 2 of 3: draft the summary"
      plan={plan}
    />
  );
  const PLAN3 = foldPlanEvents(null, [created(), started(0), completed(0), started(1)])!;

  it("open: the checklist sits between why and the step rows, and the plan's own lines are not said twice", () => {
    render(live(PLAN3));
    const body = screen.getByTestId("agent-run-body");
    const order = Array.from(body.children).map((c) => c.getAttribute("data-testid"));
    expect(order).toEqual(["agent-run-why", "plan-checklist", "live-work"]);
    expect(checklist(body)).toEqual([
      ["done", "read the ledger"],
      ["running", "draft the summary"],
      ["waiting", "check the totals"],
    ]);
    const rows = within(screen.getByTestId("live-work")).getAllByTestId("work-row");
    const words = rows.map((r) => r.textContent ?? "");
    for (const w of words) expect(w, w).not.toMatch(/Planned \d|Step \d/);
    // The tool lines stay (the newest is the running row's own), and the
    // running row no longer repeats "step 2 of 3: …" the checklist shows.
    expect(words).toEqual(["Using list_files", "Working…"]);
    // The header still names the step.
    expect(screen.getByTestId("agent-run-words").textContent).toBe(
      "The researcher is working · Step 2 of 3: draft the summary",
    );
    // Open, the count is the checklist's own business.
    expect(screen.queryByTestId("agent-run-plan-count")).toBeNull();
    expect(boxes(screen.getByTestId("agent-run-row"))).toEqual([]);
  });

  it("closed: the row keeps 'Step 2 of 3: …' and gains '1 of 3 done'", () => {
    render(live(PLAN3));
    fireEvent.click(screen.getByTestId("agent-run-toggle"));
    expect(screen.queryByTestId("plan-checklist")).toBeNull();
    expect(screen.getByTestId("agent-run-words").textContent).toBe(
      "The researcher is working · Step 2 of 3: draft the summary",
    );
    const count = screen.getByTestId("agent-run-plan-count");
    expect(count.textContent).toBe("1 of 3 done");
    // In the header, which is not a live region (the clock's rule).
    expect(screen.getByTestId("agent-run-head").contains(count)).toBe(true);
  });

  it("without a plan the row is exactly wave B's: every progress line, the running row's detail", () => {
    render(live(null));
    expect(screen.queryByTestId("plan-checklist")).toBeNull();
    const words = within(screen.getByTestId("live-work"))
      .getAllByTestId("work-row")
      .map((r) => r.textContent ?? "");
    expect(words).toEqual([
      "Using list_files",
      "Step 1 of 3 done",
      "Step 2 of 3: draft the summary",
      expect.stringContaining("step 2 of 3: draft the summary"),
    ]);
    fireEvent.click(screen.getByTestId("agent-run-toggle"));
    expect(screen.queryByTestId("agent-run-plan-count")).toBeNull();
  });
});

describe("the settled row opens to the plan as it ended", () => {
  it("from the stamp alone (nothing fetched); a stamp without one opens to why only", () => {
    const run = {
      session: "s1",
      startedAt: 1_000,
      endedAt: 126_000,
      steps: 6,
      plan: { total: 3, steps: [{ goal: "read the ledger", state: "done" as const }, { goal: "draft the summary", state: "failed" as const }, { goal: "check the totals", state: "skipped" as const }] },
    };
    render(<AgentRunSettled name="The researcher" run={run} />);
    // The words are wave B's.
    expect(screen.getByTestId("agent-run-words").textContent).toBe("The researcher worked for 2 min 5 s · 6 steps");
    expect(screen.queryByTestId("plan-checklist")).toBeNull();
    fireEvent.click(screen.getByTestId("agent-run-toggle")); // a plan alone makes it a disclosure
    expect(checklist()).toEqual([
      ["done", "read the ledger"],
      ["failed", "draft the summary"],
      ["skipped", "check the totals"],
    ]);
    expect(H.api.gets).toEqual([]);
    cleanup();
    render(<AgentRunSettled name="The researcher" run={{ session: "s1", startedAt: 1_000, endedAt: 126_000, steps: 6 }} />);
    // No why and no plan: not a disclosure at all (wave B's shape).
    expect(screen.queryByTestId("agent-run-toggle")).toBeNull();
  });
});

/* ======================================================= the live hook */

function Probe(props: Parameters<typeof useLivePlan>[0]) {
  const plan = useLivePlan(props);
  return <div data-testid="probe">{plan ? JSON.stringify(plan.steps.map((s) => [s.goal, s.state])) : "none"}</div>;
}

describe("useLivePlan reads the daemon's copy only when the events cannot tell", () => {
  it("a run that never plans costs no read; a page joining mid-run reads once; a 404 leaves the events", async () => {
    const { rerender } = render(<Probe session="s1" events={[ev("tool.executed", { tool: "x" })]} />);
    await act(async () => {});
    expect(H.api.gets).toEqual([]); // no plan, no hint, not eager: nothing to read
    expect(screen.getByTestId("probe").textContent).toBe("none");
    // Joining a run under way (eager): one read, and it answers 404.
    rerender(<Probe session="s1" events={[]} eager />);
    await waitFor(() => expect(H.api.gets).toEqual([PLAN]));
    // A step event without its plan (the plan was made before we watched):
    // ONE more read, still 404 (an older daemon), so the events speak alone.
    const lone = started(1);
    rerender(<Probe session="s1" events={[lone]} eager />);
    await waitFor(() => expect(H.api.gets).toEqual([PLAN, PLAN]));
    expect(screen.getByTestId("probe").textContent).toBe(JSON.stringify([["draft the summary", "running"]]));
    // More events: no third read for this turn.
    rerender(<Probe session="s1" events={[completed(1), lone]} eager hint="step 2 of 3: x" />);
    await act(async () => {});
    expect(H.api.gets).toEqual([PLAN, PLAN]);
    expect(screen.getByTestId("probe").textContent).toBe(JSON.stringify([["draft the summary", "done"]]));
  });

  it("a phase that speaks of a plan reads it; the daemon's answer is the base the events then tick", async () => {
    H.api.getResponses[PLAN] = {
      steps: [
        { goal: "read the ledger", state: "done" },
        { goal: "draft the summary", state: "running" },
        { goal: "check the totals", state: "waiting" },
      ],
    };
    const { rerender } = render(<Probe session="s1" events={[]} hint="step 2 of 3: draft the summary" />);
    await waitFor(() =>
      expect(screen.getByTestId("probe").textContent).toBe(
        JSON.stringify([
          ["read the ledger", "done"],
          ["draft the summary", "running"],
          ["check the totals", "waiting"],
        ]),
      ),
    );
    rerender(<Probe session="s1" events={[started(2), completed(1)]} hint="step 3 of 3: check the totals" />);
    await waitFor(() =>
      expect(screen.getByTestId("probe").textContent).toBe(
        JSON.stringify([
          ["read the ledger", "done"],
          ["draft the summary", "done"],
          ["check the totals", "running"],
        ]),
      ),
    );
    expect(H.api.gets).toEqual([PLAN]);
  });

  // v1.350.0 lane C1 review: the read budget is a guard of its own, not a
  // side effect of the effect's deps. A decomposed run's phase words swing
  // between "step N of M: …" (a plan) and "checking step N" (not) on every
  // step; with an older daemon (404) and no plan events in the window, each
  // swing back must not buy another read.
  it("a phase whose words keep swinging never buys more reads than the turn allows", async () => {
    const { rerender } = render(<Probe session="s1" events={[]} hint="step 1 of 3: read the ledger" />);
    await waitFor(() => expect(H.api.gets).toEqual([PLAN]));
    for (const hint of ["checking step 1", "step 2 of 3: draft", "checking step 2", "step 3 of 3: check"]) {
      rerender(<Probe session="s1" events={[]} hint={hint} />);
      await act(async () => {});
    }
    rerender(<Probe session="s1" events={[]} eager hint="step 3 of 3: check" />);
    await act(async () => {});
    expect(H.api.gets).toEqual([PLAN]);
    expect(screen.getByTestId("probe").textContent).toBe("none");
  });

  it("two readers of the same session share one read; a failed read is no plan, never an error", async () => {
    const h = holdNext(PLAN);
    const a = readSessionPlan("s1");
    const b = readSessionPlan("s1");
    expect(a).toBe(b);
    await waitFor(() => expect(h.used()).toBe(true));
    h.release({ steps: [{ goal: "a", state: "done" }] });
    expect(await a).toEqual({ total: 1, steps: [{ index: 0, goal: "a", state: "done" }] });
    expect(H.api.gets).toEqual([PLAN]);
    // Once it has answered, the next reader reads again (nothing is cached).
    H.api.getResponses[PLAN] = new H.FakeApiError("the daemon fell over", 500);
    await expect(readSessionPlan("s1")).resolves.toBeNull();
    expect(H.api.gets).toEqual([PLAN, PLAN]);
  });
});

/* ============================================================== the page */

describe("on the chat page: the live checklist", () => {
  it("ticks off as the run's events land; its own progress lines are not said twice; closed, '1 of 3 done'", async () => {
    storeThread(WAITING);
    sessionRow(() => "running");
    H.run.phase = { phase: "running", detail: "step 2 of 3: draft the summary" };
    setEvents([created(), started(0), completed(0), started(1)]);
    render(<ChatPage />);
    const row = await screen.findByTestId("agent-run-row");
    await waitFor(() => expect(states(row)).toEqual(["done", "running", "waiting"]));
    expect(checklist(row).map(([, g]) => g)).toEqual(GOALS);
    for (const r of within(row).getAllByTestId("work-row")) expect(r.textContent).not.toMatch(/Planned \d|Step \d/);
    // The plan came with the events: the daemon was not asked for it.
    expect(H.api.gets).not.toContain(PLAN);
    await land(completed(1), started(2));
    await waitFor(() => expect(states(row)).toEqual(["done", "done", "running"]));
    await land(completed(2, { ok: false }));
    await waitFor(() => expect(states(row)).toEqual(["done", "done", "failed"]));
    fireEvent.click(screen.getByTestId("agent-run-toggle"));
    expect(screen.getByTestId("agent-run-plan-count").textContent).toBe("2 of 3 done, 1 failed");
    expect(screen.getByTestId("agent-run-words").textContent).toBe(
      "The researcher is working · Step 2 of 3: draft the summary",
    );
  });

  it("a plan.created that leaves the page's bounded event window is still known", async () => {
    storeThread(WAITING);
    sessionRow(() => "running");
    setEvents([created(), started(0), completed(0), started(1)]);
    render(<ChatPage />);
    const row = await screen.findByTestId("agent-run-row");
    await waitFor(() => expect(states(row)).toEqual(["done", "running", "waiting"]));
    // The window moves on: only the newest events remain (useEvents keeps 150).
    await act(async () => {
      setEvents([completed(1), started(2)]);
    });
    await waitFor(() => expect(states(row)).toEqual(["done", "done", "running"]));
    expect(checklist(row).map(([, g]) => g)).toEqual(GOALS);
  });

  it("a reload mid-run (the plan was made before this page): the daemon's copy, then the events tick it", async () => {
    storeThread(WAITING);
    sessionRow(() => "running");
    H.api.getResponses[PLAN] = {
      steps: [
        { goal: "read the ledger", state: "done" },
        { goal: "draft the summary", state: "running" },
        { goal: "check the totals", state: "waiting" },
      ],
    };
    render(<ChatPage />);
    const row = await screen.findByTestId("agent-run-row");
    await waitFor(() => expect(states(row)).toEqual(["done", "running", "waiting"]));
    await land(completed(1), started(2));
    await waitFor(() => expect(states(row)).toEqual(["done", "done", "running"]));
  });

  it("an older daemon (GET /plan 404s) and no plan events: the row is exactly wave B's, and no error shows", async () => {
    storeThread(WAITING);
    sessionRow(() => "running");
    render(<ChatPage />);
    await screen.findByTestId("agent-run-row");
    await waitFor(() => expect(H.api.gets).toContain(PLAN));
    await act(async () => {});
    expect(screen.queryByTestId("plan-checklist")).toBeNull();
    expect(screen.queryByText(/unmocked GET/)).toBeNull();
  });
});

describe("finalize saves the plan as it ENDED on the hand-off", () => {
  it("the live path: the ticked checklist is stamped, saved, and the settled row opens to it", async () => {
    let done = false;
    storeThread(WAITING);
    sessionRow(() => (done ? "completed" : "running"));
    H.api.getResponses["/sessions/s1/result"] = RESULT;
    setEvents([created(), started(0), completed(0), started(1), completed(1), started(2)]);
    render(<ChatPage />);
    const row = await screen.findByTestId("agent-run-row");
    await waitFor(() => expect(states(row)).toEqual(["done", "done", "running"]));
    done = true;
    await land(completed(2), ev("agent.completed", {}));
    expect(await screen.findByText("The summary is ready.", {}, { timeout: 6000 })).toBeTruthy();
    await waitFor(() => expect(readAgentRun(lastSavedTo("/chat/threads/t7")?.[1])?.plan).toBeTruthy());
    expect(readAgentRun(lastSavedTo("/chat/threads/t7")![1])!.plan).toEqual({
      total: 3,
      steps: GOALS.map((goal) => ({ goal, state: "done" })),
    });
    // Every step ended: the daemon was not asked.
    expect(H.api.gets).not.toContain(PLAN);
    // The settled row opens to it.
    await waitFor(() => expect(screen.getByTestId("agent-run-row").getAttribute("data-state")).toBe("settled"));
    const settled = screen.getByTestId("agent-run-row");
    fireEvent.click(within(settled).getByTestId("agent-run-toggle"));
    expect(states(settled)).toEqual(["done", "done", "done"]);
  }, 15_000);

  it("the poll saw the run done before the last step's event: the daemon's copy says how it ended", async () => {
    let done = false;
    storeThread(WAITING);
    sessionRow(() => (done ? "completed" : "running"));
    H.api.getResponses["/sessions/s1/result"] = RESULT;
    H.api.getResponses[PLAN] = { steps: GOALS.map((goal) => ({ goal, state: "done" })) };
    setEvents([created(), started(0), completed(0), started(1), completed(1), started(2)]);
    render(<ChatPage />);
    const row = await screen.findByTestId("agent-run-row");
    await waitFor(() => expect(states(row)).toEqual(["done", "done", "running"]));
    done = true;
    await land(ev("agent.completed", {})); // step 3's completion has not landed
    await waitFor(() => expect(readAgentRun(lastSavedTo("/chat/threads/t7")?.[1])?.plan).toBeTruthy(), { timeout: 6000 });
    expect(readAgentRun(lastSavedTo("/chat/threads/t7")![1])!.plan!.steps.map((s) => s.state)).toEqual([
      "done",
      "done",
      "done",
    ]);
    expect(H.api.gets).toContain(PLAN);
  }, 15_000);

  it("...and with no daemon copy (404), the open step is saved as not finished, never as running", async () => {
    let done = false;
    storeThread(WAITING);
    sessionRow(() => (done ? "cancelled" : "running"));
    H.api.getResponses["/sessions/s1/result"] = RESULT;
    setEvents([created(), started(0), completed(0), started(1)]);
    render(<ChatPage />);
    const row = await screen.findByTestId("agent-run-row");
    await waitFor(() => expect(states(row)).toEqual(["done", "running", "waiting"]));
    done = true;
    await land(ev("agent.completed", {}));
    await waitFor(() => expect(readAgentRun(lastSavedTo("/chat/threads/t7")?.[1])?.plan).toBeTruthy(), { timeout: 6000 });
    expect(readAgentRun(lastSavedTo("/chat/threads/t7")![1])!.plan!.steps.map((s) => s.state)).toEqual([
      "done",
      "skipped",
      "skipped",
    ]);
  }, 15_000);

  it("a reopened chat whose run finished while it was closed: the daemon's copy is saved (the daemon-appended path)", async () => {
    const daemonLine = {
      role: "assistant",
      content: "Made the summary.",
      sessionId: "s1",
      agentType: "researcher",
      receipt: RESULT,
    };
    storeThread((n) =>
      n === 1
        ? { id: "t7", title: "saved", updated_at: "S1", messages: WAITING }
        : { id: "t7", title: "saved", updated_at: "S3", messages: [...WAITING, daemonLine] },
    );
    sessionRow(() => "completed");
    H.api.getResponses[PLAN] = {
      steps: [
        { goal: "read the ledger", state: "done" },
        { goal: "draft the summary", state: "failed" },
        { goal: "check the totals", state: "skipped" },
      ],
    };
    render(<ChatPage />);
    expect(await screen.findByText("Made the summary.")).toBeTruthy();
    await waitFor(() => expect(putsTo("/chat/threads/t7").length).toBeGreaterThan(0));
    const saved = putsTo("/chat/threads/t7")[0].body.messages as Msg[];
    expect(readAgentRun(saved[1])!.plan).toEqual({
      total: 3,
      steps: [
        { goal: "read the ledger", state: "done" },
        { goal: "draft the summary", state: "failed" },
        { goal: "check the totals", state: "skipped" },
      ],
    });
    // The daemon's line once; nothing composed by the page.
    expect((saved as Msg[]).filter((m) => m.content === "Made the summary.")).toHaveLength(1);
    await waitFor(() => expect(screen.getByTestId("agent-run-row").getAttribute("data-state")).toBe("settled"));
    const settled = screen.getByTestId("agent-run-row");
    fireEvent.click(within(settled).getByTestId("agent-run-toggle"));
    expect(states(settled)).toEqual(["done", "failed", "skipped"]);
  });

  it("the user left (New chat) while finalize's plan read was out: nothing is composed or saved", async () => {
    storeThread(WAITING);
    sessionRow(() => "completed");
    H.api.getResponses["/sessions/s1/result"] = RESULT;
    const hold = holdNext(PLAN);
    render(<ChatPage />);
    await waitFor(() => expect(hold.used()).toBe(true), { timeout: 4000 });
    await act(async () => {
      window.dispatchEvent(new Event("ij:new-chat"));
    });
    await waitFor(() => expect(screen.queryByText("summarise the ledger")).toBeNull());
    const putsBefore = H.api.puts.length;
    await act(async () => {
      hold.release({ steps: GOALS.map((goal) => ({ goal, state: "done" })) });
    });
    await act(async () => {
      await new Promise((r) => setTimeout(r, 50));
    });
    expect(screen.queryByText("The summary is ready.")).toBeNull();
    expect(H.api.puts.length).toBe(putsBefore);
    expect(H.api.gets).not.toContain("/sessions/s1/result");
    // Anti-vacuity: this read is finalize's (the session read came first).
    expect(H.api.gets.indexOf(SESSION)).toBeLessThan(H.api.gets.indexOf(PLAN));
  }, 10_000);

  it("a stamped hand-off with a plan reopens settled, read from the thread alone", async () => {
    storeThread([
      { role: "user", content: "summarise the ledger" },
      {
        role: "assistant",
        content: "",
        escalated: "needs the agent",
        escalatedTo: "the researcher",
        agentRun: {
          session: "s1",
          startedAt: 1_000,
          endedAt: 126_000,
          steps: 6,
          plan: { total: 2, steps: [{ goal: "read the ledger", state: "done" }, { goal: "draft the summary", state: "done" }] },
        },
      },
      { role: "assistant", content: "Done.", fromSession: "s1" },
    ]);
    render(<ChatPage />);
    await waitFor(() =>
      expect(screen.getByTestId("agent-run-words").textContent).toBe("The researcher worked for 2 min 5 s · 6 steps"),
    );
    fireEvent.click(screen.getByTestId("agent-run-toggle"));
    expect(screen.getByText(SENTENCE)).toBeTruthy();
    expect(checklist()).toEqual([
      ["done", "read the ledger"],
      ["done", "draft the summary"],
    ]);
    expect(H.api.gets.filter((g) => g.startsWith("/sessions/"))).toEqual([]);
  });
});

it("the page hands the row its plan and finalize its ended plan (the call sites, CRLF-normalised)", () => {
  const page = readFileSync(path.join(__dirname, "..", "app", "chat", "page.tsx"), "utf8").replace(/\r\n/g, "\n");
  expect(page).toMatch(/<AgentRunLive[\s\S]{0,1200}?plan=\{livePlan\}[\s\S]{0,200}?\/>/);
  // v1.352.0 (G19), moved ON PURPOSE: both calls also hand over the ledger
  // result they already read (how the run ended, what it did), after the plan.
  expect(page.match(/settleHandOff\([^)]*endedPlan, [\w.?]*runResult\)/g)).toHaveLength(2);
  // The page holds the plan, never a per-token value: the hook reads the
  // events and the phase the page already had.
  expect(page).toMatch(/useLivePlan\(\{[\s\S]{0,300}?events,[\s\S]{0,200}?hint: runStream\.phase\?\.detail/);
});

/* ============================================================ the plan ask */

describe("the plan ask: approve_plan's steps as a numbered list on the ordinary card", () => {
  const ask = (args: Record<string, unknown>, tool = "approve_plan") =>
    render(<ApprovalCard approval={{ id: "ap1", callId: "c1", tool, args }} />);

  it("numbers the plan's steps, each goal whole; the decision buttons are the card's own", () => {
    const long = `check the totals against ${"the bank statement ".repeat(20)}`.trim();
    ask({ steps: ["read the ledger", "draft the summary", long] });
    const preview = screen.getByTestId("approval-preview");
    expect(preview.getAttribute("data-preview")).toBe("plan");
    const list = within(preview).getByRole("list", { name: "The plan, 3 steps" });
    expect(list.tagName).toBe("OL");
    expect(classes(list)).toContain("list-decimal");
    const items = within(list).getAllByRole("listitem");
    expect(items.map((li) => li.textContent)).toEqual(["read the ledger", "draft the summary", long]);
    expect(within(preview).getByTestId("approval-plan").querySelector("p")!.textContent).toBe("plan: 3 steps");
    // v1.350.0 integrate (changed on purpose): a plan is approved or
    // declined, nothing wider, even when the card was given no `onceOnly`
    // (a listing or a session row carries no flags): Allow once and Deny, no
    // conversation answer, and not the local-only line.
    expect(screen.getByRole("button", { name: "Allow once" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Allow for this conversation" })).toBeNull();
    expect(screen.getByRole("button", { name: "Deny" })).toBeTruthy();
    expect(screen.queryByTestId("approval-local-only")).toBeNull();
    expect(screen.getByText(/No step runs until you answer\. “Allow once” lets it follow this plan; Deny ends the run before any step runs\./)).toBeTruthy();
    expect(boxes(within(preview).getByTestId("approval-plan"))).toEqual([]);
  });

  it("a long plan shows its first lines and opens to all of them; past the ceiling it says how many more", () => {
    const steps = Array.from({ length: 23 }, (_, i) => `step number ${i + 1}`);
    ask({ steps });
    const items = () => screen.getAllByTestId("approval-plan-step");
    expect(items()).toHaveLength(12);
    const toggle = screen.getByTestId("approval-plan-toggle");
    expect(toggle.textContent).toBe(`Show all ${PLAN_STEPS_MAX} steps`);
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(toggle);
    expect(items()).toHaveLength(PLAN_STEPS_MAX);
    expect(toggle.getAttribute("aria-controls")).toBe(within(screen.getByTestId("approval-plan")).getByRole("list").id);
    expect(screen.getByTestId("approval-plan-more").textContent).toBe("…and 3 more steps");
  });

  it("only approve_plan with a list of goals is a plan; anything else keeps today's preview", () => {
    expect(approvalPreview("approve_plan", { steps: [{ goal: "a" }, "b"] }).parts).toEqual([
      { type: "plan", steps: ["a", "b"], more: 0 },
    ]);
    expect(approvalPreview("approve_plan", { steps: "do it all" }).kind).toBe("args");
    expect(approvalPreview("approve_plan", { steps: [1, 2] }).kind).toBe("args");
    expect(approvalPreview("some_tool", { steps: ["a", "b"] }).kind).toBe("args");
    // A reason beside the steps stays a plain line, in its place.
    expect(approvalPreview("approve_plan", { reason: "big job", steps: ["a"] }).parts.map((p) => p.type)).toEqual([
      "arg",
      "plan",
    ]);
  });
});
