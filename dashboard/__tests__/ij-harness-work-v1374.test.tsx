/**
 * v1.374.0 (IJ harness part 2, Phase 3) — the conversation shows the work.
 * The mocks and fixtures are ij-harness-slash-at-v1373's (copied, not
 * imported: a test file is not a module other tests import).
 *
 *  - THE TASK LIST UNDER THE REPLY: while a turn works, its plan_update steps
 *    are drawn under the live reply with the chat's checklist and tick off as
 *    the step frames arrive; when the turn ends the list folds into the
 *    settled reply ("Plan · 1 of 2 done"), opened to the settled checklist;
 *  - THE METER: its numbers are the DAEMON's (the reply line's `context`,
 *    written from the turn's done frame) — never computed here; past the
 *    daemon's threshold it offers Compact;
 *  - COMPACT posts, the meter then says the earlier turns are summarized and
 *    opens the summary; a refusal is said in the daemon's words; "/compact"
 *    presses the same Compact;
 *  - the session tells the bell the user is LOOKING at it while it is shown
 *    in front and focused (lib/buildWatching).
 */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const api = vi.hoisted(() => {
  class ApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
    }
  }
  return {
    ApiError,
    gets: {} as Record<string, unknown | ((path: string) => unknown)>,
    calls: [] as { method: string; path: string; body?: unknown }[],
    postAnswers: {} as Record<string, unknown>,
  };
});

vi.mock("@/lib/api", () => {
  const answer = (method: string, path: string, body?: unknown) => {
    api.calls.push({ method, path, body });
    const hit = method === "GET" ? api.gets[path] : api.postAnswers[`${method} ${path}`];
    const value = typeof hit === "function" ? (hit as (p: string) => unknown)(path) : hit;
    if (value instanceof Error) return Promise.reject(value);
    if (value === undefined && method === "GET") return Promise.reject(new api.ApiError(`unmocked ${path}`, 404));
    return Promise.resolve(value ?? {});
  };
  return {
    ApiError: api.ApiError,
    API_BASE: "http://d",
    ijToken: () => "",
    flattenDetail: (d: unknown) => String(d),
    get: (p: string) => answer("GET", p),
    post: (p: string, b?: unknown) => answer("POST", p, b),
    put: (p: string, b?: unknown) => answer("PUT", p, b),
    patch: (p: string, b?: unknown) => answer("PATCH", p, b),
    del: (p: string) => answer("DELETE", p),
  };
});

vi.mock("@/lib/daemon", () => ({
  useDaemon: () => ({
    online: true,
    checking: false,
    health: { default_provider: "mock", default_model: "mock-1", providers: [] },
    refresh: () => {},
  }),
}));
vi.mock("@/lib/useModels", () => ({ useModels: () => ({ models: [], usable: [], loading: false }) }));
vi.mock("@/components/terminal/FilesPanel", () => ({
  FilesPanel: ({ folder }: { folder: string | null }) => <div data-testid="files-panel">{folder}</div>,
}));

import { HarnessSession } from "@/components/harness/HarnessSession";
import type { SessionTerminalApi } from "@/components/harness/HarnessPanes";
import { decodeTurnContext, latestContext, offersCompact, readingIsBeforeSummary } from "@/lib/buildContext";
import { __resetLooking, lookingAt } from "@/lib/buildWatching";
import { LiveHelperRows, SavedHelperRows } from "@/components/harness/HarnessHelpers";
import { applyHelperFrame } from "@/lib/buildHelpers";

/* ------------------------------------------------------------- fixtures */

const FOLDER = "C:\\work\\shop";
const terminal: SessionTerminalApi = {
  find: () => null,
  open: async () => null,
  render: () => <div data-testid="term-body" />,
  close: () => {},
};

function view(over: Record<string, unknown> = {}) {
  return {
    id: "s1",
    title: "Add a cart",
    updated_at: "2026-10-22T10:00:00",
    running: false,
    waiting: false,
    archived: false,
    folder: FOLDER,
    folder_name: "shop",
    is_project: false,
    project_id: null,
    mode: "act",
    plan: null,
    setup: {},
    messages: [],
    session_id: "build:s1",
    queued: [],
    turn_id: "",
    compaction: null,
    ...over,
  };
}

