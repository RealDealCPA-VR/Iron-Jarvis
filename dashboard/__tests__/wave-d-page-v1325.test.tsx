/**
 * v1.325.0 — wave D on the chat page.
 *
 * Ctrl+Enter mid-reply queues the box and sends it when the reply finishes
 * cleanly (after a Stop it waits for "Send now"). An edited question and a
 * Try again keep what they replaced as another version ("‹ 1/2 ›"), the model
 * sees only the live one, and a Try again that fails with nothing to show
 * puts the earlier answer back. "Try again with…" another model is for that
 * turn only. "Ask Jarvis about this page" rides the next message (and its
 * Try again). A selection in a reply quotes into the box; the map jumps.
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
  const stream = {
    bodies: [] as Record<string, unknown>[],
    replies: [] as string[],
    extra: [] as Record<string, unknown>[],
    hold: false,
    reject: null as Error | null,
    streaming: false,
    version: 0,
    live: { tools: [] as unknown[], mcpAsks: [] as unknown[] },
    listeners: new Set<() => void>(),
    settle: null as null | ((r: Record<string, unknown>) => void),
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
  const run = async (body: Record<string, unknown>, onDelta: (d: string, f: string) => void) => {
    H.stream.bodies.push(body);
    if (H.stream.reject) throw H.stream.reject;
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
        tools: H.stream.live.tools,
        mcpAsks: H.stream.live.mcpAsks,
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
vi.mock("@/lib/useProviderHealth", () => ({
  useProviderHealth: () => ({ byProvider: {}, defaultProvider: "", loading: false, stale: false, refresh: () => {} }),
}));
vi.mock("react-markdown", async () => await vi.importActual("react-markdown"));

import ChatPage from "@/app/chat/page";
import { stashPageContext } from "@/lib/pageContext";

const PROMPTS = {
  prompts: [
    {
      pack: "files",
      name: "summarize",
      title: "Summarize a folder",
      description: "Writes a short summary",
      arguments: [{ name: "folder", title: "Folder", description: "", required: true }],
    },
  ],
  failed: [],
};
const RESOURCES = {
  resources: [
    {
      pack: "files",
      uri: "file:///notes/budget.md",
      name: "budget.md",
      title: "Budget notes",
      description: "this year's budget",
      mime_type: "text/markdown",
    },
  ],
  failed: [],
};

beforeEach(() => {
  H.api.posts.length = 0;
  H.api.puts.length = 0;
  H.stream.bodies.length = 0;
  H.stream.replies.length = 0;
  H.stream.extra.length = 0;
  H.stream.hold = false;
  H.stream.reject = null;
  H.stream.streaming = false;
  H.stream.settle = null;
  H.stream.live = { tools: [], mcpAsks: [] };
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
    "/mcp/prompts": PROMPTS,
    "/mcp/resources": RESOURCES,
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

async function box() {
  return (await screen.findByPlaceholderText(/Message Iron Jarvis/)) as HTMLTextAreaElement;
}

function type(el: HTMLTextAreaElement, value: string) {
  fireEvent.change(el, { target: { value, selectionStart: value.length, selectionEnd: value.length } });
  el.selectionStart = el.selectionEnd = value.length;
  fireEvent.select(el);
}


const MODELS = {
  models: [
    { provider: "ollama", model: "qwen3:32b", available: true, name: "Ollama", kind: "local" },
    { provider: "anthropic", model: "claude-opus-5-5", available: true, name: "Anthropic", kind: "api" },
  ],
};

async function send(el: HTMLTextAreaElement, text: string) {
  type(el, text);
  fireEvent.keyDown(el, { key: "Enter" });
}

/** A reply is on screen BEFORE its turn ends (the release gate found the
 *  gap): wait for the text AND for the box to stop steering. */
async function answered(text: string | RegExp) {
  await screen.findByText(text);
  await waitFor(() => expect(screen.queryByPlaceholderText(/Steer Jarvis mid-turn/)).toBeNull());
}

function lastUserText(body: Record<string, unknown>): string {
  const msgs = body.messages as { role: string; content: string }[];
  return [...msgs].reverse().find((m) => m.role === "user")?.content ?? "";
}

