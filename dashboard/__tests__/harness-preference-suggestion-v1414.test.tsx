/**
 * v1.414.0 — a correction repeated in a coding session (Chat › Code) is
 * offered under the reply as a standing preference: the chat's OWN quiet
 * line (components/chat/PreferenceSuggestion: Keep · Edit · Not this), on
 * the REAL HarnessSession (the mocks are harness-gaps-failure-receipt-v1389's).
 * The reply line keeps the question (`suggestion`, a line key on both sides)
 * and the daemon folds in its CURRENT answer, so a reopened session shows
 * "Remembered: …", nothing for a gone row, and Keep posts the chat's route.
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
    gets: {} as Record<string, unknown | ((path: string) => unknown)>,
    calls: [] as { method: string; path: string; body?: unknown }[],
    postAnswers: {} as Record<string, unknown>,
  };
});

vi.mock("@/lib/api", () => {
  const answer = (method: string, path: string, body?: unknown) => {
    api.calls.push({ method, path, body });
    const hit = method === "GET" ? api.gets[path] : api.postAnswers[`${method} ${path}`];
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
import { decodeSessionView } from "@/lib/buildSessions";

const FOLDER = "C:\\work\\shop";
const NET = { timeout: 5000 };
const terminal: SessionTerminalApi = {
  find: () => null,
  open: async () => null,
  render: () => <div data-testid="term-body" />,
  close: () => {},
};

function view(messages: Record<string, unknown>[], over: Record<string, unknown> = {}) {
  return {
    id: "s1",
    title: "Add a cart",
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
    ...over,
  };
}

const QUESTION = { role: "user", content: "stop using tables", turnId: "t2", at: "2026-10-30T10:05:00" };
const SUGGESTION = {
  id: "lesson_9",
  text: "Don't use tables.",
  count: 2,
  quotes: [
    { quote: "stop using tables", at: "2026-10-30T10:05:00", where: "code" },
    { quote: "no tables please", at: "2026-10-30T10:01:00", where: "code" },
  ],
  since: "2026-10-30T10:01:00",
};
const reply = (suggestion: Record<string, unknown> | undefined) => ({
  role: "assistant",
  content: "Done, as a list.",
  turnId: "t2",
  route: { provider: "claude-cli", model: "opus" },
  ...(suggestion ? { suggestion } : {}),
});

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  api.gets = {
    "/build/accounts": new api.ApiError("no such route", 404),
    "/skills": { skills: [] },
    "/build/sessions/s1/skills": { skills: [], note: "" },
  };
  api.calls = [];
  api.postAnswers = {};
  localStorage.clear();
  fetchMock = vi.fn(async () => new Response("", { status: 200 }));
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

describe("a coding session offers a repeated correction as a standing preference", () => {
  it("the line key survives the view's whitelist", () => {
    const v = decodeSessionView(view([QUESTION, reply(SUGGESTION)]));
    expect(v?.messages.at(-1)?.suggestion).toEqual(SUGGESTION);
  });

  it("an open question shows the chat's Keep line, and Keep posts the chat's route", async () => {
    api.gets["/build/sessions/s1"] = view([QUESTION, reply(SUGGESTION)]);
    api.postAnswers["POST /memory/preferences/lesson_9/keep"] = { id: "lesson_9", text: "Don't use tables." };
    renderSession();
    const line = await screen.findByTestId("pref-suggestion", {}, NET);
    expect(line.getAttribute("data-state")).toBe("open");
    expect(line.textContent).toContain("Don't use tables");
    fireEvent.click(within(line).getByRole("button", { name: /^keep$/i }));
    await waitFor(() => expect(screen.getByTestId("pref-suggestion-kept").textContent).toContain("Don't use tables."), NET);
    expect(api.calls.some((c) => c.method === "POST" && c.path === "/memory/preferences/lesson_9/keep")).toBe(true);
  });

  it("a reopened session shows the answer the daemon folded in: kept reads 'Remembered'", async () => {
    api.gets["/build/sessions/s1"] = view([QUESTION, reply({ ...SUGGESTION, state: "kept", text: "Never use tables." })]);
    renderSession();
    const kept = await screen.findByTestId("pref-suggestion-kept", {}, NET);
    expect(kept.textContent).toContain("Never use tables.");
  });

  it("a gone row renders nothing; a reply with no suggestion renders no line", async () => {
    api.gets["/build/sessions/s1"] = view([QUESTION, reply({ ...SUGGESTION, state: "gone" })]);
    renderSession();
    await screen.findByText("Done, as a list.", {}, NET);
    expect(screen.queryByTestId("pref-suggestion")).toBeNull();
    cleanup();
    api.gets["/build/sessions/s1"] = view([QUESTION, reply(undefined)]);
    renderSession();
    await screen.findByText("Done, as a list.", {}, NET);
    expect(screen.queryByTestId("pref-suggestion")).toBeNull();
  });
});
