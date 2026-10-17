/**
 * v1.344.0 (lane Q) — an ESCALATION carries what the chat already ran.
 *
 * Plain words: when a chat turn decides it needs the full agent, the daemon's
 * `done` frame now carries `prior_tool_results` — the tool calls the chat lane
 * already made this turn, as display shapes (redacted args, a masked output
 * head). The page hands that list to the session the turn re-runs as, so the
 * agent does not redo those reads.
 *
 * `useChatStream` is NOT mocked here: the page drives the REAL hook, whose
 * real `streamSSE` reads a fake fetch body the test feeds frame by frame (the
 * v1.312.0 Wire harness). So a pin here covers decodeSSE (the whitelist), the
 * hook's reducer (`priorToolResults`), the page's escalation and the body of
 * the POST — the whole path the field takes.
 *
 * Two-sided, like every wire-field pin: a frame WITHOUT the field leaves the
 * session body exactly as it is today (its keys are pinned), and the
 * escalation bubble's words are unchanged either way.
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
import {
  decodePriorToolResults,
  decodeSSE,
  PRIOR_ARGS_SUMMARY_MAX,
  PRIOR_OUTPUT_HEAD_MAX,
  PRIOR_TOOL_RESULTS_CHARS,
  PRIOR_TOOL_RESULTS_MAX,
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

/** The daemon's `done` with an escalation, then the stream closes. */
async function finishEscalating(extra: Record<string, unknown>): Promise<void> {
  await send("done", { reply: "", escalate: true, escalate_reason: "needs the agent", ...extra });
  await act(async () => {
    wires[wires.length - 1].end();
    await pump();
  });
}