describe("queued follow-ups (v1.325.0)", () => {
  it("Ctrl+Enter mid-reply holds the message and sends it when the reply finishes", async () => {
    H.stream.hold = true;
    render(<ChatPage />);
    const el = await box();
    await send(el, "first question");
    const steer = (await screen.findByPlaceholderText(/Steer Jarvis mid-turn/)) as HTMLTextAreaElement;
    type(steer, "second question");
    fireEvent.keyDown(steer, { key: "Enter", ctrlKey: true });
    expect((await screen.findByTestId("queued-message")).textContent).toContain("Sends after this reply:");
    expect(screen.getByTestId("queued-message").textContent).toContain("second question");
    expect(steer.value).toBe("");
    expect(H.stream.bodies.length).toBe(1);
    // The reply finishes cleanly → the queued message goes out on its own.
    H.stream.hold = false;
    H.stream.replies.push("second answer");
    act(() => {
      H.stream.streaming = false;
      H.stream.bump();
      H.stream.settle?.({ reply: "first answer" });
    });
    await answered("second answer");
    expect(H.stream.bodies.length).toBe(2);
    expect(lastUserText(H.stream.bodies[1])).toBe("second question");
    expect(screen.queryByTestId("queued-message")).toBeNull();
  });

  it("after Stop the queue waits for Send now", async () => {
    H.stream.hold = true;
    render(<ChatPage />);
    const el = await box();
    await send(el, "first question");
    const steer = (await screen.findByPlaceholderText(/Steer Jarvis mid-turn/)) as HTMLTextAreaElement;
    type(steer, "then this");
    fireEvent.keyDown(steer, { key: "Enter", ctrlKey: true });
    await screen.findByTestId("queued-message");
    fireEvent.click(screen.getByTitle("Stop this turn"));
    await waitFor(() =>
      expect(screen.getByTestId("queued-message").textContent).toContain("Waiting to send:"),
    );
    await new Promise((r) => setTimeout(r, 30));
    expect(H.stream.bodies.length).toBe(1);
    H.stream.hold = false;
    H.stream.replies.push("sent now");
    fireEvent.click(screen.getByRole("button", { name: "Send now" }));
    await answered("sent now");
    expect(lastUserText(H.stream.bodies[1])).toBe("then this");
  });

  it("Edit puts a queued message back in the box", async () => {
    H.stream.hold = true;
    render(<ChatPage />);
    const el = await box();
    await send(el, "first question");
    const steer = (await screen.findByPlaceholderText(/Steer Jarvis mid-turn/)) as HTMLTextAreaElement;
    type(steer, "fix me later");
    fireEvent.keyDown(steer, { key: "Enter", ctrlKey: true });
    await screen.findByTestId("queued-message");
    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    expect(screen.queryByTestId("queued-message")).toBeNull();
    expect(steer.value).toBe("fix me later");
  });
});

