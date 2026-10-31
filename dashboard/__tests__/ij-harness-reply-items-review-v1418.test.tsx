/**
 * v1.418.0 (fix plan C2.3) — the INDEPENDENT REVIEW's pins on the reply in
 * order, each red on the first cut:
 *
 *  - REPLAY: a reply joined mid-way (a reload: `resume` … `caught_up`) is
 *    folded undrawn, and the text store does not move while it is — so every
 *    replayed step was placed at the text's length BEFORE the replay (all
 *    steps above all words). The tap now counts the replayed words itself.
 *  - RESET: the daemon's `reset` (a cut-off call handed back as text, the
 *    text-tool-call nudge, the language rewrite) throws the words away; a
 *    step's old place in the OLD words was clamped into the NEW ones and cut
 *    them mid-word. A step from before a reset now sits before the new words.
 *  - SETTLED: the saved content can carry words this window already drew
 *    between the steps (the approved plan prefixed to the reply, a closing
 *    offer's answer combined with what came after): a kept segment the
 *    content already holds is not drawn twice.
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

describe("review: a reply joined mid-way places its replayed steps where they ran", () => {
  it("text → read → text replayed (resume … caught_up) draws in that order", async () => {
    await mountFollowing();
    await push("resume", { resumed: false, running: true });
    await push("token", { text: "Let me look at the cart. " });
    await push("tool_call", { ...READ, status: "started" });
    await push("tool_call", { ...READ, status: "finished", ok: true });
    await push("token", { text: "Found the bug, fixing it. " });
    await push("tool_call", { ...EDIT, status: "started" });
    await push("caught_up", { id: "" });
    const live = screen.getByTestId("harness-live");
    const part = within(live).getByTestId("harness-live-text-part");
    const run = within(live).getByTestId("step-run");
    const last = within(live).getByTestId("harness-live-text");
    const current = within(live).getByTestId("live-tool-rows");
    expect(part).toHaveTextContent("Let me look at the cart.");
    expect(last).toHaveTextContent("Found the bug, fixing it.");
    expect(inOrder([part, run, last, current])).toBe(true);
  });
});

describe("review: a reset throws the old words away, and their places with them", () => {
  it("a step from before a reset sits BEFORE the new words, never inside them", async () => {
    await mountFollowing();
    await push("resume", { resumed: false, running: true });
    await push("caught_up", { id: "" });
    await push("token", { text: "Let me look at the cart. " });
    await push("tool_call", { ...READ, status: "started" });
    await push("tool_call", { ...READ, status: "finished", ok: true });
    await push("token", { text: "Found it." });
    await push("reset", { reason: "cut_off" });
    await push("token", { text: "Arreglado: el total estaba mal." });
    const live = screen.getByTestId("harness-live");
    expect(within(live).queryByTestId("harness-live-text-part")).toBeNull();
    const text = within(live).getByTestId("harness-live-text");
    expect(text).toHaveTextContent("Arreglado: el total estaba mal.");
    const rows = within(live).getByTestId("live-tool-rows");
    expect(inOrder([rows, text])).toBe(true);
  });
});

describe("review: the settled reply never draws the same words twice", () => {
  it("a kept segment the saved content already holds (the approved plan) is not drawn again", () => {
    const plan = "Here is the plan: read the cart, fix the total.";
    const shape = { text: `${plan} `, cuts: [{ offset: plan.length + 1, count: 1 }] };
    const saved = [{ name: "exit_plan_mode", ok: true }];
    const items = settledReplyItems(`${plan}\n\nPlan approved, ready.`, saved, shape);
    const words = items.filter((it) => it.kind === "text").map((it) => (it as { text: string }).text);
    expect(words.join("\n").split(plan).length - 1).toBe(1);
    expect(items.map((it) => it.kind)).toEqual(["steps", "text"]);
  });

  it("a kept segment the content does NOT hold is still drawn in its place", () => {
    const shape = { text: "Let me look. ", cuts: [{ offset: 13, count: 1 }] };
    const items = settledReplyItems("Fixed.", [{ name: "read_file", ok: true }], shape);
    expect(items.map((it) => it.kind)).toEqual(["text", "steps", "text"]);
  });
});
