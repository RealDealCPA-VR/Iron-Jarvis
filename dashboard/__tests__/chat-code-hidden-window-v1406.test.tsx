/**
 * v1.406.0 part 7 review — the chat kept behind the Code view never answers
 * the page's WINDOW events.
 *
 * The chat page stays mounted (hidden + `inert`) behind Chat | Code's Code
 * view, and two of its listeners sit on `window`: a file dropped ANYWHERE
 * (its full-page drop) and "Switch model" (`ij:open-switcher`, from Ctrl K or
 * the demo strip). Before this review both still answered while Code was on
 * screen: a file dropped on a coding session's box was ALSO uploaded into
 * the hidden chat (and gave it a work folder in Documents), and "Switch
 * model" opened the hidden chat's model menu — portaled over the Code view —
 * so a pick there changed the chat, not the session. The real page, with
 * only the transport mocked and the Code view a stub; each half has its
 * CONTROL (the Chat view still takes the drop and opens its menu).
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

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
      getResponses: {} as Record<string, unknown>,
      /** POST outcome per path: an Error rejects, a function is called with the
       *  body, any other value resolves. */
      postResponses: {} as Record<string, unknown>,
    },
    stream: { bodies: [] as Record<string, unknown>[] },
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
    const r = H.api.postResponses[path];
    if (r instanceof Error) throw r;
    if (typeof r === "function") return (r as (b: Record<string, unknown>) => unknown)(body);
    return r ?? {};
  },
  put: async (path: string) => {
    const m = /^\/chat\/threads\/(.+)$/.exec(path);
    return { id: m && m[1] !== "new" ? m[1] : "t1", title: "t" };
  },
  del: async () => ({}),
}));

vi.mock("@/lib/useChatStream", () => ({
  StreamError: H.FakeStreamError,
  // v1.250.0 (S-03): see the real hook — no store on a mock, so the text is
  // read straight off the object these fakes return.
  useLiveText: (s: { text?: string }) => s?.text ?? "",
  useChatStream: () => ({
    streaming: false,
    text: "",
    tools: [],
    approval: null,
    run: async (body: Record<string, unknown>, onDelta: (d: string, f: string) => void) => {
      H.stream.bodies.push(body);
      await new Promise<void>((r) => setTimeout(r, 0));
      onDelta("done", "done");
      return { reply: "done" };
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

vi.mock("@/components/harness/CodeView", () => ({
  CodeView: () => <div data-testid="code-view-stub" />,
  default: () => <div data-testid="code-view-stub" />,
}));

import ChatPage from "@/app/chat/page";

beforeEach(() => {
  H.api.posts.length = 0;
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
  H.api.postResponses["/documents/upload"] = (b: Record<string, unknown>) => ({
    path: `C:/home/uploads/${String(b.filename)}`,
    name: String(b.filename),
    bytes: 3,
  });
  H.api.postResponses["/documents/workfolder"] = { path: "C:/f", created: true, files: [], skipped: [], note: "" };
  window.localStorage.clear();
  window.sessionStorage.clear();
  Element.prototype.scrollIntoView = vi.fn();
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const uploads = () => H.api.posts.filter((p) => p.path === "/documents/upload");

function dropOnWindow() {
  const file = new File(["abc"], "spec.pdf", { type: "application/pdf" });
  const dataTransfer = { types: ["Files"], files: [file], items: [] };
  fireEvent.dragEnter(window, { dataTransfer });
  fireEvent.dragOver(window, { dataTransfer });
  fireEvent.drop(window, { dataTransfer });
}

async function openOn(view: "chat" | "code") {
  window.history.replaceState({}, "", view === "code" ? "/chat?view=code" : "/chat?view=chat");
  render(<ChatPage />);
  await screen.findByPlaceholderText(/Message Iron Jarvis/);
  if (view === "code") {
    await waitFor(() => expect(screen.getByTestId("chat-surface")).toHaveAttribute("data-view", "code"));
    await screen.findByTestId("code-view-stub", {}, { timeout: 10_000 });
  } else {
    await waitFor(() => expect(screen.getByTestId("chat-surface")).toHaveAttribute("data-view", "chat"));
  }
}

describe("a file dropped while the Code view shows", () => {
  it("is never uploaded into the hidden chat", async () => {
    await openOn("code");
    dropOnWindow();
    await act(async () => {
      await new Promise((r) => setTimeout(r, 60));
    });
    expect(uploads()).toEqual([]);
  });

  it("CONTROL: the Chat view still takes a file dropped anywhere", async () => {
    await openOn("chat");
    dropOnWindow();
    await waitFor(() => expect(uploads().length).toBe(1));
  });
});

describe("Switch model while the Code view shows", () => {
  it("never opens the hidden chat's model menu", async () => {
    await openOn("code");
    act(() => {
      window.dispatchEvent(new Event("ij:open-switcher"));
    });
    await act(async () => {
      await new Promise((r) => setTimeout(r, 60));
    });
    expect(screen.queryByTestId("model-menu")).toBeNull();
  });

  it("CONTROL: in the Chat view it opens the chat's menu", async () => {
    await openOn("chat");
    act(() => {
      window.dispatchEvent(new Event("ij:open-switcher"));
    });
    await screen.findByTestId("model-menu", {}, { timeout: 10_000 });
  });
});
