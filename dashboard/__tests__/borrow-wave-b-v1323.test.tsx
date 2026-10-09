/**
 * v1.323.0 — wave B of the assistant-ui / tambo borrow list: chat polish on
 * the page itself. Times on hover; per-chat drafts; Continue merges a cut-off
 * reply into one; the reasoning folds above a reply; step timings reach the
 * receipt; Read aloud is one press per reply; follow-up questions (only when
 * switched on) go into the box, never sent.
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
  const stream = {
    bodies: [] as Record<string, unknown>[],
    replies: [] as string[],
    hold: false,
    streaming: false,
    version: 0,
    listeners: new Set<() => void>(),
    settle: null as null | ((r: Record<string, unknown>) => void),
    extra: [] as Record<string, unknown>[],
    bump() {
      stream.version += 1;
      for (const l of [...stream.listeners]) l();
    },
  };
  return {
    FakeApiError,
    FakeStreamError,
    stream,
    api: {
      posts: [] as { path: string; body: Record<string, unknown> }[],
      puts: [] as { path: string; body: Record<string, unknown> }[],
      dels: [] as string[],
      getResponses: {} as Record<string, unknown>,
      postResponses: {} as Record<string, unknown>,
    },
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: H.FakeApiError,
  API_BASE: "http://127.0.0.1:8787",
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
  del: async (path: string) => {
    H.api.dels.push(path);
    return {};
  },
}));

vi.mock("@/lib/useChatStream", async () => {
  const React = await import("react");
  const subscribe = (cb: () => void) => {
    H.stream.listeners.add(cb);
    return () => {
      H.stream.listeners.delete(cb);
    };
  };
  const run = async (body: Record<string, unknown>, onDelta: (d: string, f: string) => void) => {
    H.stream.bodies.push(body);
    H.stream.streaming = true;
    H.stream.bump();
    if (H.stream.hold) {
      return new Promise<Record<string, unknown>>((ok) => {
        H.stream.settle = ok;
      });
    }
    await new Promise<void>((r) => setTimeout(r, 0));
    const reply = H.stream.replies.shift() ?? "done";
    onDelta(reply, reply);
    H.stream.streaming = false;
    H.stream.bump();
    return { reply, ...(H.stream.extra.shift() ?? {}) };
  };
  const abort = () => {
    const s = H.stream.settle;
    H.stream.settle = null;
  H.stream.extra.length = 0;
  TTS.readAloud.mockClear();
  TTS.readingKey = null;
    H.stream.streaming = false;
    H.stream.bump();
    s?.({ reply: "" });
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
        approval: null,
        phase: H.stream.streaming ? "working" : null,
        run,
        abort,
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
const TTS = vi.hoisted(() => ({ readAloud: vi.fn(), readingKey: null as string | null }));
vi.mock("@/lib/useTTS", () => ({
  useTTS: () => ({
    supported: true, enabled: false, speaking: false, enable: () => {}, disable: () => {},
    toggle: () => {}, speak: () => {}, resetStream: () => {}, speakMore: () => {}, cancel: () => {},
    readAloud: TTS.readAloud, readingKey: TTS.readingKey,
  }),
}));
vi.mock("@/lib/useProviderHealth", () => ({
  useProviderHealth: () => ({ byProvider: {}, defaultProvider: "", loading: false, stale: false, refresh: () => {} }),
}));
vi.mock("react-markdown", async () => await vi.importActual("react-markdown"));

import ChatPage from "@/app/chat/page";
import { NEW_CHAT_EVENT } from "@/lib/sidebarSlot";
import { readDraft, writeDraft } from "@/lib/chatDrafts";
import { CONTINUE_PROMPT, joinContinuation, mergeContinuation } from "@/lib/continueReply";

beforeEach(() => {
  H.api.posts.length = 0;
  H.api.puts.length = 0;
  H.api.dels.length = 0;
  H.stream.bodies.length = 0;
  H.stream.replies.length = 0;
  H.stream.hold = false;
  H.stream.streaming = false;
  H.stream.settle = null;
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

function attach(...names: string[]) {
  const input = document.querySelector('input[type="file"]') as HTMLInputElement;
  fireEvent.change(input, { target: { files: names.map((n) => new File(["abc"], n, { type: "application/pdf" })) } });
}

/** Uploads that settle only when the test says so. */
function heldUploads() {
  // No work folder here: the uploads attach exactly as they are.
  H.api.postResponses["/documents/workfolder"] = new H.FakeApiError("no folder", 503);
  const pending: { name: string; resolve: () => void; reject: (e: Error) => void }[] = [];
  H.api.postResponses["/documents/upload"] = (body: Record<string, unknown>) =>
    new Promise((resolve, reject) => {
      const name = String(body.filename);
      pending.push({
        name,
        resolve: () => resolve({ name, path: `C:/home/uploads/${name}` }),
        reject,
      });
    });
  return pending;
}

