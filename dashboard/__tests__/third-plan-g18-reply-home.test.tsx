/**
 * Third north-star fix plan, group 18 — a reply's home is the daemon (the
 * Chat page's side of the contract). The page for real, transports mocked
 * (the harness of chat-durable-v1336). The daemon is stubbed exactly as the
 * contract describes it: `GET /chat/turns/{id}` -> `{turn_id, status,
 * running, started_at, thread_id}` or 404; a line it writes into a chat
 * itself carries `turnId` and `savedBy: "daemon"`; `chat.thread_updated`
 * announces such a write.
 *
 *  - point 1: the question and the reply the page builds (a stopped one
 *    too) carry the turn's name; a Retry names a NEW turn.
 *  - CS2: the 409 merge matches by name — the daemon's plain line is
 *    replaced by the page's own message, never joined by it; a reply saved
 *    by the daemon is rebuilt once on open when the daemon still has it.
 *  - CS5: the daemon's line arriving while this page follows that reply is
 *    not shown beside the reply being written.
 *  - CS1 / CS10: opening a chat while another chat's reply is on screen
 *    follows the opened chat's own reply; a question whose own reply never
 *    started is never "answered" by the one before it.
 *  - CS3: 60 s of silence asks the daemon first; a reply still running is
 *    followed again once, and its cut text is never saved as the reply.
 *  - CS6: the first reply of a new chat is linked to the chat once the chat
 *    has an id, even when its first save failed.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

type Behaviour =
  | { kind: "reply"; result: Record<string, unknown> }
  | { kind: "fail"; err: Record<string, unknown> }
  | { kind: "gate"; result: Record<string, unknown>; gate: Promise<unknown> };

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
    stalled = false;
    constructor(message: string, status = 0, fields: Record<string, unknown> = {}) {
      super(message, status);
      this.name = "StreamError";
      Object.assign(this, fields);
    }
  }
  return {
    FakeApiError,
    FakeStreamError,
    api: {
      gets: [] as string[],
      posts: [] as { path: string; body: Record<string, unknown> }[],
      puts: [] as { path: string; body: Record<string, unknown> }[],
      /** A value, an Error, or a function of nothing that returns one. */
      getResponses: {} as Record<string, unknown>,
      putQueue: [] as unknown[],
    },
    stream: { live: false, text: "" },
    /** What each run does, in order; empty = reply "streamed reply". */
    plan: [] as Behaviour[],
    runs: [] as { body: Record<string, unknown>; rejoin?: string }[],
    events: { list: [] as { id: string; type: string; payload: unknown }[] },
    /** The daemon's answer to GET /chat/turns/{id} for a name minted at
     *  run time (null = 404, the default). */
    turnRow: null as null | ((id: string) => unknown),
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: H.FakeApiError,
  API_BASE: "",
  ijToken: () => "",
  get: async (path: string) => {
    H.api.gets.push(path);
    let r = H.api.getResponses[path];
    const named = /^\/chat\/turns\/([^/?]+)$/.exec(path);
    if (r === undefined && named && H.turnRow) r = H.turnRow(decodeURIComponent(named[1]));
    if (typeof r === "function") r = (r as () => unknown)();
    if (r === undefined) throw new H.FakeApiError(`unmocked GET ${path}`, 404);
    if (r instanceof Error) throw r;
    return r;
  },
  post: async (path: string, body: Record<string, unknown>) => {
    H.api.posts.push({ path, body });
    return {};
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
  useLiveText: (s: { text?: string }) => s?.text ?? "",
  useChatStream: () => ({
    streaming: H.stream.live,
    text: H.stream.text,
    textStore: { get: () => H.stream.text, subscribe: () => () => {} },
    tools: [],
    approval: null,
    run: async (
      body: Record<string, unknown>,
      _onDelta: (delta: string, full: string) => void,
      _onReset?: () => void,
      opts?: { rejoin?: string },
    ) => {
      H.runs.push({ body, rejoin: opts?.rejoin });
      const b = H.plan.shift() ?? ({ kind: "reply", result: { reply: "streamed reply" } } as Behaviour);
      await new Promise<void>((r) => setTimeout(r, 0));
      if (b.kind === "gate") {
        await b.gate;
        return b.result;
      }
      if (b.kind === "fail") {
        throw new H.FakeStreamError(String(b.err.message ?? "failed"), Number(b.err.status ?? 0), b.err);
      }
      return b.result;
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
  H.plan.length = 0;
  H.runs.length = 0;
  H.stream.live = false;
  H.stream.text = "";
  H.turnRow = null;
}

function storeThread(id: string, messages: Msg[], extra: Record<string, unknown> = {}) {
  H.api.getResponses[`/chat/threads/${id}`] = { id, title: `chat ${id}`, messages, ...extra };
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
const threadPuts = () => H.api.puts.filter((p) => /^\/chat\/threads\/[^/]+$/.test(p.path));
const msgsOf = (p: { body: Record<string, unknown> }) => p.body.messages as Msg[];
const lastPut = () => threadPuts().at(-1)!;
const replies = (list: Msg[]) => list.filter((m) => m.role === "assistant");
const retryShown = () =>
  screen.queryByText(/This didn't get a reply/) !== null ||
  screen.queryAllByRole("button").some((b) => /^Retry/.test(b.textContent ?? ""));

describe("point 1: the question and its reply carry the turn's name", () => {
  it("a sent question and the reply built from its turn are both named", async () => {
    render(<ChatPage />);
    await typeAndSend("hello there");
    await waitFor(() => expect(threadPuts().length).toBe(2));
    const turnId = H.runs[0].body.turn_id as string;
    expect(turnId).toMatch(/^chat-/);
    // The at-send save: the question carries the name.
    expect(msgsOf(threadPuts()[0])).toEqual([expect.objectContaining({ role: "user", turnId })]);
    // The end save: the reply carries it too.
    expect(msgsOf(lastPut()).map((m) => [m.role, m.turnId])).toEqual([
      ["user", turnId],
      ["assistant", turnId],
    ]);
  });

  it("a Retry names a NEW turn for the new question/answer pair", async () => {
    H.plan.push({ kind: "fail", err: { message: "the model is down", status: 502 } });
    render(<ChatPage />);
    await typeAndSend("hello there");
    const retry = await screen.findByRole("button", { name: /^Retry/ });
    fireEvent.click(retry);
    await waitFor(() => expect(H.runs.length).toBe(2));
    const [first, second] = H.runs.map((r) => r.body.turn_id as string);
    expect(second).not.toBe(first);
    await waitFor(() => expect(replies(msgsOf(lastPut())).length).toBe(1));
    expect(msgsOf(lastPut()).map((m) => [m.role, m.turnId])).toEqual([
      ["user", second],
      ["assistant", second],
    ]);
  });

  it("a stopped reply carries the name of the turn it cut", async () => {
    let open!: () => void;
    H.plan.push({ kind: "gate", result: { reply: "late" }, gate: new Promise<void>((r) => (open = r)) });
    H.stream.live = true;
    H.stream.text = "half of it";
    render(<ChatPage />);
    await typeAndSend("long question");
    fireEvent.click(await screen.findByTitle("Stop this turn"));
    await waitFor(() => expect(threadPuts().some((p) => replies(msgsOf(p)).length === 1)).toBe(true));
    const turnId = H.runs[0].body.turn_id as string;
    const saved = threadPuts().find((p) => replies(msgsOf(p)).length === 1)!;
    expect(replies(msgsOf(saved))[0]).toMatchObject({ content: "half of it", interrupted: true, turnId });
    open();
  });
});

describe("CS2: one reply per turn, whoever saved it", () => {
  it("a 409 merge REPLACES the daemon's plain line with the page's own message", async () => {
    H.plan.push({ kind: "reply", result: { reply: "the full reply", tools_used: ["read_file"] } });
    render(<ChatPage />);
    await typeAndSend("hello there");
    await waitFor(() => expect(threadPuts().length).toBe(1));
    // The end save meets a newer chat: the daemon wrote the reply home.
    H.api.putQueue.push(new H.FakeApiError("newer", 409));
    H.api.getResponses["/chat/threads/t1"] = () => {
      const turnId = H.runs[0].body.turn_id as string;
      return {
        id: "t1",
        title: "t",
        updated_at: "2026-11-16T10:00:05Z",
        messages: [
          { role: "user", content: "hello there", turnId },
          { role: "assistant", content: "the full reply", turnId, savedBy: "daemon" },
        ],
      };
    };
    await waitFor(() => expect(threadPuts().length).toBe(3));
    const turnId = H.runs[0].body.turn_id as string;
    const merged = msgsOf(lastPut());
    expect(merged.map((m) => m.role)).toEqual(["user", "assistant"]);
    expect(merged[1]).toMatchObject({ content: "the full reply", turnId, toolsUsed: ["read_file"] });
    expect(merged[1].savedBy).toBeUndefined();
  });

  it("a reply the daemon saved is rebuilt ONCE on open, from its turn, in its place", async () => {
    storeThread("t7", [
      { role: "user", content: "write the notes", turnId: "chat-a1" },
      { role: "assistant", content: "the words only", turnId: "chat-a1", savedBy: "daemon" },
    ]);
    H.api.getResponses["/chat/turns/chat-a1"] = { turn_id: "chat-a1", status: "done", running: false, started_at: null, thread_id: "t7" };
    H.plan.push({ kind: "reply", result: { reply: "the words only", tools_used: ["write_file"] } });
    window.history.replaceState({}, "", "/chat?thread=t7");
    render(<ChatPage />);
    await waitFor(() => expect(threadPuts().length).toBe(1));
    expect(H.runs.map((r) => r.rejoin)).toEqual(["chat-a1"]);
    const saved = msgsOf(lastPut());
    expect(saved.map((m) => m.role)).toEqual(["user", "assistant"]);
    expect(saved[1]).toMatchObject({ content: "the words only", turnId: "chat-a1", toolsUsed: ["write_file"] });
    expect(saved[1].savedBy).toBeUndefined();
    await act(async () => {
      await new Promise((r) => setTimeout(r, 20));
    });
    expect(H.runs.length).toBe(1);
  });

  it("when the daemon no longer has that turn, the plain reply stays and nothing is said", async () => {
    storeThread("t7", [
      { role: "user", content: "write the notes", turnId: "chat-a1" },
      { role: "assistant", content: "the words only", turnId: "chat-a1", savedBy: "daemon" },
    ]);
    window.history.replaceState({}, "", "/chat?thread=t7");
    render(<ChatPage />);
    expect(await screen.findByText("the words only")).toBeInTheDocument();
    await waitFor(() => expect(H.api.gets).toContain("/chat/turns/chat-a1"));
    await act(async () => {
      await new Promise((r) => setTimeout(r, 20));
    });
    expect(H.runs).toEqual([]);
    expect(threadPuts()).toEqual([]);
    expect(screen.queryByRole("alert")).toBeNull();
    expect(retryShown()).toBe(false);
  });
});

describe("CS5: the daemon's line for the reply being followed", () => {
  it("is not shown beside the reply being written, and ends as ONE reply", async () => {
    let open!: () => void;
    H.plan.push({ kind: "gate", result: { reply: "the page's reply" }, gate: new Promise<void>((r) => (open = r)) });
    const { rerender } = render(<ChatPage />);
    await typeAndSend("hello there");
    await waitFor(() => expect(threadPuts().length).toBe(1));
    const turnId = H.runs[0].body.turn_id as string;
    H.api.getResponses["/chat/threads/t1"] = {
      id: "t1",
      title: "t",
      updated_at: "2026-11-16T10:00:05Z",
      messages: [
        { role: "user", content: "hello there", turnId },
        { role: "assistant", content: "plain words from home", turnId, savedBy: "daemon" },
      ],
    };
    H.events.list = [{ id: "e1", type: "chat.thread_updated", payload: { thread_id: "t1", who: "chat" } }];
    rerender(<ChatPage />);
    await waitFor(() => expect(H.api.gets).toContain("/chat/threads/t1"));
    await act(async () => {
      await new Promise((r) => setTimeout(r, 20));
    });
    expect(screen.queryByText("plain words from home")).toBeNull();
    open();
    await waitFor(() => expect(threadPuts().length).toBe(2));
    const saved = msgsOf(lastPut());
    expect(saved.map((m) => [m.role, m.content])).toEqual([
      ["user", "hello there"],
      ["assistant", "the page's reply"],
    ]);
  });
});

describe("CS1 / CS10: opening a chat whose reply the daemon ran", () => {
  it("while ANOTHER chat's reply is on screen, follows the opened chat's reply — no Retry", async () => {
    H.api.getResponses["/chat/threads"] = {
      threads: [{ id: "tB", title: "Second chat", updated_at: "2026-11-16T09:00:00Z" }],
    };
    let openA!: () => void;
    H.plan.push({ kind: "gate", result: { reply: "reply in A" }, gate: new Promise<void>((r) => (openA = r)) });
    render(<ChatPage />);
    await typeAndSend("question in A");
    await waitFor(() => expect(H.runs.length).toBe(1));
    storeThread("tB", [{ role: "user", content: "question in B", at: "2026-11-16T09:00:00Z", turnId: "chat-b1" }]);
    H.api.getResponses["/chat/turns/chat-b1"] = { turn_id: "chat-b1", status: "running", running: true, started_at: "2026-11-16T09:00:01Z", thread_id: "tB" };
    // An older page asked the list; the same answer there, so the only
    // difference between red and green is the page's own busy reading.
    H.api.getResponses["/chat/turns?thread_id=tB&limit=1"] = { turns: [H.api.getResponses["/chat/turns/chat-b1"]] };
    fireEvent.click(await screen.findByText("Second chat"));
    await waitFor(() => expect(H.runs.length).toBe(2));
    expect(H.runs[1].rejoin).toBe("chat-b1");
    expect(retryShown()).toBe(false);
    openA();
  });

  it("a question whose own reply never started is not answered by the reply before it", async () => {
    storeThread("t7", [
      { role: "user", content: "first", turnId: "chat-0" },
      { role: "assistant", content: "first reply", turnId: "chat-0" },
      { role: "user", content: "second", at: "2026-11-16T10:00:00.000Z", turnId: "chat-1" },
    ]);
    // The newest turn of the chat is the one BEFORE: it started 4 s before
    // the question, inside the clock-skew allowance of the old time rule.
    H.api.getResponses["/chat/turns?thread_id=t7&limit=1"] = {
      turns: [{ turn_id: "chat-0", status: "done", running: false, started_at: "2026-11-16T09:59:56Z" }],
    };
    window.history.replaceState({}, "", "/chat?thread=t7");
    render(<ChatPage />);
    expect(await screen.findByText(/This didn't get a reply/)).toBeInTheDocument();
    expect(H.runs).toEqual([]);
  });
});

describe("CS3: 60 s of silence asks the daemon before it says anything", () => {
  const stall = { kind: "fail" as const, err: { message: "Nothing came back", status: 0, stalled: true, partial: "half of the answ", committed: true } };

  it("a reply still running is followed again once, and its cut text is never saved", async () => {
    H.plan.push(stall);
    H.plan.push({ kind: "reply", result: { reply: "the whole answer" } });
    // The daemon still runs the turn this page lost contact with.
    H.turnRow = (id) => ({ turn_id: id, status: "running", running: true, started_at: null, thread_id: "t1" });
    render(<ChatPage />);
    await typeAndSend("a long question");
    await waitFor(() => expect(H.runs.length).toBe(2));
    const turnId = H.runs[0].body.turn_id as string;
    expect(H.runs[1].rejoin).toBe(turnId);
    await waitFor(() => expect(replies(msgsOf(lastPut())).length).toBe(1));
    expect(replies(msgsOf(lastPut()))[0]).toMatchObject({ content: "the whole answer", turnId });
    expect(H.api.puts.some((p) => JSON.stringify(p.body).includes("half of the answ"))).toBe(false);
    expect(retryShown()).toBe(false);
  });

  it("a reply the daemon does not know is said plainly and offered as Retry", async () => {
    H.plan.push(stall);
    render(<ChatPage />);
    await typeAndSend("a long question");
    expect(await screen.findByRole("button", { name: /^Retry/ })).toBeInTheDocument();
    expect(H.runs.length).toBe(1);
    const turnId = H.runs[0].body.turn_id as string;
    expect(H.api.gets).toContain(`/chat/turns/${turnId}`);
    expect(screen.getByTestId("chat-turn-error").textContent).toMatch(/no longer running/);
    expect(screen.getByTestId("chat-turn-error").textContent).not.toMatch(/was stopped/);
  });

  it("is followed again only ONCE; a second silence still never offers Retry over a running reply", async () => {
    H.plan.push(stall);
    H.plan.push(stall);
    H.turnRow = (id) => ({ turn_id: id, status: "running", running: true, started_at: null, thread_id: "t1" });
    render(<ChatPage />);
    await typeAndSend("a long question");
    expect((await screen.findByTestId("chat-notice")).textContent).toMatch(
      /still working on this reply, but this page keeps losing contact/,
    );
    await act(async () => {
      await new Promise((r) => setTimeout(r, 20));
    });
    expect(H.runs.length).toBe(2);
    expect(retryShown()).toBe(false);
    // The cut text is not saved as the reply: Iron Jarvis writes the reply home.
    expect(H.api.puts.some((p) => JSON.stringify(p.body).includes("half of the answ"))).toBe(false);
  });
});

describe("CS6: the first reply of a new chat is linked to it", () => {
  it("once the chat has an id, even when its first save failed", async () => {
    H.api.putQueue.push(new H.FakeApiError("disk busy", 503));
    render(<ChatPage />);
    await typeAndSend("hello there");
    await waitFor(() => expect(threadPuts().length).toBe(2));
    const turnId = H.runs[0].body.turn_id as string;
    await waitFor(() =>
      expect(H.api.posts.some((p) => p.path === `/chat/turns/${encodeURIComponent(turnId)}/thread`)).toBe(true),
    );
    const link = H.api.posts.find((p) => p.path.endsWith("/thread"))!;
    expect(link.body).toEqual({ thread_id: "t1" });
  });
});

describe("SB3 (decision G7): a chat the browser sidebar wrote", () => {
  const commThread = (id: string, channel: string) => {
    storeThread(id, [{ role: "user", content: "from elsewhere" }, { role: "assistant", content: "an answer" }], {
      owner: "daemon",
      comm_channel: channel,
      comm_display: channel,
      updated_at: "2026-11-16T10:00:00Z",
    });
    window.history.replaceState({}, "", `/chat?thread=${id}`);
  };
  const sentAnywhere = () =>
    H.api.posts.filter((p) => p.path.startsWith("/comm/threads/") || p.path === "/chat" || p.path === "/chat/stream");

  it("a message typed into it is not sent: the page says where to carry it on, and keeps the words", async () => {
    commThread("tS", "browser sidebar");
    render(<ChatPage />);
    expect(await screen.findByText("an answer")).toBeInTheDocument();
    const box = (await screen.findByPlaceholderText(/Message Iron Jarvis/)) as HTMLTextAreaElement;
    fireEvent.change(box, { target: { value: "and one more thing" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() =>
      expect(screen.getByTestId("chat-notice").textContent).toContain(
        "This chat is from the browser sidebar. To carry it on, ask in the sidebar.",
      ),
    );
    await act(async () => {
      await new Promise((r) => setTimeout(r, 20));
    });
    expect(box.value).toBe("and one more thing");
    expect(sentAnywhere()).toEqual([]);
    expect(H.runs).toEqual([]);
  });

  it("control: a phone chat still sends through its own door", async () => {
    commThread("tP", "telegram");
    render(<ChatPage />);
    expect(await screen.findByText("an answer")).toBeInTheDocument();
    const box = (await screen.findByPlaceholderText(/Message Iron Jarvis/)) as HTMLTextAreaElement;
    fireEvent.change(box, { target: { value: "and one more thing" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(H.api.posts.some((p) => p.path === "/comm/threads/tP/send")).toBe(true));
    expect(H.api.posts.find((p) => p.path === "/comm/threads/tP/send")!.body).toEqual({ text: "and one more thing" });
    expect(H.runs).toEqual([]);
    expect(screen.queryByText(/This chat is from the browser sidebar/)).toBeNull();
  });

  it("the chat list's badge says where each kind comes from", async () => {
    H.api.getResponses["/chat/threads"] = {
      threads: [
        { id: "tS", title: "Sidebar talk", owner: "daemon", comm_channel: "browser sidebar", updated_at: "2026-11-16T10:00:00Z" },
        { id: "tP", title: "Phone talk", owner: "daemon", comm_channel: "telegram", updated_at: "2026-11-16T09:00:00Z" },
      ],
    };
    render(<ChatPage />);
    await screen.findByText("Sidebar talk");
    const sidebarBadges = await screen.findAllByText("Browser sidebar");
    expect(sidebarBadges.map((b) => b.getAttribute("title"))).toContain("From the browser sidebar");
    expect(sidebarBadges.some((b) => b.getAttribute("title") === "Messaging thread")).toBe(false);
    const phoneBadges = screen.getAllByText("Telegram");
    expect(phoneBadges.map((b) => b.getAttribute("title"))).toContain("Messaging thread");
    expect(phoneBadges.some((b) => b.getAttribute("title") === "From the browser sidebar")).toBe(false);
  });
});
