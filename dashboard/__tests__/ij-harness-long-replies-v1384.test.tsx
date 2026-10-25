/**
 * v1.384.0 — long Build replies, on the SCREEN (components/harness).
 *
 *  1. A reply that met its STEP LIMIT says so plainly under it, and the
 *     newest one offers ONE press — "Keep going" — that sends "continue" as
 *     a new turn (never automatic, never while one runs).
 *  2. THE PLAN STAYS ON THE RIGHT: a running reply's plan is drawn under the
 *     reply only below `xl` (`LivePlan` is `xl:hidden`); from `xl` it is in
 *     the Plan pane (`hidden xl:block`), which below `xl` says where it is.
 *     A plan that appears while the user is on another pane tab MARKS the
 *     Plan tab (a dot + words for a screen reader) and never switches tabs.
 *  3. NO LAG OR JUMP ON RETURN: leaving the session mid-reply keeps it
 *     (lib/turnCache); coming back draws the conversation, the reply's steps
 *     and words AT ONCE (before anything is read), then asks the daemon only
 *     for the frames after the last one it drew (`?after=<position>`); the
 *     scroll position comes back too (the bottom, or exactly where it was).
 *  4. The Build view this window showed is the one a return opens on.
 */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

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
  };
});

vi.mock("@/lib/api", () => {
  const answer = (method: string, path: string, body?: unknown) => {
    api.calls.push({ method, path, body });
    const hit = method === "GET" ? api.gets[path] : undefined;
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
import { HarnessPanes, LIVE_PLAN_ELSEWHERE, type SessionTerminalApi } from "@/components/harness/HarnessPanes";
import { scrollFor, turnFor, viewFor } from "@/lib/turnCache";
import { decodeSessionView, stepLimitWords } from "@/lib/buildSessions";

const terminal: SessionTerminalApi = {
  find: () => null,
  open: async () => null,
  render: () => <div data-testid="term-body" />,
  close: () => {},
};

function view(over: Record<string, unknown> = {}) {
  return {
    id: "s1",
    title: "Long job",
    updated_at: "2026-10-24T10:00:00",
    running: false,
    waiting: false,
    archived: false,
    folder: "C:\\work\\shop",
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
    ...over,
  };
}

/** A controllable SSE body: `push` raw frames, `end` it. */
function controlledStream() {
  let ctl!: ReadableStreamDefaultController<Uint8Array>;
  const body = new ReadableStream<Uint8Array>({
    start(c) {
      ctl = c;
    },
  });
  const enc = new TextEncoder();
  const response = new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } });
  return {
    response,
    push: (event: string, data: unknown, id?: string) =>
      ctl.enqueue(enc.encode(`${id ? `id: ${id}\n` : ""}event: ${event}\ndata: ${JSON.stringify(data)}\n\n`)),
    end: () => ctl.close(),
    /** A fetch that honours its AbortSignal, like a browser's: an abort
     *  fails the body's next read. */
    fetch: async (_url: string, init?: RequestInit) => {
      init?.signal?.addEventListener("abort", () => {
        try {
          ctl.error(new DOMException("The operation was aborted.", "AbortError"));
        } catch {
          /* already closed */
        }
      });
      return response;
    },
  };
}

