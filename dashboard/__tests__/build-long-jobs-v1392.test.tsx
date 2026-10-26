/**
 * v1.392.0 (the long-jobs plan's Phase 3) — a coding reply NEVER STALLS
 * SILENTLY, on the SCREEN (components/harness):
 *
 *  1. A `stall` frame (no progress for ten minutes) puts the daemon's own
 *     sentence on the live reply with **Stop the step** (only that tool call;
 *     `POST /build/sessions/{id}/stop-step`) and **Stop** (the reply AND the
 *     step); its `over` frame and the reply's end take it away. With no step
 *     (the model is what it waits on) only Stop is offered.
 *  2. A reply that summarized its own older rounds says "Summarized N earlier
 *     steps to fit the model" — live and on the saved reply — and the
 *     Timeline names the summary's own model call so.
 *  3. The session's OPTIONAL cap (decision 8) is set in ⋯ ("Limit each
 *     reply", minutes and/or dollars; both empty removes it), and a reply
 *     that met it says which and offers Keep going (a new turn).
 *  4. The bell rings once for a stalled reply (`build.turn_stalled`), in the
 *     same words as the tray (one-notice fixture).
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
    posts: {} as Record<string, unknown>,
    calls: [] as { method: string; path: string; body?: unknown }[],
  };
});

vi.mock("@/lib/api", () => {
  const answer = (method: string, path: string, body?: unknown) => {
    api.calls.push({ method, path, body });
    const hit = method === "GET" ? api.gets[path] : method === "POST" ? api.posts[path] : undefined;
    const value = typeof hit === "function" ? (hit as (p: string, b?: unknown) => unknown)(path, body) : hit;
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
import {
  capFromForm,
  capMetWords,
  decodeCapMet,
  decodeSessionCap,
  decodeStall,
  decodeSummarized,
  sessionCapWords,
  summarizedWords,
} from "@/lib/buildLongJobs";
import { replyTimeline } from "@/lib/buildTimeline";
import type { BuildMessage } from "@/lib/buildSessions";

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
    push: (event: string, data: unknown, id?: string) =>
      ctl.enqueue(enc.encode(`${id ? `id: ${id}\n` : ""}event: ${event}\ndata: ${JSON.stringify(data)}\n\n`)),
    end: () => ctl.close(),
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

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  api.gets = { "/build/sessions/s1": view(), "/build/accounts": new api.ApiError("no such route", 404) };
  api.posts = {};
  api.calls = [];
  localStorage.clear();
  fetchMock = vi.fn();
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

const STALL_WORDS = "No progress for 10 minutes — waiting on this step: running a command (pytest)";

/** A running reply the screen rejoins, with its frames pushed by the test. */
async function runningReply() {
  api.gets["/build/sessions/s1"] = view({ running: true, turn_id: "tr1" });
  const s = controlledStream();
  fetchMock.mockImplementation(s.fetch);
  renderSession();
  await waitFor(() => expect(fetchMock).toHaveBeenCalled());
  await act(async () => {
    s.push("resume", { resumed: false, running: true });
    s.push("caught_up", { id: "" });
    s.push("round", { round: 0, start: 0 }, "0:0");
    s.push("tool_call", { id: "c1", name: "shell", status: "started", args: { command: "pytest" }, start: 10 }, "1:0");
    await new Promise((r) => setTimeout(r, 20));
  });
  return s;
}

/* -------------------------------------------------- 1. the stall line */

