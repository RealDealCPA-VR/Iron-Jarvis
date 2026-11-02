/**
 * v1.426.0 (Wave 6, E2) — the REAL chat page carries the request into the
 * coding session.
 *
 * A reply to "build me a todo app" ends with the daemon's coding door
 * (`/chat?view=code`). Pressing it on the chat page:
 *  - puts the request the reply answers (the user's own words) and the
 *    chat's project folder where the Code view takes them (lib/codingStart —
 *    this tab's sessionStorage, never the address);
 *  - switches the page to its Code view in place (no navigation);
 *  - sends nothing.
 *
 * The transport doubles are connect-door-v13590's (the real page, the real
 * DoorsStrip; the stream hook's run() is settled by hand).
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
    code = "";
    provider = "";
    model = "";
    title = "";
    fix = "";
    technical = "";
    fixKind = "";
    fixProvider = "";
  }
  const stream = {
    bodies: [] as Record<string, unknown>[],
    streaming: false,
    approval: null as null,
    version: 0,
    listeners: new Set<() => void>(),
    settle: null as null | { ok: (r: Record<string, unknown>) => void; fail: (e: unknown) => void },
    bump() {
      stream.version += 1;
      for (const l of [...stream.listeners]) l();
    },
    release(r: Record<string, unknown>) {
      const s = stream.settle;
      stream.settle = null;
      stream.streaming = false;
      stream.bump();
      s?.ok(r);
    },
  };
  return {
    FakeApiError,
    FakeStreamError,
    api: { posts: [] as string[], getResponses: {} as Record<string, unknown> },
    stream,
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
  post: async (path: string) => {
    H.api.posts.push(path);
    return {};
  },
  put: async (path: string) => {
    const m = /^\/chat\/threads\/(.+)$/.exec(path);
    return { id: m && m[1] !== "new" ? m[1] : "t1", title: "t" };
  },
  del: async () => ({}),
}));
vi.mock("@/lib/useChatStream", async () => {
  const React = await import("react");
  const subscribe = (cb: () => void) => {
    H.stream.listeners.add(cb);
    return () => {
      H.stream.listeners.delete(cb);
    };
  };
  const run = (body: Record<string, unknown>) => {
    H.stream.bodies.push(body);
    H.stream.streaming = true;
    H.stream.bump();
    return new Promise<Record<string, unknown>>((ok, fail) => {
      H.stream.settle = { ok, fail };
    });
  };
  return {
    StreamError: H.FakeStreamError,
    useLiveText: (s: { text?: string }) => s?.text ?? "",
    useChatStream: () => {
      React.useSyncExternalStore(subscribe, () => H.stream.version);
      return {
        streaming: H.stream.streaming,
        text: "",
        tools: [],
        approval: H.stream.approval,
        phase: H.stream.streaming ? "working" : null,
        withFiles: false,
        startedAt: null,
        lastEventAt: null,
        run,
        abort: () => {},
      };
    },
  };
});
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
vi.mock("react-markdown", () => ({ default: ({ children }: { children?: string }) => <div>{children}</div> }));
vi.mock("remark-gfm", () => ({ default: () => {} }));

import ChatPage from "@/app/chat/page";
import { CHAT_VIEW_EVENT, resetViewMemory, viewOfEvent } from "@/lib/chatSurface";
import { CODING_OFFER_HREF, CODING_START_KEY, peekCodingStart } from "@/lib/codingStart";

const DOOR = { href: CODING_OFFER_HREF, label: "Work on this in a coding session (Chat › Code)" };
const REQUEST = "build me a todo app in react";

beforeEach(() => {
  H.api.posts.length = 0;
  H.stream.bodies.length = 0;
  H.stream.streaming = false;
  H.stream.settle = null;
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
  window.history.replaceState({}, "", "/chat");
  window.localStorage.clear();
  window.sessionStorage.clear();
  // v1.426.0 review: a door press switches the page to Code and the WINDOW
  // remembers it (module state): every test starts on the Chat view.
  resetViewMemory();
  Element.prototype.scrollIntoView = vi.fn();
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

async function askAndAnswer() {
  const box = (await screen.findByPlaceholderText(/Message Iron Jarvis/)) as HTMLTextAreaElement;
  fireEvent.change(box, { target: { value: REQUEST } });
  fireEvent.keyDown(box, { key: "Enter" });
  await waitFor(() => expect(H.stream.settle).not.toBeNull());
  act(() => H.stream.release({ reply: "Sure, here is how.", doors: [DOOR] }));
  return screen.findByRole("link", { name: DOOR.label });
}

describe("the coding door on the chat page", () => {
  it("carries the request into the Code view, switches in place and sends nothing", async () => {
    render(<ChatPage />);
    const link = await askAndAnswer();
    expect(link).toHaveAttribute("href", "/chat?view=code");
    const views: string[] = [];
    const on = (e: Event) => views.push(viewOfEvent(e) ?? "");
    window.addEventListener(CHAT_VIEW_EVENT, on);
    const turns = H.stream.bodies.length;
    let navigated: boolean;
    try {
      navigated = fireEvent.click(link);
    } finally {
      window.removeEventListener(CHAT_VIEW_EVENT, on);
    }
    expect(navigated).toBe(false); // handled in place: the link does not navigate
    expect(views).toEqual(["code"]);
    expect(peekCodingStart()).toEqual({ text: REQUEST, folder: null });
    // Nothing was sent: no new turn, no request in the address.
    expect(H.stream.bodies.length).toBe(turns);
    expect(window.location.href).not.toContain("todo");
    expect(sessionStorage.getItem(CODING_START_KEY)).toContain(REQUEST);
  });

  it("v1.426.0 review: a Local only chat's request starts its coding session Local only", async () => {
    render(<ChatPage />);
    // The chat is kept on this PC (the composer's own Local only chip).
    fireEvent.click(await screen.findByTestId("local-only-chip"));
    await waitFor(() => expect(screen.getByTestId("local-only-chip")).toHaveAttribute("aria-pressed", "true"));
    const link = await askAndAnswer();
    fireEvent.click(link);
    expect(peekCodingStart()).toEqual({ text: REQUEST, folder: null, localOnly: true });
  });

  it("a chat in a project hands over the project's folder", async () => {
    H.api.getResponses["/projects"] = {
      projects: [{ id: "p1", name: "Shop", root: "C:\\work\\shop", root_exists: true, status: "active" }],
    };
    window.history.replaceState({}, "", "/chat?project=p1");
    render(<ChatPage />);
    const link = await askAndAnswer();
    await waitFor(() => {
      fireEvent.click(link);
      expect(peekCodingStart()).toEqual({ text: REQUEST, folder: "C:\\work\\shop" });
    });
  });
});
