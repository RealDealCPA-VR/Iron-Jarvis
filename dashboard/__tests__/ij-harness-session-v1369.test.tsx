/**
 * v1.369.0 (IJ harness Phase 6) — one Build SESSION on the screen
 * (components/harness/HarnessSession), driven through the REAL chat stream
 * (lib/useChatStream) against a fetch double, and the chat page's own pieces.
 *
 *  - the transcript renders with the chat's pieces (work line, receipt);
 *  - a message goes to the session's OWN turn route with a minted turn id,
 *    the screen NEVER saves the session's messages, and it adopts the lines
 *    the daemon wrote when the turn ends;
 *  - the model's own question (`ask_user`) takes the composer's place and is
 *    answered through `POST /build/sessions/{id}/answer`;
 *  - the plan door (`exit_plan_mode`, no arguments) shows the reply above as
 *    its plan, with Allow once and Deny only;
 *  - an `account_limit` frame shows the limit card; Resume when ready arms
 *    the wait (Cancel while it waits), Choose account continues on another;
 *  - a steer note the turn never read goes back in the box — watched (the
 *    done frame's list) AND unwatched (the turn ended while nobody looked);
 *  - a session already answering is rejoined; Stop posts the turn's stop;
 *  - an Iron Jarvis without the account list gets no account chip.
 */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

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

/* ------------------------------------------------------------- fixtures */

const FOLDER = "C:\\work\\shop";
const terminal: SessionTerminalApi = {
  find: () => null,
  open: async () => null,
  render: () => <div data-testid="term-body" />,
};

function view(over: Record<string, unknown> = {}) {
  return {
    id: "s1",
    title: "Add a cart",
    updated_at: "2026-10-22T10:00:00",
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
    messages: [],
    session_id: "build:s1",
    queued: [],
    turn_id: "",
    ...over,
  };
}

const userLine = (content: string, turnId = "t0") => ({ role: "user", content, turnId, at: "2026-10-22T10:00:00" });
const replyLine = (content: string, turnId = "t0", extra: Record<string, unknown> = {}) => ({
  role: "assistant",
  content,
  turnId,
  route: { provider: "mock", model: "mock-1", reason: "mock" },
  toolsUsed: ["write_file"],
  steps: [{ name: "write_file", ok: true, ms: 12 }],
  timing: { startedAt: 1, firstTokenAt: 2, endedAt: 3 },
  ...extra,
});

