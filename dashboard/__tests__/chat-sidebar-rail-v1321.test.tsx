/**
 * Calm UI redesign S7 (AUDIT Q5) — Chat's thread list lives in the app
 * sidebar on a wide screen: the SAME rail (state, search, rename, pin, move,
 * delete), portaled into the sidebar's slot. Layout only. The sidebar's New
 * chat starts one in place; ?new=1 (New chat from another page) never
 * reopens the last conversation.
 *
 * Harness: the edit-and-resend page harness.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

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
      postResponses: {} as Record<string, unknown>,
    },
    stream: {
      bodies: [] as Record<string, unknown>[],
      replies: [] as string[],
      extras: [] as Record<string, unknown>[],
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
    const r = H.api.postResponses[path];
    if (r instanceof Error) throw r;
    if (typeof r === "function") return (r as (b: Record<string, unknown>) => unknown)(body);
    return r ?? {};
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
    run: async (body: Record<string, unknown>, onDelta: (d: string, f: string) => void) => {
      H.stream.bodies.push(body);
      await new Promise<void>((r) => setTimeout(r, 0));
      const reply = H.stream.replies.shift() ?? "done";
      const extra = H.stream.extras.shift() ?? {};
      onDelta(reply, reply);
      return { reply, ...extra };
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
import { NEW_CHAT_EVENT, setChatSlot } from "@/lib/sidebarSlot";

beforeEach(() => {
  H.api.posts.length = 0;
  H.api.puts.length = 0;
  H.stream.bodies.length = 0;
  H.stream.replies.length = 0;
  H.stream.extras.length = 0;
  for (const k of Object.keys(H.api.postResponses)) delete H.api.postResponses[k];
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
  Element.prototype.scrollIntoView = vi.fn();
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

async function send(text: string, reply: string) {
  const el = (await screen.findByPlaceholderText(/Message Iron Jarvis/)) as HTMLTextAreaElement;
  H.stream.replies.push(reply);
  fireEvent.change(el, { target: { value: text } });
  fireEvent.keyDown(el, { key: "Enter" });
  await screen.findByText(reply);
}

describe("Chat's thread list in the app sidebar (redesign S7, AUDIT Q5)", () => {
  it("with a sidebar slot, the rail renders there — one list, same behaviour", async () => {
    H.api.getResponses["/chat/threads"] = {
      threads: [{ id: "t9", title: "Ledger totals", updated_at: "2026-10-08T10:00:00", messages: [] }],
    };
    const slot = document.createElement("div");
    slot.id = "ij-sidebar-chat-slot";
    document.body.appendChild(slot);
    setChatSlot(slot);
    try {
      render(<ChatPage />);
      const rail = await screen.findByTestId("chat-thread-rail");
      expect(slot.contains(rail)).toBe(true);
      expect(rail.getAttribute("data-in-sidebar")).toBe("true");
      expect(await within(rail).findByText("Ledger totals")).toBeTruthy();
      expect(within(rail).getByLabelText("Search chats")).toBeTruthy();
      // The in-page phone toggle is gone while the sidebar holds the list.
      expect(screen.queryByRole("button", { name: /^Chats/ })).toBeNull();
    } finally {
      setChatSlot(null);
      slot.remove();
    }
  });

  // v1.329.0 (calm chat W4 F2): with no slot the rail sits beside the chat
  // from md up only (a pop-out, a collapsed sidebar); a phone reaches it in
  // the nav drawer (sidebar-chats-v1329.test.tsx pins both halves).
  it("without a slot (a pop-out, a collapsed sidebar) the rail stays in the page", async () => {
    render(<ChatPage />);
    const rail = await screen.findByTestId("chat-thread-rail");
    expect(rail.getAttribute("data-in-sidebar")).toBeNull();
    expect(rail.closest("aside")).not.toBeNull();
  });

  it("the sidebar's New chat clears the conversation in place", async () => {
    render(<ChatPage />);
    await send("first question", "first answer");
    await act(async () => {
      window.dispatchEvent(new Event(NEW_CHAT_EVENT));
    });
    await waitFor(() => expect(screen.queryByText("first answer")).toBeNull());
  });

  it("?new=1 opens a fresh chat instead of reopening the last one, and is stripped", async () => {
    H.api.getResponses["/chat/threads/t9"] = {
      id: "t9",
      title: "Ledger totals",
      messages: [{ role: "assistant", content: "the reopened reply" }],
    };
    // CONTROL first: with no ?new the last conversation IS reopened.
    window.sessionStorage.setItem("ij_chat_open_thread", JSON.stringify({ id: "t9", project: null }));
    render(<ChatPage />);
    expect(await screen.findByText("the reopened reply")).toBeTruthy();
    cleanup();
    window.sessionStorage.setItem("ij_chat_open_thread", JSON.stringify({ id: "t9", project: null }));
    window.history.replaceState({}, "", "/chat?new=1");
    render(<ChatPage />);
    await screen.findByPlaceholderText(/Message Iron Jarvis/);
    await act(async () => {
      await new Promise((r) => setTimeout(r, 20));
    });
    expect(screen.queryByText("the reopened reply")).toBeNull();
    expect(window.location.search).toBe("");
  });
});
