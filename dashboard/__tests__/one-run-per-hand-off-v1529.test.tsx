/**
 * v1.529.0 — one run per hand-off (owner decision 9 after the third fix plan).
 *
 * Plain words: with two windows open on one chat, a reply that hands the
 * request to an agent was posted by BOTH windows, and the agent run started
 * twice. The hand-off now names the chat turn that handed off
 * (`handed_from_turn`, the same name the turn was streamed under), and the
 * daemon starts one run per name and gives the second window the first run
 * (tests/test_one_run_per_hand_off_v1529.py pins that half).
 *
 * This file pins the page's half: the opener (`POST /sessions`), the
 * continue and the custom-agent door (`POST /agents/{slug}/spawn`) all carry
 * the name of the turn that handed off. Harness: the
 * v1.344.0 one (the REAL
 * `useChatStream` reading a fake fetch body frame by frame).
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

/** One Wire per /chat/stream call, in order, and the body each was asked with. */
let wires: Wire[] = [];
let streamBodies: Record<string, unknown>[] = [];

async function pump(): Promise<void> {
  for (let i = 0; i < 4; i++) await new Promise((r) => setTimeout(r, 0));
}

async function send(event: string, data: unknown): Promise<void> {
  await act(async () => {
    wires[wires.length - 1].frame(event, data);
    await pump();
  });
}

/** The daemon's `done` with an escalation, then the stream closes. */
async function finishEscalating(extra: Record<string, unknown> = {}): Promise<void> {
  await send("done", { reply: "", escalate: true, escalate_reason: "needs the agent", ...extra });
  await act(async () => {
    wires[wires.length - 1].end();
    await pump();
  });
}

beforeEach(() => {
  wires = [];
  streamBodies = [];
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
    "/sessions/s1/continue": { id: "s2", status: "running" },
    "/agents/helper/spawn": { id: "s3", status: "running" },
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: { body?: string }) => {
      if (String(url).endsWith("/chat/stream")) {
        streamBodies.push(init?.body ? JSON.parse(init.body) : {});
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

describe("v1.529.0 — a hand-off names the chat turn that handed off", () => {
  it("POST /sessions carries the name the turn was streamed under", async () => {
    render(<ChatPage />);
    await startTurn("do the long thing");
    const turn = streamBodies[0].turn_id;
    expect(typeof turn === "string" && turn.length > 0).toBe(true);
    await finishEscalating();
    await waitFor(() => expect(postTo("/sessions")).toBeTruthy());
    expect(postTo("/sessions")!.body.handed_from_turn).toBe(turn);
  });

  it("a continue of the chat's earlier run carries ITS turn's name", async () => {
    H.api.getResponses["/sessions/s1?transcript=0"] = {
      session: { id: "s1", status: "completed", summary: "Done: 42" },
    };
    render(<ChatPage />);
    await startTurn("do the long thing");
    await finishEscalating();
    await waitFor(() => expect(postTo("/sessions")).toBeTruthy());
    expect(await screen.findByText("Done: 42", {}, { timeout: 6_000 })).toBeInTheDocument();

    await startTurn("and now the rest");
    const second = streamBodies[1].turn_id;
    expect(second).not.toBe(streamBodies[0].turn_id);
    await finishEscalating();
    await waitFor(() => expect(postTo("/sessions/s1/continue")).toBeTruthy(), { timeout: 6_000 });
    expect(postTo("/sessions/s1/continue")!.body.handed_from_turn).toBe(second);
  });

  it("the custom-agent door (POST /agents/{slug}/spawn) carries it too", async () => {
    render(<ChatPage />);
    await startTurn("do the long thing");
    const turn = streamBodies[0].turn_id;
    await finishEscalating({ escalate_agent: "custom:helper" });
    await waitFor(() => expect(postTo("/agents/helper/spawn")).toBeTruthy());
    expect(postTo("/agents/helper/spawn")!.body.handed_from_turn).toBe(turn);
    expect(postTo("/sessions")).toBeUndefined();
  });
});
