/**
 * v1.422.0 (Wave 2 "Never lose work", B1) — on the REAL HarnessSession (the
 * mocks are harness-gaps-failure-receipt-v1389's):
 *  - a failed reply whose rounds the daemon KEPT offers "Continue from where
 *    it stopped" first; pressing it sends the session's turn route
 *    `continue_turn` = that reply's turn (nothing typed, nothing goes in the
 *    box), and Retry still sends the question again;
 *  - while a round waits out a passing provider error, the live line says the
 *    daemon's words (`provider_wait`), and the next try's "trying" frame
 *    takes the line away.
 */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

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
    health: { default_provider: "claude-cli", default_model: "opus", providers: [] },
    refresh: () => {},
  }),
}));
vi.mock("@/lib/useModels", () => ({ useModels: () => ({ models: [], usable: [], loading: false }) }));
vi.mock("@/components/terminal/FilesPanel", () => ({
  FilesPanel: ({ folder }: { folder: string | null }) => <div data-testid="files-panel">{folder}</div>,
}));

import { HarnessSession } from "@/components/harness/HarnessSession";
import type { SessionTerminalApi } from "@/components/harness/HarnessPanes";
import { CONTINUE_WORDS } from "@/lib/buildRetry";

const NET = { timeout: 5000 };
const terminal: SessionTerminalApi = {
  find: () => null,
  open: async () => null,
  render: () => <div data-testid="term-body" />,
  close: () => {},
};

function view(messages: Record<string, unknown>[]) {
  return {
    id: "s1",
    title: "Add a cart",
    updated_at: "2026-11-01T10:00:00",
    running: false,
    waiting: false,
    archived: false,
    folder: "C:\\work\\shop",
    folder_name: "shop",
    is_project: false,
    project_id: null,
    mode: "act",
    plan: null,
    setup: { provider: "claude-cli", model: "opus" },
    messages,
    session_id: "build:s1",
    queued: [],
    turn_id: "",
  };
}

const QUESTION = { role: "user", content: "Make three files", turnId: "t1", at: "2026-11-01T10:05:00" };
const FAILED_KEPT = {
  role: "assistant",
  content: "",
  turnId: "t1",
  error: "Claude Code is busy. Iron Jarvis tried 5 times over 1 min 50 s. Try again shortly, or pick another model for this session.",
  errorTitle: "Claude Code is busy",
  canContinue: true,
};

/** A stream the test feeds frame by frame. */
class Feed {
  private ctl: ReadableStreamDefaultController<Uint8Array> | null = null;
  private enc = new TextEncoder();
  response(): Response {
    const body = new ReadableStream<Uint8Array>({
      start: (c) => {
        this.ctl = c;
      },
    });
    return new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } });
  }
  frame(event: string, data: unknown): void {
    this.ctl?.enqueue(this.enc.encode(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`));
  }
  end(): void {
    this.ctl?.close();
  }
}

let fetchMock: ReturnType<typeof vi.fn>;
let feed: Feed;

beforeEach(() => {
  api.gets = {
    "/build/accounts": new api.ApiError("no such route", 404),
    "/skills": { skills: [] },
    "/build/sessions/s1/skills": { skills: [], note: "" },
  };
  api.calls = [];
  localStorage.clear();
  feed = new Feed();
  fetchMock = vi.fn(async () => feed.response());
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const turnBodies = () =>
  (fetchMock.mock.calls as unknown as [string, RequestInit][])
    .filter(([url]) => String(url).endsWith("/build/sessions/s1/turn"))
    .map(([, init]) => JSON.parse(String(init.body)));

function renderSession() {
  return render(
    <HarnessSession threadId="s1" terminal={terminal} listRunning={false} onChanged={() => {}} onGone={() => {}} />,
  );
}

describe("Continue from where it stopped, on the real session", () => {
  it("is offered first, and sends continue_turn for that reply (nothing typed)", async () => {
    api.gets["/build/sessions/s1"] = view([QUESTION, FAILED_KEPT]);
    renderSession();
    const press = await screen.findByTestId("harness-continue", {}, NET);
    expect(press.textContent).toContain(CONTINUE_WORDS);
    expect(screen.getByTestId("harness-retry")).toBeTruthy();
    fireEvent.click(press);
    await waitFor(() => expect(turnBodies()).toHaveLength(1), NET);
    const body = turnBodies()[0];
    expect(body.continue_turn).toBe("t1");
    expect(body.text).toBe("");
    expect(body.files).toBeUndefined();
    expect(body.attachments).toBeUndefined();
    feed.frame("done", { reply: "ok" });
    feed.end();
    // The box was left alone.
    expect((screen.getByTestId("harness-input") as HTMLTextAreaElement).value).toBe("");
  });

  it("a failed reply with no kept rounds offers Retry alone", async () => {
    api.gets["/build/sessions/s1"] = view([QUESTION, { ...FAILED_KEPT, canContinue: undefined }]);
    renderSession();
    await screen.findByTestId("harness-retry", {}, NET);
    expect(screen.queryByTestId("harness-continue")).toBeNull();
  });
});

describe("the live line while a round waits out a passing error", () => {
  it("says the daemon's words, and the next try takes them away", async () => {
    api.gets["/build/sessions/s1"] = view([QUESTION, FAILED_KEPT]);
    renderSession();
    fireEvent.click(await screen.findByTestId("harness-continue", {}, NET));
    await waitFor(() => expect(turnBodies()).toHaveLength(1), NET);
    const words = "Claude Code is busy (overloaded). Trying again in 30 s, attempt 2 of 5.";
    feed.frame("round", { round: 0 });
    feed.frame("provider_wait", { state: "waiting", words, attempt: 2, of: 5, wait_s: 30, why: "overloaded" });
    await waitFor(() => expect(screen.getByTestId("harness-provider-wait").textContent).toContain(words), NET);
    feed.frame("provider_wait", { state: "trying", attempt: 2, of: 5 });
    await waitFor(() => expect(screen.queryByTestId("harness-provider-wait")).toBeNull(), NET);
    feed.frame("done", { reply: "ok" });
    feed.end();
  });
});
