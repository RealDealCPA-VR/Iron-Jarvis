/**
 * v1.349.0 re-review (wave B): the helper-agent row settles, and finalize never writes
 * into a chat the user has left.
 *
 * Plain words: when an agent run finishes, finalize reads the session, then
 * (on the composing path) the run's ledger card (GET /sessions/{id}/result),
 * then stamps the hand-off message and appends the agent's reply. v1.345.0
 * guarded the first two reads (the user may leave while they are out); the
 * LEDGER read had no guard, so a user who opened another chat (or New chat)
 * while it was out got the agent's reply composed into THAT chat and saved
 * there (a New chat was minted out of another conversation's reply). The
 * reply belongs to the chat it was asked in: reopening that chat resumes the
 * wait (its last bubble still names the session) and lands it there.
 *
 * And the step-detail half, through the SAME real page: a saved thread's
 * step, reopened, is a disclosure that opens to its masked detail (the lane
 * pins drove WorkLine directly; this drives page -> MessageRow -> WorkLine).
 *
 * Harness: agent-run-row-v1349's (the real page, transport mocked), plus a
 * GET hook that holds one read until the test releases it.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const H = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 500) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  return {
    FakeApiError,
    api: {
      gets: [] as string[],
      posts: [] as { path: string; body: Record<string, unknown> }[],
      puts: [] as { path: string; body: Record<string, unknown> }[],
      /** A value, an Error, or a function of the call count (1-based). */
      getResponses: {} as Record<string, unknown>,
      getCounts: {} as Record<string, number>,
      /** Answers a GET before getResponses when it returns non-undefined. */
      getHook: null as null | ((path: string) => unknown),
    },
    /** What the run stream shows (useRunStream's fields the page reads). */
    run: {
      text: "",
      tools: [] as unknown[],
      phase: null as null | { phase: string; detail: string },
    },
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: H.FakeApiError,
  API_BASE: "",
  ijToken: () => "",
  sseUrl: (p: string) => `http://localhost/stub${p}`,
  get: async (path: string) => {
    H.api.gets.push(path);
    H.api.getCounts[path] = (H.api.getCounts[path] ?? 0) + 1;
    const hooked = H.api.getHook?.(path);
    if (hooked !== undefined) return hooked;
    let r = H.api.getResponses[path];
    if (typeof r === "function") r = (r as (n: number) => unknown)(H.api.getCounts[path]);
    if (r === undefined) throw new H.FakeApiError(`unmocked GET ${path}`, 404);
    if (r instanceof Error) throw r;
    return JSON.parse(JSON.stringify(r));
  },
  post: async (path: string, body: Record<string, unknown>) => {
    H.api.posts.push({ path, body });
    return {};
  },
  put: async (path: string, body: Record<string, unknown>) => {
    H.api.puts.push({ path, body: JSON.parse(JSON.stringify(body)) });
    const m = /^\/chat\/threads\/(.+)$/.exec(path);
    return { id: m && m[1] !== "new" ? m[1] : "t1", title: "t" };
  },
  del: async () => ({}),
}));

vi.mock("@/lib/useChatStream", () => {
  class StreamError extends Error {
    status = 0;
    committed = false;
    offline = false;
    partial = "";
  }
  return {
    StreamError,
    useLiveText: (s: { text?: string }) => s?.text ?? "",
    useChatStream: () => ({
      streaming: false,
      text: "",
      tools: [],
      approval: null,
      run: async () => ({ reply: "" }),
      abort: () => {},
    }),
  };
});

vi.mock("@/lib/useRunStream", () => ({
  useRunStream: () => ({
    text: H.run.text,
    tools: H.run.tools,
    phase: H.run.phase,
    active: true,
    start: () => {},
    stop: () => {},
  }),
}));

vi.mock("@/lib/useEvents", () => ({
  useEvents: () => ({ events: [], connected: false }),
}));
vi.mock("@/lib/useDictation", () => ({
  useDictation: () => ({
    supported: false,
    reason: null,
    engine: null,
    listening: false,
    processing: false,
    transcript: "",
    interim: "",
    error: null,
    start: () => {},
    stop: () => {},
    reset: () => {},
  }),
}));
vi.mock("@/lib/useTTS", () => ({
  useTTS: () => ({
    supported: false,
    enabled: false,
    speaking: false,
    enable: () => {},
    disable: () => {},
    toggle: () => {},
    speak: () => {},
    resetStream: () => {},
    speakMore: () => {},
    cancel: () => {},
  }),
}));
vi.mock("@/lib/useProviderHealth", () => ({
  useProviderHealth: () => ({
    byProvider: {},
    defaultProvider: "",
    loading: false,
    stale: false,
    refresh: () => {},
  }),
}));
vi.mock("react-markdown", () => ({
  default: ({ children }: { children?: string }) => <div>{children}</div>,
}));
vi.mock("remark-gfm", () => ({ default: () => {} }));

import ChatPage from "@/app/chat/page";
import { readAgentRun } from "@/lib/agentRun";

type Msg = Record<string, unknown>;