describe("a reply that made no progress for ten minutes", () => {
  it("says the daemon's own sentence with Stop the step, which stops ONLY that step", async () => {
    const s = await runningReply();
    api.posts["/build/sessions/s1/stop-step"] = { ok: true, step: true };
    expect(screen.queryByTestId("harness-stall")).toBeNull();
    await act(async () => {
      s.push("stall", { words: STALL_WORDS, minutes: 10, step: { id: "c1", name: "shell" } }, "2:0");
      await new Promise((r) => setTimeout(r, 20));
    });
    const line = await screen.findByTestId("harness-stall");
    expect(screen.getByTestId("harness-stall-words")).toHaveTextContent(STALL_WORDS);
    expect(line).toHaveAttribute("role", "status");
    fireEvent.click(screen.getByTestId("harness-stop-step"));
    await waitFor(() =>
      expect(api.calls.find((c) => c.path === "/build/sessions/s1/stop-step")).toBeTruthy(),
    );
    const call = api.calls.find((c) => c.path === "/build/sessions/s1/stop-step")!;
    expect(call.method).toBe("POST");
    expect(call.body).toEqual({ step_id: "c1", turn_id: "tr1" });
    // Only the step: the reply's own Stop was never asked.
    expect(api.calls.find((c) => c.path === "/chat/turns/tr1/stop")).toBeUndefined();
    expect(await screen.findByTestId("harness-stall-note")).toHaveTextContent(
      "Stopped that step. The reply carries on without it.",
    );
    // The next progress ends the episode: the line goes.
    await act(async () => {
      s.push("stall", { over: true }, "3:0");
      s.push("tool_call", { id: "c1", name: "shell", status: "finished", ok: false, start: 10, ms: 5 }, "4:0");
      await new Promise((r) => setTimeout(r, 20));
    });
    await waitFor(() => expect(screen.queryByTestId("harness-stall")).toBeNull());
    await act(async () => {
      s.push("done", { reply: "Moved on." }, "5:0");
      s.end();
    });
  });

  it("Stop on the line stops the reply AND the hung step, so it ends at once", async () => {
    const s = await runningReply();
    api.posts["/build/sessions/s1/stop-step"] = { ok: true, step: true };
    await act(async () => {
      s.push("stall", { words: STALL_WORDS, minutes: 10, step: { id: "c1", name: "shell" } }, "2:0");
      await new Promise((r) => setTimeout(r, 20));
    });
    fireEvent.click(await screen.findByTestId("harness-stall-stop"));
    await waitFor(() =>
      expect(api.calls.find((c) => c.path === "/build/sessions/s1/stop-step")).toBeTruthy(),
    );
    expect(api.calls.find((c) => c.path === "/build/sessions/s1/stop-step")!.body).toEqual({
      step_id: "c1",
      turn_id: "tr1",
      reply: true,
    });
    await act(async () => {
      s.push("done", { reply: "" }, "3:0");
      s.end();
    });
  });

  it("waiting on the model offers only Stop (there is no step to stop)", async () => {
    const s = await runningReply();
    await act(async () => {
      s.push("stall", { words: "No progress for 10 minutes — waiting on Claude Code's answer", minutes: 10 }, "2:0");
      await new Promise((r) => setTimeout(r, 20));
    });
    await screen.findByTestId("harness-stall");
    expect(screen.queryByTestId("harness-stop-step")).toBeNull();
    fireEvent.click(screen.getByTestId("harness-stall-stop"));
    await waitFor(() => expect(api.calls.find((c) => c.path === "/chat/turns/tr1/stop")).toBeTruthy());
    expect(api.calls.find((c) => c.path === "/build/sessions/s1/stop-step")).toBeUndefined();
    await act(async () => {
      s.push("done", { reply: "" }, "3:0");
      s.end();
    });
  });

  it("the reply's end takes the line away", async () => {
    const s = await runningReply();
    await act(async () => {
      s.push("stall", { words: STALL_WORDS, minutes: 10, step: { id: "c1", name: "shell" } }, "2:0");
      await new Promise((r) => setTimeout(r, 20));
    });
    await screen.findByTestId("harness-stall");
    api.gets["/build/sessions/s1"] = view({
      messages: [{ role: "user", content: "x", turnId: "tr1" }, { role: "assistant", content: "Done.", turnId: "tr1" }],
    });
    await act(async () => {
      s.push("done", { reply: "Done." }, "3:0");
      s.end();
    });
    await waitFor(() => expect(screen.queryByTestId("harness-stall")).toBeNull());
  });
});

/* ------------------------------------------- 2. the summary mid-reply */

describe("a reply that summarized its own older rounds", () => {
  it("says so live, and on the saved reply", async () => {
    const s = await runningReply();
    await act(async () => {
      s.push("summarized", { steps: 42, covers: 90, stripped: 1, times: 1, words: summarizedWords({ steps: 42 }) }, "2:0");
      await new Promise((r) => setTimeout(r, 20));
    });
    expect(await screen.findByTestId("harness-summarized")).toHaveTextContent(
      "Summarized 42 earlier steps to fit the model",
    );
    api.gets["/build/sessions/s1"] = view({
      messages: [
        { role: "user", content: "x", turnId: "tr1" },
        { role: "assistant", content: "Done.", turnId: "tr1", summarized: { steps: 42, times: 1 } },
      ],
    });
    await act(async () => {
      s.push("done", { reply: "Done." }, "3:0");
      s.end();
    });
    await waitFor(() =>
      expect(screen.getAllByTestId("harness-summarized").map((n) => n.textContent)).toEqual([
        "Summarized 42 earlier steps to fit the model",
      ]),
    );
  });

  it("the Timeline names the summary's own model call", () => {
    const line = {
      role: "assistant",
      content: "Done.",
      turnId: "t1",
      timing: { startedAt: 1, endedAt: 10_000, ms: 9_000 },
      calls: [
        { start: 0, ms: 1000, provider: "anthropic", model: "m", usage: { input_tokens: 5, output_tokens: 1 } },
        { start: 1000, ms: 500, provider: "anthropic", model: "m", kind: "summary", summarized: 12 },
        { start: 1500, ms: 1000, provider: "anthropic", model: "m", kind: "bogus" },
      ],
    } as unknown as BuildMessage;
    const t = replyTimeline(line)!;
    expect(t.calls[1].kind).toBe("summary");
    expect(t.calls[1].summarized).toBe(12);
    expect(t.calls[2].kind).toBeUndefined();
  });
});

/* -------------------------------------------------- 3. the optional cap */

