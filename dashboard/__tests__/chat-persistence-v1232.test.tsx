/**
 * Chat persistence, audit Wave 6 (v1.232.0) — CL2, CL3, CL5, A10.
 *
 * Converted from __audit_20260904__/chat-persistence-audit R3/R4 and
 * chat-session-approvals.audit D2 (those asserted the BUG; these assert the
 * behaviour delivered):
 *  - CL2: the "Couldn't save" Retry re-sends the array ON SCREEN, and the chip
 *    retires only when the LATEST queued save for the conversation lands —
 *    an older save landing first does not clear it;
 *  - CL3: a save carries the `updated_at` this window loaded; a 409 (another
 *    window saved) refetches and appends this window's new bubbles onto the
 *    server array instead of clobbering, and later saves in the same turn
 *    stay rebased;
 *  - CL5: a reopened thread that ends on an unanswered question offers the
 *    same Retry a failed turn does ("This didn't get a reply");
 *  - A10: while a reload resumes a wait, the 1.5 s poll also reads
 *    /chat/approvals/pending for that session so the card appears with no
 *    live event.
 *
 * Same harness as chat-durable-turns-v1226: the page is rendered for real,
 * only the transport hooks are mocked.
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
  useChatStream: () => ({
    streaming: false,
    text: "",
    tools: [],
    approval: null,
    run: async (
      _body: Record<string, unknown>,
      onDelta: (delta: string, full: string) => void,
    ) => {
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

/* ------------------------------------------------------------------------- */
describe("CL2 — the 'Couldn't save' Retry sends the truth and the chip waits for the latest save", () => {
  it("Retry re-sends the array on screen (reply included); an older save landing does not retire the chip", async () => {
    storeThread([
      { role: "user", content: "hi" },
      { role: "assistant", content: "yo" },
    ]);
    render(<ChatPage />);
    await screen.findByText("yo");
    let landEndSave!: (v: unknown) => void;
    const endSave = new Promise((r) => (landEndSave = r));
    let landRetry!: (v: unknown) => void;
    const retrySave = new Promise((r) => (landRetry = r));
    // PUT#1 (turn start) fails -> chip. PUT#2 (turn end, WITH the reply)
    // hangs. PUT#3 (the retry) hangs too, so the window between #2 landing
    // and #3 landing is observable.
    H.api.putQueue.push(new H.FakeApiError("db locked", 500), endSave, retrySave);
    await typeAndSend("more");
    expect(await screen.findByText(/Couldn't save this conversation/)).toBeInTheDocument();
    await waitFor(() => expect(H.api.puts.length).toBe(2));
    expect(lastMsg(H.api.puts[1])).toMatchObject({ role: "assistant", content: "streamed reply" });

    fireEvent.click(screen.getByRole("button", { name: /Retry/ }));
    // Clicking does not clear the chip — it says "Retrying…" until it lands.
    expect(await screen.findByRole("button", { name: /Retrying/ })).toBeDisabled();
    expect(chip()).not.toBeNull();

    landEndSave({ id: "t7", title: "t" });
    await waitFor(() => expect(H.api.puts.length).toBe(3));
    // PUT#3 is the retry: the TRUTH on screen, reply included — not the
    // failed save's snapshot from before the reply.
    const final = msgsOf(H.api.puts[2]);
    expect(final[final.length - 1]).toMatchObject({ role: "assistant", content: "streamed reply" });
    expect(final.some((m) => m.content === "more")).toBe(true);
    // PUT#2 (an OLDER save) landed; the latest (#3) has not — chip stays.
    expect(chip()).not.toBeNull();

    landRetry({ id: "t7", title: "t" });
    await waitFor(() => expect(chip()).toBeNull());
    expect(screen.getByText("streamed reply")).toBeInTheDocument();
  });
});

/* ------------------------------------------------------------------------- */
describe("CL3 — two windows on one thread", () => {
  it("sends the loaded updated_at; on 409 it refetches and appends its own bubbles onto the server array, and the end-of-turn save stays rebased", async () => {
    const T1 = "2026-09-05T10:00:00";
    const T2 = "2026-09-05T10:05:00";
    storeThread(
      [
        { role: "user", content: "hi" },
        { role: "assistant", content: "yo" },
      ],
      T1,
    );
    render(<ChatPage />);
    await screen.findByText("yo");
    // The OTHER window adds a turn: the row is now newer than T1.
    H.api.getResponses["/chat/threads/t7"] = {
      id: "t7",
      title: "saved",
      messages: [
        { role: "user", content: "hi" },
        { role: "assistant", content: "yo" },
        { role: "user", content: "from the other window" },
        { role: "assistant", content: "other reply" },
      ],
      updated_at: T2,
    };
    H.api.putQueue.push(new H.FakeApiError("updated elsewhere", 409));
    await typeAndSend("more");
    await waitFor(() => expect(H.api.puts.length).toBe(3));
    // PUT#1 carried the stamp this window loaded and was refused.
    expect(H.api.puts[0].body.if_updated_at).toBe(T1);
    // PUT#2 is the rebase: the server's four + this window's new bubble,
    // under the stamp the refetch returned — nothing of the other window's
    // work was clobbered.
    expect(H.api.puts[1].body.if_updated_at).toBe(T2);
    expect(msgsOf(H.api.puts[1]).map((m) => m.content)).toEqual([
      "hi", "yo", "from the other window", "other reply", "more",
    ]);
    // PUT#3 (turn end) was built by the turn's closure on the OLD array and
    // is still rebased onto the server copy.
    expect(msgsOf(H.api.puts[2]).map((m) => m.content)).toEqual([
      "hi", "yo", "from the other window", "other reply", "more", "streamed reply",
    ]);
    // The screen shows the merged conversation.
    expect(await screen.findByText("from the other window")).toBeInTheDocument();
    expect(screen.getByText("streamed reply")).toBeInTheDocument();
    expect(chip()).toBeNull();
  });

  it("a thread with no stamp saves unconditionally (older daemon)", async () => {
    storeThread([
      { role: "user", content: "hi" },
      { role: "assistant", content: "yo" },
    ]);
    render(<ChatPage />);
    await screen.findByText("yo");
    await typeAndSend("more");
    await waitFor(() => expect(H.api.puts.length).toBe(2));
    expect("if_updated_at" in H.api.puts[0].body).toBe(false);
  });
});

/* ------------------------------------------------------------------------- */
describe("CL5 — a reopened thread that ends on an unanswered question", () => {
  it("offers 'This didn't get a reply' + Retry, and Retry re-sends without duplicating the question", async () => {
    storeThread([{ role: "user", content: "rename all the files please" }]);
    render(<ChatPage />);
    expect(await screen.findByText("rename all the files please")).toBeInTheDocument();
    expect(await screen.findByText(/This didn't get a reply/)).toBeInTheDocument();
    const retry = screen.getByRole("button", { name: /Retry/ });
    // No wait resumes: nothing to poll.
    expect(H.api.gets.filter((g) => g.startsWith("/sessions/"))).toEqual([]);
    fireEvent.click(retry);
    expect(await screen.findByText("streamed reply")).toBeInTheDocument();
    await waitFor(() => expect(H.api.puts.length).toBe(2));
    expect(msgsOf(H.api.puts[1]).map((m) => m.content)).toEqual([
      "rename all the files please",
      "streamed reply",
    ]);
    expect(screen.queryByText(/This didn't get a reply/)).toBeNull();
  });

  it("a thread that ends on a reply shows neither", async () => {
    storeThread([
      { role: "user", content: "hi" },
      { role: "assistant", content: "yo" },
    ]);
    render(<ChatPage />);
    await screen.findByText("yo");
    expect(screen.queryByRole("button", { name: /Retry/ })).toBeNull();
    expect(screen.queryByText(/This didn't get a reply/)).toBeNull();
  });

  it("a MESSAGING thread that ends on the phone's message (daemon still answering) offers neither the line nor the chat-lane Retry", async () => {
    // comm/inbound appends the user turn BEFORE it asks the model, so a
    // daemon-owned thread ends on a user bubble while the reply is composed;
    // the chat-lane Retry could not reach disk or the phone from here.
    storeThread([{ role: "user", content: "what is my balance" }], undefined, {
      owner: "daemon",
      comm_channel: "telegram",
      comm_display: "Telegram",
    });
    render(<ChatPage />);
    expect(await screen.findByText("what is my balance")).toBeInTheDocument();
    // Settle: the line would render in the same pass as the origin banner.
    await screen.findByText(/Telegram/);
    expect(screen.queryByText(/This didn't get a reply/)).toBeNull();
    expect(screen.queryByRole("button", { name: /Retry/ })).toBeNull();
  });

  it("a chat.thread_updated refetch that brings the reply retires the Retry", async () => {
    storeThread([{ role: "user", content: "rename all the files please" }]);
    const view = render(<ChatPage />);
    expect(await screen.findByText(/This didn't get a reply/)).toBeInTheDocument();
    // The server now holds the answer; the socket announces the write.
    H.api.getResponses["/chat/threads/t7"] = {
      id: "t7",
      title: "saved",
      messages: [
        { role: "user", content: "rename all the files please" },
        { role: "assistant", content: "done, renamed 3 files" },
      ],
    };
    H.events.list = [
      { id: "ev1", type: "chat.thread_updated", payload: { thread_id: "t7" } },
    ];
    view.rerender(<ChatPage />);
    expect(await screen.findByText("done, renamed 3 files")).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByText(/This didn't get a reply/)).toBeNull());
    expect(screen.queryByRole("button", { name: /Retry/ })).toBeNull();
  });
});

/* ------------------------------------------------------------------------- */
describe("A10 — a reload resuming a paused run shows the pending ask without a live event", () => {
  it("polls /chat/approvals/pending for the awaited session and renders one card per ask; a cleared ask drops on the next poll", async () => {
    storeThread([{ role: "user", content: "rename the files", awaitingSession: "s9" }]);
    H.api.getResponses["/sessions/s9"] = { session: { id: "s9", status: "active" } };
    H.api.getResponses["/chat/approvals/pending"] = {
      approvals: [
        { id: "apr_9", tool: "rename_file", session_id: "s9", requested_at: "2026-09-04T00:00:00" },
        { id: "apr_other", tool: "shell", session_id: "s_other", requested_at: "2026-09-04T00:00:00" },
      ],
    };
    render(<ChatPage />);
    await waitFor(
      () => expect(H.api.gets.filter((g) => g === "/sessions/s9").length).toBeGreaterThan(0),
      { timeout: 4000 },
    );
    await waitFor(() => expect(H.api.gets.includes("/chat/approvals/pending")).toBe(true), {
      timeout: 4000,
    });
    // Exactly the awaited session's ask — the other session's is not ours.
    await waitFor(() =>
      expect(screen.queryAllByRole("button", { name: /Allow once/ }).length).toBe(1),
    );
    expect(screen.getAllByText(/rename_file/).length).toBeGreaterThan(0);
    // Answered elsewhere: the list empties and the card goes on the next tick.
    H.api.getResponses["/chat/approvals/pending"] = { approvals: [] };
    await waitFor(
      () => expect(screen.queryAllByRole("button", { name: /Allow once/ }).length).toBe(0),
      { timeout: 4000 },
    );
  });
});