function sse(frames: [string, unknown, string?][]): Response {
  const text = frames
    .map(([e, d, id]) => `${id ? `id: ${id}\n` : ""}event: ${e}\ndata: ${JSON.stringify(d)}\n\n`)
    .join("");
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
  api.gets = { "/build/sessions/s1": view(), "/build/accounts": new api.ApiError("no such route", 404) };
  api.calls = [];
  localStorage.clear();
  fetchMock = vi.fn(async () => sse([["done", { reply: "ok" }]]));
  vi.stubGlobal("fetch", fetchMock);
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

const replyAt = (over: Record<string, unknown>) => ({
  role: "assistant",
  content: "Reached the step limit. Changed shop/cart.py; next I will fix the receipt.",
  turnId: "t1",
  timing: { startedAt: 1, firstTokenAt: 2, endedAt: 3 },
  ...over,
});

/* -------------------------------------------------- 1. the step limit */

describe("a reply that met its step limit", () => {
  it("says so plainly and offers Keep going, which sends 'continue' as a new turn", async () => {
    api.gets["/build/sessions/s1"] = view({
      messages: [{ role: "user", content: "fix it all", turnId: "t1" }, replyAt({ stepLimit: { limit: 60, safety: false } })],
    });
    renderSession();
    const line = await screen.findByTestId("harness-step-limit");
    expect(line).toHaveTextContent("This reply stopped at its step limit (60 steps).");
    fireEvent.click(screen.getByTestId("harness-keep-going"));
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("http://d/build/sessions/s1/turn");
    const body = JSON.parse(String(init.body));
    expect(body.text).toBe("continue");
    expect(body.ids).toBe(true);
  });

  it("names the SAFETY stop of a reply with no limit; an older reply keeps the words but no button", async () => {
    api.gets["/build/sessions/s1"] = view({
      messages: [
        { role: "user", content: "a", turnId: "t1" },
        replyAt({ stepLimit: { limit: 1000, safety: true } }),
        { role: "user", content: "b", turnId: "t2" },
        replyAt({ turnId: "t2", content: "Finished." }),
      ],
    });
    renderSession();
    const line = await screen.findByTestId("harness-step-limit");
    expect(line).toHaveTextContent("This reply stopped at the safety limit of 1,000 steps.");
    expect(screen.queryByTestId("harness-keep-going")).toBeNull();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("the line's words, and nothing for a line without a limit", () => {
    expect(stepLimitWords({ limit: 3, safety: false })).toBe("This reply stopped at its step limit (3 steps).");
    const v = decodeSessionView(view({ messages: [replyAt({ stepLimit: { limit: 5, safety: true } })] }));
    expect(v?.messages[0].stepLimit).toEqual({ limit: 5, safety: true });
  });
});

/* ---------------------------------------------- 2. the plan on the right */

const PLAN_ARGS = { plan: [{ step: "Read the code", status: "completed" }, { step: "Fix the cart", status: "in_progress" }] };

describe("the plan stays on the right", () => {
  it("while it works: under the reply only below xl, in the Plan pane from xl", async () => {
    api.gets["/build/sessions/s1"] = view({ running: true, turn_id: "tr1" });
    const s = controlledStream();
    fetchMock.mockImplementation(s.fetch);
    renderSession();
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    await act(async () => {
      s.push("resume", { resumed: false, running: true });
      s.push("caught_up", { id: "" });
      s.push("tool_call", { id: "p1", name: "plan_update", status: "started", args: PLAN_ARGS }, "0:0");
      s.push("tool_call", { id: "p1", name: "plan_update", status: "finished", ok: true }, "1:0");
      await new Promise((r) => setTimeout(r, 30));
    });
    const under = await screen.findByTestId("harness-live-plan");
    expect(under.className).toMatch(/(^| )xl:hidden( |$)/);
    const pane = screen.getByTestId("pane-plan-live");
    expect(pane.className).toMatch(/(^| )hidden( |$)/);
    expect(pane.className).toMatch(/(^| )xl:block( |$)/);
    expect(pane).toHaveTextContent("Fix the cart");
    const note = screen.getByTestId("pane-plan-elsewhere");
    expect(note).toHaveTextContent(LIVE_PLAN_ELSEWHERE);
    expect(note.className).toMatch(/(^| )xl:hidden( |$)/);
    await act(async () => {
      s.push("done", { reply: "done" }, "2:0");
      s.end();
    });
  });

  it("a plan that appears while another tab is open marks the Plan tab, and never switches it", () => {
    const v = decodeSessionView(view())!;
    const plan = { steps: [{ index: 0, goal: "Fix the cart", state: "running" as const }], total: 1 };
    const { rerender } = render(
      <HarnessPanes view={v} livePlan={null} terminal={terminal} refreshKey={0} undoFor={() => null} onUndo={async () => {}} />,
    );
    fireEvent.click(screen.getByTestId("harness-pane-tab-files"));
    expect(screen.queryByTestId("harness-plan-new")).toBeNull();
    rerender(
      <HarnessPanes view={v} livePlan={plan} terminal={terminal} refreshKey={0} undoFor={() => null} onUndo={async () => {}} />,
    );
    const dot = screen.getByTestId("harness-plan-new");
    expect(dot).toHaveTextContent("(a new plan)");
    expect(dot.className).toMatch(/(^| )hidden( |$)/);
    expect(dot.className).toMatch(/xl:inline/);
    // The tab did NOT switch.
    expect(screen.getByTestId("harness-pane-tab-files")).toHaveAttribute("aria-selected", "true");
    expect(screen.getByTestId("files-panel")).toBeInTheDocument();
    // Opening Plan clears the mark.
    fireEvent.click(screen.getByTestId("harness-pane-tab-plan"));
    expect(screen.queryByTestId("harness-plan-new")).toBeNull();
  });

  it("no mark when the Plan tab is already open", () => {
    const v = decodeSessionView(view())!;
    const plan = { steps: [{ index: 0, goal: "Fix it", state: "running" as const }], total: 1 };
    const { rerender } = render(
      <HarnessPanes view={v} livePlan={null} terminal={terminal} refreshKey={0} undoFor={() => null} onUndo={async () => {}} />,
    );
    rerender(
      <HarnessPanes view={v} livePlan={plan} terminal={terminal} refreshKey={0} undoFor={() => null} onUndo={async () => {}} />,
    );
    expect(screen.queryByTestId("harness-plan-new")).toBeNull();
  });
});

/* -------------------------------------------- 3. no lag or jump on return */

describe("leaving mid-reply and coming back", () => {
  it("draws the reply at once, then asks only for the frames after the last one drawn", async () => {
    api.gets["/build/sessions/s1"] = view({
      running: true,
      turn_id: "tr1",
      messages: [{ role: "user", content: "a long job", turnId: "tr1" }],
    });
    const s = controlledStream();
    fetchMock.mockImplementation(s.fetch);
    const first = renderSession();
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(String((fetchMock.mock.calls[0] as unknown[])[0])).toBe("http://d/chat/turns/tr1/stream?ids=1");
    await act(async () => {
      s.push("resume", { resumed: false, running: true });
      s.push("caught_up", { id: "" });
      s.push("tool_call", { id: "r1", name: "read_file", status: "started", args: { path: "cart.py" } }, "0:0");
      s.push("tool_call", { id: "r1", name: "read_file", status: "finished", ok: true }, "1:0");
      s.push("token", { text: "Half of the " }, "2:12");
      s.push("token", { text: "answer" }, "2:18");
      await new Promise((r) => setTimeout(r, 40));
    });
    expect(await screen.findByTestId("harness-live-text")).toHaveTextContent("Half of the answer");
    // The user goes elsewhere in the app: the session unmounts (and its
    // stream's close has run to its end).
    first.unmount();
    await act(async () => {
      await new Promise((r) => setTimeout(r, 40));
    });
    const kept = turnFor("s1");
    expect(kept?.turnId).toBe("tr1");
    expect(kept?.lastId).toBe("2:18");
    expect(viewFor("s1")).not.toBeNull();
    // Coming back: everything is drawn on the FIRST render, before any read
    // or rejoin has answered.
    let release!: () => void;
    const gate = new Promise<void>((r) => {
      release = r;
    });
    api.gets["/build/sessions/s1"] = async () => {
      await gate;
      return view({ running: true, turn_id: "tr1", messages: [{ role: "user", content: "a long job", turnId: "tr1" }] });
    };
    const back = controlledStream();
    fetchMock.mockReset();
    fetchMock.mockImplementation(back.fetch);
    renderSession();
    expect(screen.getByTestId("harness-live-text")).toHaveTextContent("Half of the answer");
    expect(screen.getByTestId("harness-user-bubble")).toHaveTextContent("a long job");
    expect(screen.getByTestId("harness-stop")).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
    await act(async () => {
      release();
      await new Promise((r) => setTimeout(r, 20));
    });
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(String((fetchMock.mock.calls[0] as unknown[])[0])).toBe("http://d/chat/turns/tr1/stream?after=2%3A18");
    await act(async () => {
      back.push("resume", { resumed: true, running: true });
      back.push("token", { text: " is here." }, "2:27");
      back.push("caught_up", { id: "2:27" });
      await new Promise((r) => setTimeout(r, 40));
    });
    // Carried on: the words follow on, never twice.
    expect(screen.getByTestId("harness-live-text").textContent).toBe("Half of the answer is here.");
    await act(async () => {
      back.push("done", { reply: "Half of the answer is here." }, "3:0");
      back.end();
      await new Promise((r) => setTimeout(r, 20));
    });
    await waitFor(() => expect(turnFor("s1")).toBeNull());
  });

  it("a reply that ended while the user was away is not carried on: the transcript has it", async () => {
    api.gets["/build/sessions/s1"] = view({ running: true, turn_id: "tr1" });
    const s = controlledStream();
    fetchMock.mockImplementation(s.fetch);
    const first = renderSession();
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    await act(async () => {
      s.push("resume", { resumed: false, running: true });
      s.push("caught_up", { id: "" });
      s.push("token", { text: "Working" }, "0:7");
      await new Promise((r) => setTimeout(r, 30));
    });
    first.unmount();
    expect(turnFor("s1")).not.toBeNull();
    api.gets["/build/sessions/s1"] = view({
      running: false,
      messages: [{ role: "user", content: "go", turnId: "tr1" }, replyAt({ turnId: "tr1", content: "All finished." })],
    });
    fetchMock.mockReset();
    renderSession();
    expect(await screen.findByText("All finished.")).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByTestId("harness-live")).toBeNull());
    expect(fetchMock).not.toHaveBeenCalled();
    expect(turnFor("s1")).toBeNull();
  });

  it("a reload's WHOLE replay is drawn in one pass: nothing shows until caught_up", async () => {
    api.gets["/build/sessions/s1"] = view({ running: true, turn_id: "tr1" });
    const s = controlledStream();
    fetchMock.mockImplementation(s.fetch);
    renderSession();
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    await act(async () => {
      s.push("resume", { resumed: false, running: true });
      s.push("tool_call", { id: "r1", name: "read_file", status: "started", args: { path: "a.py" } }, "0:0");
      s.push("tool_call", { id: "r1", name: "read_file", status: "finished", ok: true }, "1:0");
      s.push("helper", { id: "h1", agent: "researcher", task: "look around", state: "working", since: 1 }, "2:0");
      s.push("token", { text: "Replayed words" }, "3:14");
      await new Promise((r) => setTimeout(r, 40));
    });
    // Folded, not drawn (a network chunk of a 1.2 MB replay is one of many).
    expect(screen.queryByTestId("helper-row")).toBeNull();
    expect(screen.queryByTestId("harness-live-text")).toBeNull();
    expect(screen.queryByText(/a\.py/)).toBeNull();
    await act(async () => {
      s.push("caught_up", { id: "3:14" });
      await new Promise((r) => setTimeout(r, 40));
    });
    expect(screen.getByTestId("helper-row")).toHaveAttribute("data-helper", "h1");
    expect(screen.getByTestId("harness-live-text")).toHaveTextContent("Replayed words");
    await act(async () => {
      s.push("done", { reply: "Replayed words" }, "4:0");
      s.end();
    });
  });

  it("a running reply the open read AND the list both see is followed ONCE", async () => {
    api.gets["/build/sessions/s1"] = view({ running: true, turn_id: "tr1" });
    const s = controlledStream();
    fetchMock.mockImplementation(s.fetch);
    render(
      <HarnessSession threadId="s1" terminal={terminal} listRunning={true} onChanged={() => {}} onGone={() => {}} />,
    );
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    await act(async () => {
      await new Promise((r) => setTimeout(r, 60));
    });
    expect(fetchMock.mock.calls.map((c) => String((c as unknown[])[0]))).toEqual([
      "http://d/chat/turns/tr1/stream?ids=1",
    ]);
    await act(async () => {
      s.end();
    });
  });

  it("the scroll position comes back: where the user was, or the bottom", async () => {
    api.gets["/build/sessions/s1"] = view({
      messages: [{ role: "user", content: "q", turnId: "t1" }, replyAt({ content: "r" })],
    });
    const first = renderSession();
    await screen.findByText("r");
    const el = screen.getByTestId("harness-transcript");
    Object.defineProperty(el, "scrollHeight", { configurable: true, value: 2000 });
    Object.defineProperty(el, "clientHeight", { configurable: true, value: 500 });
    el.scrollTop = 640;
    fireEvent.scroll(el);
    expect(scrollFor("s1")).toEqual({ top: 640, atBottom: false });
    first.unmount();
    // A layout effect sets it BEFORE the first paint: the element is
    // measured as the same size again.
    const proto = Object.getOwnPropertyDescriptor(HTMLElement.prototype, "scrollHeight");
    Object.defineProperty(HTMLElement.prototype, "scrollHeight", { configurable: true, get: () => 2000 });
    try {
      renderSession();
      expect(screen.getByTestId("harness-transcript").scrollTop).toBe(640);
    } finally {
      if (proto) Object.defineProperty(HTMLElement.prototype, "scrollHeight", proto);
      else delete (HTMLElement.prototype as unknown as { scrollHeight?: number }).scrollHeight;
    }
  });
});