/** A naive-UTC ISO stamp (the daemon's own shape) `ago` ms in the past. */
const isoAgo = (ago: number, from = Date.now()) => new Date(from - ago).toISOString().replace("Z", "");

const RESULT = {
  found: true,
  session_id: "s1",
  status: "completed",
  task: "draft the summary",
  summary: "",
  steps: 6,
  tools_used: [{ tool: "read_file", count: 2 }],
  tools_failed: [],
  files_created: [],
  files_changed: [],
  errors: [],
  revertable: 0,
  duration_s: 125,
};

function resetApi() {
  H.api.gets.length = 0;
  H.api.posts.length = 0;
  H.api.puts.length = 0;
  for (const k of Object.keys(H.api.getResponses)) delete H.api.getResponses[k];
  for (const k of Object.keys(H.api.getCounts)) delete H.api.getCounts[k];
  H.api.getResponses["/models"] = { models: [] };
  H.api.getResponses["/chat/personas"] = { personas: [] };
  H.api.getResponses["/chat/threads"] = { threads: [] };
  H.api.getResponses["/settings"] = { settings: {} };
  H.api.getResponses["/projects"] = { projects: [] };
  H.api.getResponses["/agents/mentionable"] = { agents: [] };
  H.api.getResponses["/skills"] = { skills: [] };
  H.api.getResponses["/workflows"] = { workflows: [] };
  H.api.getResponses["/tools"] = { tools: [] };
  H.api.getResponses["/undo?session_id=chat"] = { actions: [] };
  H.run.text = "";
  H.run.tools = [];
  H.run.phase = null;
  H.api.getHook = null;
}

/** A saved thread the page opens from ?thread=t7. */
function storeThread(messages: Msg[] | ((n: number) => unknown)) {
  H.api.getResponses["/chat/threads/t7"] =
    typeof messages === "function"
      ? messages
      : { id: "t7", title: "saved", updated_at: "S1", messages };
  window.history.replaceState({}, "", "/chat?thread=t7");
}

const WAITING: Msg[] = [
  { role: "user", content: "summarise the ledger" },
  {
    role: "assistant",
    content: "",
    escalated: "needs the agent",
    escalatedTo: "the researcher",
    awaitingSession: "s1",
  },
];

const putsTo = (p: string) => H.api.puts.filter((x) => x.path === p);

beforeEach(() => {
  resetApi();
  window.history.replaceState({}, "", "/chat");
  window.localStorage.clear();
  Element.prototype.scrollIntoView = vi.fn();
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.clearAllMocks();
});


async function settle(ticks = 6) {
  for (let i = 0; i < ticks; i++) {
    await act(async () => {
      await new Promise<void>((r) => setTimeout(r, 0));
    });
  }
}

/** Hold the FIRST GET of `path`; returns its release (or its failure). */
function holdFirst(path: string): {
  release: (v: unknown) => void;
  fail: (e: unknown) => void;
  used: () => boolean;
} {
  let release!: (v: unknown) => void;
  let fail!: (e: unknown) => void;
  const held = new Promise<unknown>((r, j) => {
    release = r;
    fail = j;
  });
  let used = false;
  H.api.getHook = (p) => {
    if (p === path && !used) {
      used = true;
      return held;
    }
    return undefined;
  };
  return { release, fail, used: () => used };
}

const putsCarrying = (text: string) => H.api.puts.filter((p) => JSON.stringify(p.body).includes(text));

const T8 = {
  id: "t8",
  title: "orlando",
  updated_at: "O1",
  messages: [
    { role: "user", content: "find me a place to eat in orlando" },
    { role: "assistant", content: "Try Se7en Bites." },
  ],
};

/** t7 (waiting on s1) and t8 (a plain chat) in the thread list. */
function twoThreads() {
  H.api.getResponses["/chat/threads/t8"] = T8;
  H.api.getResponses["/chat/threads"] = {
    threads: [
      { id: "t7", title: "saved", updated_at: "S1", messages: 2 },
      { id: "t8", title: "orlando", updated_at: "O1", messages: 2 },
    ],
  };
}

async function openT8() {
  fireEvent.click(await screen.findByTestId("thread-row-t8"));
  await screen.findByText("Try Se7en Bites.");
}

function finishedRun() {
  const end = Date.now();
  H.api.getResponses["/sessions/s1?transcript=0"] = {
    session: {
      id: "s1",
      status: "completed",
      summary: "The summary is ready.",
      created_at: isoAgo(125_000, end),
      finished_at: isoAgo(0, end),
    },
  };
}

