/**
 * Harness gaps REVIEW — two defects in the coding session's fixes, each red
 * on the doer's code first.
 *
 * 1. RETRY RE-READ THE SAVED WORDS AS SOMETHING TYPED (fix 2). Retry sent the
 *    failed reply's question back through the composer's "/" reading, so:
 *    - a Claude Code command question ("/review app.py", saved with its
 *      `command`) went as plain words "/review app.py" whenever the
 *      session's command list could not be read — the command was lost;
 *    - a skill question whose words begin with a "/" ("/perf" + "/model is
 *      slow after the update") ran the session's OWN /model command instead
 *      (the model menu opened, nothing was sent).
 *    Retry now sends what the question CARRIED: its words as they are, its
 *    command by name, its skill, files and uploads.
 *
 * 2. THE PLAN TAB SHOWED A STALE PLAN OVER A NEWER APPROVED ONE (fix 5). The
 *    pane took any reply's saved plan before the session's approved plan, so
 *    a plan approved AFTER an older reply kept one (switch to plan mode, the
 *    model writes a new plan, Allow) never showed: the tab kept the old
 *    reply's steps. Newest wins now — a reply's plan only when that reply
 *    ended at or after the approval (the approving turn's own reply, or a
 *    later one), else the approved plan.
 *
 * The mocks are harness-gaps-failure-receipt-v1389's.
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
    health: { default_provider: "claude-cli", default_model: "opus", providers: [] },
    refresh: () => {},
  }),
}));
vi.mock("@/lib/useModels", () => ({ useModels: () => ({ models: [], usable: [], loading: false }) }));
vi.mock("@/components/terminal/FilesPanel", () => ({
  FilesPanel: ({ folder }: { folder: string | null }) => <div data-testid="files-panel">{folder}</div>,
}));

import { HarnessSession } from "@/components/harness/HarnessSession";
import { panePlan, type SessionTerminalApi } from "@/components/harness/HarnessPanes";
import { retryItemFor } from "@/lib/buildRetry";
import { decodeSessionView, type BuildMessage, type BuildSessionView } from "@/lib/buildSessions";

const FOLDER = "C:\\work\\shop";
const NET = { timeout: 5000 };
const terminal: SessionTerminalApi = {
  find: () => null,
  open: async () => null,
  render: () => <div data-testid="term-body" />,
  close: () => {},
};

function sessionView(messages: Record<string, unknown>[]) {
  return {
    id: "s1",
    title: "Review",
    updated_at: "2026-10-25T10:00:00",
    running: false,
    waiting: false,
    archived: false,
    folder: FOLDER,
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

const FAILED = {
  role: "assistant",
  content: "",
  turnId: "t1",
  error: "Claude Code couldn't finish this answer. Press Retry. If it happens again, pick another model for this chat.",
  errorTitle: "Claude Code couldn't finish this answer",
};

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
    "/build/accounts": new api.ApiError("no such route", 404),
    "/skills": { skills: [] },
    "/build/sessions/s1/skills": { skills: [], note: "" },
    // The session's command list cannot be read right now (a busy daemon,
    // an older one): the composer would read "/review …" as plain words.
    "/build/sessions/s1/commands": new api.ApiError("busy", 503),
  };
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

const turnBodies = () =>
  (fetchMock.mock.calls as unknown as [string, RequestInit][])
    .filter(([url]) => String(url).endsWith("/build/sessions/s1/turn"))
    .map(([, init]) => JSON.parse(String(init.body)));

function renderSession() {
  return render(
    <HarnessSession threadId="s1" terminal={terminal} listRunning={false} onChanged={() => {}} onGone={() => {}} />,
  );
}

describe("Retry sends what the question carried, never a new reading of its words", () => {
  it("a Claude Code command question keeps its command, even when the command list cannot be read", async () => {
    api.gets["/build/sessions/s1"] = sessionView([
      { role: "user", content: "/review app.py", command: "review", turnId: "t1", at: "2026-10-25T10:05:00" },
      FAILED,
    ]);
    renderSession();
    fireEvent.click(await screen.findByTestId("harness-retry", {}, NET));
    await waitFor(() => expect(turnBodies()).toHaveLength(1), NET);
    const body = turnBodies()[0];
    expect(body.command).toBe("review");
    expect(body.text).toBe("app.py");
  });

  it("a skill question whose words start with '/model' is sent as words, never run as the session's /model", async () => {
    api.gets["/build/sessions/s1"] = sessionView([
      {
        role: "user",
        content: "/model is slow after the update, find why",
        skill: "perf",
        skillSource: "yours",
        turnId: "t1",
        at: "2026-10-25T10:05:00",
      },
      FAILED,
    ]);
    renderSession();
    fireEvent.click(await screen.findByTestId("harness-retry", {}, NET));
    await waitFor(() => expect(turnBodies()).toHaveLength(1), NET);
    const body = turnBodies()[0];
    expect(body.text).toBe("/model is slow after the update, find why");
    expect(body.skill).toBe("perf");
    expect(body.command).toBeUndefined();
  });

  it("CONTROL: the composer still reads a typed '/review app.py' as the command when the list has it", async () => {
    api.gets["/build/sessions/s1/commands"] = {
      commands: [{ name: "review", description: "Review a file", source: "project" }],
    };
    api.gets["/build/sessions/s1"] = sessionView([]);
    renderSession();
    await screen.findByTestId("harness-empty", {}, NET);
    fireEvent.change(screen.getByTestId("harness-input"), { target: { value: "/review app.py" } });
    // Enter waits while the "/" lists are read (v1.379.0): let them land.
    await waitFor(
      () => expect(api.calls.some((c) => c.method === "GET" && c.path === "/build/sessions/s1/commands")).toBe(true),
      NET,
    );
    await act(async () => {});
    fireEvent.keyDown(screen.getByTestId("harness-input"), { key: "Enter" });
    await waitFor(() => expect(turnBodies()).toHaveLength(1), NET);
    expect(turnBodies()[0].command).toBe("review");
    expect(turnBodies()[0].text).toBe("app.py");
  });

  it("lib/buildRetry: the command's words are what followed its name; a question with no command carries none", () => {
    const m = (x: Record<string, unknown>) => x as unknown as BuildMessage;
    const cmd = [m({ role: "user", content: "/review app.py", command: "review", turnId: "t1" }), m(FAILED)];
    expect(retryItemFor(cmd, 1, cmd, FOLDER)).toEqual({ text: "app.py", resend: { command: "review" } });
    const bare = [m({ role: "user", content: "/init", command: "init", turnId: "t1" }), m(FAILED)];
    expect(retryItemFor(bare, 1, bare, FOLDER)).toEqual({ text: "", resend: { command: "init" } });
    const plain = [m({ role: "user", content: "/etc/hosts — what is in it?", turnId: "t1" }), m(FAILED)];
    expect(retryItemFor(plain, 1, plain, FOLDER)).toEqual({ text: "/etc/hosts — what is in it?", resend: { command: "" } });
  });
});

describe("the Plan tab: the newest plan wins, a reply's or the approved one", () => {
  function view(messages: Record<string, unknown>[], plan: Record<string, unknown> | null): BuildSessionView {
    const v = decodeSessionView({
      id: "s1",
      title: "Shop",
      folder: FOLDER,
      folder_name: "shop",
      session_id: "build:s1",
      messages,
      plan,
    });
    if (!v) throw new Error("bad fixture");
    return v;
  }
  const OLD_REPLY = {
    role: "assistant",
    content: "Started.",
    turnId: "t1",
    at: "2026-10-25T10:10:00.000000+00:00",
    plan: { steps: [{ goal: "the old plan", state: "done" }], total: 1 },
  };
  const APPROVED = {
    text: "1. Add the cart\n2. Add the checkout",
    steps: [
      { step: "Add the cart", status: "pending" },
      { step: "Add the checkout", status: "pending" },
    ],
    approved_at: "2026-10-25T10:20:00.000000+00:00",
  };

  it("a plan approved AFTER an older reply kept one shows the approved plan", () => {
    const approving = { role: "assistant", content: "Plan approved — ready.", turnId: "t2", at: "2026-10-25T10:20:05.000000+00:00" };
    const goals = panePlan(view([OLD_REPLY, approving], APPROVED), null)?.steps.map((s) => s.goal);
    expect(goals).toEqual(["Add the cart", "Add the checkout"]);
  });

  it("the approving turn's own reply (and any later one) with a plan keeps the reply's plan — what the reply shows", () => {
    const own = {
      role: "assistant",
      content: "Here is the plan.",
      turnId: "t2",
      at: "2026-10-25T10:20:05.000000+00:00",
      plan: { steps: [{ goal: "step one", state: "waiting" }, { goal: "step two", state: "waiting" }, { goal: "step three", state: "waiting" }], total: 3 },
    };
    const goals = panePlan(view([OLD_REPLY, own], APPROVED), null)?.steps.map((s) => s.goal);
    expect(goals).toEqual(["step one", "step two", "step three"]);
  });

  it("CONTROL: an approved plan OLDER than the newest reply's plan does not win", () => {
    const later = { ...OLD_REPLY, turnId: "t3", at: "2026-10-25T10:30:00.000000+00:00" };
    const goals = panePlan(view([later], APPROVED), null)?.steps.map((s) => s.goal);
    expect(goals).toEqual(["the old plan"]);
  });
});
