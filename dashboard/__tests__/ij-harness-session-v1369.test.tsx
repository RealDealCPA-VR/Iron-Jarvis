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
import { resetWords } from "@/lib/buildAccounts";

/* ------------------------------------------------------------- fixtures */

const FOLDER = "C:\\work\\shop";
const terminal: SessionTerminalApi = {
  find: () => null,
  open: async () => null,
  render: () => <div data-testid="term-body" />,
  close: () => {},
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

describe("a message sent while the last turn's lines are still being read (v1.369.0 review)", () => {
  it("keeps the new question on screen: the old turn's settle does not clear it", async () => {
    renderSession();
    await screen.findByTestId("harness-empty");
    let release!: () => void;
    const held = new Promise<void>((r) => (release = r));
    const second = controlledStream();
    let turns = 0;
    fetchMock.mockImplementation(async (_url: string, init: RequestInit) => {
      turns += 1;
      if (turns === 1) {
        const id = JSON.parse(String(init.body)).turn_id;
        // The session read after turn one is held until the second is sent.
        api.gets["/build/sessions/s1"] = () =>
          held.then(() => view({ messages: [userLine("First", id), replyLine("Did the first.", id)] }));
        return sse([["done", { reply: "Did the first." }]]);
      }
      return second.response;
    });
    await send("First");
    // Turn one is over (the box is free again); its re-read is still out.
    await waitFor(() => expect(screen.queryByTestId("harness-stop")).toBeNull());
    await waitFor(() => expect(api.calls.filter((c) => c.path === "/build/sessions/s1").length).toBe(2));
    await send("Second");
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    await act(async () => {
      release();
      await held;
    });
    await waitFor(() => expect(screen.getAllByTestId("harness-reply").at(-1)).toHaveTextContent("Did the first."));
    expect(screen.getAllByTestId("harness-user-bubble").map((b) => b.textContent)).toEqual(["First", "Second"]);
    second.end();
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
  // Phase 5's real shapes (v1.368.0): the list, and the frame sent right
  // before the turn's last frame — whose error repeats it in other words.
  const ACCOUNTS = {
    claude: [
      { id: "work", name: "Work", vendor: "claude", vendor_label: "Claude Code", provider: "claude-cli", default: true, plan: "max", plan_label: "Max", status: "limit", resets_at: "2030-01-01T16:12:00Z", limit_kind: "usage" },
      { id: "personal", name: "Personal", vendor: "claude", vendor_label: "Claude Code", provider: "claude-cli", default: false, plan: "pro", plan_label: "Pro", status: "ready" },
    ],
    codex: [],
    iron_proxy: { enabled: true, running: true, needs_update: false, note: "" },
  };
  const LIMIT = {
    account: "work",
    account_name: "Work",
    provider: "claude-cli",
    vendor: "claude",
    kind: "usage",
    resets_at: "2030-01-01T16:12:00Z",
    resumable: true,
    turn_id: "x",
  };

  async function hitLimit(limit: Record<string, unknown> = LIMIT) {
    fetchMock.mockImplementation(async () =>
      sse([
        ["round", { round: 0 }],
        ["account_limit", limit],
        [
          "error",
          {
            detail: "parked",
            title: 'The Claude Code account "Work" is out of usage until 4:12 PM. Wait for it to reset, or choose another account for this session.',
            fix: "Your conversation and the work so far are kept.",
          },
        ],
      ]),
    );
    renderSession();
    await screen.findByTestId("harness-empty");
    await send("Keep going");
    return await screen.findByTestId("account-limit-card");
  }

  it("shows the card in the mockup's words, with the account and its reset time — and says it once", async () => {
    const card = await hitLimit();
    expect(card).toHaveTextContent(
      "This account reached its limit. Wait for it to reset, or choose another account. Your conversation and work are preserved.",
    );
    expect(screen.getByTestId("account-limit-line").textContent).toMatch(/^Work · resets /);
    expect(within(card).getByRole("button", { name: "Resume when ready" })).toBeInTheDocument();
    expect(within(card).getByRole("button", { name: "Choose account" })).toBeInTheDocument();
    // The error frame says the same news: not a second time under the card.
    await waitFor(() => expect(screen.queryByText(/is out of usage until/)).toBeNull());
    expect(screen.queryByText("Your conversation and the work so far are kept.")).toBeNull();
  });

  it("an account that must sign in again is not a reset: no reset time, no Resume", async () => {
    const card = await hitLimit({ ...LIMIT, kind: "signin", resumable: false });
    expect(card).toHaveTextContent("This account needs to sign in again.");
    expect(screen.getByTestId("account-limit-line").textContent).toBe("Work");
    expect(within(card).queryByRole("button", { name: "Resume when ready" })).toBeNull();
    expect(within(card).getByRole("button", { name: "Choose account" })).toBeInTheDocument();
  });

  it("a removed account and a turned-off one say so in their own words", async () => {
    const card = await hitLimit({ ...LIMIT, kind: "missing", resumable: false });
    expect(card).toHaveTextContent("The account this session used was removed.");
    expect(within(card).queryByRole("button", { name: "Resume when ready" })).toBeNull();
    cleanup();
    // `resumable` not said: a turned-off account is not a reset, so no Resume.
    const off = await hitLimit({ ...LIMIT, kind: "disabled", resumable: undefined });
    expect(off).toHaveTextContent("This account is turned off.");
    expect(within(off).queryByRole("button", { name: "Resume when ready" })).toBeNull();
  });

  /** The daemon's state, as both its answers and a later GET read it. */
  function serverState(over: Record<string, unknown>) {
    const v = view(over);
    api.gets["/build/sessions/s1"] = v;
    return v;
  }
  const WAIT = { account: "work", account_name: "Work", resets_at: "2030-01-01T16:12:00Z" };

  it("Resume when ready arms the wait and offers Cancel while it waits", async () => {
    api.postAnswers["POST /build/sessions/s1/resume-when-ready"] = () =>
      serverState({ account_limit: LIMIT, account_wait: WAIT });
    api.postAnswers["DELETE /build/sessions/s1/resume-when-ready"] = () =>
      serverState({ account_limit: LIMIT, account_wait: null });
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

  it("each press reads the session again: the re-read, not the press's answer, decides (v1.368.0 review)", async () => {
    // A Cancel that lands while the daemon re-checks the account: its answer
    // can still show the wait; the session read after it does not.
    api.postAnswers["POST /build/sessions/s1/resume-when-ready"] = () =>
      serverState({ account_limit: LIMIT, account_wait: WAIT });
    api.postAnswers["DELETE /build/sessions/s1/resume-when-ready"] = () => {
      const stale = view({ account_limit: LIMIT, account_wait: WAIT });
      serverState({ account_limit: LIMIT, account_wait: null });
      return stale;
    };
    await hitLimit();
    fireEvent.click(screen.getByTestId("account-limit-resume"));
    expect(await screen.findByTestId("account-limit-waiting")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("account-limit-cancel"));
    expect(await screen.findByTestId("account-limit-resume")).toBeInTheDocument();
    expect(screen.queryByTestId("account-limit-waiting")).toBeNull();
  });

  it("a wait the daemon could resume at once is followed straight away", async () => {
    // The account was ready by the time Resume was pressed: the daemon cleared
    // the card and started the turn; the screen follows it by its name.
    api.postAnswers["POST /build/sessions/s1/resume-when-ready"] = () =>
      serverState({ running: true, turn_id: "tr_now", account_limit: null, account_wait: null });
    await hitLimit();
    fetchMock.mockImplementation(async () => sse([["done", { reply: "Carried on." }]]));
    fireEvent.click(screen.getByTestId("account-limit-resume"));
    await waitFor(() =>
      expect(fetchMock.mock.calls.some((c) => (c as unknown[])[0] === "http://d/chat/turns/tr_now/stream")).toBe(true),
    );
    await waitFor(() => expect(screen.queryByTestId("account-limit-card")).toBeNull());
  });

  it("Choose account lists the accounts and continues on the one picked, following the new turn", async () => {
    api.gets["/build/accounts"] = ACCOUNTS;
    api.postAnswers["POST /build/sessions/s1/account"] = {
      session: view({ running: true, turn_id: "bt_9", account: ACCOUNTS.claude[1], account_vendor: "claude" }),
      turn_id: "bt_9",
    };
    await hitLimit();
    // The continuing turn (followed by name) carries on and ends cleanly.
    fetchMock.mockImplementation(async () => sse([["done", { reply: "Carried on." }]]));
    fireEvent.click(screen.getByTestId("account-limit-choose"));
    fireEvent.click(await screen.findByTestId("account-pick-personal"));
    await waitFor(() =>
      expect(api.calls).toContainEqual({
        method: "POST",
        path: "/build/sessions/s1/account",
        body: { account: "personal", continue: true },
      }),
    );
    await waitFor(() =>
      expect(fetchMock.mock.calls.some((c) => (c as unknown[])[0] === "http://d/chat/turns/bt_9/stream")).toBe(true),
    );
    expect(screen.queryByTestId("account-limit-card")).toBeNull();
  });

  it("an older Iron Jarvis without the wait says so in words", async () => {
    api.postAnswers["POST /build/sessions/s1/resume-when-ready"] = new api.ApiError("Not Found", 404);
    await hitLimit();
    fireEvent.click(screen.getByTestId("account-limit-resume"));
    expect(await screen.findByText(/cannot wait for a reset yet/)).toBeInTheDocument();
  });

  it("the card and an armed wait survive a reload (the session view keeps them)", async () => {
    api.gets["/build/sessions/s1"] = view({
      account_limit: LIMIT,
      account_wait: { account: "work", account_name: "Work", resets_at: "2030-01-01T16:12:00Z" },
    });
    renderSession();
    expect(await screen.findByTestId("account-limit-card")).toBeInTheDocument();
    expect(screen.getByTestId("account-limit-waiting")).toBeInTheDocument();
    expect(screen.getByTestId("account-limit-cancel")).toBeInTheDocument();
  });

  it("the daemon's own continuation question is hidden; a reply that hit the limit says so", async () => {
    api.gets["/build/sessions/s1"] = view({
      messages: [
        userLine("Build the page", "t1"),
        { ...replyLine("Started the page.", "t1"), accountLimit: LIMIT, error: "parked" },
        { role: "user", content: "Carry on with the work you were doing.", continuation: true, turnId: "t2" },
        replyLine("Finished the page.", "t2"),
      ],
    });
    renderSession();
    expect(await screen.findByText("Finished the page.")).toBeInTheDocument();
    expect(screen.queryByText("Carry on with the work you were doing.")).toBeNull();
    expect(screen.getAllByTestId("harness-user-bubble")).toHaveLength(1);
    expect(screen.getByTestId("harness-reply-limit")).toHaveTextContent("Work: This account reached its limit.");
  });

  // ---- v1.369.0 review ---------------------------------------------------

  it("while it waits the reset time is said ONCE: on the account's line, not again in the sentence", async () => {
    api.gets["/build/sessions/s1"] = view({ account_limit: LIMIT, account_wait: WAIT });
    renderSession();
    const card = await screen.findByTestId("account-limit-card");
    const line = screen.getByTestId("account-limit-line").textContent ?? "";
    expect(line).toMatch(/^Work · resets /);
    expect(screen.getByTestId("account-limit-waiting").textContent).toBe("Waiting for the reset, then it continues.");
    expect((card.textContent ?? "").match(/resets /g)).toHaveLength(1);
  });

  it("the wait's own reset time wins on the line (the daemon's re-check moved it)", async () => {
    api.gets["/build/sessions/s1"] = view({
      account_limit: LIMIT,
      account_wait: { ...WAIT, resets_at: "2030-01-02T09:30:00Z" },
    });
    renderSession();
    await screen.findByTestId("account-limit-card");
    expect(screen.getByTestId("account-limit-line").textContent).toBe(`Work · ${resetWords("2030-01-02T09:30:00Z")}`);
    expect(resetWords("2030-01-02T09:30:00Z")).not.toBe(resetWords("2030-01-01T16:12:00Z"));
  });

  it("with no reset time known, the waiting sentence says it waits for the account to be ready", async () => {
    api.gets["/build/sessions/s1"] = view({
      account_limit: { ...LIMIT, resets_at: null },
      account_wait: { ...WAIT, resets_at: null },
    });
    renderSession();
    await screen.findByTestId("account-limit-card");
    expect(screen.getByTestId("account-limit-line").textContent).toBe("Work");
    expect(screen.getByTestId("account-limit-waiting").textContent).toBe("Waiting for it to be ready, then it continues.");
  });

  it("after the turn the SESSION VIEW is the truth for the card: the daemon wrote it before its last frame", async () => {
    // routes/build `recorded_turn` writes the card on the session BEFORE the
    // terminal frame (tests/test_build_turn_end_order_v1369.py), so the
    // screen's one re-read carries it — and a re-read that carries none
    // (the user chose another account in another window) takes it away.
    serverState({ account_limit: null, account_wait: null });
    fetchMock.mockImplementation(async () => {
      serverState({ account_limit: LIMIT, account_wait: null });
      return sse([
        ["account_limit", LIMIT],
        ["error", { detail: "parked", title: "Work is out of usage.", fix: "Kept." }],
      ]);
    });
    renderSession();
    await screen.findByTestId("harness-empty");
    await send("Keep going");
    expect(await screen.findByTestId("account-limit-card")).toBeInTheDocument();
    await waitFor(() => expect(api.calls.filter((c) => c.path === "/build/sessions/s1").length).toBeGreaterThan(1));
    expect(screen.getByTestId("account-limit-card")).toBeInTheDocument();
    cleanup();
    serverState({ account_limit: null, account_wait: null });
    fetchMock.mockImplementation(async () =>
      sse([
        ["account_limit", LIMIT],
        ["error", { detail: "parked", title: "Work is out of usage.", fix: "Kept." }],
      ]),
    );
    api.calls = [];
    renderSession();
    await screen.findByTestId("harness-empty");
    await send("Keep going");
    await waitFor(() => expect(api.calls.filter((c) => c.path === "/build/sessions/s1").length).toBeGreaterThan(1));
    await waitFor(() => expect(screen.queryByTestId("account-limit-card")).toBeNull());
  });

  it("a turn that ended on a limit reads the account list again, so the chip says so without opening it", async () => {
    const ready = { ...ACCOUNTS, claude: [{ ...ACCOUNTS.claude[0], status: "ready", resets_at: null, limit_kind: "" }, ACCOUNTS.claude[1]] };
    api.gets["/build/accounts"] = ready;
    serverState({
      setup: { provider: "claude-cli", account: "work", account_name: "Work" },
      account: ACCOUNTS.claude[0],
      account_vendor: "claude",
      account_limit: null,
      account_wait: null,
    });
    fetchMock.mockImplementation(async () => {
      // The daemon refreshes its snapshot as the turn ends on the limit.
      api.gets["/build/accounts"] = ACCOUNTS;
      return sse([
        ["account_limit", LIMIT],
        ["error", { detail: "parked", title: "Work is out of usage.", fix: "Kept." }],
      ]);
    });
    renderSession();
    expect(await screen.findByTestId("account-chip-name")).toHaveTextContent("Work");
    expect(screen.queryByTestId("account-chip-status")).toBeNull();
    await send("Keep going");
    await waitFor(() => expect(screen.getByTestId("account-chip-status").textContent).toBe("Limit reached"));
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

  it("a wait the daemon resumed while the screen was open is followed (the list saw it run)", async () => {
    const limit = {
      account: "work", account_name: "Work", provider: "claude-cli", vendor: "claude",
      kind: "usage", resets_at: "2030-01-01T16:12:00Z", resumable: true, turn_id: "",
    };
    api.gets["/build/sessions/s1"] = view({
      account_limit: limit,
      account_wait: { account: "work", account_name: "Work", resets_at: "2030-01-01T16:12:00Z" },
    });
    const { rerender } = renderSession();
    expect(await screen.findByTestId("account-limit-waiting")).toBeInTheDocument();
    // The reset came: the daemon started the session's turn by itself.
    api.gets["/build/sessions/s1"] = view({ running: true, turn_id: "tr_resumed", account_limit: null, account_wait: null });
    fetchMock.mockImplementation(async () => sse([["done", { reply: "Carried on." }]]));
    rerender(
      <HarnessSession threadId="s1" terminal={terminal} listRunning onChanged={() => {}} onGone={() => {}} />,
    );
    await waitFor(() =>
      expect(fetchMock.mock.calls.some((c) => (c as unknown[])[0] === "http://d/chat/turns/tr_resumed/stream")).toBe(true),
    );
    await waitFor(() => expect(screen.queryByTestId("account-limit-card")).toBeNull());
  });

  it("a turn refused in words (an account while Iron-Proxy is off) says so, and keeps the message in the box", async () => {
    const words = "This session runs on the account Work, and Iron-Proxy is off — turn it on in Connections to continue.";
    fetchMock.mockImplementation(
      async () =>
        new Response(JSON.stringify({ detail: words }), { status: 409, headers: { "Content-Type": "application/json" } }),
    );
    renderSession();
    await screen.findByTestId("harness-empty");
    await send("Keep going");
    expect(await screen.findByText(words)).toBeInTheDocument();
    await waitFor(() => expect(box().value).toBe("Keep going"));
    // Not a running turn: nothing was rejoined.
    expect(fetchMock.mock.calls.some((c) => String((c as unknown[])[0]).includes("/chat/turns/"))).toBe(false);
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
  const WORK = {
    id: "work", name: "Work", vendor: "claude", vendor_label: "Claude Code", provider: "claude-cli",
    default: true, plan: "max", plan_label: "Max", status: "usage",
    usage: { percent: 38, window: "7 days", window_mins: 10080, resets_at: "2030-01-01T16:12:00Z" },
  };
  const HOME = {
    id: "home", name: "Home", vendor: "claude", vendor_label: "Claude Code", provider: "claude-cli",
    default: false, plan: "pro", plan_label: "Pro", status: "limit", resets_at: "2030-01-01T16:12:00Z", limit_kind: "usage",
  };

  it("names the session's account and lists the accounts, grouped, with Iron-Proxy's note", async () => {
    api.gets["/build/sessions/s1"] = view({
      setup: { provider: "claude-cli", account: "work", account_name: "Work" },
      account: WORK,
      account_vendor: "claude",
    });
    api.gets["/build/accounts"] = {
      claude: [WORK, HOME],
      codex: [{ id: "cx", name: "Codex", vendor: "codex", vendor_label: "Codex", provider: "codex-cli", default: true, plan: "plus", plan_label: "Plus", status: "ready" }],
      iron_proxy: { enabled: true, running: true, needs_update: true, note: "Iron-Proxy needs an update to keep a Build session on one account." },
    };
    api.postAnswers["PATCH /build/sessions/s1"] = () => {
      const v = view({ account: HOME, account_vendor: "claude", setup: { provider: "claude-cli", account: "home", account_name: "Home" } });
      api.gets["/build/sessions/s1"] = v;
      return v;
    };
    renderSession();
    const chip = await screen.findByTestId("account-chip");
    expect(chip).toHaveTextContent("Work");
    fireEvent.click(chip);
    const menu = await screen.findByTestId("account-menu");
    await waitFor(() =>
      expect(within(menu).getByTestId("account-iron-proxy-note")).toHaveTextContent("Iron-Proxy needs an update"),
    );
    expect(within(menu).getByText("Claude Code accounts")).toBeInTheDocument();
    expect(within(menu).getByText("Codex accounts")).toBeInTheDocument();
    expect(within(within(menu).getByTestId("account-card-work")).getByTestId("account-vendor-plan")).toHaveTextContent(
      "Claude Code · Max",
    );
    fireEvent.click(within(menu).getByTestId("account-pick-home"));
    await waitFor(() =>
      expect(api.calls).toContainEqual({ method: "PATCH", path: "/build/sessions/s1", body: { account: "home" } }),
    );
    // The answer is adopted: the chip names the new account.
    await waitFor(() => expect(screen.getByTestId("account-chip-name")).toHaveTextContent("Home"));
    // A plain pick in the list never asks the daemon to carry on now: that
    // is offered only from the limit card.
    expect(api.calls.some((c) => c.path === "/build/sessions/s1/account")).toBe(false);
  });

  it("a session with no account names where its model runs, in plain words", async () => {
    api.gets["/build/sessions/s1"] = view({ setup: { provider: "anthropic", model: "claude-opus-4-8" }, account: null, account_vendor: "" });
    api.gets["/build/accounts"] = { claude: [], codex: [], iron_proxy: { enabled: false, running: false, needs_update: false, note: "Iron-Proxy is off." } };
    renderSession();
    expect(await screen.findByTestId("harness-place")).toHaveTextContent("Anthropic");
    expect(screen.queryByTestId("account-chip")).toBeNull();
  });
});

describe("a new session on a Default account that is at its limit (v1.368.0 ship)", () => {
  // The daemon starts a NEW session on the account the list badges Default,
  // even at its limit. The screen shows that, picks nothing else, and the
  // first turn ends on the limit card.
  const WORK = {
    id: "work", name: "Work", vendor: "claude", vendor_label: "Claude Code", provider: "claude-cli",
    default: true, plan: "max", plan_label: "Max", status: "limit", resets_at: "2030-01-01T16:12:00Z", limit_kind: "usage",
  };
  const PERSONAL = { ...WORK, id: "personal", name: "Personal", default: false, plan_label: "Pro", status: "ready", resets_at: null, limit_kind: "" };

  it("the chip says the limit before anything is sent, and the screen moves the session nowhere", async () => {
    api.gets["/build/sessions/s1"] = view({
      setup: { provider: "claude-cli", account: "work", account_name: "Work" },
      account: WORK,
      account_vendor: "claude",
      account_limit: null,
      account_wait: null,
    });
    api.gets["/build/accounts"] = { claude: [WORK, PERSONAL], codex: [], iron_proxy: { enabled: true, running: true, needs_update: false, note: "" } };
    const kept = { account: "work", account_name: "Work", provider: "claude-cli", vendor: "claude", kind: "usage", resets_at: "2030-01-01T16:12:00Z", resumable: true, turn_id: "x" };
    fetchMock.mockImplementation(async () => {
      // The daemon keeps the card on the session before its last frame
      // (v1.369.0 review): the screen's re-read carries it.
      api.gets["/build/sessions/s1"] = view({
        setup: { provider: "claude-cli", account: "work", account_name: "Work" },
        account: WORK,
        account_vendor: "claude",
        account_limit: kept,
        account_wait: null,
      });
      return sse([
        ["round", { round: 0 }],
        ["account_limit", kept],
        ["error", { detail: "parked", title: "Work is out of usage.", fix: "Your conversation and the work so far are kept." }],
      ]);
    });
    renderSession();
    expect(await screen.findByTestId("account-chip-name")).toHaveTextContent("Work");
    const status = await screen.findByTestId("account-chip-status");
    expect(status.textContent).toBe("Limit reached");
    expect(status.getAttribute("title")).toMatch(/^Limit reached · resets /);
    // Nothing on the screen side picks another account.
    expect(api.calls.some((c) => c.method === "PATCH" || c.path === "/build/sessions/s1/account")).toBe(false);
    await send("Start the page");
    const card = await screen.findByTestId("account-limit-card");
    expect(within(card).getByRole("button", { name: "Choose account" })).toBeInTheDocument();
    expect(api.calls.some((c) => c.method === "PATCH" || c.path === "/build/sessions/s1/account")).toBe(false);
  });

  it("a ready account shows no status beside its name", async () => {
    api.gets["/build/sessions/s1"] = view({ account: PERSONAL, account_vendor: "claude", account_limit: null, account_wait: null });
    api.gets["/build/accounts"] = { claude: [WORK, PERSONAL], codex: [], iron_proxy: { enabled: true, running: true, needs_update: false, note: "" } };
    renderSession();
    expect(await screen.findByTestId("account-chip-name")).toHaveTextContent("Personal");
    expect(screen.queryByTestId("account-chip-status")).toBeNull();
  });
});
