/**
 * v1.510.0 (second review S2, the screen side) — a coding reply the session
 * could not save. The daemon now says so on the turn's LAST frame (`done` or
 * `error`): `not_saved`, a plain sentence. The page used to re-read the
 * session, find no reply, and drop the live one: the reply vanished with no
 * words. Now the page KEEPS the reply it was showing (its words and steps)
 * with the daemon's sentence on it as a warning line, until a later read of
 * the session holds that turn's reply — then only the saved one is drawn.
 *
 * Driven through the REAL chat stream (lib/useChatStream) against a fetch
 * double, the way harness-live-settle-v1417 drives a followed turn.
 */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";

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
  };
});

vi.mock("@/lib/api", () => {
  const answer = (method: string, path: string) => {
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
    post: (p: string) => answer("POST", p),
    put: (p: string) => answer("PUT", p),
    patch: (p: string) => answer("PATCH", p),
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
vi.mock("@/components/terminal/FilesPanel", () => ({ FilesPanel: () => <div data-testid="files-panel" /> }));

import { HarnessSession } from "@/components/harness/HarnessSession";
import type { SessionTerminalApi } from "@/components/harness/HarnessPanes";

/** The daemon's own sentences (routes/build REPLY_NOT_SAVED / PLAN_NOT_SAVED),
 *  shown exactly as sent. */
const REPLY_NOT_SAVED =
  "This reply could not be saved to the session. It will be put back when Iron Jarvis next starts.";
const PLAN_NOT_SAVED =
  "The plan you approved could not be saved to the session, so the work did not start and the session is still on Plan. Ask again to carry it out.";

const terminal: SessionTerminalApi = { find: () => null, open: async () => null, render: () => <div />, close: () => {} };

function view(id: string, over: Record<string, unknown> = {}) {
  return {
    id,
    title: "Add a cart",
    updated_at: "2026-11-16T10:00:00",
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
    session_id: `build:${id}`,
    queued: [],
    turn_id: "",
    ...over,
  };
}
const userLine = (content: string, turnId: string) => ({ role: "user", content, turnId, at: "2026-11-16T10:00:00" });
const replyLine = (content: string, turnId: string) => ({
  role: "assistant",
  content,
  turnId,
  route: { provider: "mock", model: "mock-1", reason: "mock" },
  timing: { startedAt: 1, firstTokenAt: 2, endedAt: 3 },
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
    push: (event: string, data: unknown) =>
      ctl.enqueue(enc.encode(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`)),
    end: () => {
      try {
        ctl.close();
      } catch {
        /* closed */
      }
    },
    response: (init?: RequestInit) => {
      init?.signal?.addEventListener("abort", () => {
        try {
          ctl.error(new DOMException("The operation was aborted.", "AbortError"));
        } catch {
          /* closed */
        }
      });
      return new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } });
    },
  };
}

let stream: ReturnType<typeof controlledStream>;
/** Each test its own session id: the kept replies live for the window's life. */
let sid = "";
let n = 0;

beforeEach(() => {
  localStorage.clear();
  n += 1;
  sid = `nsv${n}`;
  api.gets = {
    [`/build/sessions/${sid}`]: view(sid, { running: true, turn_id: "tr1", messages: [userLine("Add a cart", "tr1")] }),
    "/build/sessions/other": view("other"),
    "/build/accounts": new api.ApiError("no such route", 404),
  };
  stream = controlledStream();
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      if (/\/chat\/turns\/tr1\/stream/.test(String(url))) return stream.response(init);
      return new Response("{}", { status: 200 });
    }),
  );
});

afterEach(() => {
  stream.end();
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const session = (id: string) => (
  <HarnessSession key={id} threadId={id} terminal={terminal} listRunning={false} onChanged={() => {}} onGone={() => {}} />
);

async function followed() {
  const out = render(session(sid));
  await waitFor(() => expect(fetch).toHaveBeenCalledWith(expect.stringContaining("/chat/turns/tr1/stream"), expect.anything()), {
    timeout: 10_000,
  });
  await act(async () => {
    stream.push("resume", { resumed: false, running: true });
    stream.push("caught_up", { id: "" });
    await new Promise((r) => setTimeout(r, 20));
  });
  return out;
}

async function push(event: string, data: unknown) {
  await act(async () => {
    stream.push(event, data);
    await new Promise((r) => setTimeout(r, 20));
  });
}

/** The live reply: words around one finished step. */
async function workAndWords() {
  await push("token", { text: "Added the " });
  await push("tool_call", { id: "c1", name: "read_file", status: "started", args: { path: "cart.tsx" } });
  await push("tool_call", { id: "c1", name: "read_file", status: "finished", ok: true, output: "export {}" });
  await push("token", { text: "cart page." });
}

async function end(event: "done" | "error", data: Record<string, unknown>) {
  await act(async () => {
    stream.push(event, data);
    stream.end();
    await new Promise((r) => setTimeout(r, 30));
  });
}

describe("a coding reply the session could not save", () => {
  it("keeps the live reply with the daemon's sentence, then lets a later read's saved reply take its place", async () => {
    const { rerender } = await followed();
    await workAndWords();
    // The session after the turn: the question, and NO reply (it was not saved).
    api.gets[`/build/sessions/${sid}`] = view(sid, { messages: [userLine("Add a cart", "tr1")] });
    await end("done", { reply: "Added the cart page.", not_saved: REPLY_NOT_SAVED });

    await waitFor(() => expect(screen.queryByTestId("harness-live")).toBeNull(), { timeout: 5_000 });
    const reply = screen.getByTestId("harness-reply");
    expect(reply).toHaveTextContent("Added the cart page.");
    expect(within(reply).getByTestId("step-run")).toHaveAttribute("data-count", "1");
    // The daemon's words, unchanged, as the reply's warning line.
    expect(within(reply).getByTestId("harness-reply-not-saved-words").textContent).toBe(REPLY_NOT_SAVED);
    // Nothing can be branched from a reply the session does not have.
    expect(within(reply).queryByTestId("harness-branch-reply")).toBeNull();

    // Left and come back while the session still lacks it: still kept.
    rerender(session("other"));
    await screen.findByTestId("harness-empty");
    rerender(session(sid));
    await waitFor(() => expect(screen.getAllByTestId("harness-reply")).toHaveLength(1));
    expect(screen.getByTestId("harness-reply-not-saved-words").textContent).toBe(REPLY_NOT_SAVED);

    // The next start put it back: a later read holds the reply — drawn ONCE,
    // and the warning goes with the kept copy.
    api.gets[`/build/sessions/${sid}`] = view(sid, {
      messages: [userLine("Add a cart", "tr1"), replyLine("Added the cart page.", "tr1")],
    });
    rerender(session("other"));
    await screen.findByTestId("harness-empty");
    rerender(session(sid));
    await waitFor(() => expect(screen.queryByTestId("harness-reply-not-saved")).toBeNull(), { timeout: 5_000 });
    expect(screen.getAllByTestId("harness-reply")).toHaveLength(1);
    expect(screen.getByTestId("harness-reply")).toHaveTextContent("Added the cart page.");
  });

  it("keeps the words of a reply that ended on an error frame the session could not save", async () => {
    await followed();
    await push("token", { text: "Half of the cart" });
    api.gets[`/build/sessions/${sid}`] = view(sid, { messages: [userLine("Add a cart", "tr1")] });
    await end("error", { detail: "The model stopped answering.", not_saved: REPLY_NOT_SAVED });

    await waitFor(() => expect(screen.queryByTestId("harness-live")).toBeNull(), { timeout: 5_000 });
    const reply = screen.getByTestId("harness-reply");
    expect(reply).toHaveTextContent("Half of the cart");
    expect(within(reply).getByTestId("harness-reply-not-saved-words").textContent).toBe(REPLY_NOT_SAVED);
  });

  it("puts the plan sentence on a reply that WAS saved, drawn once", async () => {
    await followed();
    await push("token", { text: "Here is the plan." });
    api.gets[`/build/sessions/${sid}`] = view(sid, {
      messages: [userLine("Add a cart", "tr1"), replyLine("Here is the plan.", "tr1")],
    });
    await end("done", { reply: "Here is the plan.", not_saved: PLAN_NOT_SAVED });

    await waitFor(() => expect(screen.getByTestId("harness-reply-not-saved-words").textContent).toBe(PLAN_NOT_SAVED), {
      timeout: 5_000,
    });
    expect(screen.getAllByTestId("harness-reply")).toHaveLength(1);
    expect(screen.queryByTestId("harness-live")).toBeNull();
  });

  it("an ordinary end (nothing unsaved) shows no warning and the saved reply once", async () => {
    await followed();
    await push("token", { text: "Done." });
    api.gets[`/build/sessions/${sid}`] = view(sid, {
      messages: [userLine("Add a cart", "tr1"), replyLine("Done.", "tr1")],
    });
    await end("done", { reply: "Done." });
    await waitFor(() => expect(screen.getAllByTestId("harness-reply")).toHaveLength(1), { timeout: 5_000 });
    expect(screen.queryByTestId("harness-reply-not-saved")).toBeNull();
  });
});
