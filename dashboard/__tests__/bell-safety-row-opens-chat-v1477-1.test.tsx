/**
 * v1.477.1 (a USER BUG): the bell's "Blocked 1 suspicious passage" row,
 * pressed from Chat, went nowhere.
 *
 *  - The row linked bare `/chat` (every chat turn's session id is "chat"). It
 *    now opens the chat the daemon names (`thread_id`), else the activity log.
 *  - The chat page read `?thread=` only when it first mounted, so ANY
 *    `/chat?thread=` link pressed from inside Chat (the bell, Ctrl K) changed
 *    only the address. It now follows the bell's announcement, a back/forward
 *    step and the browser's own "address changed" signal.
 *  - The row's body says the kind of passage in words, never a raw id.
 *
 * Harness: the v1.326.0 chat mocks (ChatPage rendered for real).
 */

import { type ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen } from "@testing-library/react";

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
    api: { responses: {} as Record<string, unknown> },
    dict: { supported: false, starts: 0, stops: 0 },
    tts: { supported: false, enabled: false, toggles: 0 },
    stream: { result: { reply: "Noted.", tools_used: [] } as Record<string, unknown> },
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: H.FakeApiError,
  API_BASE: "",
  ijToken: () => "",
  onUnauthorizedChange: () => () => {},
  onRequestErrorChange: () => () => {},
  onNetworkError: () => () => {},
  get: async (path: string) => {
    const r = H.api.responses[path];
    if (r === undefined && path.startsWith("/fs/files")) return {};
    if (r instanceof Error) throw r;
    return r === undefined ? {} : r;
  },
  post: async () => ({}),
  put: async (path: string) => {
    const m = /^\/chat\/threads\/(.+)$/.exec(path);
    return { id: m && m[1] !== "new" ? m[1] : "t1", title: "t" };
  },
  patch: async () => ({}),
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
    run: () => Promise.resolve(H.stream.result),
    abort: () => {},
  }),
}));
vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: [], connected: false }) }));
vi.mock("@/lib/useRunStream", () => ({
  useRunStream: () => ({ text: "", tools: [], phase: null, active: false, start: () => {}, stop: () => {} }),
}));
vi.mock("@/lib/useDictation", () => ({
  useDictation: () => ({
    supported: H.dict.supported,
    reason: null,
    engine: null,
    listening: false,
    processing: false,
    transcript: "",
    interim: "",
    error: null,
    start: () => {
      H.dict.starts += 1;
    },
    stop: () => {
      H.dict.stops += 1;
    },
    reset: () => {},
  }),
}));
vi.mock("@/lib/useTTS", () => ({
  useTTS: () => ({
    supported: H.tts.supported,
    enabled: H.tts.enabled,
    speaking: false,
    enable: () => {},
    disable: () => {},
    toggle: () => {
      H.tts.toggles += 1;
    },
    speak: () => {},
    resetStream: () => {},
    speakMore: () => {},
    cancel: () => {},
  }),
}));
vi.mock("@/lib/useProviderHealth", () => ({
  useProviderHealth: () => ({
    byProvider: {}, signedOutByProvider: {}, defaultProvider: "", loading: false, stale: false, refresh: () => {},
  }),
}));
vi.mock("react-markdown", () => ({ default: ({ children }: { children?: string }) => <div>{children}</div> }));
vi.mock("remark-gfm", () => ({ default: () => {} }));
vi.mock("next/link", () => ({
  default: ({ children, href, ...rest }: { children: ReactNode; href: string } & Record<string, unknown>) => (
    <a href={href} {...(rest as Record<string, never>)}>
      {children}
    </a>
  ),
}));
vi.mock("@/components/project/ProjectSurfaces", () => ({
  ProjectSurface: ({ view }: { view: string }) => <div data-testid="surface-stub">surface:{view}</div>,
}));

import ChatPage from "@/app/chat/page";
import { blockedCategoryWords, chatEventHref, toActivity } from "@/components/NotificationBell";
import { CHAT_THREAD_EVENT, announceChatThreadLink, threadOfHref } from "@/lib/chatThreadLink";

const AT = "2026-10-01T10:00:00Z";
const QUICK = { id: "tq", title: "Quick question", updated_at: AT, messages: 2 };
const TQ = {
  id: "tq",
  title: "Quick question",
  updated_at: AT,
  messages: [
    { role: "user", content: "what is due friday" },
    { role: "assistant", content: "The payroll filing is due Friday." },
  ],
};