const CLIENT_THREADS = {
  threads: [
    { id: "t1", title: "Client K-1", updated_at: "2026-10-08T10:00:00", messages: [] },
    { id: "t2", title: "Budget", updated_at: "2026-10-08T09:00:00", messages: [] },
  ],
};

const T1 = {
  id: "t1",
  title: "Client K-1",
  messages: [
    { role: "user", content: "hi" },
    { role: "assistant", content: "hello" },
  ],
};

const savedMessages = () => {
  const saved = H.api.puts.filter((p) => p.path.startsWith("/chat/threads/")).at(-1)!;
  return saved.body.messages as Record<string, unknown>[];
};

describe("a message says when it was sent (v1.323.0)", () => {
  it("a sent question and its reply carry a time, saved with the thread and shown on hover", async () => {
    render(<ChatPage />);
    await send("what is due?", "the K-1");
    await waitFor(() => expect(savedMessages().length).toBe(2));
    expect(savedMessages().map((m) => typeof m.at)).toEqual(["string", "string"]);
    expect(screen.getAllByTestId("message-time").length).toBe(2);
  });
});

describe("a half-typed message waits for its own conversation (v1.323.0)", () => {
  it("leaving a saved chat keeps its draft; opening it again puts the words back", async () => {
    H.api.getResponses["/chat/threads"] = CLIENT_THREADS;
    H.api.getResponses["/chat/threads/t1"] = T1;
    window.history.replaceState({}, "", "/chat?thread=t1");
    render(<ChatPage />);
    await screen.findByText("hello");
    const box = (await screen.findByPlaceholderText(/Message Iron Jarvis/)) as HTMLTextAreaElement;
    fireEvent.change(box, { target: { value: "half a question about the K-1" } });
    await act(async () => {
      window.dispatchEvent(new Event(NEW_CHAT_EVENT));
    });
    expect(box.value).toBe("");
    expect(readDraft("t1")).toBe("half a question about the K-1");
    fireEvent.click(await screen.findByText("Client K-1"));
    await waitFor(() => expect(box.value).toBe("half a question about the K-1"));
  });

  it("a sent message is never restored as a draft", async () => {
    writeDraft("t1", "an old draft");
    H.api.getResponses["/chat/threads/t1"] = T1;
    window.history.replaceState({}, "", "/chat?thread=t1");
    render(<ChatPage />);
    await screen.findByText("hello");
    await send("an old draft", "answered");
    expect(readDraft("t1")).toBe("");
  });
});

describe("Continue makes a cut-off reply whole (v1.323.0)", () => {
  it("a reply that ran out of room offers Continue; the carry-on is merged into ONE reply", async () => {
    render(<ChatPage />);
    H.stream.extra.push({ truncated: true });
    await send("write the memo", "The memo begins and then");
    expect(screen.getByTestId("reply-cut-note").textContent).toMatch(/ran out of room/);
    // Hold the carry-on mid-flight: the hidden "please continue" turn is in
    // the conversation then, and must not be on screen.
    H.stream.hold = true;
    const before = H.stream.bodies.length;
    fireEvent.click(screen.getByTestId("continue-reply"));
    await waitFor(() => expect(H.stream.bodies.length).toBe(before + 1));
    expect(screen.queryByText(CONTINUE_PROMPT)).toBeNull();
    await act(async () => {
      H.stream.settle?.({ reply: "it ends properly." });
      await new Promise((r) => setTimeout(r, 20));
    });
    H.stream.hold = false;
    await screen.findByText("The memo begins and then it ends properly.");
    // The model was asked to carry on, with the cut reply in the history.
    const body = H.stream.bodies.at(-1) as { messages: { role: string; content: string }[] };
    expect(body.messages.at(-1)).toEqual({ role: "user", content: CONTINUE_PROMPT });
    expect(body.messages.at(-2)?.content).toBe("The memo begins and then");
    // One reply on screen and on disk; the hidden turn is gone.
    expect(screen.queryByText(CONTINUE_PROMPT)).toBeNull();
    expect(screen.queryByTestId("continue-reply")).toBeNull();
    await waitFor(() =>
      expect(savedMessages().map((m) => m.content)).toEqual([
        "write the memo",
        "The memo begins and then it ends properly.",
      ]),
    );
  });

  it("mergeContinuation is a no-op without a hidden turn, and keeps steer notes in order", () => {
    const plain = [
      { role: "user" as const, content: "q" },
      { role: "assistant" as const, content: "a" },
    ];
    expect(mergeContinuation(plain)).toBe(plain);
    const merged = mergeContinuation([
      { role: "user" as const, content: "q" },
      { role: "assistant" as const, content: "part one", toolsUsed: ["read_file"], interrupted: true },
      { role: "user" as const, content: CONTINUE_PROMPT, continuation: true },
      { role: "user" as const, content: "also cite it", steer: true },
      { role: "assistant" as const, content: " part two", toolsUsed: ["read_file", "web_search"] },
    ]);
    expect(merged.map((m) => m.content)).toEqual(["q", "also cite it", "part one part two"]);
    expect(merged.at(-1)?.toolsUsed).toEqual(["read_file", "web_search"]);
    expect(merged.at(-1)?.interrupted).toBeUndefined();
  });

  it("the join puts back the space a trim removed, and adds none before punctuation", () => {
    expect(joinContinuation("and then", "it ends.")).toBe("and then it ends.");
    expect(joinContinuation("the total is $1,200", ", due April 15.")).toBe("the total is $1,200, due April 15.");
    expect(joinContinuation("see (", "page 3)")).toBe("see (page 3)");
    expect(joinContinuation("line one\n", "line two")).toBe("line one\nline two");
  });
});