/** A controllable SSE body: `push` frames, `end` it. */
function controlledStream() {
  let ctl!: ReadableStreamDefaultController<Uint8Array>;
  const body = new ReadableStream<Uint8Array>({
    start(c) {
      ctl = c;
    },
  });
  const enc = new TextEncoder();
  return {
    response: new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } }),
    push: (event: string, data: unknown) => ctl.enqueue(enc.encode(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`)),
    end: () => ctl.close(),
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
  api.postAnswers = {};
  localStorage.clear();
  fetchMock = vi.fn(async () => sse([["done", { reply: "ok" }]]));
  vi.stubGlobal("fetch", fetchMock);
  vi.spyOn(window, "confirm").mockReturnValue(true);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function renderSession(extra: Partial<React.ComponentProps<typeof HarnessSession>> = {}) {
  return render(
    <HarnessSession
      threadId="s1"
      terminal={terminal}
      listRunning={false}
      onChanged={() => {}}
      onGone={() => {}}
      {...extra}
    />,
  );
}

const box = () => screen.getByTestId("harness-input") as HTMLTextAreaElement;
async function send(text: string) {
  fireEvent.change(box(), { target: { value: text } });
  fireEvent.keyDown(box(), { key: "Enter" });
}

/* ---------------------------------------------------------------- tests */

describe("the transcript is drawn with the chat page's own pieces", () => {
  it("shows the question, the work line, the reply and its receipt; the header names the folder", async () => {
    api.gets["/build/sessions/s1"] = view({
      messages: [userLine("Add a cart"), replyLine("Added the cart page.")],
    });
    renderSession();
    expect(await screen.findByText("Added the cart page.")).toBeInTheDocument();
    expect(screen.getByTestId("harness-user-bubble")).toHaveTextContent("Add a cart");
    expect(screen.getByTestId("work-line")).toBeInTheDocument();
    expect(screen.getByTestId("turn-receipt")).toBeInTheDocument();
    expect(screen.getByTestId("harness-folder")).toHaveTextContent("shop");
    // No account list on this Iron Jarvis (a 404): no chip; the demo model
    // is named where it runs, in plain words.
    expect(screen.queryByTestId("account-chip")).toBeNull();
    expect(screen.getByTestId("harness-place")).toHaveTextContent("Demo model");
  });
});

describe("a message is a turn on the session's own route", () => {
  it("posts {text, turn_id} to /build/sessions/s1/turn, never saves the transcript, and adopts the daemon's lines", async () => {
    renderSession();
    await screen.findByTestId("harness-empty");
    // After the turn the daemon's transcript holds the question and reply.
    fetchMock.mockImplementation(async (_url: string, init: RequestInit) => {
      const id = JSON.parse(String(init.body)).turn_id;
      api.gets["/build/sessions/s1"] = view({
        messages: [userLine("Make it blue", id), replyLine("Made the header blue.", id)],
      });
      return sse([
        ["token", { text: "Made the header blue." }],
        ["done", { reply: "Made the header blue." }],
      ]);
    });
    await send("Make it blue");
    // The ADOPTED line (a reply in the transcript, not the live text).
    await waitFor(() =>
      expect(screen.getAllByTestId("harness-reply").at(-1)).toHaveTextContent("Made the header blue."),
    );
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("http://d/build/sessions/s1/turn");
    const body = JSON.parse(String(init.body));
    expect(body.text).toBe("Make it blue");
    expect(body.turn_id).toMatch(/^[A-Za-z0-9_.-]{1,80}$/);
    // THE DAEMON WRITES THE TRANSCRIPT: no save of the session's messages.
    expect(api.calls.filter((c) => c.method === "PUT" && c.path === "/chat/threads/s1")).toEqual([]);
    // The session was read again after the turn (adopting what was written).
    expect(api.calls.filter((c) => c.method === "GET" && c.path === "/build/sessions/s1").length).toBeGreaterThan(1);
    expect(box().value).toBe("");
  });
});

describe("the model's own question takes the composer's place", () => {
  it("answers through the session's answer route, and goes when the turn says it is settled", async () => {
    const s = controlledStream();
    fetchMock.mockImplementation(async () => s.response);
    renderSession();
    await screen.findByTestId("harness-empty");
    await send("Set up the project");
    await act(async () => {
      s.push("ask_user", {
        id: "ask1",
        call_id: "c1",
        questions: [{ id: "q1", question: "Which framework?", header: "", options: [{ label: "React" }, { label: "Vue" }], multi: false }],
      });
    });
    const card = await screen.findByTestId("ask-user-card");
    expect(card).toHaveTextContent("Which framework?");
    // The composer stays mounted, hidden and inert, while the question is up.
    expect(screen.getByTestId("harness-composer")).toHaveAttribute("aria-hidden", "true");
    fireEvent.click(within(card).getByTestId("ask-user-choice-q1-A"));
    fireEvent.click(within(card).getByRole("button", { name: "Send" }));
    await waitFor(() =>
      expect(api.calls).toContainEqual({
        method: "POST",
        path: "/build/sessions/s1/answer",
        body: { ask_id: "ask1", answer: "React" },
      }),
    );
    await act(async () => {
      s.push("mcp_resolved", { id: "ask1", kind: "ask_user", outcome: "answered" });
    });
    await waitFor(() => expect(screen.queryByTestId("ask-user-card")).toBeNull());
    await act(async () => {
      s.push("done", { reply: "Set up with React." });
      s.end();
    });
  });

  it("Skip sends the skip, not an answer", async () => {
    const s = controlledStream();
    fetchMock.mockImplementation(async () => s.response);
    renderSession();
    await screen.findByTestId("harness-empty");
    await send("Go");
    await act(async () => {
      s.push("ask_user", { id: "ask2", call_id: "c2", questions: [{ id: "q1", question: "Name?", options: [] }] });
    });
    fireEvent.click(await screen.findByTestId("ask-user-skip"));
    await waitFor(() =>
      expect(api.calls).toContainEqual({
        method: "POST",
        path: "/build/sessions/s1/answer",
        body: { ask_id: "ask2", skip: true },
      }),
    );
    await act(async () => {
      s.end();
    });
  });
});

describe("the plan door", () => {
  it("shows the reply above as the plan, with Allow once and Deny only (never Always or a wider scope)", async () => {
    const s = controlledStream();
    fetchMock.mockImplementation(async () => s.response);
    renderSession();
    await screen.findByTestId("harness-empty");
    await send("Plan the cart");
    await act(async () => {
      s.push("token", { text: "My plan: build the cart model, then the page." });
      s.push("approval", { id: "ap1", call_id: "c3", tool: "exit_plan_mode", args: {}, can_always: true });
    });
    const card = await screen.findByTestId("chat-approval-card");
    expect(within(card).getByTestId("approval-plan-text")).toHaveTextContent("build the cart model, then the page.");
    expect(within(card).getByRole("button", { name: "Allow once" })).toBeInTheDocument();
    expect(within(card).getByRole("button", { name: "Deny" })).toBeInTheDocument();
    expect(within(card).queryByTestId("approval-always")).toBeNull();
    expect(within(card).queryByTestId("approval-scope")).toBeNull();
    await act(async () => {
      s.end();
    });
  });
});

describe("an account limit", () => {
  const ACCOUNTS = {
    claude: [
      { id: "work", name: "Work", default: true, vendor: "claude", plan: "Max", status: "limit", resets_at: "2030-01-01T16:12:00Z" },
      { id: "personal", name: "Personal", default: false, vendor: "claude", plan: "Pro", status: "ready" },
    ],
    codex: [],
  };

  async function hitLimit() {
    fetchMock.mockImplementation(async () =>
      sse([
        ["round", { round: 0 }],
        ["account_limit", { account: "work", account_name: "Work", provider: "claude-cli", kind: "limit", resets_at: "2030-01-01T16:12:00Z" }],
        ["done", { reply: "" }],
      ]),
    );
    renderSession();
    await screen.findByTestId("harness-empty");
    await send("Keep going");
    return await screen.findByTestId("account-limit-card");
  }

  it("shows the card in the mockup's words, with the account and its reset time", async () => {
    const card = await hitLimit();
    expect(card).toHaveTextContent(
      "This account reached its limit. Wait for it to reset, or choose another account. Your conversation and work are preserved.",
    );
    expect(screen.getByTestId("account-limit-line").textContent).toMatch(/^Work · resets /);
    expect(within(card).getByRole("button", { name: "Resume when ready" })).toBeInTheDocument();
    expect(within(card).getByRole("button", { name: "Choose account" })).toBeInTheDocument();
  });

  it("Resume when ready arms the wait and offers Cancel while it waits", async () => {
    await hitLimit();
    fireEvent.click(screen.getByTestId("account-limit-resume"));
    await waitFor(() =>
      expect(api.calls.some((c) => c.method === "POST" && c.path === "/build/sessions/s1/resume-when-ready")).toBe(true),
    );
    expect(await screen.findByTestId("account-limit-waiting")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("account-limit-cancel"));
    await waitFor(() =>
      expect(api.calls.some((c) => c.method === "DELETE" && c.path === "/build/sessions/s1/resume-when-ready")).toBe(true),
    );
    expect(await screen.findByTestId("account-limit-resume")).toBeInTheDocument();
  });

  it("Choose account lists the accounts and continues on the one picked", async () => {
    api.gets["/build/accounts"] = ACCOUNTS;
    await hitLimit();
    fireEvent.click(screen.getByTestId("account-limit-choose"));
    fireEvent.click(await screen.findByTestId("account-pick-personal"));
    await waitFor(() =>
      expect(api.calls).toContainEqual({
        method: "POST",
        path: "/build/sessions/s1/account",
        body: { account: "personal", continue: true },
      }),
    );
  });

  it("an older Iron Jarvis without the wait says so in words", async () => {
    api.postAnswers["POST /build/sessions/s1/resume-when-ready"] = new api.ApiError("Not Found", 404);
    await hitLimit();
    fireEvent.click(screen.getByTestId("account-limit-resume"));
    expect(await screen.findByText(/cannot wait for a reset yet/)).toBeInTheDocument();
  });
});

describe("a steer note the turn never read goes back in the box", () => {
  it("watched: the done frame's unread notes are put back", async () => {
    fetchMock.mockImplementation(async () =>
      sse([["done", { reply: "Done.", unread_steers: ["also add tests"] }]]),
    );
    renderSession();
    await screen.findByTestId("harness-empty");
    await send("Refactor");
    await waitFor(() => expect(box().value).toBe("also add tests"));
  });

  it("unwatched: a note this window sent to a turn that ended while nobody looked comes back; a read one does not", async () => {
    localStorage.setItem(
      "ij.build.steers.s1",
      JSON.stringify([
        { turnId: "t9", text: "use tabs" },
        { turnId: "t9", text: "keep it short" },
      ]),
    );
    api.gets["/build/sessions/s1"] = view({
      messages: [
        userLine("Format it", "t9"),
        { role: "user", content: "keep it short", steer: true, turnId: "t9" },
        replyLine("Formatted.", "t9"),
      ],
    });
    renderSession();
    await waitFor(() => expect(box().value).toBe("use tabs"));
    // The record of sent notes is cleared for that turn.
    expect(localStorage.getItem("ij.build.steers.s1")).toBeNull();
  });

  it("while a turn runs, Enter steers it through the chat route and the note is shown", async () => {
    const s = controlledStream();
    fetchMock.mockImplementation(async () => s.response);
    renderSession();
    await screen.findByTestId("harness-empty");
    await send("Build it");
    await act(async () => {
      s.push("round", { round: 0 });
    });
    await send("and add a footer");
    await waitFor(() =>
      expect(api.calls.some((c) => c.method === "POST" && /\/chat\/turns\/.+\/steer$/.test(c.path))).toBe(true),
    );
    const steer = api.calls.find((c) => /\/steer$/.test(c.path));
    expect(steer?.body).toEqual({ text: "and add a footer" });
    expect(await screen.findByTestId("harness-steer-notes")).toHaveTextContent("and add a footer");
    await act(async () => {
      s.end();
    });
  });
});

describe("following a turn already running, and stopping one", () => {
  it("a session answering when it opens is rejoined", async () => {
    api.gets["/build/sessions/s1"] = view({ running: true, turn_id: "tr1", messages: [userLine("Long job", "tr1")] });
    fetchMock.mockImplementation(async () => sse([["done", { reply: "Finished." }]]));
    renderSession();
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("http://d/chat/turns/tr1/stream");
    expect(init.method).toBe("GET");
  });

  it("Stop posts the running turn's stop by its name", async () => {
    const s = controlledStream();
    fetchMock.mockImplementation(async () => s.response);
    renderSession();
    await screen.findByTestId("harness-empty");
    await send("Long job");
    fireEvent.click(await screen.findByTestId("harness-stop"));
    await waitFor(() =>
      expect(api.calls.some((c) => c.method === "POST" && /^\/chat\/turns\/.+\/stop$/.test(c.path))).toBe(true),
    );
    const [, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    const turnId = JSON.parse(String(init.body)).turn_id;
    expect(api.calls.find((c) => /\/stop$/.test(c.path))?.path).toBe(`/chat/turns/${turnId}/stop`);
    await act(async () => {
      s.end();
    });
  });
});

describe("the account chip", () => {
  it("names the session's account (its vendor's default when none is picked) and lists the accounts", async () => {
    api.gets["/build/sessions/s1"] = view({ setup: { provider: "claude-cli", model: "claude-opus-4-8" } });
    api.gets["/build/accounts"] = {
      claude: [
        { id: "work", name: "Work", default: true, vendor: "claude", plan: "Max", status: "ready", usage: { percent: 38, window: "7 days" } },
        { id: "home", name: "Home", default: false, vendor: "claude", plan: "Pro", status: "limit", resets_at: "2030-01-01T16:12:00Z" },
      ],
      codex: [{ id: "cx", name: "Codex", default: true, vendor: "codex", plan: "Plus", status: "ready" }],
    };
    renderSession();
    const chip = await screen.findByTestId("account-chip");
    expect(chip).toHaveTextContent("Work");
    fireEvent.click(chip);
    const menu = screen.getByTestId("account-menu");
    expect(within(menu).getByText("Claude Code accounts")).toBeInTheDocument();
    expect(within(menu).getByText("Codex accounts")).toBeInTheDocument();
    fireEvent.click(within(menu).getByTestId("account-pick-home"));
    await waitFor(() =>
      expect(api.calls).toContainEqual({ method: "PATCH", path: "/build/sessions/s1", body: { account: "home" } }),
    );
  });
});
