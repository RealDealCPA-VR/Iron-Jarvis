/**
 * v1.406.0 — a chat turn's brain receipt reaches the saved message and the
 * reply's receipt, in BOTH lanes.
 *
 * `useChatStream` is NOT mocked: the page drives the REAL hook, whose real
 * `streamSSE` reads a fake fetch body the test feeds frame by frame (the
 * chat-escalation-carry-v1344 Wire harness). So a pin here covers decodeSSE
 * (the whitelist: an un-listed field dies there silently), the hook's done
 * mapping, the page's receipt object (`brain` saved LAST, only when
 * non-null) and the TurnReceipt line. The POST /chat fallback (the stream
 * answers 404) carries the same field from the response body.
 */


import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const H = vi.hoisted(() => ({
  api: {
    posts: [] as { path: string; body: Record<string, unknown> }[],
    puts: [] as { path: string; body: Record<string, unknown> }[],
    getResponses: {} as Record<string, unknown>,
    postResponses: {} as Record<string, unknown>,
  },
}));

vi.mock("@/lib/api", async (orig) => {
  const real = await orig<typeof import("@/lib/api")>();
  return {
    ...real,
    API_BASE: "http://localhost",
    ijToken: () => "",
    get: async (path: string) => {
      const r = H.api.getResponses[path];
      if (r === undefined) throw new real.ApiError(`unmocked GET ${path}`, 404);
      return r;
    },
    post: async (path: string, body?: unknown) => {
      H.api.posts.push({ path, body: (body ?? {}) as Record<string, unknown> });
      return H.api.postResponses[path] ?? {};
    },
    // The page saves through put; POST /chat (the fallback lane) is a post.
    put: async (path: string, body?: unknown) => {
      H.api.puts.push({ path, body: (body ?? {}) as Record<string, unknown> });
      const m = /^\/chat\/threads\/(.+)$/.exec(path);
      return { id: m && m[1] !== "new" ? m[1] : "t1", title: "t" };
    },
    del: async () => ({}),
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
vi.mock("@/lib/useProviderHealth", () => ({
  useProviderHealth: () => ({
    byProvider: {}, cooldownByProvider: {}, signedOutByProvider: {}, defaultProvider: "",
    loading: false, stale: false, refresh: () => {},
  }),
}));
vi.mock("react-markdown", () => ({ default: ({ children }: { children?: string }) => <div>{children}</div> }));
vi.mock("remark-gfm", () => ({ default: () => {} }));

import ChatPage from "@/app/chat/page";
import { decodeSSE } from "@/lib/useChatStream";

// ------------------------------------------------------------------ transport

/** A response body the test writes SSE frames into, one at a time. */
class Wire {
  private chunks: Uint8Array[] = [];
  private waiting: ((r: { value?: Uint8Array; done: boolean }) => void) | null = null;
  private ended = false;
  private enc = new TextEncoder();

  frame(event: string, data: unknown): void {
    this.deliver(this.enc.encode(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`));
  }

  end(): void {
    this.ended = true;
    if (this.waiting) {
      const w = this.waiting;
      this.waiting = null;
      w({ done: true });
    }
  }

  private deliver(c: Uint8Array): void {
    if (this.waiting) {
      const w = this.waiting;
      this.waiting = null;
      w({ value: c, done: false });
    } else this.chunks.push(c);
  }

  response(): Response {
    return {
      ok: true,
      status: 200,
      statusText: "OK",
      body: {
        getReader: () => ({
          read: () =>
            new Promise<{ value?: Uint8Array; done: boolean }>((res) => {
              const c = this.chunks.shift();
              if (c) res({ value: c, done: false });
              else if (this.ended) res({ done: true });
              else this.waiting = res;
            }),
          cancel: async () => {
            this.end();
          },
        }),
      },
    } as unknown as Response;
  }
}

/** One Wire per /chat/stream call, in order — a second turn gets its own. */
let wires: Wire[] = [];

async function pump(): Promise<void> {
  for (let i = 0; i < 4; i++) await new Promise((r) => setTimeout(r, 0));
}

/** Deliver ONE frame on the newest stream in its own act(), then let the hook consume it. */
async function send(event: string, data: unknown): Promise<void> {
  await act(async () => {
    wires[wires.length - 1].frame(event, data);
    await pump();
  });
}

/** A plain answer, then the daemon's `done` with `extra`, then the close. */
async function finishWith(extra: Record<string, unknown>): Promise<void> {
  await send("token", { text: "Noted." });
  await send("done", { reply: "Noted.", route: ROUTE, ...extra });
  await act(async () => {
    wires[wires.length - 1].end();
    await pump();
  });
}

let streamAbsent = false;

beforeEach(() => {
  streamAbsent = false;
  wires = [];
  H.api.posts.length = 0;
  H.api.puts.length = 0;
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
  H.api.postResponses = {
    "/sessions": { id: "s1", status: "running" },
    "/agents/helper/spawn": { id: "s3", status: "running" },
    "/sessions/s1/continue": { id: "s2", status: "running" },
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (String(url).endsWith("/chat/stream") && streamAbsent) {
        return new Response(JSON.stringify({ detail: "Not Found" }), { status: 404 });
      }
      if (String(url).endsWith("/chat/stream")) {
        const w = new Wire();
        wires.push(w);
        return w.response();
      }
      return new Response("{}", { status: 404 });
    }),
  );
  window.history.replaceState({}, "", "/chat");
  window.localStorage.clear();
  window.sessionStorage.clear();
  Element.prototype.scrollIntoView = vi.fn();
});
afterEach(() => {
  for (const w of wires) w.end();
  cleanup();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

async function startTurn(text: string) {
  const expected = wires.length + 1;
  const box = (await screen.findByPlaceholderText(/Message Iron Jarvis/)) as HTMLTextAreaElement;
  await waitFor(() => expect(box).toBeEnabled());
  fireEvent.change(box, { target: { value: text } });
  fireEvent.keyDown(box, { key: "Enter" });
  await waitFor(() => expect(wires).toHaveLength(expected));
  await act(async () => {
    await pump();
  });
}

const postTo = (path: string) => H.api.posts.find((p) => p.path === path);

const ROUTE = { requested: "", provider: "claude-cli", model: "opus", reason: "default" };
const BRAIN = {
  name: "Notion",
  base: "Notion",
  searched: 2,
  guide: "loaded",
  saved: [{ title: "Q4 plan", ref: "notion://1", action: "created", verified: true }],
  unsaved: [],
  changed: [],
};

type Saved = Record<string, unknown> & { role?: string; brain?: unknown };

/** The assistant message of the newest thread save that carries a reply. */
function lastSavedReply(): Saved | undefined {
  const saves = H.api.puts.filter((p) => p.path.startsWith("/chat/threads/"));
  for (let i = saves.length - 1; i >= 0; i--) {
    const msgs = saves[i].body.messages as Saved[];
    const last = msgs?.at(-1);
    if (last?.role === "assistant" && last.content) return last;
  }
  return undefined;
}

describe("decodeSSE keeps `brain` (whitelisted)", () => {
  it("a done frame's brain is decoded; null and junk leave the key absent", () => {
    const ev = decodeSSE("done", JSON.stringify({ reply: "ok", brain: { ...BRAIN, secret: "x" } })) as {
      brain?: Record<string, unknown>;
    };
    expect(ev.brain).toBeTruthy();
    expect("secret" in (ev.brain ?? {})).toBe(false);
    expect((ev.brain as { saved: unknown[] }).saved).toHaveLength(1);
    expect("brain" in (decodeSSE("done", JSON.stringify({ reply: "ok", brain: null })) as object)).toBe(false);
    expect("brain" in (decodeSSE("done", JSON.stringify({ reply: "ok", brain: 3 })) as object)).toBe(false);
  });
});

describe("stream lane: the brain receipt reaches the message and the receipt", () => {
  it("is saved LAST on the reply, and the collapsed line says the save", async () => {
    render(<ChatPage />);
    await startTurn("remember the Q4 plan");
    await finishWith({ brain: BRAIN, folder_rules: ["AGENTS.md"] });
    await waitFor(() => expect(lastSavedReply()?.brain).toBeTruthy());
    const saved = lastSavedReply()!;
    expect(Object.keys(saved).at(-1)).toBe("brain");
    expect((saved.brain as { saved: { title: string }[] }).saved[0].title).toBe("Q4 plan");
    const line = await screen.findByTestId("turn-brain", {}, { timeout: 10_000 });
    expect(line.textContent).toBe("Saved to Notion: Q4 plan");
  });

  it("an unsaved note shows in the warn tone", async () => {
    render(<ChatPage />);
    await startTurn("remember the Q4 plan");
    await finishWith({ brain: { ...BRAIN, saved: [], unsaved: [{ title: "Q4 plan", reason: "Notion did not answer" }] } });
    const line = await screen.findByTestId("turn-brain", {}, { timeout: 10_000 });
    await waitFor(() => expect(line.textContent).toBe("Not saved yet: Q4 plan. Notion did not answer."));
    expect(line.getAttribute("data-tone")).toBe("warn");
  });

  it("control: brain null leaves no key and no line", async () => {
    render(<ChatPage />);
    await startTurn("hello");
    await finishWith({ brain: null });
    await waitFor(() => expect(lastSavedReply()).toBeTruthy());
    expect("brain" in lastSavedReply()!).toBe(false);
    expect(screen.queryByTestId("turn-brain")).toBeNull();
  });
});

describe("POST lane (the stream answers 404): the same field off the response", () => {
  it("is saved last on the reply and said on the line", async () => {
    streamAbsent = true;
    H.api.postResponses["/chat"] = { reply: "Posted reply.", route: ROUTE, brain: BRAIN };
    render(<ChatPage />);
    const box = (await screen.findByPlaceholderText(/Message Iron Jarvis/)) as HTMLTextAreaElement;
    await waitFor(() => expect(box).toBeEnabled());
    fireEvent.change(box, { target: { value: "remember the Q4 plan" } });
    fireEvent.keyDown(box, { key: "Enter" });
    await waitFor(() => expect(postTo("/chat")).toBeTruthy(), { timeout: 10_000 });
    await waitFor(() => expect(lastSavedReply()?.brain).toBeTruthy(), { timeout: 10_000 });
    expect(Object.keys(lastSavedReply()!).at(-1)).toBe("brain");
    const line = await screen.findByTestId("turn-brain", {}, { timeout: 10_000 });
    expect(line.textContent).toBe("Saved to Notion: Q4 plan");
  });

  it("control: a response without brain saves no key", async () => {
    streamAbsent = true;
    H.api.postResponses["/chat"] = { reply: "Posted reply.", route: ROUTE };
    render(<ChatPage />);
    const box = (await screen.findByPlaceholderText(/Message Iron Jarvis/)) as HTMLTextAreaElement;
    await waitFor(() => expect(box).toBeEnabled());
    fireEvent.change(box, { target: { value: "hello" } });
    fireEvent.keyDown(box, { key: "Enter" });
    await waitFor(() => expect(lastSavedReply()?.content).toBe("Posted reply."), { timeout: 10_000 });
    expect("brain" in lastSavedReply()!).toBe(false);
  });
});