describe("the session's optional cap", () => {
  it("is set in the ⋯ menu — minutes and dollars — and saved on the session", async () => {
    renderSession();
    fireEvent.click(await screen.findByLabelText("Session actions"));
    fireEvent.click(screen.getByTestId("harness-cap-open"));
    expect(screen.getByTestId("harness-cap-form")).toHaveTextContent("No limit on a reply's time or cost.");
    fireEvent.change(screen.getByTestId("harness-cap-minutes"), { target: { value: "30" } });
    fireEvent.change(screen.getByTestId("harness-cap-usd"), { target: { value: "2.5" } });
    fireEvent.click(screen.getByTestId("harness-cap-save"));
    await waitFor(() => expect(api.calls.find((c) => c.method === "PATCH")).toBeTruthy());
    const call = api.calls.find((c) => c.method === "PATCH")!;
    expect(call.path).toBe("/build/sessions/s1");
    expect(call.body).toEqual({ cap: { minutes: 30, usd: 2.5 } });
    await waitFor(() => expect(screen.queryByTestId("harness-cap-form")).toBeNull());
  });

  it("refuses junk in words and removes the cap when both boxes are empty", async () => {
    api.gets["/build/sessions/s1"] = view({ setup: { cap: { minutes: 45 } } });
    renderSession();
    fireEvent.click(await screen.findByLabelText("Session actions"));
    expect(screen.getByTestId("harness-cap-open")).toHaveTextContent("Change the reply limit");
    fireEvent.click(screen.getByTestId("harness-cap-open"));
    expect(screen.getByTestId("harness-cap-form")).toHaveTextContent("Each reply stops at 45 minutes");
    fireEvent.change(screen.getByTestId("harness-cap-minutes"), { target: { value: "0" } });
    fireEvent.click(screen.getByTestId("harness-cap-save"));
    expect(await screen.findByTestId("harness-cap-error")).toHaveTextContent("Minutes must be a whole number");
    expect(api.calls.find((c) => c.method === "PATCH")).toBeUndefined();
    fireEvent.change(screen.getByTestId("harness-cap-minutes"), { target: { value: "" } });
    fireEvent.click(screen.getByTestId("harness-cap-save"));
    await waitFor(() => expect(api.calls.find((c) => c.method === "PATCH")).toBeTruthy());
    expect(api.calls.find((c) => c.method === "PATCH")!.body).toEqual({ cap: null });
  });

  it("a reply that met it says which and offers Keep going, a new turn", async () => {
    api.gets["/build/sessions/s1"] = view({
      messages: [
        { role: "user", content: "build it", turnId: "t1" },
        { role: "assistant", content: "Reached the limit; next I will…", turnId: "t1", capMet: { kind: "time", limit: 30 } },
      ],
    });
    fetchMock.mockImplementation(async () =>
      new Response(new ReadableStream({ start: (c) => c.close() }), { status: 200 }),
    );
    renderSession();
    const line = await screen.findByTestId("harness-cap-met");
    expect(line).toHaveTextContent("This reply stopped at this session's 30-minute time limit.");
    expect(screen.queryByTestId("harness-step-limit")).toBeNull();
    fireEvent.click(screen.getByTestId("harness-cap-keep-going"));
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("http://d/build/sessions/s1/turn");
    expect(JSON.parse(String(init.body)).text).toBe("continue");
  });
});

/* -------------------------------------------------- the words */

describe("lib/buildLongJobs", () => {
  it("decodes what the daemon sends and drops junk", () => {
    expect(decodeStall({ over: true })).toBe("over");
    expect(decodeStall({ words: " x ", step: { id: "c1", name: "shell" } })).toEqual({
      words: "x",
      step: { id: "c1", name: "shell" },
    });
    expect(decodeStall({ words: "x", step: { name: "shell" } })).toEqual({ words: "x", step: null });
    expect(decodeStall({ words: "" })).toBeNull();
    expect(decodeSummarized({ steps: 3 })).toEqual({ steps: 3, times: 1 });
    expect(decodeSummarized({ steps: -1 })).toBeNull();
    expect(decodeCapMet({ kind: "cost", limit: 5 })).toEqual({ kind: "cost", limit: 5 });
    expect(decodeCapMet({ kind: "steps", limit: 5 })).toBeNull();
    expect(decodeSessionCap({ minutes: 0, usd: 0 })).toBeNull();
    expect(decodeSessionCap({ minutes: 30, usd: "2" })).toEqual({ minutes: 30 });
  });

  it("says the cap in plain words", () => {
    expect(capMetWords({ kind: "cost", limit: 5 })).toBe("This reply stopped at this session's $5.00 cost limit (list price).");
    expect(capMetWords({ kind: "time", limit: 120 })).toBe("This reply stopped at this session's 2-hour time limit.");
    expect(sessionCapWords({ minutes: 30, usd: 2.5 })).toBe(
      "Each reply stops at 30 minutes or $2.50 list price, whichever comes first.",
    );
    expect(summarizedWords({ steps: 1 })).toBe("Summarized 1 earlier step to fit the model");
    expect(capFromForm("", "")).toEqual({ cap: null });
    expect(capFromForm("15", "$1.234")).toEqual({ cap: { minutes: 15, usd: 1.23 } });
    expect(capFromForm("1.5", "")).toHaveProperty("error");
    expect(capFromForm("", "0")).toHaveProperty("error");
  });
});
