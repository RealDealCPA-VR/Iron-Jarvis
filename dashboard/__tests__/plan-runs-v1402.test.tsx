/**
 * v1.402.0 ("approve once, then off to the races"), part 2: AN APPROVED PLAN
 * RUNS — on the Build screen (components/harness/HarnessSession, the REAL
 * chat stream against a fetch double).
 *
 * When a plan turn approved its plan and ended cleanly, the daemon starts the
 * act turn BY ITSELF (routes/build.run_approved_plan). The view says so
 * (`plan_run`) until that turn is running; the screen keeps reading (bounded)
 * and FOLLOWS the turn by its id — the same follow a turn the list saw start
 * gets — so the user watches the work begin without typing anything. With
 * nothing due (Deny, a failed plan turn), the screen follows nothing.
 *
 * The plan door's one Allow says it runs the plan ("Allow and run"); the
 * value it posts is still `once`.
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
import type { SessionTerminalApi } from "@/components/harness/HarnessPanes";
import { ApprovalCard } from "@/components/chat/ApprovalCard";
import { ALLOW_AND_RUN, ALLOW_ONCE, PLAN_RUN_SENTENCE } from "@/lib/approvalWords";
import { decodeSessionView } from "@/lib/buildSessions";

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
    updated_at: "2026-10-28T10:00:00",
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

const q = (content: string, turnId: string, extra: Record<string, unknown> = {}) => ({
  role: "user",
  content,
  turnId,
  at: "2026-10-28T10:00:00",
  ...extra,
});
const reply = (content: string, turnId: string) => ({ role: "assistant", content, turnId });

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

const fetchedUrls = () => fetchMock.mock.calls.map((c) => String((c as unknown[])[0]));

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  api.gets = { "/build/sessions/s1": view({ mode: "plan" }), "/build/accounts": new api.ApiError("no such route", 404) };
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

const box = () => screen.getByTestId("harness-input") as HTMLTextAreaElement;
async function send(text: string) {
  fireEvent.change(box(), { target: { value: text } });
  fireEvent.keyDown(box(), { key: "Enter" });
}

describe("the plan door says Allow runs the plan", () => {
  it("one Allow, worded for what it does; the value posted is still once", () => {
    render(
      <ApprovalCard
        approval={{ id: "ap1", callId: "c1", tool: "exit_plan_mode", args: {} }}
        docked
        scope="session"
        planText="1. Add the cart model"
      />,
    );
    const card = screen.getByTestId("chat-approval-card");
    expect(within(card).getByRole("button", { name: ALLOW_AND_RUN })).toBeInTheDocument();
    expect(within(card).queryByRole("button", { name: ALLOW_ONCE })).toBeNull();
    expect(card).toHaveTextContent(PLAN_RUN_SENTENCE);
  });

  it("every other card keeps Allow once", () => {
    render(<ApprovalCard approval={{ id: "a2", callId: "c2", tool: "shell", args: { command: "npm test" } }} docked scope="session" />);
    expect(screen.getByRole("button", { name: ALLOW_ONCE })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: ALLOW_AND_RUN })).toBeNull();
  });
});

describe("the view's plan_run", () => {
  it("is decoded (and absent reads as nothing due)", () => {
    expect(decodeSessionView(view({ plan_run: true }))?.planRun).toBe(true);
    expect(decodeSessionView(view())?.planRun).toBe(false);
  });
});

describe("the act turn the daemon starts is FOLLOWED", () => {
  it("a plan turn ends; the view says the act turn is due, then running: the screen follows it by its id", async () => {
    renderSession();
    await screen.findByTestId("harness-empty");
    let phase: "plan" | "due" | "running" | "done" = "plan";
    let planTurn = "";
    const PLAN_LINES = () => [q("Plan the cart", planTurn), reply("My plan: 1. model 2. page\n\nStarting on the plan.", planTurn)];
    api.gets["/build/sessions/s1"] = () => {
      if (phase === "due") {
        // The plan turn's line is written; its act turn is DUE, not yet running.
        phase = "running";
        return view({ messages: PLAN_LINES(), plan_run: true });
      }
      if (phase === "running") {
        return view({
          messages: [...PLAN_LINES(), q("Your plan above is approved.", "bt_plan", { continuation: true })],
          running: true,
          turn_id: "bt_plan",
        });
      }
      if (phase === "done") {
        return view({
          messages: [
            ...PLAN_LINES(),
            q("Your plan above is approved.", "bt_plan", { continuation: true }),
            reply("Built the cart model and page.", "bt_plan"),
          ],
        });
      }
      return view({ mode: "plan" });
    };
    fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
      if (String(url).includes("/chat/turns/bt_plan/stream")) {
        phase = "done";
        return sse([
          ["token", { text: "Built the cart model and page." }],
          ["done", { reply: "Built the cart model and page." }],
        ]);
      }
      planTurn = JSON.parse(String(init?.body)).turn_id;
      phase = "due";
      return sse([
        ["token", { text: "Starting on the plan." }],
        ["done", { reply: "Starting on the plan." }],
      ]);
    });
    await send("Plan the cart");
    // The screen followed the daemon's act turn (a tagged rejoin, by its id)…
    await waitFor(
      () => expect(fetchedUrls()).toContain("http://d/chat/turns/bt_plan/stream?ids=1"),
      { timeout: 8000 },
    );
    // …and ends on its reply, with the hidden question never drawn.
    await waitFor(() =>
      expect(screen.getAllByTestId("harness-reply").at(-1)).toHaveTextContent("Built the cart model and page."),
    );
    expect(screen.queryByText("Your plan above is approved.")).toBeNull();
    // Followed ONCE.
    expect(fetchedUrls().filter((u) => u.includes("/chat/turns/bt_plan/stream"))).toHaveLength(1);
  });

  it("nothing due (a Deny, a failed plan turn): the screen follows nothing and keeps the box free", async () => {
    renderSession();
    await screen.findByTestId("harness-empty");
    let turn = "";
    fetchMock.mockImplementation(async (_url: string, init?: RequestInit) => {
      turn = JSON.parse(String(init?.body)).turn_id;
      api.gets["/build/sessions/s1"] = view({
        mode: "plan",
        messages: [q("Plan the cart", turn), reply("Revising the plan.", turn)],
      });
      return sse([["done", { reply: "Revising the plan." }]]);
    });
    await send("Plan the cart");
    await waitFor(() =>
      expect(screen.getAllByTestId("harness-reply").at(-1)).toHaveTextContent("Revising the plan."),
    );
    await act(async () => {
      await new Promise((r) => setTimeout(r, 700));
    });
    expect(fetchedUrls().filter((u) => u.includes("/chat/turns/"))).toEqual([]);
    expect(box().disabled).toBe(false);
  });
});