beforeEach(() => {
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

/** What the daemon sends: the contract's four keys per row, oldest first. */
const ROWS = [
  {
    tool: "read_file",
    args_summary: '{"path": "notes.md"}',
    ok: true,
    output_head: "# Notes\nThe plan for Q4.",
  },
  {
    tool: "app_search",
    args_summary: '{"query": "schedule"}',
    ok: false,
    output_head: "",
  },
];

/* ======================================================================= */
describe("v1.344.0 — the escalation carries what the chat already ran", () => {
  it("POST /sessions carries the done frame's prior_tool_results, whitelisted per row", async () => {
    render(<ChatPage />);
    await startTurn("do the long thing");
    await finishEscalating({
      prior_tool_results: [
        // An unknown key and a junk `ok` on the wire: both die at the decode.
        { ...ROWS[0], secret: "sk-live-123", ok: true },
        { ...ROWS[1], ok: "yes" },
      ],
    });
    await waitFor(() => expect(postTo("/sessions")).toBeTruthy());
    const body = postTo("/sessions")!.body;
    expect(body.prior_tool_results).toEqual(ROWS);
    // The rest of the body is today's.
    expect(body).toMatchObject({ task: "do the long thing", agent_type: "builder", wait: false, origin: "chat" });
    // The bubble's words are unchanged by the carried work: the hand-off is
    // saved with the daemon's reason, exactly as before.
    await waitFor(() =>
      expect(
        H.api.puts.some((p) => {
          const msgs = p.body.messages as { escalated?: string }[];
          return msgs[msgs.length - 1]?.escalated === "needs the agent";
        }),
      ).toBe(true),
    );
  });

  it("control: a frame without them leaves the session body exactly as today", async () => {
    render(<ChatPage />);
    await startTurn("do the long thing");
    await finishEscalating({});
    await waitFor(() => expect(postTo("/sessions")).toBeTruthy());
    const body = postTo("/sessions")!.body;
    expect(Object.keys(body).sort()).toEqual(["agent_type", "origin", "task", "wait"]);
    expect("prior_tool_results" in body).toBe(false);
  });

  it("control: an empty list on the wire sends nothing either", async () => {
    render(<ChatPage />);
    await startTurn("do the long thing");
    await finishEscalating({ prior_tool_results: [] });
    await waitFor(() => expect(postTo("/sessions")).toBeTruthy());
    expect("prior_tool_results" in postTo("/sessions")!.body).toBe(false);
  });

  it("the custom-agent door (POST /agents/{slug}/spawn) carries them too", async () => {
    render(<ChatPage />);
    await startTurn("do the long thing");
    await finishEscalating({ escalate_agent: "custom:helper", prior_tool_results: ROWS });
    await waitFor(() => expect(postTo("/agents/helper/spawn")).toBeTruthy());
    expect(postTo("/agents/helper/spawn")!.body.prior_tool_results).toEqual(ROWS);
    expect(postTo("/sessions")).toBeUndefined();
  });

  it("a continue of the conversation's session carries them too", async () => {
    // The first hand-off opens s1 and the run finishes (the 1.5 s poll reads
    // it completed), so the next escalation CONTINUES s1 instead of opening.
    H.api.getResponses["/sessions/s1?transcript=0"] = {
      session: { id: "s1", status: "completed", summary: "Done: 42" },
    };
    render(<ChatPage />);
    await startTurn("do the long thing");
    await finishEscalating({});
    await waitFor(() => expect(postTo("/sessions")).toBeTruthy());
    expect(await screen.findByText("Done: 42", {}, { timeout: 6_000 })).toBeInTheDocument();

    await startTurn("and now the rest");
    await finishEscalating({ prior_tool_results: ROWS });
    await waitFor(() => expect(postTo("/sessions/s1/continue")).toBeTruthy(), { timeout: 6_000 });
    const body = postTo("/sessions/s1/continue")!.body;
    expect(body.prior_tool_results).toEqual(ROWS);
    expect(body).toMatchObject({ message: "and now the rest", wait: false });
    // Still one opener: the second turn continued, it did not reopen.
    expect(H.api.posts.filter((p) => p.path === "/sessions")).toHaveLength(1);
  });
});

/* ======================================================================= */
describe("decodePriorToolResults — the whitelist and the caps", () => {
  const done = (data: Record<string, unknown>) =>
    decodeSSE("done", JSON.stringify({ reply: "ok", ...data }));

  it("keeps the four keys and drops the rest; ok is only a real true", () => {
    const ev = done({
      prior_tool_results: [
        { tool: "read_file", args_summary: "a", ok: true, output_head: "b", extra: 1, error: "boom" },
        { tool: "shell", args_summary: "ls", ok: 1, output_head: "x" },
      ],
    });
    expect(ev && ev.type === "done" && ev.prior_tool_results).toEqual([
      { tool: "read_file", args_summary: "a", ok: true, output_head: "b" },
      { tool: "shell", args_summary: "ls", ok: false, output_head: "x" },
    ]);
  });

  it("a row without a tool name, a non-object row, and a non-list all read as nothing", () => {
    expect(decodePriorToolResults([{ args_summary: "a", ok: true, output_head: "b" }])).toEqual([]);
    expect(decodePriorToolResults([{ tool: "   ", ok: true }])).toEqual([]);
    expect(decodePriorToolResults(["read_file", 3, null, ["read_file"]])).toEqual([]);
    expect(decodePriorToolResults({ tool: "read_file" })).toEqual([]);
    expect(decodePriorToolResults("read_file")).toEqual([]);
    expect(decodePriorToolResults(undefined)).toEqual([]);
    // And the frame leaves the key absent rather than carrying [].
    const ev = done({ prior_tool_results: [{ ok: true }] });
    expect(ev && ev.type === "done" && "prior_tool_results" in ev).toBe(false);
    const bare = done({ provider: "mock" });
    expect(bare && bare.type === "done" && "prior_tool_results" in bare).toBe(false);
  });

  it("cuts args_summary and output_head to the contract's caps; a missing string is empty", () => {
    const [row] = decodePriorToolResults([
      {
        tool: "read_file",
        args_summary: "a".repeat(PRIOR_ARGS_SUMMARY_MAX + 50),
        ok: true,
        output_head: "b".repeat(PRIOR_OUTPUT_HEAD_MAX + 500),
      },
    ]);
    expect(row.args_summary).toHaveLength(PRIOR_ARGS_SUMMARY_MAX);
    expect(row.output_head).toHaveLength(PRIOR_OUTPUT_HEAD_MAX);
    const [bare] = decodePriorToolResults([{ tool: "read_file", ok: true }]);
    expect(bare).toEqual({ tool: "read_file", args_summary: "", ok: true, output_head: "" });
    const [typed] = decodePriorToolResults([{ tool: "read_file", args_summary: 7, output_head: null }]);
    expect(typed).toEqual({ tool: "read_file", args_summary: "", ok: false, output_head: "" });
  });

  it("keeps the NEWEST rows past the row cap", () => {
    const many = Array.from({ length: PRIOR_TOOL_RESULTS_MAX + 3 }, (_, i) => ({
      tool: `t${i}`,
      args_summary: "",
      ok: true,
      output_head: "",
    }));
    const out = decodePriorToolResults(many);
    expect(out).toHaveLength(PRIOR_TOOL_RESULTS_MAX);
    expect(out[0].tool).toBe("t3");
    expect(out[out.length - 1].tool).toBe(`t${PRIOR_TOOL_RESULTS_MAX + 2}`);
  });

  it("keeps the NEWEST rows past the character cap, before the row cap is reached", () => {
    // Each row is 1,500 chars of output plus a short name: well under twelve
    // of them fill the character budget.
    const rows = Array.from({ length: 10 }, (_, i) => ({
      tool: `t${i}`,
      args_summary: "",
      ok: true,
      output_head: "x".repeat(PRIOR_OUTPUT_HEAD_MAX),
    }));
    const out = decodePriorToolResults(rows);
    const perRow = PRIOR_OUTPUT_HEAD_MAX + 2;
    const fit = Math.floor(PRIOR_TOOL_RESULTS_CHARS / perRow);
    expect(fit).toBeLessThan(10);
    expect(out).toHaveLength(fit);
    expect(out[out.length - 1].tool).toBe("t9");
    expect(out[0].tool).toBe(`t${10 - fit}`);
    const total = out.reduce((n, r) => n + r.tool.length + r.args_summary.length + r.output_head.length, 0);
    expect(total).toBeLessThanOrEqual(PRIOR_TOOL_RESULTS_CHARS);
  });
});
