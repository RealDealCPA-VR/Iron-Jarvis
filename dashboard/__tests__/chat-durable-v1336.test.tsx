/**
 * v1.336.0 — the chat page rejoins an answer the daemon kept running.
 *
 * A NAMED turn is the daemon's own task now (daemon/durable_turns): a
 * reload, a closed window or leaving the chat no longer ends it. Reopening a
 * chat whose last message is the unanswered question asks
 * GET /chat/turns?thread_id= and, when the newest turn started after that
 * question, FOLLOWS it (useChatStream.run with { rejoin }) and saves its
 * reply like any other; otherwise it is the failed turn it always was (CL5,
 * Retry). The messages waiting for the reply come back too. Same harness as
 * chat-persistence-v1232 (the page for real, transports mocked).
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const H = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 500) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  class FakeStreamError extends FakeApiError {
    committed = false;
    offline = false;
    partial = "";
  }
  return {
    FakeApiError,
    FakeStreamError,
    api: {
      gets: [] as string[],
      posts: [] as { path: string; body: Record<string, unknown> }[],
      puts: [] as { path: string; body: Record<string, unknown> }[],
      getResponses: {} as Record<string, unknown>,
      postResponses: {} as Record<string, unknown>,
      /** Queue of PUT outcomes: an Error rejects, a Promise is awaited, any
       *  other value resolves; empty = the default `{id, title}`. */
      putQueue: [] as unknown[],
    },
    stream: {
      result: { reply: "streamed reply" } as Record<string, unknown>,
      gate: null as Promise<unknown> | null,
    },
    runs: [] as { body: Record<string, unknown>; rejoin?: string }[],
    /** What `useEvents` hands the page; a test swaps the array and rerenders
     *  to deliver a frame (the socket is one subscriber the page reads). */
    events: { list: [] as { id: string; type: string; payload: unknown }[] },
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: H.FakeApiError,
  API_BASE: "",
  ijToken: () => "",
  get: async (path: string) => {
    H.api.gets.push(path);
    const r = H.api.getResponses[path];
    if (r === undefined) throw new H.FakeApiError(`unmocked GET ${path}`, 404);
    if (r instanceof Error) throw r;
    return r;
  },
  post: async (path: string, body: Record<string, unknown>) => {
    H.api.posts.push({ path, body });
    const r = H.api.postResponses[path];
    if (r instanceof Error) throw r;
    return r ?? {};
  },
  put: async (path: string, body: Record<string, unknown>) => {
    H.api.puts.push({ path, body });
    const next = H.api.putQueue.length ? H.api.putQueue.shift() : undefined;
    if (next instanceof Error) throw next;
    if (next !== undefined) return next;
    const m = /^\/chat\/threads\/(.+)$/.exec(path);
    return { id: m && m[1] !== "new" ? m[1] : "t1", title: "t" };
  },
  del: async () => ({}),
}));

vi.mock("@/lib/useChatStream", () => ({
  StreamError: H.FakeStreamError,
  // v1.250.0 (S-03): the live bubble's text comes through this hook; with the
  // module mocked it falls back to the plain `text` field, as the real one does.
  useLiveText: (s: { text?: string }) => s?.text ?? "",
  useChatStream: () => ({
    streaming: false,
    text: "",
    tools: [],
    approval: null,
    run: async (
      _body: Record<string, unknown>,
      onDelta: (delta: string, full: string) => void,
      _onReset?: () => void,
      opts?: { rejoin?: string },
    ) => {
      H.runs.push({ body: _body, rejoin: opts?.rejoin });
      await new Promise<void>((r) => setTimeout(r, 0));
      onDelta("streamed", "streamed");
      if (H.stream.gate) await H.stream.gate;
      return H.stream.result;
    },
    abort: () => {},
  }),
}));
vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: H.events.list, connected: false }) }));
vi.mock("@/lib/useRunStream", () => ({
  useRunStream: () => ({ text: "", tools: [], phase: null, active: false, start: () => {}, stop: () => {} }),
}));
vi.mock("@/lib/useDictation", () => ({
  useDictation: () => ({
    supported: false, reason: null, engine: null, listening: false, processing: false,
    transcript: "", interim: "", error: null, start: () => {}, stop: () => {}, reset: () => {},
  }),
}));
vi.mock("@/lib/useTTS", () => ({
  useTTS: () => ({
    supported: false, enabled: false, speaking: false, enable: () => {}, disable: () => {},
    toggle: () => {}, speak: () => {}, resetStream: () => {}, speakMore: () => {}, cancel: () => {},
  }),
}));
vi.mock("@/lib/useProviderHealth", () => ({
  useProviderHealth: () => ({ byProvider: {}, defaultProvider: "", loading: false, stale: false, refresh: () => {} }),
}));
vi.mock("react-markdown", () => ({ default: ({ children }: { children?: string }) => <div>{children}</div> }));
vi.mock("remark-gfm", () => ({ default: () => {} }));

import ChatPage from "@/app/chat/page";

type Msg = Record<string, unknown>;

function resetApi() {
  H.api.gets.length = 0;
  H.api.posts.length = 0;
  H.api.puts.length = 0;
  H.api.putQueue.length = 0;
  for (const k of Object.keys(H.api.getResponses)) delete H.api.getResponses[k];
  for (const k of Object.keys(H.api.postResponses)) delete H.api.postResponses[k];
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
  H.api.getResponses["/chat/approvals/pending"] = { approvals: [] };
  H.stream.result = { reply: "streamed reply" };
  H.stream.gate = null;
  H.runs.length = 0;
}

