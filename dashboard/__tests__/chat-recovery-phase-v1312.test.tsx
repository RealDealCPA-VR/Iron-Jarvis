/**
 * Wave 4 (RECOVERY), v1.312.0 — W4-1/W4-3, the client half: the bubble says
 * what a PREPARING turn is doing.
 *
 * Plain words: the daemon now opens /chat/stream at once and does its
 * preparation (recall, reading attachments, an automatic summary, choosing
 * tools) INSIDE the stream, announcing each stage with an additive
 * `event: phase` frame `{"phase": "recalling" | "reading_files" |
 * "summarizing" | "choosing_tools"}` before the first token. The bubble used
 * to say "Thinking…" for all of it.
 *
 * `useChatStream` is NOT mocked here: the page drives the REAL hook, whose
 * real `streamSSE` reads a fake fetch body the test feeds frame by frame
 * (the v1.311.0 reset harness's Wire). So these pins cover decodeSSE, the
 * hook's reducer AND the bubble — the whole path a phase frame takes.
 *
 * The prep bound (STREAM_PREP_MS) stays for an OLDER daemon that answers only
 * after preparing; once a response is open, the stall rule (heartbeat-reset)
 * is what ends a dead preparation.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const H = vi.hoisted(() => ({
  api: {
    posts: [] as { path: string; body: unknown }[],
    getResponses: {} as Record<string, unknown>,
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
      H.api.posts.push({ path, body });
      return {};
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
import {
  decodeSSE,
  STREAM_PREP_MS,
  STREAM_STALL_MS,
  stallDetail,
  streamSSE,
  type SSEEvent,
} from "@/lib/useChatStream";

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

let wire: Wire;
let streamCalls: string[] = [];

async function pump(): Promise<void> {
  for (let i = 0; i < 4; i++) await new Promise((r) => setTimeout(r, 0));
}

/** Deliver ONE frame in its own act(), then let the hook consume it. */
async function send(event: string, data: unknown): Promise<void> {
  await act(async () => {
    wire.frame(event, data);
    await pump();
  });
}

beforeEach(() => {
  wire = new Wire();
  streamCalls = [];
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
  };
  // The daemon opens the response at once (W4-1); only /chat/stream is served.
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (String(url).endsWith("/chat/stream")) {
        streamCalls.push(String(url));
        return wire.response();
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
  wire.end();
  cleanup();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

async function startTurn(text: string) {
  const box = (await screen.findByPlaceholderText(/Message Iron Jarvis/)) as HTMLTextAreaElement;
  fireEvent.change(box, { target: { value: text } });
  fireEvent.keyDown(box, { key: "Enter" });
  await waitFor(() => expect(streamCalls).toHaveLength(1));
  await act(async () => {
    await pump();
  });
}

const RECALLING = /Recalling what['’]s relevant…/;
const SUMMARIZING = /Summarizing earlier conversation…/;
const READING = /Reading your files…/;
const CHOOSING = /Choosing tools…/;

/* ======================================================================= */
describe("W4-3 — the bubble names each preparation stage", () => {
  it("each phase frame changes the waiting words, and the model starting ends them", async () => {
    render(<ChatPage />);
    await startTurn("summarize what we decided");
    // Opened, nothing announced yet: the generic word.
    expect(await screen.findByText("Thinking…")).toBeInTheDocument();

    await send("phase", { phase: "recalling" });
    expect(await screen.findByText(RECALLING)).toBeInTheDocument();

    await send("phase", { phase: "summarizing" });
    expect(await screen.findByText(SUMMARIZING)).toBeInTheDocument();
    expect(screen.queryByText(RECALLING)).toBeNull();

    await send("phase", { phase: "reading_files" });
    expect(await screen.findByText(READING)).toBeInTheDocument();
    expect(screen.queryByText(SUMMARIZING)).toBeNull();

    await send("phase", { phase: "choosing_tools" });
    expect(await screen.findByText(CHOOSING)).toBeInTheDocument();
    expect(screen.queryByText(READING)).toBeNull();

    // The first model round has begun: preparation is over.
    await send("round", { round: 0 });
    await waitFor(() => expect(screen.queryByText(CHOOSING)).toBeNull());
    expect(screen.getByText("Thinking…")).toBeInTheDocument();

    await send("done", { reply: "All set." });
    await act(async () => {
      wire.end();
      await pump();
    });
    expect(await screen.findByText("All set.")).toBeInTheDocument();
  });

  it("control: an unknown phase word is ignored — the bubble keeps its generic word, the turn goes on", async () => {
    render(<ChatPage />);
    await startTurn("hello");
    await send("phase", { phase: "levitating" });
    expect(await screen.findByText("Thinking…")).toBeInTheDocument();
    for (const w of [RECALLING, SUMMARIZING, READING, CHOOSING]) expect(screen.queryByText(w)).toBeNull();
    await send("done", { reply: "Hi there." });
    await act(async () => {
      wire.end();
      await pump();
    });
    expect(await screen.findByText("Hi there.")).toBeInTheDocument();
  });
});

/* ======================================================================= */
describe("decodeSSE — the phase frame", () => {
  it("decodes each of the four stages", () => {
    for (const phase of ["recalling", "reading_files", "summarizing", "choosing_tools"]) {
      expect(decodeSSE("phase", JSON.stringify({ phase }))).toEqual({ type: "phase", phase });
    }
  });
  it("control: an unknown or missing stage decodes to nothing (older clients are unaffected either way)", () => {
    expect(decodeSSE("phase", JSON.stringify({ phase: "levitating" }))).toBeNull();
    expect(decodeSSE("phase", JSON.stringify({}))).toBeNull();
    expect(decodeSSE("phase", JSON.stringify({ phase: 3 }))).toBeNull();
  });
});

/* ======================================================================= */
describe("the watchdogs once the response opens eagerly", () => {
  async function collect(stallMs: number, prepMs: number): Promise<SSEEvent[]> {
    const out: SSEEvent[] = [];
    for await (const ev of streamSSE("/chat/stream", { messages: [] }, undefined, { stallMs, prepMs }))
      out.push(ev);
    return out;
  }

  it("a preparation announced on an open response and then silent is ended by the STALL rule, with words", async () => {
    const w = new Wire();
    vi.stubGlobal("fetch", vi.fn(async () => w.response()));
    setTimeout(() => w.frame("phase", { phase: "reading_files" }), 0);
    const events = await collect(80, 60_000);
    expect(events[0]).toEqual({ type: "phase", phase: "reading_files" });
    expect(events.at(-1)).toEqual({ type: "error", detail: stallDetail(80), status: 0 });
  });

  it("guard: an OLDER daemon that answers only after preparing keeps the prep bound, not the stall rule", async () => {
    const w = new Wire();
    vi.stubGlobal(
      "fetch",
      vi.fn(
        () =>
          new Promise<Response>((res) =>
            setTimeout(() => {
              w.frame("done", { reply: "late but fine" });
              w.end();
              res(w.response());
            }, 200),
          ),
      ),
    );
    // 200 ms with no response at all is far past a 50 ms stall — and allowed.
    const events = await collect(50, 5_000);
    expect(events).toEqual([{ type: "done", reply: "late but fine" }]);
  });

  it("guard: the prep bound stays longer than the stall rule (an older daemon's OCR is not a stall)", () => {
    expect(STREAM_PREP_MS).toBeGreaterThan(STREAM_STALL_MS);
  });
});
