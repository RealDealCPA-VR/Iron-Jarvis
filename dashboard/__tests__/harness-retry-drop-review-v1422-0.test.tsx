/**
 * v1.422.0 review (Wave 2 "Never lose work", B1) — on the REAL HarnessSession
 * (the mocks are ij-harness-reply-items-review-v1418's): a round tried again
 * after a passing provider failure takes back ONLY its own words (`reset`
 * with `drop`), and the daemon counts `drop` in CODE POINTS (Python's len).
 * The session's frame tap counted the words in UTF-16 units and took `drop`
 * off that count, so a failed try that streamed an emoji (two units, one code
 * point) left the tap one unit long: every step placed after the retry sat
 * one character too late and cut the next words ("The fix.D" | step |
 * "one."), and the live text kept a stray piece of the failed try.
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
const DAEMON = {
  online: true,
  checking: false,
  health: { default_provider: "mock", default_model: "mock-1", providers: [] },
  refresh: () => {},
};
vi.mock("@/lib/daemon", () => ({ useDaemon: () => DAEMON }));
const MODELS = { models: [], usable: [], loading: false };
vi.mock("@/lib/useModels", () => ({ useModels: () => MODELS }));
vi.mock("@/components/terminal/FilesPanel", () => ({ FilesPanel: () => <div data-testid="files-panel" /> }));

import { HarnessSession } from "@/components/harness/HarnessSession";
import type { SessionTerminalApi } from "@/components/harness/HarnessPanes";
import { forgetReplyShapes, settledReplyItems } from "@/lib/replyItems";

const terminal: SessionTerminalApi = { find: () => null, open: async () => null, render: () => <div />, close: () => {} };

function view(id: string, over: Record<string, unknown> = {}) {
  return {
    id,
    title: "Fix the cart",
    updated_at: "2026-10-31T10:00:00",
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
const userLine = (content: string, turnId: string) => ({ role: "user", content, turnId, at: "2026-10-31T10:00:00" });

function controlledStream() {
  let ctl!: ReadableStreamDefaultController<Uint8Array>;
  const body = new ReadableStream<Uint8Array>({
    start(c) {
      ctl = c;
    },
  });
  const enc = new TextEncoder();
  return {
    push: (event: string, data: unknown) => ctl.enqueue(enc.encode(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`)),
    end: () => {
      try {
        ctl.close();
      } catch {
        /* closed */
      }
    },
    response: () => new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } }),
  };
}

let stream: ReturnType<typeof controlledStream>;

beforeEach(() => {
  localStorage.clear();
  forgetReplyShapes();
  api.calls = [];
  api.gets = {
    "/build/sessions/s1": view("s1", { running: true, turn_id: "tr1", messages: [userLine("Fix the cart total", "tr1")] }),
    "/build/accounts": new api.ApiError("no such route", 404),
  };
  stream = controlledStream();
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (/\/chat\/turns\/tr1\/stream/.test(String(url))) return stream.response();
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

async function push(event: string, data: unknown) {
  await act(async () => {
    stream.push(event, data);
    await new Promise((r) => setTimeout(r, 20));
  });
}

function inOrder(els: Element[]): boolean {
  for (let i = 1; i < els.length; i++) {
    if (!(els[i - 1].compareDocumentPosition(els[i]) & Node.DOCUMENT_POSITION_FOLLOWING)) return false;
  }
  return true;
}

const READ = { id: "r1", name: "read_file", args: { path: "cart.py" } };
const EDIT = { id: "e1", name: "edit_file", args: { path: "cart.py" } };


async function mountFollowing() {
  render(<HarnessSession threadId="s1" terminal={terminal} listRunning={false} onChanged={() => {}} onGone={() => {}} />);
  await waitFor(() => expect(fetch).toHaveBeenCalledWith(expect.stringContaining("/chat/turns/tr1/stream"), expect.anything()), {
    timeout: 10_000,
  });
}

describe("review: a retried round's take-back counts characters the way the daemon does", () => {
  it("a failed try with an emoji: the steps after the retry sit exactly between the words", async () => {
    await mountFollowing();
    await push("resume", { resumed: false, running: true });
    await push("caught_up", { id: "" });
    await push("token", { text: "Let me look 😀 at the cart. " });
    await push("tool_call", { ...READ, status: "started" });
    await push("tool_call", { ...READ, status: "finished", ok: true });
    await push("round", { round: 1 });
    // The failed try: its words, then the daemon's take-back (Python's len).
    await push("token", { text: "Half 😀 answ" });
    await push("reset", { reason: "retry", drop: [..."Half 😀 answ"].length });
    await push("provider_wait", { state: "trying", attempt: 2, of: 5 });
    await push("token", { text: "The fix." });
    await push("tool_call", { ...EDIT, status: "started" });
    await push("tool_call", { ...EDIT, status: "finished", ok: true });
    await push("token", { text: "Done." });
    const live = screen.getByTestId("harness-live");
    const parts = within(live).getAllByTestId("harness-live-text-part").map((el) => el.textContent ?? "");
    expect(parts.map((t) => t.trim())).toEqual(["Let me look 😀 at the cart.", "The fix."]);
    expect(within(live).getByTestId("harness-live-text")).toHaveTextContent(/^Done\.$/);
    expect(live.textContent ?? "").not.toContain("Half");
  });
});
