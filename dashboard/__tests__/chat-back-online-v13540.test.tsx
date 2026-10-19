/**
 * v1.354.0 (product-readiness G31): the chat's own offline hint clears BY
 * ITSELF when Iron Jarvis answers again.
 *
 * The chat page keeps its own `offline` flag (a status-0 answer to one of its
 * reads). Nothing cleared it but the user — a send, a Retry, another chat —
 * because the page never read the reconnect edge. It now reads
 * lib/daemonEpoch (the edge alone, never the whole /health context) and, on a
 * new epoch, clears the flag and reads the chat list again.
 *
 * The page is rendered for real (chat-durable-turns-v1226's harness); the
 * epoch is the real DaemonEpochContext, moved the way DaemonProvider moves it
 * on an offline->online edge.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";

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
    gets: [] as string[],
    getResponses: {} as Record<string, unknown>,
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: H.FakeApiError,
  API_BASE: "",
  ijToken: () => "",
  get: async (path: string) => {
    H.gets.push(path);
    const r = H.getResponses[path];
    if (r === undefined) throw new H.FakeApiError(`unmocked GET ${path}`, 404);
    if (r instanceof Error) throw r;
    return r;
  },
  post: async () => ({}),
  put: async (path: string) => {
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
    // Mock contract (v1.250.0): every useChatStream mock exports useLiveText.
    useLiveText: (s: { text?: string }) => s?.text ?? "",
    useChatStream: () => ({
      streaming: false,
      text: "",
      tools: [],
      approval: null,
      run: async () => ({ reply: "ok" }),
      abort: () => {},
    }),
  };
});
vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: [], connected: false }) }));
vi.mock("@/lib/useRunStream", () => ({
  useRunStream: () => ({ text: "", tools: [], phase: null, active: false, start: () => {}, stop: () => {} }),
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
  useProviderHealth: () => ({ byProvider: {}, defaultProvider: "", loading: false, stale: false, refresh: () => {} }),
}));
vi.mock("react-markdown", () => ({
  default: ({ children }: { children?: string }) => <div>{children}</div>,
}));
vi.mock("remark-gfm", () => ({ default: () => {} }));

import ChatPage from "@/app/chat/page";
import { DaemonEpochContext } from "@/lib/daemonEpoch";

const HINT = "Chat needs it running to reach your agent.";

function resetApi() {
  H.gets.length = 0;
  for (const k of Object.keys(H.getResponses)) delete H.getResponses[k];
  H.getResponses["/models"] = { models: [] };
  H.getResponses["/chat/personas"] = { personas: [] };
  H.getResponses["/chat/threads"] = { threads: [] };
  H.getResponses["/settings"] = { settings: {} };
  H.getResponses["/projects"] = { projects: [] };
  H.getResponses["/agents/mentionable"] = { agents: [] };
  H.getResponses["/skills"] = { skills: [] };
  H.getResponses["/workflows"] = { workflows: [] };
  H.getResponses["/tools"] = { tools: [] };
  H.getResponses["/undo?session_id=chat"] = { actions: [] };
}

function at(epoch: number) {
  return (
    <DaemonEpochContext.Provider value={epoch}>
      <ChatPage />
    </DaemonEpochContext.Provider>
  );
}

const listReads = () => H.gets.filter((g) => g === "/chat/threads" || g.startsWith("/chat/threads?")).length;

beforeEach(() => {
  resetApi();
  window.localStorage.clear();
  Element.prototype.scrollIntoView = vi.fn();
  // Opening a saved chat while Iron Jarvis is down: the read answers status 0.
  H.getResponses["/chat/threads/t7"] = new H.FakeApiError("Iron Jarvis isn't responding", 0);
  window.history.replaceState({}, "", "/chat?thread=t7");
});
afterEach(() => {
  cleanup();
  window.history.replaceState({}, "", "/chat");
  vi.clearAllMocks();
});

describe("G31 — the chat's offline hint clears by itself", () => {
  it("a status-0 read shows the hint; the reconnect edge clears it and reads the list again", async () => {
    const view = render(at(1));
    expect(await screen.findByText(HINT, { exact: false }, { timeout: 10_000 })).toBeInTheDocument();
    // The hint says the app's name, never "daemon".
    expect(screen.getByText("Iron Jarvis isn't responding.")).toBeInTheDocument();
    const before = listReads();

    // Iron Jarvis answers again: DaemonProvider moves the epoch.
    view.rerender(at(2));
    await waitFor(() => expect(screen.queryByText(HINT, { exact: false })).toBeNull());
    await waitFor(() => expect(listReads()).toBeGreaterThan(before));
  });

  it("CONTROL: with no new epoch the hint stays (only a reconnect clears it)", async () => {
    const view = render(at(1));
    expect(await screen.findByText(HINT, { exact: false }, { timeout: 10_000 })).toBeInTheDocument();
    view.rerender(at(1));
    // Give any stray effect its turn; the hint must still be there.
    await new Promise((r) => setTimeout(r, 50));
    expect(screen.getByText(HINT, { exact: false })).toBeInTheDocument();
  });

  it("CONTROL: a page that was never offline does nothing on the edge (no extra list read)", async () => {
    H.getResponses["/chat/threads/t7"] = { id: "t7", title: "saved", messages: [] };
    const view = render(at(1));
    await waitFor(() => expect(H.gets).toContain("/chat/threads/t7"));
    await waitFor(() => expect(listReads()).toBeGreaterThan(0));
    await new Promise((r) => setTimeout(r, 50));
    const before = listReads();
    view.rerender(at(2));
    await new Promise((r) => setTimeout(r, 50));
    expect(listReads()).toBe(before);
    expect(screen.queryByText(HINT, { exact: false })).toBeNull();
  });
});
