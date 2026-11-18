/**
 * Fourth review C-CS-1: a coding reply that was CUT SHORT (its answer ran out
 * at the model's output limit, or its model calls kept being cut off and never
 * recovered) says so in the next-step box and offers "Keep going" — the
 * existing press, which sends only what the user would type ("continue"),
 * and only when they press it. Before: a small "cut off" chip and nothing to
 * press (and a cut the route did not recover was not shown at all).
 *
 * The words are the daemon's own (`harness/reply_end.CUT_WORDS`, through the
 * twin `lib/replyEnd`). A cut the route recovered from is a reply that
 * finished: no such box.
 */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

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
    gets: {} as Record<string, unknown>,
    calls: [] as { method: string; path: string; body?: unknown }[],
  };
});

vi.mock("@/lib/api", () => {
  const answer = (method: string, path: string, body?: unknown) => {
    api.calls.push({ method, path, body });
    const hit = method === "GET" ? api.gets[path] : undefined;
    if (hit instanceof Error) return Promise.reject(hit);
    if (hit === undefined && method === "GET") return Promise.reject(new api.ApiError(`unmocked ${path}`, 404));
    return Promise.resolve(hit ?? {});
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
import { NEXT_KEEP_GOING_TEXT, codeNextStep, codeReplyFacts } from "@/lib/nextStep";
import { CUT_WORDS } from "@/lib/replyEnd";

const LENGTH_WORDS = "Its answer was cut off at the model's output limit, so it did not finish.";
const CUT_OFF_WORDS = "The model's answers kept being cut off, so it did not finish.";

const line = (over: Record<string, unknown> = {}) => ({ content: "Done.", documents: ["a.py"], ...over });

describe("the box's reading of a reply cut short", () => {
  it("a reply cut at its length says so and offers Keep going", () => {
    const step = codeNextStep(codeReplyFacts(line({ truncated: true, content: "Want me to also add tests?" })), null)!;
    expect(step.recap).toBe(`Changed 1 file. ${LENGTH_WORDS}`);
    expect(step.actions.map((a) => a.kind)).toEqual(["keep_going"]);
    expect(step.actions[0]).toMatchObject({ how: "send", text: NEXT_KEEP_GOING_TEXT, testid: "harness-keep-going" });
  });

  it("a reply whose model calls kept being cut off (not recovered) says so too, even with no file changed", () => {
    const step = codeNextStep(
      codeReplyFacts({ content: "Half an answer", route: { provider: "x", cut_off: { times: 3, recovered: false } } }),
      null,
    )!;
    expect(step.recap).toBe(CUT_OFF_WORDS);
    expect(step.actions.map((a) => a.kind)).toEqual(["keep_going"]);
  });

  it("a cut the route recovered from is a reply that finished: no box over a plain answer", () => {
    expect(codeNextStep(codeReplyFacts({ content: "In cart.py.", route: { cut_off: { times: 1, recovered: true } } }), null)).toBeNull();
  });

  it("a cut beats a limit (the daemon's order): its words, one Keep going", () => {
    const step = codeNextStep(codeReplyFacts(line({ truncated: true, capMet: { kind: "time", limit: 30 } })), null)!;
    expect(step.recap).toBe(`Changed 1 file. ${LENGTH_WORDS}`);
    expect(step.actions.map((a) => a.kind)).toEqual(["keep_going"]);
  });

  it("a failed reply still gets no box (its own line has Retry), and a restart's cut keeps its own Continue", () => {
    expect(codeNextStep(codeReplyFacts(line({ truncated: true, error: "It failed." })), null)).toBeNull();
    const restart = codeNextStep(codeReplyFacts(line({ truncated: true, interrupted: true })), null)!;
    expect(restart.actions).toEqual([]);
  });

  it("the words are the twin's", () => {
    expect([CUT_WORDS.length, CUT_WORDS.cut_off]).toEqual([LENGTH_WORDS, CUT_OFF_WORDS]);
  });
});

/* ---------------------------------------------------------- the screen */

const terminal: SessionTerminalApi = {
  find: () => null,
  open: async () => null,
  render: () => <div data-testid="term-body" />,
  close: () => {},
};

function view(over: Record<string, unknown> = {}) {
  return {
    id: "s1",
    title: "Cart",
    updated_at: "2026-10-29T10:00:00",
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

const q = (turnId: string, content = "fix the cart") => ({ role: "user", content, turnId });
const reply = (over: Record<string, unknown> = {}) => ({
  role: "assistant",
  content: "Fixed half the cart.",
  turnId: "t1",
  timing: { startedAt: 1, firstTokenAt: 2, endedAt: 3 },
  ...over,
});
const sentTexts = () =>
  fetchMock.mock.calls
    .filter((c) => String(c[0]).endsWith("/build/sessions/s1/turn"))
    .map((c) => JSON.parse(String((c[1] as RequestInit).body)).text as string);

function renderSession() {
  return render(
    <HarnessSession threadId="s1" terminal={terminal} listRunning={false} onChanged={() => {}} onGone={() => {}} />,
  );
}

describe("a coding session's screen under a reply cut short", () => {
  it("the box says it did not finish and Keep going sends 'continue' only when pressed", async () => {
    api.gets["/build/sessions/s1"] = view({
      messages: [q("t1"), reply({ truncated: true, documents: ["cart.py"] })],
    });
    renderSession();
    const next = await screen.findByTestId("next-step-box");
    const keep = within(next).getByTestId("harness-keep-going");
    expect(keep).toHaveTextContent("Keep going");
    fireEvent.click(screen.getAllByTestId("reply-line-toggle").at(-1)!);
    expect(screen.getByTestId("next-step-recap")).toHaveTextContent(`Changed 1 file. ${LENGTH_WORDS}`);
    await new Promise((r) => setTimeout(r, 30));
    expect(sentTexts()).toEqual([]); // nothing is sent without the press
    fireEvent.click(keep);
    await waitFor(() => expect(sentTexts()).toEqual([NEXT_KEEP_GOING_TEXT]));
  });

  it("an unrecovered cut off shows the box and the line's 'cut off'; a recovered one shows neither", async () => {
    api.gets["/build/sessions/s1"] = view({
      messages: [q("t1"), reply({ route: { provider: "x", model: "y", cut_off: { times: 3, recovered: false } } })],
    });
    renderSession();
    const next = await screen.findByTestId("next-step-box");
    expect(within(next).getByTestId("harness-keep-going")).toBeInTheDocument();
    expect(screen.getAllByTestId("reply-line-toggle").at(-1)!.textContent).toContain("cut off");
    cleanup();
    api.gets["/build/sessions/s1"] = view({
      messages: [q("t1"), reply({ content: "In cart.py.", route: { provider: "x", model: "y", cut_off: { times: 1, recovered: true } } })],
    });
    renderSession();
    await screen.findByText("In cart.py.");
    await new Promise((r) => setTimeout(r, 30));
    expect(screen.queryByTestId("next-step-box")).toBeNull();
    expect(screen.queryByTestId("harness-keep-going")).toBeNull();
  });
});
