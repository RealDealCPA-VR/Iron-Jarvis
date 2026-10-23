/**
 * v1.378.0 — a provider's SAFETY REFUSAL in a Build session (the IJ harness).
 * The mocks and fixtures are ij-harness-slash-at-v1373's (copied, not
 * imported: a test file is not a module other tests import).
 *
 * The daemon marks the reply line of a refused turn `errorKind: "refusal"`
 * (routes/build `_ReplyWatch.lines`) and words it for a Build session
 * ("Rewind to before the message that set it off, or start a new session").
 * On the NEWEST reply the session offers ONE press, "Choose where to rewind",
 * which opens the "/rewind" list of the session's own messages (each with
 * its two-press Rewind). Never a Retry: the classifier judges the whole
 * conversation, so the same conversation is declined again.
 *
 * Controls: an older refused reply (not the newest) offers no press; a
 * failure that is not a refusal offers none either.
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
import { REWIND_PICK_WORDS } from "@/lib/failureActions";

const FOLDER = "C:\\work\\shop";
const terminal: SessionTerminalApi = {
  find: () => null,
  open: async () => null,
  render: () => <div data-testid="term-body" />,
  close: () => {},
};

/** The Build line exactly as the daemon writes it (title. fix). */
const REFUSED_LINE = {
  role: "assistant",
  content: "",
  turnId: "t1",
  error:
    "Claude Code declined to answer this conversation. Its safety check on computer security flagged " +
    "something in this conversation, so every new message here will be declined too. Rewind to before " +
    "the message that set it off, or start a new session.",
  errorKind: "refusal",
};

function view(messages: Record<string, unknown>[]) {
  return {
    id: "s1",
    title: "Env check",
    updated_at: "2026-10-23T10:00:00",
    running: false,
    waiting: false,
    archived: false,
    folder: FOLDER,
    folder_name: "shop",
    is_project: false,
    project_id: null,
    mode: "act",
    plan: null,
    setup: {},
    messages,
    session_id: "build:s1",
    queued: [],
    turn_id: "",
  };
}

const CONVERSATION = [
  { role: "user", content: "Print the shell's environment", turnId: "t0", at: "2026-10-23T10:00:00" },
  { role: "assistant", content: "Here it is.", turnId: "t0" },
  { role: "user", content: "Say hello", turnId: "t1", at: "2026-10-23T10:05:00" },
  REFUSED_LINE,
];

beforeEach(() => {
  api.gets = {
    "/build/accounts": new api.ApiError("no such route", 404),
    "/skills": { skills: [] },
    "/build/sessions/s1/skills": { skills: [], note: "" },
    "/build/sessions/s1/rewind": {
      checkpoints: [
        { turn_id: "t0", question: "Print the shell's environment", at: "2026-10-23T10:00:00", continuation: false, files: 0, commands: 1 },
        { turn_id: "t1", question: "Say hello", at: "2026-10-23T10:05:00", continuation: false, files: 0, commands: 0 },
      ],
    },
  };
  api.calls = [];
  api.postAnswers = {};
  localStorage.clear();
  vi.stubGlobal("fetch", vi.fn(async () => new Response("", { status: 200 })));
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

const NET = { timeout: 5000 };

describe("v1.378.0: a refused Build turn offers Rewind, never Retry", () => {
  it("the newest refused reply says the sentence and its press opens the /rewind list", async () => {
    api.gets["/build/sessions/s1"] = view(CONVERSATION);
    renderSession();
    const line = await screen.findByTestId("harness-reply-error", {}, NET);
    expect(line).toHaveTextContent("Rewind to before the message that set it off, or start a new session.");
    expect(screen.queryAllByRole("button", { name: /Retry/ })).toHaveLength(0);
    const press = screen.getByTestId("harness-refusal-rewind");
    expect(press).toHaveTextContent(REWIND_PICK_WORDS);
    expect(screen.queryByTestId("harness-rewind-list")).toBeNull();
    fireEvent.click(press);
    const list = await screen.findByTestId("harness-rewind-list", {}, NET);
    await waitFor(() => expect(within(list).getAllByTestId("harness-rewind-point")).toHaveLength(2), NET);
    // The press only OPENS the list: nothing rewound, nothing sent.
    expect(api.calls.some((c) => c.method === "POST" && c.path.endsWith("/rewind"))).toBe(false);
  });

  it("control: an OLDER refused reply has no press (only the newest one offers it)", async () => {
    api.gets["/build/sessions/s1"] = view([
      ...CONVERSATION,
      { role: "user", content: "Something else", turnId: "t2", at: "2026-10-23T10:09:00" },
      { role: "assistant", content: "Done.", turnId: "t2" },
    ]);
    renderSession();
    await screen.findByTestId("harness-reply-error", {}, NET);
    await waitFor(() => expect(screen.getAllByTestId("harness-reply")).toHaveLength(3), NET);
    expect(screen.queryByTestId("harness-refusal-rewind")).toBeNull();
  });

  it("review: an ARCHIVED session offers no press (nothing can be rewound there)", async () => {
    api.gets["/build/sessions/s1"] = { ...view(CONVERSATION), archived: true };
    renderSession();
    await screen.findByTestId("harness-reply-error", {}, NET);
    expect(screen.queryByTestId("harness-refusal-rewind")).toBeNull();
  });

  it("review: no press when no message of the session can be rewound to (none the daemon wrote)", async () => {
    api.gets["/build/sessions/s1"] = view([
      { role: "user", content: "Say hello", at: "2026-10-23T10:05:00" },
      { ...REFUSED_LINE, turnId: undefined },
    ]);
    renderSession();
    await screen.findByTestId("harness-reply-error", {}, NET);
    expect(screen.queryByTestId("harness-refusal-rewind")).toBeNull();
  });

  it("control: a failure that is not a refusal has no rewind press", async () => {
    api.gets["/build/sessions/s1"] = view([
      CONVERSATION[0],
      CONVERSATION[1],
      CONVERSATION[2],
      { role: "assistant", content: "", turnId: "t1", error: "Anthropic is busy. Try again shortly, or pick another model for this chat." },
    ]);
    renderSession();
    await screen.findByTestId("harness-reply-error", {}, NET);
    expect(screen.queryByTestId("harness-refusal-rewind")).toBeNull();
  });
});
