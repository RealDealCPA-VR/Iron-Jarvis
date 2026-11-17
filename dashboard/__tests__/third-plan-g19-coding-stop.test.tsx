/**
 * Third north-star fix plan, group 19, decision G9 (finding RB1) — on the
 * REAL HarnessSession (the mocks are harness-continue-session-v1422-0's):
 * a reply the user STOPPED, whose wind-down Iron Jarvis closing cut off, is
 * never carried on by itself at the next start. The daemon writes its line as
 * stopped (`sessions.STOPPED_LINE`) with `canContinue` when its rounds were
 * kept, and the page:
 *  - says it was stopped, in the daemon's words;
 *  - offers "Continue from where it stopped" as the user's own press (and
 *    Retry beside it) — and sends nothing until it is pressed;
 *  - on the press, sends the session's turn route `continue_turn` = that
 *    reply's turn, nothing typed;
 *  - with no rounds kept, offers Retry alone.
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
import { CONTINUE_WORDS, RETRY_WORDS } from "@/lib/buildRetry";

const NET = { timeout: 5000 };
const terminal: SessionTerminalApi = {
  find: () => null,
  open: async () => null,
  render: () => <div data-testid="term-body" />,
  close: () => {},
};

/** The daemon's own sentence for such a reply (`sessions.STOPPED_LINE`). */
const STOPPED_LINE = "You stopped this reply. Iron Jarvis did not carry it on by itself.";

function view(messages: Record<string, unknown>[]) {
  return {
    id: "s1",
    title: "Write the notes",
    updated_at: "2026-11-17T10:00:00",
    running: false,
    waiting: false,
    archived: false,
    folder: "C:\\work\\notes",
    folder_name: "notes",
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

const QUESTION = { role: "user", content: "Make three files", turnId: "t1", at: "2026-11-17T10:05:00" };
const STOPPED_KEPT = {
  role: "assistant",
  content: "Wrote one.txt and two.txt.",
  turnId: "t1",
  interrupted: true,
  error: STOPPED_LINE,
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

describe("a reply you stopped, after Iron Jarvis started again", () => {
  it("says it was stopped and offers Continue from where it stopped as your own press", async () => {
    api.gets["/build/sessions/s1"] = view([QUESTION, STOPPED_KEPT]);
    renderSession();
    const press = await screen.findByTestId("harness-continue", {}, NET);
    expect(press.textContent).toContain(CONTINUE_WORDS);
    expect(screen.getByTestId("harness-retry").textContent).toContain(RETRY_WORDS);
    expect(screen.getByTestId("harness-reply-error").textContent).toContain(STOPPED_LINE);
    // What it had done stays on screen.
    expect(screen.getByText("Wrote one.txt and two.txt.")).toBeTruthy();
    // Nothing starts by itself: no turn was sent before the press.
    expect(turnBodies()).toHaveLength(0);

    fireEvent.click(press);
    await waitFor(() => expect(turnBodies()).toHaveLength(1), NET);
    const body = turnBodies()[0];
    expect(body.continue_turn).toBe("t1");
    expect(body.text).toBe("");
    expect(body.files).toBeUndefined();
    expect(body.attachments).toBeUndefined();
    feed.frame("done", { reply: "All three are there." });
    feed.end();
    expect((screen.getByTestId("harness-input") as HTMLTextAreaElement).value).toBe("");
  });

  it("with no work kept to continue from, offers Retry alone (and sends nothing by itself)", async () => {
    api.gets["/build/sessions/s1"] = view([QUESTION, { ...STOPPED_KEPT, canContinue: undefined }]);
    renderSession();
    await screen.findByTestId("harness-retry", {}, NET);
    expect(screen.getByTestId("harness-reply-error").textContent).toContain(STOPPED_LINE);
    expect(screen.queryByTestId("harness-continue")).toBeNull();
    expect(turnBodies()).toHaveLength(0);
  });
});
