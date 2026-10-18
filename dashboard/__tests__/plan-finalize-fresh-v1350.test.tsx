/**
 * v1.350.0 integrate: a FRESH hand-off whose run made no plan costs no plan
 * read (lane C1 review, note 4: mutation M24, "finalize reads GET
 * /sessions/{id}/plan on every turn", stayed green because every page test
 * reopens a waiting chat).
 *
 * Plain words: finalize asks the daemon for a run's plan only when the live
 * checklist still has an unfinished step, or when the chat was reopened after
 * the run ended. A chat that hands a turn to an agent, watches it finish and
 * saw no plan reads nothing more than it did before v1.350.0.
 *
 * Harness: chat-escalation-carry-v1344's (the REAL useChatStream fed by a
 * fake fetch body), with every GET recorded.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const H = vi.hoisted(() => ({
  api: {
    gets: [] as string[],
    posts: [] as { path: string; body: Record<string, unknown> }[],
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
      H.api.gets.push(path);
      const r = H.api.getResponses[path];
      if (r === undefined) throw new real.ApiError(`unmocked GET ${path}`, 404);
      return r;
    },
    post: async (path: string, body?: unknown) => {
      H.api.posts.push({ path, body: (body ?? {}) as Record<string, unknown> });
      return H.api.postResponses[path] ?? {};
    },
    put: async (path: string) => {
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
    const c = this.enc.encode(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`);
    if (this.waiting) {
      const w = this.waiting;
      this.waiting = null;
      w({ value: c, done: false });
    } else this.chunks.push(c);
  }

  end(): void {
    this.ended = true;
    if (this.waiting) {
      const w = this.waiting;
      this.waiting = null;
      w({ done: true });
    }
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

let wires: Wire[] = [];

async function pump(): Promise<void> {
  for (let i = 0; i < 4; i++) await new Promise((r) => setTimeout(r, 0));
}

async function escalate(): Promise<void> {
  await act(async () => {
    wires[wires.length - 1].frame("done", { reply: "", escalate: true, escalate_reason: "needs the agent" });
    await pump();
  });
  await act(async () => {
    wires[wires.length - 1].end();
    await pump();
  });
}

beforeEach(() => {
  wires = [];
  H.api.gets.length = 0;
  H.api.posts.length = 0;
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
    "/sessions/s1?transcript=0": { session: { id: "s1", status: "completed", summary: "Done: 42" } },
  };
  H.api.postResponses = { "/sessions": { id: "s1", status: "running" } };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
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

describe("v1.350.0: finalize asks for a plan only when it needs one", () => {
  it("a fresh hand-off that made no plan finishes without reading GET /sessions/{id}/plan", async () => {
    render(<ChatPage />);
    const box = (await screen.findByPlaceholderText(/Message Iron Jarvis/)) as HTMLTextAreaElement;
    await waitFor(() => expect(box).toBeEnabled());
    fireEvent.change(box, { target: { value: "do the long thing" } });
    fireEvent.keyDown(box, { key: "Enter" });
    await waitFor(() => expect(wires).toHaveLength(1));
    await escalate();
    await waitFor(() => expect(H.api.posts.some((p) => p.path === "/sessions")).toBe(true));
    // The run is over and its reply is composed: finalize has run.
    expect(await screen.findByText("Done: 42", {}, { timeout: 6_000 })).toBeInTheDocument();
    // Anti-vacuity: finalize did read the session (so it ran its reads).
    expect(H.api.gets).toContain("/sessions/s1?transcript=0");
    expect(H.api.gets.filter((g) => g.endsWith("/plan"))).toEqual([]);
  }, 15_000);
});
