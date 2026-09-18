/**
 * v1.278.0 — edit a sent message and resend it.
 *
 * A wrong word in a sent question meant retyping the whole thing. Every user
 * bubble now carries Edit and resend: the message goes back into the box with
 * its files, the conversation is cut BEFORE it (the cut is saved), and Send is
 * a fresh turn over exactly the history that preceded it. Never mid-turn, and
 * never on a steer note.
 *
 * Header, mocks and helpers are the reasoning-level harness with thread saves recorded.
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
      posts: [] as { path: string; body: Record<string, unknown> }[],
      puts: [] as { path: string; body: Record<string, unknown> }[],
      getResponses: {} as Record<string, unknown>,
      postResponses: {} as Record<string, unknown>,
    },
    stream: { bodies: [] as Record<string, unknown>[], replies: [] as string[] },
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
      onDelta(reply, reply);
      return { reply };
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

beforeEach(() => {
  H.api.posts.length = 0;
  H.api.puts.length = 0;
  H.stream.bodies.length = 0;
  H.stream.replies.length = 0;
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

describe("edit and resend (v1.278.0)", () => {
  it("puts the message back in the box, cuts the conversation before it, saves the cut, and the resend is a fresh turn", async () => {
    render(<ChatPage />);
    await send("first question", "first answer");
    await send("second question", "second answer");
    expect(screen.getAllByLabelText("Edit and resend")).toHaveLength(2);

    fireEvent.click(screen.getAllByLabelText("Edit and resend")[1]);
    const el = (await screen.findByPlaceholderText(/Message Iron Jarvis/)) as HTMLTextAreaElement;
    expect(el.value).toBe("second question");
    // Cut before it: the second exchange is gone, the first stays.
    await waitFor(() => expect(screen.queryByText("second answer")).toBeNull());
    // One user bubble left (the box holds the words now, and a textarea's value
    // is text too — count the bubbles' own pencils, not the words).
    expect(screen.getAllByLabelText("Edit and resend")).toHaveLength(1);
    expect(screen.getByText("first answer")).not.toBeNull();
    // The cut is saved at once.
    const cut = H.api.puts.filter((p) => p.path.startsWith("/chat/threads/")).at(-1)!;
    expect((cut.body.messages as { content: string }[]).map((m) => m.content)).toEqual([
      "first question",
      "first answer",
    ]);

    // The resend is a fresh turn over exactly the history that preceded it.
    H.stream.replies.push("revised answer");
    fireEvent.change(el, { target: { value: "second question, revised" } });
    fireEvent.keyDown(el, { key: "Enter" });
    await screen.findByText("revised answer");
    const body = H.stream.bodies.at(-1)!;
    expect((body.messages as { role: string; content: string }[]).map((m) => m.content)).toEqual([
      "first question",
      "first answer",
      "second question, revised",
    ]);
    expect(screen.getAllByLabelText("Edit and resend")).toHaveLength(2);
  });

  it("editing the first message empties the conversation and the resend starts over", async () => {
    render(<ChatPage />);
    await send("only question", "only answer");
    fireEvent.click(screen.getByLabelText("Edit and resend"));
    const el = (await screen.findByPlaceholderText(/Message Iron Jarvis/)) as HTMLTextAreaElement;
    expect(el.value).toBe("only question");
    await waitFor(() => expect(screen.queryByText("only answer")).toBeNull());
    H.stream.replies.push("fresh answer");
    fireEvent.change(el, { target: { value: "better question" } });
    fireEvent.keyDown(el, { key: "Enter" });
    await screen.findByText("fresh answer");
    const body = H.stream.bodies.at(-1)!;
    expect((body.messages as { content: string }[]).map((m) => m.content)).toEqual(["better question"]);
  });
});
