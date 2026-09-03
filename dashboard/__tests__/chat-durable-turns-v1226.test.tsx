/**
 * Chat page durability v1.226.0 (F-D-1 + F-F-3).
 *
 * What carries weight here:
 *  - a chat-lane turn PUTs the user's message BEFORE the first stream frame
 *    (a reload mid-stream used to lose the question with the partial, F-D-1);
 *  - an agent-lane turn marks the last bubble with the session it waits on
 *    (`awaitingSession`), and reopening a thread whose stored session already
 *    completed appends the reply through finalize and saves it with the mark
 *    stripped (F-D-1);
 *  - a 404 on autosave resets the save target so the NEXT save re-creates the
 *    thread via /chat/threads/new (F-F-3);
 *  - any other non-0 status renders the "Couldn't save" chip whose Retry
 *    re-issues the PUT; status 0 stays silent (F-F-3).
 *
 * The page is rendered for real; only the transport hooks are mocked, at the
 * same seams the other dashboard tests use.
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
  return {
    FakeApiError,
    /** Chronological trace of the seams under test. */
    timeline: [] as string[],
    api: {
      gets: [] as string[],
      /** `at` = position in the timeline when the call was issued. */
      posts: [] as { path: string; body: Record<string, unknown>; at: number }[],
      puts: [] as { path: string; body: Record<string, unknown>; at: number }[],
      getResponses: {} as Record<string, unknown>,
      postResponses: {} as Record<string, unknown>,
      /** Queue of PUT outcomes; an Error rejects, anything else resolves. */
      putQueue: [] as unknown[],
    },
    stream: {
      result: { reply: "streamed reply" } as Record<string, unknown>,
    },
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
    H.api.posts.push({ path, body, at: H.timeline.length });
    H.timeline.push(`post ${path}`);
    const r = H.api.postResponses[path];
    if (r instanceof Error) throw r;
    return r ?? {}; // a Promise here defers the answer (D2)
  },
  put: async (path: string, body: Record<string, unknown>) => {
    H.api.puts.push({ path, body, at: H.timeline.length });
    H.timeline.push(`put ${path}`);
    const next = H.api.putQueue.length ? H.api.putQueue.shift() : undefined;
    if (next instanceof Error) throw next;
    if (next !== undefined) return next;
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
    useChatStream: () => ({
      streaming: false,
      text: "",
      tools: [],
      approval: null,
      run: async (
        _body: Record<string, unknown>,
        onDelta: (delta: string, full: string) => void,
      ) => {
        // A real stream's first frame arrives on a later tick.
        await new Promise<void>((r) => setTimeout(r, 0));
        H.timeline.push("frame");
        onDelta("streamed", "streamed");
        return H.stream.result;
      },
      abort: () => {},
    }),
  };
});

