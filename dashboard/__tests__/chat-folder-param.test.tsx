// @vitest-environment jsdom
/**
 * Next leaps 03-a (build-out plan R3.1, idea 03): a suggestion's Yes ("Pick
 * it up?") opens `/chat?new=1&folder=<path>`. The chat page reads `folder`
 * ONCE at mount, beside `ask`, and sets it through the folder chip's own pick
 * (the folder check, the file tools): a fresh chat in that folder. It never
 * sends anything (the folder's card and Continue wait for the owner's press),
 * and the param is spent so a refresh does not pick it again.
 *
 * Driven on the REAL page (transport mocked, as the quick window's test does).
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
import { FOLDER_CHECK_PATH } from "@/lib/chatFolder";

const FOLDER = String.raw`D:\Clients\Hartleys`;

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
  window.localStorage.clear();
  Element.prototype.scrollIntoView = vi.fn();
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  window.history.replaceState({}, "", "/chat");
});

describe("?folder= opens a new chat in that folder (03-a)", () => {
  it("sets the chat's folder through the folder pick and sends nothing", async () => {
    window.history.replaceState({}, "", `/chat?new=1&folder=${encodeURIComponent(FOLDER)}`);
    render(<ChatPage />);
    // The folder chip's own pick: the folder is checked, and kept as the chat's folder.
    await waitFor(() =>
      expect(H.api.posts.some((p) => p.path === FOLDER_CHECK_PATH && p.body.path === FOLDER)).toBe(true),
    );
    await waitFor(() => expect(window.localStorage.getItem("ij_chat_workspace")).toBe(FOLDER));
    await screen.findByPlaceholderText(/Message Iron Jarvis/);
    // Nothing was sent, and the param is spent.
    expect(H.stream.bodies).toHaveLength(0);
    expect(window.location.search).not.toContain("folder=");
  });

  it("without ?folder= the page picks no folder", async () => {
    window.history.replaceState({}, "", "/chat?new=1");
    render(<ChatPage />);
    await screen.findByPlaceholderText(/Message Iron Jarvis/);
    expect(H.api.posts.some((p) => p.path === FOLDER_CHECK_PATH)).toBe(false);
    expect(window.localStorage.getItem("ij_chat_workspace")).toBeNull();
    expect(H.stream.bodies).toHaveLength(0);
  });
});