describe("finalize's LEDGER read lands after the user left (re-review)", () => {
  it("left for a NEW chat: nothing is composed or saved there; the hand-off stays waiting for its own chat", async () => {
    storeThread(WAITING);
    finishedRun();
    const hold = holdFirst("/sessions/s1/result");
    render(<ChatPage />);
    await waitFor(() => expect(hold.used()).toBe(true), { timeout: 4000 });
    await act(async () => {
      window.dispatchEvent(new Event("ij:new-chat"));
    });
    await waitFor(() => expect(screen.queryByText("summarise the ledger")).toBeNull());
    const putsBefore = H.api.puts.length;
    await act(async () => {
      hold.release(JSON.parse(JSON.stringify(RESULT)));
    });
    await settle();
    expect(screen.queryByText("The summary is ready.")).toBeNull();
    expect(screen.queryByTestId("agent-run-row")).toBeNull();
    expect(H.api.puts.length).toBe(putsBefore); // nothing minted, nothing saved
    expect(putsCarrying("The summary is ready.")).toHaveLength(0);
    // The chat it was asked in was never written as settled behind the
    // user's back: its saved copy still waits on s1, so reopening it resumes.
    expect(putsTo("/chat/threads/t7").filter((p) => readAgentRun((p.body.messages as Msg[])[1]))).toHaveLength(0);
  }, 10_000);

  it("left for ANOTHER saved chat: that chat is not written and does not show the reply", async () => {
    storeThread(WAITING);
    finishedRun();
    twoThreads();
    const hold = holdFirst("/sessions/s1/result");
    render(<ChatPage />);
    await waitFor(() => expect(hold.used()).toBe(true), { timeout: 4000 });
    await openT8();
    await act(async () => {
      hold.release(JSON.parse(JSON.stringify(RESULT)));
    });
    await settle();
    expect(screen.getByText("find me a place to eat in orlando")).toBeTruthy();
    expect(screen.queryByText("The summary is ready.")).toBeNull();
    expect(putsTo("/chat/threads/t8")).toHaveLength(0);
    expect(putsCarrying("The summary is ready.")).toHaveLength(0);
  }, 10_000);

  it("the SESSION read FAILS after the user left: no error and no save land in the chat open now", async () => {
    // A hard failure of the old wait (a 500) used to run its cleanup against
    // whatever chat was open: an error banner there, and a PUT of that chat.
    storeThread(WAITING);
    twoThreads();
    const hold = holdFirst("/sessions/s1?transcript=0");
    render(<ChatPage />);
    await waitFor(() => expect(hold.used()).toBe(true), { timeout: 4000 });
    await openT8();
    await act(async () => {
      hold.fail(new H.FakeApiError("the daemon fell over", 500));
    });
    await settle();
    expect(screen.getByText("Try Se7en Bites.")).toBeTruthy();
    expect(screen.queryByText(/the daemon fell over/)).toBeNull();
    expect(putsTo("/chat/threads/t8")).toHaveLength(0);
  }, 10_000);

  it("anti-vacuity: with nobody leaving, the same held read lands the settled row and the reply in t7", async () => {
    storeThread(WAITING);
    finishedRun();
    const hold = holdFirst("/sessions/s1/result");
    render(<ChatPage />);
    await waitFor(() => expect(hold.used()).toBe(true), { timeout: 4000 });
    await act(async () => {
      hold.release(JSON.parse(JSON.stringify(RESULT)));
    });
    expect(await screen.findByText("The summary is ready.")).toBeTruthy();
    await waitFor(() =>
      expect(screen.getByTestId("agent-run-words").textContent).toBe("The researcher worked for 2 min 5 s · 6 steps"),
    );
    await waitFor(() => expect(putsCarrying("The summary is ready.").length).toBeGreaterThan(0));
    const saved = putsCarrying("The summary is ready.").at(-1)!;
    expect(saved.path).toBe("/chat/threads/t7");
    expect(readAgentRun((saved.body.messages as Msg[])[1])).toMatchObject({ session: "s1", steps: 6 });
  }, 10_000);
});

describe("a reopened chat's saved step opens to its detail on the real page (re-review)", () => {
  it("the page draws the saved step as a disclosure, and opening it shows the masked detail, never a secret", async () => {
    const KEY = "sk-ant-api03-REREVIEWFAKEFAKE1234";
    storeThread([
      { role: "user", content: "what changed?" },
      {
        role: "assistant",
        content: "Two files changed.",
        steps: [
          {
            name: "shell",
            ok: true,
            ms: 300,
            target: "git",
            // A detail an older or hand-edited thread file holds unmasked:
            // the page reads it back through the one reader, masked again.
            detail: { input: '{"command": "git status --short"}', output: [" M notes.md", `key ${KEY}`].join("\n") },
          },
          { name: "read_file", ok: true, ms: 120, target: "harbor.xlsx" },
        ],
      },
    ]);
    render(<ChatPage />);
    const line = await screen.findByTestId("work-line");
    fireEvent.click(within(line).getAllByRole("button")[0]);
    const rows = within(screen.getByTestId("work-rows")).getAllByTestId("work-row");
    expect(rows.map((r) => r.tagName)).toEqual(["BUTTON", "DIV"]);
    expect(rows[0].getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(rows[0]);
    expect(rows[0].getAttribute("aria-expanded")).toBe("true");
    const panel = screen.getByTestId("step-detail");
    expect(rows[0].getAttribute("aria-controls")).toBe(panel.id);
    expect(within(panel).getByTestId("step-detail-command").textContent).toBe("$ git status --short");
    expect(within(panel).getByTestId("step-detail-output").textContent).toBe([" M notes.md", "key sk-ant-***"].join("\n"));
    expect(document.body.textContent).not.toContain(KEY);
  });
});