describe("the reply's reasoning, steps and timing are kept (v1.323.0)", () => {
  it("the reasoning folds above the settled reply; the message keeps steps and timing", async () => {
    render(<ChatPage />);
    H.stream.extra.push({
      thinking: "First I check the due dates.",
      thinkingMs: 2400,
      steps: [{ name: "read_file", ok: true, ms: 300 }],
      timing: { startedAt: 1000, firstTokenAt: 2200, endedAt: 4200 },
    });
    await send("when is it due?", "April 15.");
    const fold = screen.getByTestId("thinking-disclosure");
    expect(fold.textContent).toMatch(/Thought for 2 s/);
    expect(fold.textContent).not.toMatch(/due dates/); // folded by default
    await waitFor(() => expect(savedMessages().at(-1)?.thinking).toBe("First I check the due dates."));
    const reply = savedMessages().at(-1)!;
    expect(reply.steps).toEqual([{ name: "read_file", ok: true, ms: 300 }]);
    expect(reply.timing).toEqual({ startedAt: 1000, firstTokenAt: 2200, endedAt: 4200 });
  });

  it("the reasoning is never sent back to a model", async () => {
    render(<ChatPage />);
    H.stream.extra.push({ thinking: "private chain of thought" });
    await send("one", "first answer");
    await send("two", "second answer");
    expect(JSON.stringify(H.stream.bodies.at(-1))).not.toContain("private chain of thought");
  });
});

describe("Read aloud is one press per reply (v1.323.0)", () => {
  it("the button reads THAT reply, keyed by its place", async () => {
    render(<ChatPage />);
    await send("hi", "Hello there.");
    fireEvent.click(screen.getByTestId("read-aloud"));
    expect(TTS.readAloud).toHaveBeenCalledWith("Hello there.", "reply-1");
  });
});

describe("follow-up questions are a suggestion, never a send (v1.323.0)", () => {
  it("with the setting on, a press puts the question in the box and sends nothing", async () => {
    H.api.getResponses["/settings"] = { settings: { chat_followups: true } };
    H.api.postResponses["/chat/followups"] = { suggestions: ["What about state returns?"], reason: "" };
    render(<ChatPage />);
    H.stream.extra.push({ route: { requested: "", provider: "ollama", model: "glm", reason: "default" } });
    await send("federal due date?", "April 15.");
    const chip = await screen.findByTestId("followup-chip");
    const ask = H.api.posts.find((p) => p.path === "/chat/followups")!;
    expect(ask.body).toMatchObject({ provider: "ollama", model: "glm" });
    const sentBefore = H.stream.bodies.length;
    fireEvent.click(chip);
    const box = screen.getByPlaceholderText(/Message Iron Jarvis/) as HTMLTextAreaElement;
    expect(box.value).toBe("What about state returns?");
    expect(H.stream.bodies.length).toBe(sentBefore);
    expect(screen.queryByTestId("followup-chip")).toBeNull();
  });

  it("with the setting off (the default), nothing is asked", async () => {
    render(<ChatPage />);
    await send("federal due date?", "April 15.");
    await new Promise((r) => setTimeout(r, 20));
    expect(H.api.posts.some((p) => p.path === "/chat/followups")).toBe(false);
    expect(screen.queryByTestId("followup-chip")).toBeNull();
  });
});