/** A question + reply, the reply carrying `extra` (the daemon's keys). */
function turn(tid: string, q: string, a: string, extra: Record<string, unknown> = {}, at = "2026-10-22T10:00:05") {
  return [
    { role: "user", content: q, turnId: tid, at: "2026-10-22T10:00:00" },
    { role: "assistant", content: a, turnId: tid, at, ...extra },
  ];
}

/** A daemon reading, as `routes/build.context_receipt` writes it. */
const reading = (percent: number, level: string, used = 9000, window = 32000) => ({
  window,
  used,
  tokens: Math.round((percent / 100) * window),
  percent,
  level,
  suggest_at: 70,
});

function controlledStream() {
  let ctl!: ReadableStreamDefaultController<Uint8Array>;
  const body = new ReadableStream<Uint8Array>({
    start(c) {
      ctl = c;
    },
  });
  const enc = new TextEncoder();
  return {
    response: new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } }),
    push: (event: string, data: unknown) => ctl.enqueue(enc.encode(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`)),
    end: () => ctl.close(),
  };
}

function sse(frames: [string, unknown][]): Response {
  const text = frames.map(([e, d]) => `event: ${e}\ndata: ${JSON.stringify(d)}\n\n`).join("");
  return new Response(
    new ReadableStream<Uint8Array>({
      start(c) {
        c.enqueue(new TextEncoder().encode(text));
        c.close();
      },
    }),
    { status: 200, headers: { "Content-Type": "text/event-stream" } },
  );
}

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  api.gets = {
    "/build/sessions/s1": view(),
    "/build/accounts": new api.ApiError("no such route", 404),
    "/skills": { skills: [] },
    "/build/sessions/s1/skills": { skills: [], note: "" },
  };
  api.calls = [];
  api.postAnswers = {};
  localStorage.clear();
  __resetLooking();
  fetchMock = vi.fn(async () => sse([["done", { reply: "ok" }]]));
  vi.stubGlobal("fetch", fetchMock);
  vi.spyOn(window, "confirm").mockReturnValue(true);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function renderSession() {
  return render(
    <HarnessSession threadId="s1" terminal={terminal} listRunning={false} onChanged={() => {}} onGone={() => {}} />,
  );
}

const box = () => screen.getByTestId("harness-input") as HTMLTextAreaElement;
const type = (text: string) => fireEvent.change(box(), { target: { value: text } });
const key = (k: string, extra: Record<string, unknown> = {}) => fireEvent.keyDown(box(), { key: k, ...extra });
const NET = { timeout: 5000 };
const posts = (path: string) => api.calls.filter((c) => c.method === "POST" && c.path === path);

const planArgs = (a: string, b: string) => ({
  plan: [
    { step: "read the cart code", status: a },
    { step: "fix the total", status: b },
  ],
});
const states = (root: HTMLElement) => within(root).getAllByTestId("plan-step").map((el) => el.getAttribute("data-state"));

/* ----------------------------------------------- the task list under the reply */

describe("the task list sits under the live reply and folds into the settled one", () => {
  it("ticks off on the turn's step frames, then folds into the reply as it ended", async () => {
    const s = controlledStream();
    fetchMock.mockResolvedValueOnce(s.response);
    renderSession();
    await screen.findByTestId("harness-empty");
    type("fix the cart total");
    key("Enter");
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    await act(async () => {
      s.push("tool_call", { id: "p1", name: "plan_update", status: "started", args: planArgs("in_progress", "pending") });
      s.push("token", { text: "Reading the cart code first." });
    });
    const live = await screen.findByTestId("harness-live-plan", undefined, NET);
    // UNDER the live reply: the words come first, then the list.
    const words = screen.getByTestId("harness-live-text");
    expect(words.compareDocumentPosition(live) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(states(live)).toEqual(["running", "waiting"]);
    await act(async () => {
      s.push("tool_call", { id: "p1", name: "plan_update", status: "finished", ok: true });
      s.push("tool_call", { id: "p2", name: "plan_update", status: "started", args: planArgs("completed", "in_progress") });
    });
    await waitFor(() => expect(states(screen.getByTestId("harness-live-plan"))).toEqual(["done", "running"]), NET);

    // The daemon wrote the reply line with the plan as the turn left it.
    api.gets["/build/sessions/s1"] = view({
      messages: turn("t1", "fix the cart total", "Fixed the total.", {
        plan: {
          steps: [
            { goal: "read the cart code", state: "done" },
            { goal: "fix the total", state: "running" },
          ],
          total: 2,
        },
      }),
    });
    await act(async () => {
      s.push("tool_call", { id: "p2", name: "plan_update", status: "finished", ok: true });
      s.push("done", { reply: "Fixed the total." });
      s.end();
    });
    const folded = await screen.findByTestId("harness-reply-plan", undefined, NET);
    expect(screen.queryByTestId("harness-live-plan")).toBeNull();
    // FOLDED: one line until opened, in the checklist's own words.
    expect(within(folded).getByTestId("harness-reply-plan-count").textContent).toBe("1 of 2 done");
    expect(within(folded).queryAllByTestId("plan-step")).toHaveLength(0);
    fireEvent.click(within(folded).getByRole("button", { name: /Plan/ }));
    // Settled: nothing is left working once the turn is over — and the step
    // it was on is not ticked off yet (a Build plan carries over to the next
    // turn: never "not attempted", review v1.374.0).
    expect(states(folded)).toEqual(["done", "waiting"]);
  });

  it("a reply with no plan folds nothing", async () => {
    api.gets["/build/sessions/s1"] = view({ messages: turn("t1", "hi", "Hello.") });
    renderSession();
    await screen.findByText("Hello.");
    expect(screen.queryByTestId("harness-reply-plan")).toBeNull();
  });
});

describe("a helper that plans its work shows its plan in its row", () => {
  const plan = (a: string, b: string) => ({
    steps: [
      { goal: "list the files", state: a },
      { goal: "read each one", state: b },
    ],
    total: 2,
  });

  it("live: the daemon's plan on the helper's frames ticks the row's checklist", () => {
    let hs = applyHelperFrame([], {
      id: "h1", agent: "researcher", task: "search src", state: "running", session: "sess_h1", since: 1,
      plan: plan("running", "waiting"),
    });
    const v = render(<LiveHelperRows helpers={hs} />);
    // The live row is open: its checklist, as the daemon's copy says.
    expect(states(screen.getByTestId("agent-run-body"))).toEqual(["running", "waiting"]);
    hs = applyHelperFrame(hs, { id: "h1", plan: plan("done", "running") });
    v.rerender(<LiveHelperRows helpers={hs} />);
    expect(states(screen.getByTestId("agent-run-body"))).toEqual(["done", "running"]);
    // Closed, the row still says how far it is.
    fireEvent.click(screen.getByTestId("agent-run-toggle"));
    expect(screen.getByTestId("agent-run-plan-count").textContent).toBe("1 of 2 done");
    // It ended: the row settles, the plan with it (nothing left working).
    hs = applyHelperFrame(hs, { id: "h1", state: "done", ended: 2, result: "found 3 files" });
    v.rerender(<LiveHelperRows helpers={hs} />);
    fireEvent.click(screen.getByTestId("agent-run-toggle"));
    expect(states(screen.getByTestId("agent-run-body"))).toEqual(["done", "skipped"]);
  });

  it("saved: the reply keeps it, read as untrusted (junk is no plan)", () => {
    render(
      <SavedHelperRows
        raw={[
          { id: "h1", session: "sess_h1", agent: "researcher", task: "search", state: "done", plan: plan("done", "done") },
          { id: "h2", session: "sess_h2", agent: "builder", task: "x", state: "done", plan: { steps: "junk" } },
        ]}
      />,
    );
    const [first, second] = screen.getAllByTestId("agent-run-toggle");
    fireEvent.click(first);
    expect(states(screen.getAllByTestId("agent-run-body")[0])).toEqual(["done", "done"]);
    fireEvent.click(second);
    expect(within(screen.getAllByTestId("agent-run-body")[1]).queryAllByTestId("plan-step")).toHaveLength(0);
  });
});

/* ------------------------------------------------------------------ the meter */

describe("the meter shows the daemon's own numbers", () => {
  it("reads the newest reply's reading and computes nothing (a percent the ratio would not give)", async () => {
    // used / window would be 28%; the daemon says the WHOLE conversation is
    // at 64% (what the offer keys off) — the meter says 64%.
    api.gets["/build/sessions/s1"] = view({
      messages: [...turn("t1", "a", "b", { context: reading(40, "ok") }), ...turn("t2", "c", "d", { context: reading(64, "ok") })],
    });
    renderSession();
    const meter = await screen.findByTestId("harness-context-meter", undefined, NET);
    expect(meter.getAttribute("data-state")).toBe("ok");
    expect(within(meter).getByTestId("harness-context-percent").textContent).toBe("64%");
    expect(meter.getAttribute("title")).toBe("This conversation fills about 64% of what this model can read at once.");
    expect(screen.queryByTestId("harness-compact")).toBeNull();
  });

  it("a turn's done frame carries the reading; past the daemon's threshold it offers Compact", async () => {
    renderSession();
    await screen.findByTestId("harness-empty");
    expect(screen.queryByTestId("harness-context-meter")).toBeNull(); // nothing read yet
    const ctx = reading(78, "suggest", 24000, 32000);
    fetchMock.mockResolvedValueOnce(sse([["token", { text: "Done." }], ["done", { reply: "Done.", context: ctx }]]));
    // The daemon writes the frame's reading onto the reply line it saves.
    api.gets["/build/sessions/s1"] = view({ messages: turn("t1", "do it", "Done.", { context: ctx }) });
    type("do it");
    key("Enter");
    const meter = await screen.findByTestId("harness-context-meter", undefined, NET);
    await waitFor(() => expect(meter.getAttribute("data-state")).toBe("full"), NET);
    expect(within(meter).getByTestId("harness-context-percent").textContent).toBe("78%");
    expect(await screen.findByTestId("harness-compact")).toBeTruthy();
  });
});

/* ------------------------------------------------------------------- compact */

const SUMMARY = {
  summary: "GOAL: ship the cart.\nFACTS:\nThe codename is CORAL-TELESCOPE-17.",
  through_turn: "t2",
  covers: 6,
  stripped: 1,
  stripped_claims: ["src/secret_backdoor.py"],
  provider: "anthropic",
  model: "claude-sonnet-4-5",
  at: "2026-10-22T10:05:00",
  window: 32000,
};

function fullSession() {
  return view({
    messages: [
      ...turn("t1", "a", "b"),
      ...turn("t2", "c", "d"),
      ...turn("t3", "e", "f", { context: reading(81, "suggest") }, "2026-10-22T10:01:00"),
    ],
  });
}

describe("Compact", () => {
  it("posts, then the meter says the earlier turns are summarized and opens the summary", async () => {
    api.gets["/build/sessions/s1"] = fullSession();
    api.postAnswers["POST /build/sessions/s1/compact"] = () => {
      // The daemon keeps the summary on the session as it answers.
      api.gets["/build/sessions/s1"] = { ...fullSession(), compaction: SUMMARY };
      return SUMMARY;
    };
    renderSession();
    fireEvent.click(await screen.findByTestId("harness-compact", undefined, NET));
    await waitFor(() => expect(posts("/build/sessions/s1/compact")).toHaveLength(1), NET);
    const note = await screen.findByTestId("harness-compact-note", undefined, NET);
    expect(note.textContent).toContain("Summarized 6 earlier messages.");
    expect(note.textContent).toContain("1 claim the conversation could not back up was left out.");
    // The reading is from before the summary: no stale offer, the summary instead.
    await waitFor(
      () => expect(screen.getByTestId("harness-context-meter").getAttribute("data-state")).toBe("summarized"),
      NET,
    );
    expect(screen.queryByTestId("harness-compact")).toBeNull();
    fireEvent.click(screen.getByTestId("harness-context-meter"));
    const card = await screen.findByTestId("compaction-modal");
    expect(card.textContent).toContain("CORAL-TELESCOPE-17");
    expect(card.textContent).toContain("src/secret_backdoor.py");
  });

  it("a refusal is said in the daemon's words and keeps the offer", async () => {
    api.gets["/build/sessions/s1"] = fullSession();
    api.postAnswers["POST /build/sessions/s1/compact"] = new api.ApiError(
      "Compact needs a real model: this session runs on the demo model, which would make a summary up. Choose a model for the session first.",
      400,
    );
    renderSession();
    fireEvent.click(await screen.findByTestId("harness-compact", undefined, NET));
    expect(await screen.findByText(/Compact needs a real model/)).toBeTruthy();
    expect(screen.getByTestId("harness-compact")).toBeTruthy();
  });

  it("/compact presses the same Compact", async () => {
    api.gets["/build/sessions/s1"] = fullSession();
    api.postAnswers["POST /build/sessions/s1/compact"] = SUMMARY;
    renderSession();
    await screen.findByTestId("harness-compact", undefined, NET);
    type("/comp");
    const menu = await screen.findByTestId("harness-slash-menu");
    expect(within(menu).getByText("/compact")).toBeTruthy();
    key("Enter");
    await waitFor(() => expect(posts("/build/sessions/s1/compact")).toHaveLength(1), NET);
    // The command word is taken out of the box; nothing was sent as a message.
    expect(box().value).toBe("");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("a reading taken after the summary is the meter again (and no stale offer before it)", () => {
    const msgs = [
      ...turn("t1", "a", "b", { context: reading(85, "suggest") }, "2026-10-22T10:00:05"),
    ];
    const r = latestContext(msgs)!;
    const comp = { summary: "s", throughTurn: "t1", covers: 2, stripped: 0, strippedClaims: [], provider: "", model: "", at: "2026-10-22T10:05:00" };
    expect(readingIsBeforeSummary(r.at, comp)).toBe(true);
    expect(offersCompact(r.context, true)).toBe(false);
    const later = latestContext([...msgs, ...turn("t2", "c", "d", { context: reading(30, "ok") }, "2026-10-22T10:06:00")])!;
    expect(readingIsBeforeSummary(later.at, comp)).toBe(false);
    expect(later.context.percent).toBe(30);
  });

  it("the reading decoder keeps the daemon's percent and level, and needs a window", () => {
    expect(decodeTurnContext({ used: 10 })).toBeNull();
    expect(decodeTurnContext({ window: 1000, used: 500, percent: 91, level: "auto" })).toMatchObject({
      percent: 91,
      level: "auto",
      window: 1000,
      used: 500,
    });
    // An answer with no percent falls back to what the planned turn used.
    expect(decodeTurnContext({ window: 1000, used: 720 })).toMatchObject({ percent: 72, level: "suggest" });
  });
});

/* ------------------------------------------------- the user is looking at it */

describe("the session tells the bell the user is looking at it", () => {
  it("in front and focused: looking; hidden or left: not", async () => {
    vi.spyOn(document, "hasFocus").mockReturnValue(true);
    const v = renderSession();
    await screen.findByTestId("harness-empty");
    expect(lookingAt("s1")).toBe(true);
    expect(lookingAt("s2")).toBe(false);
    // The window went to the background.
    (document.hasFocus as unknown as ReturnType<typeof vi.fn>).mockReturnValue(false);
    act(() => {
      window.dispatchEvent(new Event("blur"));
    });
    expect(lookingAt("s1")).toBe(false);
    (document.hasFocus as unknown as ReturnType<typeof vi.fn>).mockReturnValue(true);
    act(() => {
      window.dispatchEvent(new Event("focus"));
    });
    expect(lookingAt("s1")).toBe(true);
    v.unmount();
    expect(lookingAt("s1")).toBe(false);
  });
});
