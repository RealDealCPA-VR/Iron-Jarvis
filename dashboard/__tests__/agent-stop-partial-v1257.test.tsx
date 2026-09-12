/**
 * S-02 (v1.257.0), the DATA-LOSS guard: pressing Stop on an agent turn keeps
 * what the agent had already written.
 *
 * Plain words: if you stop an agent mid-answer, the part it had already written
 * stays in the conversation. It is not replaced by the word "Stopped."
 *
 * WHY THIS PIN EXISTS: S-02 moved the agent's live text out of React state and
 * into a store, so the page stops re-rendering per token. The Stop handler read
 * `runStream.text` to save the partial reply — a field that is now always empty.
 * Without this pin, a pure speed change would silently start throwing away the
 * user's answer whenever they pressed Stop. The handler reads the STORE instead,
 * and this test is what holds it there.
 *
 * The harness mirrors chat-durable-turns-v1226: the same fake api/hook mocks, so
 * the REAL chat page renders and the REAL Stop button is pressed.
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
    api: {
      gets: [] as string[],
      posts: [] as { path: string; body: Record<string, unknown> }[],
      puts: [] as { path: string; body: Record<string, unknown> }[],
      getResponses: {} as Record<string, unknown>,
      postResponses: {} as Record<string, unknown>,
    },
    /** What the agent had streamed before Stop was pressed. */
    partial: "the first half of the answer",
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: H.FakeApiError,
  API_BASE: "",
  ijToken: () => "",
  sseUrl: (p: string) => `http://localhost/stub${p}`,
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

// THE POINT OF THE HARNESS: this mock carries a textStore holding the partial,
// with `text` EMPTY — exactly the shape the real hook now has under
// `textInState: false`. A handler that reads `.text` sees nothing.
vi.mock("@/lib/useRunStream", () => ({
  useRunStream: () => ({
    text: "",
    textStore: { get: () => H.partial, subscribe: () => () => {} },
    tools: [],
    phase: null,
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

type Msg = Record<string, unknown>;

function resetApi() {
  H.api.gets.length = 0;
  H.api.posts.length = 0;
  H.api.puts.length = 0;
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
}

/** Open a saved thread that is WAITING on an agent session — the state in which
 *  the page shows a Stop button. */
function storeWaitingThread() {
  H.api.getResponses["/chat/threads/t7"] = {
    id: "t7",
    title: "saved",
    messages: [
      { role: "user", content: "do the long thing" },
      { role: "assistant", content: "", escalated: "needs the agent", awaitingSession: "s1" },
    ],
  };
  H.api.getResponses["/sessions/s1"] = { session: { id: "s1", status: "running" } };
  window.history.replaceState({}, "", "/chat?thread=t7");
}

const lastMsg = (p: { body: Record<string, unknown> }) => {
  const msgs = p.body.messages as Msg[];
  return msgs[msgs.length - 1];
};

describe("stopping an agent turn keeps the partial answer", () => {
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

  it("saves what the agent had written, not the word Stopped", async () => {
    storeWaitingThread();
    render(<ChatPage />);

    // The real control, and it must be THERE — a click on a button that does not
    // exist (or is disabled) asserts nothing, which is how three tests passed
    // vacuously in v1.251.0.
    const stop = await screen.findByRole("button", { name: /Stop/ });
    expect(stop).toBeEnabled();
    fireEvent.click(stop);

    await waitFor(() => expect(H.api.puts.length).toBeGreaterThan(0));
    const saved = lastMsg(H.api.puts[H.api.puts.length - 1]);

    expect(saved).toMatchObject({ role: "assistant", content: H.partial });
    expect(saved.content).not.toBe("Stopped.");
    // A cut-off reply is marked as such, so the transcript is honest about it.
    expect(saved.interrupted).toBe(true);
  });
});