describe("versions — an edit or a Try again keeps what it replaced (v1.325.0)", () => {
  it("an edited question keeps the earlier exchange one click away", async () => {
    H.stream.replies.push("answer one", "answer two");
    render(<ChatPage />);
    const el = await box();
    await send(el, "question one");
    await answered("answer one");
    fireEvent.click(screen.getByRole("button", { name: "Edit and resend" }));
    await waitFor(() => expect(el.value).toBe("question one"));
    await send(el, "question two");
    await answered("answer two");
    expect(screen.queryByText("answer one")).toBeNull();
    expect(await screen.findByRole("group", { name: "Version 2 of 2" })).toBeTruthy();
    // The model was sent only the live version.
    expect((H.stream.bodies[1].messages as unknown[]).length).toBe(1);
    expect(JSON.stringify(H.stream.bodies[1])).not.toContain("answer one");
    fireEvent.click(screen.getByRole("button", { name: "Previous version" }));
    await answered("answer one");
    expect(screen.getByText("question one")).toBeTruthy();
    expect(screen.queryByText("answer two")).toBeNull();
    // The switch is saved with the thread.
    const saved = H.api.puts[H.api.puts.length - 1].body.messages as { content: string }[];
    expect(saved.map((m) => m.content)).toEqual(["question one", "answer one"]);
  });

  it("Try again keeps the earlier answer and sends no versions to the model", async () => {
    H.stream.replies.push("first take", "second take");
    render(<ChatPage />);
    const el = await box();
    await send(el, "explain it");
    await answered("first take");
    fireEvent.click(screen.getByRole("button", { name: "Regenerate reply" }));
    await answered("second take");
    expect(await screen.findByRole("group", { name: "Version 2 of 2" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Previous version" }));
    await answered("first take");
    expect(screen.queryByText("second take")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Next version" }));
    await answered("second take");
  });

  it("a Try again that fails with nothing to show puts the earlier answer back", async () => {
    H.stream.replies.push("keep me");
    render(<ChatPage />);
    const el = await box();
    await send(el, "explain it");
    await answered("keep me");
    H.stream.reject = new H.FakeStreamError("the model is overloaded", 500);
    fireEvent.click(screen.getByRole("button", { name: "Regenerate reply" }));
    await answered(/the model is overloaded/);
    await answered("keep me");
    const saved = H.api.puts[H.api.puts.length - 1].body.messages as { content: string }[];
    expect(saved.map((m) => m.content)).toEqual(["explain it", "keep me"]);
  });

  it("Try again with another model asks that model for this turn only", async () => {
    H.api.getResponses["/models"] = MODELS;
    H.stream.replies.push("local answer", "cloud answer", "next answer");
    render(<ChatPage />);
    const el = await box();
    await send(el, "summarize");
    await answered("local answer");
    fireEvent.click(await screen.findByRole("button", { name: "Try again with another model" }));
    fireEvent.click(await screen.findByRole("menuitem", { name: /claude-opus-5-5/ }));
    await answered("cloud answer");
    expect(H.stream.bodies[1].provider).toBe("anthropic");
    expect(H.stream.bodies[1].model).toBe("claude-opus-5-5");
    await send(el, "and next");
    await answered("next answer");
    expect(H.stream.bodies[2].provider).toBeUndefined();
  });
});

describe("ask about a page (v1.325.0)", () => {
  it("the page the palette stashed rides the next message, and a Try again sends it again", async () => {
    stashPageContext({ title: "Clients", path: "/projects", text: "Acme Corp — 3 open returns" });
    window.history.replaceState({}, "", "/chat?about=page");
    H.stream.replies.push("about the page", "again about it");
    render(<ChatPage />);
    expect((await screen.findByTestId("page-context-chip")).textContent).toContain("About: Clients");
    const el = await box();
    await send(el, "what needs doing here?");
    await answered("about the page");
    expect(H.stream.bodies[0].page_context).toEqual({
      title: "Clients",
      path: "/projects",
      text: "Acme Corp — 3 open returns",
    });
    expect(screen.queryByTestId("page-context-chip")).toBeNull();
    expect(screen.getByText("About: Clients")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Regenerate reply" }));
    await answered("again about it");
    expect(H.stream.bodies[1].page_context).toEqual(H.stream.bodies[0].page_context);
  });
});

describe("quote and map (v1.325.0)", () => {
  it("a selection in a reply is quoted into the box, never sent", async () => {
    H.stream.replies.push("The deadline is April 15.");
    render(<ChatPage />);
    const el = await box();
    await send(el, "when?");
    await answered("The deadline is April 15.");
    const reply = screen.getByText("The deadline is April 15.");
    const range = document.createRange();
    range.selectNodeContents(reply);
    const sel = window.getSelection()!;
    sel.removeAllRanges();
    sel.addRange(range);
    act(() => {
      document.dispatchEvent(new Event("selectionchange"));
    });
    fireEvent.click(await screen.findByRole("button", { name: /Quote/ }));
    await waitFor(() => expect(el.value).toContain("> The deadline is April 15."));
    expect(H.stream.bodies.length).toBe(1);
  });

  it("the map lists the questions and jumps to one", async () => {
    H.stream.replies.push("a1", "a2");
    render(<ChatPage />);
    const el = await box();
    await send(el, "first topic");
    await answered("a1");
    await send(el, "second topic");
    await answered("a2");
    const spy = vi.fn();
    Element.prototype.scrollIntoView = spy;
    fireEvent.click(screen.getByTestId("open-conversation-map"));
    const row = await screen.findByRole("option", { name: /first topic/ });
    spy.mockClear();
    fireEvent.click(row);
    await waitFor(() => expect(spy).toHaveBeenCalled());
    const target = spy.mock.instances[0] as unknown as Element;
    expect(target.closest("[data-msg-index]")?.getAttribute("data-msg-index")).toBe("0");
  });
});