vi.mock("@/lib/useEvents", () => ({
  useEvents: () => ({ events: [], connected: false }),
}));
vi.mock("@/lib/useRunStream", () => ({
  useRunStream: () => ({
    text: "",
    tools: [],
    phase: null,
    active: false,
    start: () => {},
    stop: () => {},
  }),
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

type Msg = Record<string, unknown>;

function resetApi() {
  H.timeline.length = 0;
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
  H.stream.result = { reply: "streamed reply" };
}

/** A saved two-bubble thread the page opens from ?thread=t7. */
function storeThread(messages: Msg[]) {
  H.api.getResponses["/chat/threads/t7"] = {
    id: "t7",
    title: "saved",
    messages,
  };
  window.history.replaceState({}, "", "/chat?thread=t7");
}

beforeEach(() => {
  resetApi();
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

const lastMsg = (p: { body: Record<string, unknown> }) => {
  const msgs = p.body.messages as Msg[];
  return msgs[msgs.length - 1];
};

/* ------------------------------------------------------------------------- */
describe("F-D-1 — the in-flight turn is durable", () => {
  it("chat lane: the user's message is PUT before the first stream frame", async () => {
    render(<ChatPage />);
    await typeAndSend("hello there");
    await waitFor(() => expect(H.timeline).toContain("frame"));
    // The save of the typed message landed BEFORE the stream produced anything.
    const putAt = H.timeline.indexOf("put /chat/threads/new");
    expect(putAt).toBeGreaterThanOrEqual(0);
    expect(putAt).toBeLessThan(H.timeline.indexOf("frame"));
    expect(lastMsg(H.api.puts[0])).toEqual({ role: "user", content: "hello there" });
    // The end-of-turn save still runs, onto the id the start save minted.
    await waitFor(() => expect(H.api.puts.length).toBeGreaterThanOrEqual(2));
    expect(H.api.puts[1].path).toBe("/chat/threads/t1");
    expect(lastMsg(H.api.puts[1])).toMatchObject({
      role: "assistant",
      content: "streamed reply",
    });
  });

  it("agent lane: the hand-off is saved with the session on the last bubble", async () => {
    H.stream.result = { reply: "", escalate: true, escalateReason: "needs the agent" };
    H.api.postResponses["/sessions"] = { id: "s1", status: "running" };
    H.api.getResponses["/sessions/s1"] = { session: { id: "s1", status: "running" } };
    render(<ChatPage />);
    await typeAndSend("do the long thing");
    await waitFor(() =>
      expect(H.api.posts.some((p) => p.path === "/sessions")).toBe(true),
    );
    const sess = H.api.posts.find((p) => p.path === "/sessions")!;
    // The chat lane's own pre-save came first: the plain user bubble.
    expect(lastMsg(H.api.puts[0])).toEqual({ role: "user", content: "do the long thing" });
    // sendAgent's OWN pre-save — the hand-off bubble, unmarked — was issued
    // BEFORE POST /sessions (the post-answer mark save cannot satisfy this).
    const handoff = H.api.puts.find(
      (p) => lastMsg(p).escalated === "needs the agent" && !lastMsg(p).awaitingSession,
    );
    expect(handoff).toBeTruthy();
    expect(handoff!.at).toBeLessThan(sess.at);
    // …and once the daemon answered, the last bubble carries the session.
    await waitFor(() =>
      expect(
        H.api.puts.some((p) => lastMsg(p).awaitingSession === "s1"),
      ).toBe(true),
    );
  });

  it("New chat while POST /sessions is airborne: the mark lands in the ORIGINAL box, never the fresh one", async () => {
    H.stream.result = { reply: "", escalate: true, escalateReason: "needs the agent" };
    let answer!: (v: unknown) => void;
    H.api.postResponses["/sessions"] = new Promise((r) => (answer = r));
    H.api.getResponses["/sessions/s1"] = { session: { id: "s1", status: "running" } };
    render(<ChatPage />);
    await typeAndSend("do the long thing");
    await waitFor(() =>
      expect(H.api.posts.some((p) => p.path === "/sessions")).toBe(true),
    );
    // Box A got its id from the pre-saves before the user walked away.
    await waitFor(() =>
      expect(H.api.puts.some((p) => p.path === "/chat/threads/t1")).toBe(true),
    );
    const before = H.api.puts.length;
    fireEvent.click(screen.getByRole("button", { name: /New chat/ }));
    answer({ id: "s1", status: "running" });
    await waitFor(() => expect(H.api.puts.length).toBe(before + 1));
    const mark = H.api.puts[before];
    // Into thread A's box — not /chat/threads/new, which is the fresh chat.
    expect(mark.path).toBe("/chat/threads/t1");
    expect(lastMsg(mark)).toMatchObject({
      role: "assistant",
      escalated: "needs the agent",
      awaitingSession: "s1",
    });
    // No PUT ever carried a role-less bubble (the [] + mark defect).
    for (const p of H.api.puts)
      for (const m of p.body.messages as Msg[]) expect(typeof m.role).toBe("string");
    // The fresh conversation is untouched: thread A's bubbles do not come
    // back into the empty transcript, and nothing waits — a typed message
    // is sendable (Send is disabled while a turn is in flight).
    expect(screen.queryByText("do the long thing")).toBeNull();
    fireEvent.change(await screen.findByPlaceholderText(/Message Iron Jarvis/), {
      target: { value: "fresh" },
    });
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Send" })).toBeEnabled(),
    );
    expect(screen.queryByText("do the long thing")).toBeNull();
    expect(H.api.gets.filter((g) => g === "/sessions/s1")).toEqual([]);
  });

  it("reopening a thread whose stored session completed appends the reply and saves", async () => {
    storeThread([{ role: "user", content: "do the thing", awaitingSession: "s9" }]);
    H.api.getResponses["/sessions/s9"] = {
      session: { id: "s9", status: "completed", summary: "Done: 42" },
    };
    render(<ChatPage />);
    expect(await screen.findByText("Done: 42")).toBeInTheDocument();
    await waitFor(() =>
      expect(H.api.puts.some((p) => p.path === "/chat/threads/t7")).toBe(true),
    );
    const saved = H.api.puts.find((p) => p.path === "/chat/threads/t7")!;
    const msgs = saved.body.messages as Msg[];
    expect(msgs).toHaveLength(2);
    expect(msgs[0]).toEqual({ role: "user", content: "do the thing" }); // mark stripped
    expect(msgs[1]).toMatchObject({
      role: "assistant",
      content: "Done: 42",
      fromSession: "s9",
    });
  });

  it("reopening a thread whose stored session was PRUNED strips the mark quietly — no error", async () => {
    storeThread([{ role: "user", content: "do the thing", awaitingSession: "gone" }]);
    // /sessions/gone is unmocked → the api mock's 404.
    render(<ChatPage />);
    await waitFor(() =>
      expect(H.api.puts.some((p) => p.path === "/chat/threads/t7")).toBe(true),
    );
    const saved = H.api.puts.find((p) => p.path === "/chat/threads/t7")!;
    expect(saved.body.messages).toEqual([{ role: "user", content: "do the thing" }]);
    expect(screen.queryByText(/unmocked GET \/sessions\/gone/)).toBeNull();
    // The wait ended: a typed message can be sent (Send is disabled while a
    // turn is in flight; an empty composer shows the mic instead).
    fireEvent.change(await screen.findByPlaceholderText(/Message Iron Jarvis/), {
      target: { value: "next" },
    });
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Send" })).toBeEnabled(),
    );
  });
});

/* ------------------------------------------------------------------------- */
describe("F-F-3 — autosave failures are never swallowed", () => {
  const saved: Msg[] = [
    { role: "user", content: "hi" },
    { role: "assistant", content: "yo" },
  ];

  it("a 404 resets the target: the next save re-creates via /chat/threads/new", async () => {
    storeThread(saved);
    render(<ChatPage />);
    await screen.findByText("yo");
    H.api.putQueue.push(new H.FakeApiError("thread not found", 404));
    await typeAndSend("more");
    // 404 on t7 → the SAME messages re-queued once to /new → the end-of-turn
    // save onto the id /new minted. Exactly three, then quiet.
    await waitFor(() => expect(H.api.puts.length).toBe(3));
    expect(H.api.puts.map((p) => p.path)).toEqual([
      "/chat/threads/t7",
      "/chat/threads/new",
      "/chat/threads/t1",
    ]);
    expect(H.api.puts[1].body.messages).toEqual(H.api.puts[0].body.messages);
    await new Promise<void>((r) => setTimeout(r, 20));
    expect(H.api.puts.length).toBe(3);
    // No chip for a 404 — the reset + re-queue IS the handling.
    expect(screen.queryByText(/Couldn't save this conversation/)).toBeNull();
  });

  it("a 500 raises the chip; Retry re-issues the PUT", async () => {
    storeThread(saved);
    render(<ChatPage />);
    await screen.findByText("yo");
    H.api.putQueue.push(
      new H.FakeApiError("db locked", 500),
      new H.FakeApiError("db locked", 500),
    );
    await typeAndSend("more");
    expect(
      await screen.findByText(/Couldn't save this conversation: db locked/),
    ).toBeInTheDocument();
    await waitFor(() => expect(H.api.puts.length).toBe(2));
    fireEvent.click(screen.getByRole("button", { name: /Retry/ }));
    await waitFor(() => expect(H.api.puts.length).toBe(3));
    expect(H.api.puts[2].path).toBe("/chat/threads/t7");
    // The retry succeeded — the chip retires.
    await waitFor(() =>
      expect(screen.queryByText(/Couldn't save this conversation/)).toBeNull(),
    );
  });

  it("status 0 stays silent — the offline banner covers it", async () => {
    storeThread(saved);
    render(<ChatPage />);
    await screen.findByText("yo");
    H.api.putQueue.push(
      new H.FakeApiError("Failed to fetch", 0),
      new H.FakeApiError("Failed to fetch", 0),
    );
    await typeAndSend("more");
    await waitFor(() => expect(H.api.puts.length).toBe(2));
    expect(screen.queryByText(/Couldn't save this conversation/)).toBeNull();
  });
});
