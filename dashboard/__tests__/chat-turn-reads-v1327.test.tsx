/**
 * v1.327.0 (calm chat W2-3) — the chat page stores what a turn READ (the
 * project folder's rule files, the @-referenced chats) on the assistant
 * message, in BOTH lanes, at the END of the receipt object, and the reply's
 * expanded receipt says it.
 *
 * Header, mocks and helpers are the chat-remembered-v1282 harness; the stream
 * mock answers `folderRules` / `threadRefs` (the hook's decoded names), and
 * `H.stream.absent` makes /chat/stream look missing so the page falls back to
 * POST /chat, whose response carries the wire names `folder_rules` /
 * `thread_refs`.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

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
      absent: false,
      extra: {} as Record<string, unknown>,
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
    run: async (_body: Record<string, unknown>, onDelta: (d: string, f: string) => void) => {
      if (H.stream.absent) throw new H.FakeStreamError("no stream", 404);
      await new Promise<void>((r) => setTimeout(r, 0));
      onDelta("Noted.", "Noted.");
      return {
        reply: "Noted.",
        route: { requested: "", provider: "mock", model: "mock", reason: "default" },
        ...H.stream.extra,
      };
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

const NOT_FOUND = "No saved chat was found for this reference.";
const REFS = [
  { id: "a", title: "Tax plan", chars: 900, ok: true, note: "" },
  { id: "c", title: "", chars: 0, ok: false, note: NOT_FOUND },
];

beforeEach(() => {
  H.api.posts.length = 0;
  H.api.puts.length = 0;
  H.stream.absent = false;
  H.stream.extra = {};
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
  const box = (await screen.findByPlaceholderText(/Message Iron Jarvis/)) as HTMLTextAreaElement;
  fireEvent.change(box, { target: { value: text } });
  fireEvent.keyDown(box, { key: "Enter" });
  await screen.findByText(reply);
}

type Saved = {
  role: string;
  folderRules?: string[];
  threadRefs?: unknown[];
} & Record<string, unknown>;

/** The assistant message of the newest thread save that carries a reply. */
function lastSavedReply(): Saved | undefined {
  const saves = H.api.puts.filter((p) => p.path.startsWith("/chat/threads/"));
  for (let i = saves.length - 1; i >= 0; i--) {
    const msgs = saves[i].body.messages as Saved[];
    const last = msgs?.at(-1);
    if (last?.role === "assistant") return last;
  }
  return undefined;
}

async function openReceipt() {
  fireEvent.click(await screen.findByTestId("turn-receipt"));
  return screen.getByTestId("turn-receipt-detail");
}

describe("stream lane (v1.327.0)", () => {
  it("stores both fields on the reply, saves them LAST, and the receipt says them", async () => {
    H.stream.extra = { folderRules: ["AGENTS.md", "CLAUDE.md"], threadRefs: REFS };
    render(<ChatPage />);
    await send("what did we decide?", "Noted.");
    await waitFor(() => expect(lastSavedReply()?.threadRefs).toEqual(REFS));
    const saved = lastSavedReply()!;
    expect(saved.folderRules).toEqual(["AGENTS.md", "CLAUDE.md"]);
    // The receipt rule: new receipt fields go at the END of the receipt.
    expect(Object.keys(saved).slice(-2)).toEqual(["folderRules", "threadRefs"]);
    // A left-out chat keeps a small count on the collapsed line.
    await waitFor(() =>
      expect(screen.getByTestId("turn-refs-left-out").textContent).toBe("1 chat left out"),
    );
    await openReceipt();
    expect(screen.getByTestId("turn-folder-rules").textContent).toBe(
      "Followed the project's AGENTS.md and CLAUDE.md",
    );
    expect(screen.getByTestId("turn-thread-refs").textContent).toBe("Read 1 earlier chat: Tax plan");
    const note = screen.getByTestId("turn-thread-ref-note");
    expect(note.textContent).toBe(NOT_FOUND);
    expect(note.dataset.warn).toBe("true");
  });

  it("a turn that read nothing extra stores nothing and says nothing", async () => {
    H.stream.extra = { folderRules: [], threadRefs: [] };
    render(<ChatPage />);
    await send("hello", "Noted.");
    await waitFor(() => expect(lastSavedReply()).toBeDefined());
    const saved = lastSavedReply()!;
    expect("folderRules" in saved).toBe(false);
    expect("threadRefs" in saved).toBe(false);
    await openReceipt();
    expect(screen.queryByTestId("turn-folder-rules")).toBeNull();
    expect(screen.queryByTestId("turn-thread-refs")).toBeNull();
    expect(screen.queryByTestId("turn-refs-left-out")).toBeNull();
  });
});

describe("POST lane (no /chat/stream) (v1.327.0)", () => {
  it("reads folder_rules and thread_refs off the response, whitelisted, and saves them last", async () => {
    H.stream.absent = true;
    H.api.postResponses["/chat"] = {
      reply: "Posted reply.",
      route: { requested: "", provider: "mock", model: "mock", reason: "default" },
      folder_rules: ["AGENTS.md", 42],
      thread_refs: [...REFS, { title: "no id" }],
    };
    render(<ChatPage />);
    await send("what did we decide?", "Posted reply.");
    await waitFor(() => expect(lastSavedReply()?.threadRefs).toEqual(REFS));
    const saved = lastSavedReply()!;
    expect(saved.folderRules).toEqual(["AGENTS.md"]);
    expect(Object.keys(saved).slice(-2)).toEqual(["folderRules", "threadRefs"]);
    await openReceipt();
    expect(screen.getByTestId("turn-folder-rules").textContent).toBe("Followed the project's AGENTS.md");
    expect(screen.getByTestId("turn-thread-refs").textContent).toBe("Read 1 earlier chat: Tax plan");
  });

  it("an older daemon's response without the keys says nothing", async () => {
    H.stream.absent = true;
    H.api.postResponses["/chat"] = {
      reply: "Posted reply.",
      route: { requested: "", provider: "mock", model: "mock", reason: "default" },
    };
    render(<ChatPage />);
    await send("hello", "Posted reply.");
    await waitFor(() => expect(lastSavedReply()).toBeDefined());
    expect("folderRules" in lastSavedReply()!).toBe(false);
    expect("threadRefs" in lastSavedReply()!).toBe(false);
  });
});

describe("source pins (v1.327.0)", () => {
  const page = readFileSync(resolve(__dirname, "../app/chat/page.tsx"), "utf8").replace(/\r\n/g, "\n");

  it("the page hands both fields to the reply's receipt", () => {
    const at = page.indexOf("<TurnReceipt\n            inline");
    expect(at).toBeGreaterThan(-1);
    const tag = page.slice(at, page.indexOf("/>", at));
    expect(tag).toContain("folderRules={m.folderRules}");
    expect(tag).toContain("threadRefs={m.threadRefs}");
  });
});