beforeEach(() => {
  H.dict.supported = false;
  H.tts.supported = false;
  H.stream.result = { reply: "Noted.", tools_used: [] };
  H.api.responses = {
    "/models": { models: [] },
    "/chat/personas": { personas: [] },
    "/chat/threads": { threads: [QUICK] },
    "/chat/threads/tq": TQ,
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
  Element.prototype.scrollIntoView = function scrollIntoView() {} as unknown as Element["scrollIntoView"];
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const blocked = (payload: Record<string, unknown>) =>
  toActivity({ id: "e1", type: "context.blocked", ts: AT, session_id: "chat", payload: { session_id: "chat", ...payload } });

describe("the bell's chat safety rows open the chat they happened in", () => {
  it("a block in a named chat opens that chat", () => {
    const row = blocked({ source: "attachment memo.txt", count: 1, categories: ["phishing_urgency_payment"], thread_id: "tq" });
    expect(row!.href).toBe("/chat?thread=tq");
  });

  it("with no chat named (an older event) it opens the activity log, never bare /chat", () => {
    expect(blocked({ source: "memory", count: 1, categories: [] })!.href).toBe("/activity");
    expect(chatEventHref({ thread_id: "  " })).toBe("/activity");
    const lowered = toActivity({
      id: "e2", type: "trust.lowered", ts: AT, session_id: "chat",
      payload: { session_id: "chat", tool: "web_fetch", reason: "r", thread_id: "a b" },
    });
    expect(lowered!.href).toBe("/chat?thread=a%20b");
  });

  it("says the kind of passage in words", () => {
    expect(blocked({ source: "s", count: 1, categories: ["phishing_urgency_payment"] })!.body).toBe(
      "It looked like an urgent request for a payment or bank details. It was kept out of what the AI read.",
    );
    expect(blockedCategoryWords([])).toBe("");
    for (const kind of ["instruction_override", "credential_harvest", "phishing_urgency_payment", "embedded_imperative"]) {
      expect(blockedCategoryWords([kind])).not.toContain("_");
    }
  });
});

describe("a chat link pressed while Chat is open opens that chat", () => {
  it("reads the chat out of a link, only for the chat page", () => {
    expect(threadOfHref("/chat?thread=tq")).toBe("tq");
    expect(threadOfHref("/?thread=tq")).toBe("tq");
    expect(threadOfHref("/sessions?thread=tq")).toBe("");
    expect(threadOfHref("/chat")).toBe("");
  });

  it("announces only on the chat page", () => {
    const seen: string[] = [];
    const f = (e: Event) => seen.push((e as CustomEvent<{ thread: string }>).detail.thread);
    window.addEventListener(CHAT_THREAD_EVENT, f);
    window.history.replaceState({}, "", "/sessions");
    expect(announceChatThreadLink("/chat?thread=tq")).toBe(false);
    window.history.replaceState({}, "", "/chat");
    expect(announceChatThreadLink("/chat")).toBe(false);
    expect(announceChatThreadLink("/chat?thread=tq")).toBe(true);
    window.removeEventListener(CHAT_THREAD_EVENT, f);
    expect(seen).toEqual(["tq"]);
  });

  it("the bell's announcement opens the chat on a page already showing Chat", async () => {
    render(<ChatPage />);
    await screen.findByRole("textbox", { name: "Message" });
    expect(screen.queryByText("The payroll filing is due Friday.")).toBeNull();
    act(() => {
      announceChatThreadLink("/chat?thread=tq");
    });
    expect(await screen.findByText("The payroll filing is due Friday.", {}, { timeout: 10_000 })).toBeTruthy();
  });

  it("a back/forward step to a chat link opens it and takes the link out of the address", async () => {
    render(<ChatPage />);
    await screen.findByRole("textbox", { name: "Message" });
    act(() => {
      window.history.pushState({}, "", "/chat?thread=tq");
      window.dispatchEvent(new PopStateEvent("popstate"));
    });
    expect(await screen.findByText("The payroll filing is due Friday.", {}, { timeout: 10_000 })).toBeTruthy();
    expect(window.location.search).toBe("");
  });
});
