/**
 * v1.424.0 (review) — a chat's chosen ACCOUNT, said and kept everywhere.
 *
 * * the reply's receipt names the account the turn RAN on (the daemon's
 *   `route.account`, the Build receipt's own quiet "on the Spare account");
 *   no account on the route: nothing new;
 * * the follow-up chips are asked of the SAME account the reply ran on
 *   (`account` on POST /chat/followups — the route's account, server truth);
 * * "Summarize" on a long chat sends the chat's account (POST /chat/compact)
 *   while the chat's model is on that account's CLI.
 *
 * The real chat page; only the transport is mocked (the chat-words-v13560
 * harness).
 */

import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

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
      responses: {} as Record<string, unknown>,
    },
    stream: {
      route: null as Record<string, unknown> | null,
      context: null as Record<string, unknown> | null,
      escalate: false,
    },
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
    H.api.gets.push(path);
    const r = H.api.responses[path];
    if (r instanceof Error) throw r;
    return r === undefined ? {} : r;
  },
  post: async (path: string, body: Record<string, unknown>) => {
    H.api.posts.push({ path, body });
    if (path === "/chat/compact") return { covers: 4, stripped: 0, summary: "The start.", trigger: "manual" };
    if (path === "/chat/followups") return { suggestions: [] };
    return {};
  },
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
    run: () =>
      Promise.resolve({
        reply: H.stream.escalate ? "" : "Noted.",
        tools_used: [],
        ...(H.stream.escalate ? { escalate: true, escalate_reason: "needs the agent" } : {}),
        ...(H.stream.route ? { route: H.stream.route } : {}),
        ...(H.stream.context ? { context: H.stream.context } : {}),
      }),
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

import ChatPage from "@/app/chat/page";

const AT = "2026-10-01T10:00:00Z";
const ON_SPARE = {
  id: "ts",
  title: "Spare chat",
  updated_at: AT,
  messages: [
    { role: "user", content: "plan the week" },
    { role: "assistant", content: "Here is the plan." },
  ],
  setup: { provider: "claude-cli", model: "claude-opus-4-8", account: "c-spare", account_name: "Spare" },
};
const LONG = {
  window: 200_000, used: 148_000, headroom: 52_000, dropped: 0, tools_trimmed: 0, clipped: false,
  recap: false, suggest_larger: false, percent: 74, level: "suggest", suggest_at: 70, auto_at: 92,
};
const ROUTE = {
  requested: "claude-cli", provider: "claude-cli", model: "claude-opus-4-8", reason: "explicit",
  account: { id: "c-spare", name: "Spare" },
};

const box = async () => (await screen.findByRole("textbox", { name: "Message" })) as HTMLTextAreaElement;

async function sendHello() {
  const b = await box();
  fireEvent.change(b, { target: { value: "hello" } });
  fireEvent.keyDown(b, { key: "Enter" });
  await screen.findAllByText("Noted.");
}

beforeEach(() => {
  H.api.gets.length = 0;
  H.api.posts.length = 0;
  H.stream.route = null;
  H.stream.context = null;
  H.stream.escalate = false;
  H.api.responses = {
    "/models": {
      models: [
        { provider: "claude-cli", model: "claude-opus-4-8", label: "Opus 4.8", available: true, kind: "cli" },
      ],
    },
    "/chat/personas": { personas: [] },
    "/chat/threads": { threads: [] },
    "/chat/threads/ts": ON_SPARE,
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

describe("the receipt names the account a chat turn ran on", () => {
  it("says 'on the Spare account' quietly beside who answered", async () => {
    H.stream.route = ROUTE;
    render(<ChatPage />);
    await sendHello();
    const said = await screen.findByTestId("turn-account");
    expect(said.textContent).toBe("on the Spare account");
  });

  it("CONTROL: a route with no account adds nothing", async () => {
    H.stream.route = { ...ROUTE, account: undefined };
    render(<ChatPage />);
    await sendHello();
    await screen.findByTestId("turn-answered-by");
    expect(screen.queryByTestId("turn-account")).toBeNull();
  });
});

describe("the chat's extra calls carry its account", () => {
  it("follow-up chips are asked of the account the reply ran on", async () => {
    H.api.responses["/settings"] = { settings: { chat_followups: true } };
    H.stream.route = ROUTE;
    render(<ChatPage />);
    await sendHello();
    await waitFor(() => expect(H.api.posts.some((p) => p.path === "/chat/followups")).toBe(true));
    const ask = H.api.posts.find((p) => p.path === "/chat/followups")!;
    expect(ask.body.account).toBe("c-spare");
  });

  it("CONTROL: a reply that ran on no chosen account sends no account", async () => {
    H.api.responses["/settings"] = { settings: { chat_followups: true } };
    H.stream.route = { ...ROUTE, account: undefined };
    render(<ChatPage />);
    await sendHello();
    await waitFor(() => expect(H.api.posts.some((p) => p.path === "/chat/followups")).toBe(true));
    expect("account" in H.api.posts.find((p) => p.path === "/chat/followups")!.body).toBe(false);
  });

  it("'Summarize' on a chat kept on an account sends that account", async () => {
    window.history.replaceState({}, "", "/chat?thread=ts");
    H.stream.context = LONG;
    render(<ChatPage />);
    await screen.findByText("Here is the plan.");
    await sendHello();
    const offer = await screen.findByTestId("compaction-offer");
    fireEvent.click(within(offer).getByRole("button", { name: "Summarize" }));
    await waitFor(() => expect(H.api.posts.some((p) => p.path === "/chat/compact")).toBe(true));
    const press = H.api.posts.find((p) => p.path === "/chat/compact")!;
    expect(press.body.provider).toBe("claude-cli");
    expect(press.body.account).toBe("c-spare");
  });
});

describe("the chat's hand-off to an agent carries its account", () => {
  it("POST /sessions names the account the chat is kept on", async () => {
    window.history.replaceState({}, "", "/chat?thread=ts");
    H.stream.escalate = true;
    render(<ChatPage />);
    await screen.findByText("Here is the plan.");
    const b = await box();
    fireEvent.change(b, { target: { value: "do the long thing" } });
    fireEvent.keyDown(b, { key: "Enter" });
    await waitFor(() => expect(H.api.posts.some((p) => p.path === "/sessions")).toBe(true));
    const sent = H.api.posts.find((p) => p.path === "/sessions")!;
    expect(sent.body.provider).toBe("claude-cli");
    expect(sent.body.account).toBe("c-spare");
  });

  it("CONTROL: a chat on no account hands off with none", async () => {
    H.stream.escalate = true;
    render(<ChatPage />);
    const b = await box();
    fireEvent.change(b, { target: { value: "do the long thing" } });
    fireEvent.keyDown(b, { key: "Enter" });
    await waitFor(() => expect(H.api.posts.some((p) => p.path === "/sessions")).toBe(true));
    expect("account" in H.api.posts.find((p) => p.path === "/sessions")!.body).toBe(false);
  });
});
