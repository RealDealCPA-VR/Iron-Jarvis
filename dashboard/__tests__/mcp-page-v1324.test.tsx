/**
 * v1.324.0 — wave C on the chat page: apps that talk back.
 *
 * "/" lists the prompts the user's apps offer (picking one opens its little
 * form; its text lands in the box and is never sent by itself). "@" lists the
 * apps' resources (picking one makes a chip that rides the NEXT message as
 * `resources`, then is gone). Every stream turn tells the daemon this page
 * draws the cards (`mcp_cards`), and the live reply renders an app's question
 * card and its progress. A resource the daemon could not read says so under
 * the reply. Leaving the conversation drops the picked resources.
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
import { NEW_CHAT_EVENT } from "@/lib/sidebarSlot";

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

async function pickResource(el: HTMLTextAreaElement) {
  type(el, "look at @bud");
  fireEvent.click(await screen.findByTestId("pack-resource-option"));
  await screen.findByTestId("app-resource-chip");
}

describe('"/" offers the apps\' prompts (v1.324.0)', () => {
  it("a prompt's text lands in the box after its form — and is never sent by itself", async () => {
    H.api.postResponses["/mcp/prompts/get"] = {
      text: "Summarize the folder C:/work in five bullets.",
      messages: [],
      flagged: false,
    };
    render(<ChatPage />);
    const el = await box();
    type(el, "/summ");
    fireEvent.click(await screen.findByTestId("pack-prompt-option"));
    // The "/summ" token is consumed; the form asks for the prompt's blank.
    expect(el.value).toBe("");
    fireEvent.change(await screen.findByLabelText(/Folder/), { target: { value: "C:/work" } });
    fireEvent.click(screen.getByRole("button", { name: "Use" }));
    await waitFor(() => expect(el.value).toBe("Summarize the folder C:/work in five bullets."));
    const got = H.api.posts.find((p) => p.path === "/mcp/prompts/get")!;
    expect(got.body).toEqual({ pack: "files", name: "summarize", arguments: { folder: "C:/work" } });
    expect(H.stream.bodies.length).toBe(0); // put in the box, never sent
  });
});

describe('"@" attaches an app\'s resource to the next message (v1.324.0)', () => {
  it("the chip rides the next send as `resources`, and every stream turn says it draws the cards", async () => {
    H.stream.replies.push("read it");
    render(<ChatPage />);
    const el = await box();
    await pickResource(el);
    expect(el.value).toBe("look at "); // the "@bud" token is consumed
    type(el, "what changed in my budget?");
    fireEvent.keyDown(el, { key: "Enter" });
    await screen.findByText("read it");
    const body = H.stream.bodies[0];
    expect(body.mcp_cards).toBe(true);
    expect(body.resources).toEqual([
      { pack: "files", uri: "file:///notes/budget.md", name: "Budget notes" },
    ]);
    // Consumed: the chip is gone and the NEXT message carries none.
    expect(screen.queryByTestId("app-resource-chip")).toBeNull();
    H.stream.replies.push("again");
    type(el, "and now?");
    fireEvent.keyDown(el, { key: "Enter" });
    await screen.findByText("again");
    expect(H.stream.bodies[1].resources).toBeUndefined();
    expect(H.stream.bodies[1].mcp_cards).toBe(true);
  });

  it("Regenerate reads the message's resources again, like its attachments", async () => {
    H.stream.replies.push("first answer", "second answer");
    render(<ChatPage />);
    const el = await box();
    await pickResource(el);
    type(el, "summarize my budget");
    fireEvent.keyDown(el, { key: "Enter" });
    await screen.findByText("first answer");
    fireEvent.click(screen.getByRole("button", { name: "Regenerate reply" }));
    await screen.findByText("second answer");
    expect(H.stream.bodies[1].resources).toEqual(H.stream.bodies[0].resources);
    expect((H.stream.bodies[1].resources as unknown[]).length).toBe(1);
  });

  it("a resource the daemon could not read is said under the reply", async () => {
    H.stream.replies.push("answered without it");
    H.stream.extra.push({
      resources: [{ pack: "files", uri: "file:///notes/budget.md", ok: false, note: "The app did not answer." }],
    });
    render(<ChatPage />);
    const el = await box();
    await pickResource(el);
    type(el, "summarize");
    fireEvent.keyDown(el, { key: "Enter" });
    await screen.findByText("answered without it");
    expect((await screen.findByTestId("app-resource-failed")).textContent).toContain(
      "Couldn't read file:///notes/budget.md from files: The app did not answer.",
    );
  });

  it("leaving the conversation drops a picked resource", async () => {
    render(<ChatPage />);
    const el = await box();
    await pickResource(el);
    act(() => {
      window.dispatchEvent(new Event(NEW_CHAT_EVENT));
    });
    await waitFor(() => expect(screen.queryByTestId("app-resource-chip")).toBeNull());
  });
});

describe("the live reply shows what an app asks and how far it is (v1.324.0)", () => {
  it("an app's question card and a tool's progress render while the turn runs", async () => {
    H.stream.hold = true;
    H.stream.live = {
      tools: [{ id: "c1", name: "mcp__files__scan", status: "started", progress: { progress: 2, total: 5, message: "reading" } }],
      mcpAsks: [
        {
          kind: "elicitation",
          id: "e1",
          callId: "c1",
          pack: "files",
          message: "Which year?",
          fields: [{ name: "year", type: "integer", title: "Year", description: "", required: true }],
        },
      ],
    };
    render(<ChatPage />);
    const el = await box();
    type(el, "scan my files");
    fireEvent.keyDown(el, { key: "Enter" });
    expect(await screen.findByText("files is asking")).toBeTruthy();
    expect(screen.getByText("Which year?")).toBeTruthy();
    expect((await screen.findByTestId("tool-progress")).textContent).toBe("40% · reading");
  });
});