function storeThread(
  messages: Msg[],
  updated_at?: string,
  extra: Record<string, unknown> = {},
) {
  H.api.getResponses["/chat/threads/t7"] = {
    id: "t7",
    title: "saved",
    messages,
    ...(updated_at ? { updated_at } : {}),
    ...extra,
  };
  window.history.replaceState({}, "", "/chat?thread=t7");
}

beforeEach(() => {
  resetApi();
  H.events.list = [];
  window.history.replaceState({}, "", "/chat");
  window.localStorage.clear();
  Element.prototype.scrollIntoView = vi.fn();
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

async function typeAndSend(text: string) {
  const box = await screen.findByPlaceholderText(/Message Iron Jarvis/);
  fireEvent.change(box, { target: { value: text } });
  fireEvent.click(screen.getByRole("button", { name: "Send" }));
}
const msgsOf = (p: { body: Record<string, unknown> }) => p.body.messages as Msg[];
const lastMsg = (p: { body: Record<string, unknown> }) => msgsOf(p)[msgsOf(p).length - 1];
const chip = () => screen.queryByText(/Couldn't save this conversation/);


const ASKED = "2026-10-12T10:00:00.000Z";
const turns = (t: Record<string, unknown>[]) => {
  H.api.getResponses["/chat/turns?thread_id=t7&limit=1"] = { turns: t };
};

describe("reopening a chat the daemon is still answering", () => {
  it("follows the running turn instead of offering Retry, and saves its reply", async () => {
    storeThread([{ role: "user", content: "summarise the ledger", at: ASKED }]);
    turns([{ turn_id: "turn-9", status: "running", running: true, started_at: "2026-10-12T10:00:00.400Z" }]);
    render(<ChatPage />);
    expect(await screen.findByText("streamed reply")).toBeInTheDocument();
    expect(H.runs.map((r) => r.rejoin)).toEqual(["turn-9"]);
    await waitFor(() => expect(H.api.puts.length).toBe(1));
    expect(msgsOf(H.api.puts[0]).map((m) => m.content)).toEqual([
      "summarise the ledger",
      "streamed reply",
    ]);
    expect(screen.queryByText(/This didn't get a reply/)).toBeNull();
  });

  it("a turn that finished while nobody watched is followed the same way", async () => {
    storeThread([{ role: "user", content: "q", at: ASKED }]);
    turns([{ turn_id: "turn-10", status: "done", running: false, started_at: "2026-10-12T10:00:01Z" }]);
    render(<ChatPage />);
    expect(await screen.findByText("streamed reply")).toBeInTheDocument();
    expect(H.runs.map((r) => r.rejoin)).toEqual(["turn-10"]);
  });

  it("a turn older than the question does not answer it: Retry, as before", async () => {
    storeThread([{ role: "user", content: "q", at: ASKED }]);
    turns([{ turn_id: "old", status: "done", running: false, started_at: "2026-10-12T09:00:00Z" }]);
    render(<ChatPage />);
    expect(await screen.findByText(/This didn't get a reply/)).toBeInTheDocument();
    expect(H.runs).toEqual([]);
  });

  it("an older daemon (no turn list) keeps the Retry", async () => {
    storeThread([{ role: "user", content: "q", at: ASKED }]);
    render(<ChatPage />);
    expect(await screen.findByText(/This didn't get a reply/)).toBeInTheDocument();
    expect(H.runs).toEqual([]);
  });

  it("messages that were waiting come back, and go out after the rejoined reply finishes cleanly", async () => {
    storeThread([{ role: "user", content: "q", at: ASKED }], undefined, {
      queued: [{ id: "w1", text: "and then the totals", files: [], appRes: [], page: null, refs: [] }],
    });
    turns([{ turn_id: "turn-11", status: "running", running: true, started_at: "2026-10-12T10:00:01Z" }]);
    let open!: () => void;
    H.stream.gate = new Promise<void>((r) => (open = r));
    render(<ChatPage />);
    expect((await screen.findByTestId("queued-message")).textContent).toContain("and then the totals");
    open();
    // The rejoined turn ends cleanly, then the waiting message is SENT.
    await waitFor(() => expect(H.runs.length).toBe(2));
    expect(H.runs[1].rejoin).toBeUndefined();
    const sent = H.runs[1].body.messages as Msg[];
    expect(sent[sent.length - 1]).toMatchObject({ role: "user", content: "and then the totals" });
    // ...and the daemon is told nothing waits any more.
    await waitFor(() =>
      expect(H.api.puts.some((p) => p.path === "/chat/threads/t7/queue" && (p.body.items as unknown[]).length === 0)).toBe(true),
    );
  });
});

describe("a new chat's first question", () => {
  it("links its turn to the chat once the first save names it", async () => {
    render(<ChatPage />);
    await typeAndSend("hello there");
    await waitFor(() =>
      expect(H.api.posts.some((p) => /^\/chat\/turns\/[^/]+\/thread$/.test(p.path))).toBe(true),
    );
    const link = H.api.posts.find((p) => p.path.endsWith("/thread"))!;
    expect(link.body).toEqual({ thread_id: "t1" });
    const turnId = H.runs[0].body.turn_id as string;
    expect(link.path).toBe(`/chat/turns/${encodeURIComponent(turnId)}/thread`);
  });
});
