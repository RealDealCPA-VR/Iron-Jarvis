/**
 * v1.506.0 (north-star fix plan 6.5, decision D11): the hotkey quick window
 * goes through CHAT. It used to start a coding agent (`POST /sessions`,
 * agent_type "builder") that holds none of chat's settings tools, record
 * tools, lookup or doors, so "switch to dark mode" did nothing and said Done.
 *
 * Now the desktop opens the main window on `/chat?new=1&quick=1` and keeps the
 * words in the MAIN process; the chat page takes them once over the shell's
 * bridge (`window.ironjarvis.quickAsk.take`, sender-checked in main.js) and
 * sends them as the first message of a fresh chat, the same `send` the
 * composer's Enter calls. So the turn has every chat tool, card and door,
 * hands off to an agent run only when chat does, and is saved like any chat.
 *
 * Driven on the REAL page (transport mocked, as approval-redirect-page-review
 * does):
 *  - `?quick=1` with words waiting sends them as the user's message, once;
 *  - the words never ride in the URL: `?ask=` still only fills the box
 *    (the consent rule), and `?quick=1` with nothing waiting sends nothing;
 *  - outside the desktop (no bridge) `?quick=1` does nothing.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";

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
      posts: [] as { path: string; body: Record<string, unknown> }[],
      puts: [] as { path: string; body: Record<string, unknown> }[],
      getResponses: {} as Record<string, unknown>,
    },
    stream: {
      bodies: [] as Record<string, unknown>[],
      release: null as null | ((r: Record<string, unknown>) => void),
    },
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: H.FakeApiError,
  API_BASE: "",
  ijToken: () => "",
  get: async (path: string) => {
    const r = H.api.getResponses[path];
    if (r === undefined) throw new H.FakeApiError(`unmocked GET ${path}`, 404);
    return r;
  },
  post: async (path: string, body: Record<string, unknown>) => {
    H.api.posts.push({ path, body });
    return {};
  },
  put: async (path: string, body: Record<string, unknown>) => {
    H.api.puts.push({ path, body });
    const m = /^\/chat\/threads\/(.+)$/.exec(path);
    return { id: m && m[1] !== "new" ? m[1] : "t1", title: "t" };
  },
  del: async () => ({}),
}));

vi.mock("@/lib/useChatStream", () => ({
  StreamError: H.FakeStreamError,
  useLiveText: (s: { text?: string }) => s?.text ?? "",
  useChatStream: () => ({
    streaming: false,
    text: "",
    tools: [],
    approval: null,
    mcpAsks: [],
    run: (body: Record<string, unknown>) => {
      H.stream.bodies.push(body);
      return new Promise<Record<string, unknown>>((res) => {
        H.stream.release = res;
      });
    },
    abort: () => {},
  }),
}));
vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: [], connected: false }) }));
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
import { QUICK_ASK_PARAM, takeQuickAsk } from "@/lib/chatAsk";

type Bridge = { isDesktop: boolean; quickAsk?: { take: () => Promise<unknown> } };
const W = window as unknown as { ironjarvis?: Bridge };

let take: ReturnType<typeof vi.fn<() => Promise<unknown>>>;

beforeEach(() => {
  H.api.posts.length = 0;
  H.api.puts.length = 0;
  H.stream.bodies.length = 0;
  H.stream.release = null;
  H.api.getResponses = {
    "/models": { models: [] },
    "/chat/personas": { personas: [] },
    "/chat/threads": { threads: [] },
    "/settings": { settings: {} },
    "/projects": { projects: [] },
    "/agents/mentionable": { agents: [] },
    "/skills": { skills: [] },
    "/workflows": { workflows: [] },
    "/tools": { tools: [] },
    "/undo?session_id=chat": { actions: [] },
    "/chat/approvals/pending": { approvals: [] },
  };
  take = vi.fn(async () => "switch to dark mode");
  W.ironjarvis = { isDesktop: true, quickAsk: { take } };
  window.localStorage.clear();
  Element.prototype.scrollIntoView = vi.fn();
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  delete W.ironjarvis;
  window.history.replaceState({}, "", "/chat");
});

const box = () => screen.getByTestId("chat-composer").querySelector("textarea") as HTMLTextAreaElement;

describe("the quick window's words are sent in chat (north-star 6.5)", () => {
  it("?quick=1 takes the waiting words once and sends them as the first message", async () => {
    window.history.replaceState({}, "", `/chat?new=1&${QUICK_ASK_PARAM}=1`);
    render(<ChatPage />);
    await waitFor(() => expect(H.stream.bodies).toHaveLength(1));
    expect(take).toHaveBeenCalledTimes(1);
    const history = H.stream.bodies[0].messages as { role: string; content: string }[];
    expect(history.at(-1)).toEqual({ role: "user", content: "switch to dark mode" });
    // The param is spent: a refresh does not ask the shell again.
    expect(window.location.search).not.toContain(QUICK_ASK_PARAM);
    // The words are not left behind in the box as well.
    expect(box().value).toBe("");
    await act(async () => {
      H.stream.release!({ reply: "Dark mode is on." });
    });
    await screen.findByText("Dark mode is on.");
    // A saved conversation: it is in the chat list afterwards.
    await waitFor(() => expect(H.api.puts.some((p) => /^\/chat\/threads\//.test(p.path))).toBe(true));
    expect(H.stream.bodies).toHaveLength(1);
  });

  it("?quick=1 with nothing waiting sends nothing", async () => {
    take.mockImplementation(async () => "");
    window.history.replaceState({}, "", `/chat?new=1&${QUICK_ASK_PARAM}=1`);
    render(<ChatPage />);
    await waitFor(() => expect(take).toHaveBeenCalledTimes(1));
    await screen.findByPlaceholderText(/Message Iron Jarvis/);
    expect(H.stream.bodies).toHaveLength(0);
  });

  it("words in the URL are never sent: ?ask= still only fills the box", async () => {
    window.history.replaceState({}, "", "/chat?ask=switch%20to%20dark%20mode");
    render(<ChatPage />);
    await waitFor(() => expect(box().value).toBe("switch to dark mode"));
    expect(take).not.toHaveBeenCalled();
    expect(H.stream.bodies).toHaveLength(0);
  });

  it("outside the desktop app ?quick=1 does nothing", async () => {
    delete W.ironjarvis;
    window.history.replaceState({}, "", `/chat?new=1&${QUICK_ASK_PARAM}=1`);
    render(<ChatPage />);
    await screen.findByPlaceholderText(/Message Iron Jarvis/);
    expect(H.stream.bodies).toHaveLength(0);
  });
});

describe("takeQuickAsk (the bridge reader)", () => {
  it("reads the bridge only for ?quick=1, and only a non-empty string", async () => {
    expect(await takeQuickAsk("?new=1")).toBe("");
    expect(take).not.toHaveBeenCalled();
    expect(await takeQuickAsk(`?${QUICK_ASK_PARAM}=1`)).toBe("switch to dark mode");
    take.mockImplementation(async () => ({ not: "a string" }));
    expect(await takeQuickAsk(`?${QUICK_ASK_PARAM}=1`)).toBe("");
    take.mockImplementation(async () => {
      throw new Error("refused");
    });
    expect(await takeQuickAsk(`?${QUICK_ASK_PARAM}=1`)).toBe("");
    delete W.ironjarvis;
    expect(await takeQuickAsk(`?${QUICK_ASK_PARAM}=1`)).toBe("");
  });
});
