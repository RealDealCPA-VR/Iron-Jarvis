/**
 * v1.384.0 REVIEW — long Build replies, on the SCREEN: what the doer's pins
 * did not reach.
 *
 *  R1. The question the user SENT here, then left mid-reply: on return it is
 *      on screen at once. The session view kept for a return was read BEFORE
 *      the send (nothing re-reads it mid-turn) and the question lived only in
 *      the page's `pending`, which a remount drops — so a return painted the
 *      live reply under the PREVIOUS message and the question popped in above
 *      it when the read landed: the jump the user reported.
 *  R2. Leaving and coming back many times leaves ONE open stream — every
 *      earlier one closed (counted on the fetches' own signals).
 *  R3. A WHOLE replay (a reload) counts "Working for" from the turn's real
 *      start (the daemon's `resume.started_at`), not from the rejoin.
 */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const api = vi.hoisted(() => {
  class ApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
    }
  }
  return {
    ApiError,
    gets: {} as Record<string, unknown | ((path: string) => unknown)>,
    calls: [] as { method: string; path: string; body?: unknown }[],
  };
});

vi.mock("@/lib/api", () => {
  const answer = (method: string, path: string, body?: unknown) => {
    api.calls.push({ method, path, body });
    const hit = method === "GET" ? api.gets[path] : undefined;
    const value = typeof hit === "function" ? (hit as (p: string) => unknown)(path) : hit;
    if (value instanceof Error) return Promise.reject(value);
    if (value === undefined && method === "GET") return Promise.reject(new api.ApiError(`unmocked ${path}`, 404));
    return Promise.resolve(value ?? {});
  };
  return {
    ApiError: api.ApiError,
    API_BASE: "http://d",
    ijToken: () => "",
    flattenDetail: (d: unknown) => String(d),
    get: (p: string) => answer("GET", p),
    post: (p: string, b?: unknown) => answer("POST", p, b),
    put: (p: string, b?: unknown) => answer("PUT", p, b),
    patch: (p: string, b?: unknown) => answer("PATCH", p, b),
    del: (p: string) => answer("DELETE", p),
  };
});
vi.mock("@/lib/daemon", () => ({
  useDaemon: () => ({
    online: true,
    checking: false,
    health: { default_provider: "mock", default_model: "mock-1", providers: [] },
    refresh: () => {},
  }),
}));
vi.mock("@/lib/useModels", () => ({ useModels: () => ({ models: [], usable: [], loading: false }) }));
vi.mock("@/components/terminal/FilesPanel", () => ({
  FilesPanel: ({ folder }: { folder: string | null }) => <div data-testid="files-panel">{folder}</div>,
}));

import { HarnessSession } from "@/components/harness/HarnessSession";
import type { SessionTerminalApi } from "@/components/harness/HarnessPanes";
import { turnFor } from "@/lib/turnCache";

const terminal: SessionTerminalApi = {
  find: () => null,
  open: async () => null,
  render: () => <div data-testid="term-body" />,
  close: () => {},
};

function view(over: Record<string, unknown> = {}) {
  return {
    id: "s1",
    title: "Long job",
    updated_at: "2026-10-24T10:00:00",
    running: false,
    waiting: false,
    archived: false,
    folder: "C:\\work\\shop",
    folder_name: "shop",
    is_project: false,
    project_id: null,
    mode: "act",
    plan: null,
    setup: {},
    messages: [],
    session_id: "build:s1",
    queued: [],
    turn_id: "",
    ...over,
  };
}

/** A controllable SSE body that honours its fetch's AbortSignal. */
function controlledStream() {
  let ctl!: ReadableStreamDefaultController<Uint8Array>;
  const body = new ReadableStream<Uint8Array>({
    start(c) {
      ctl = c;
    },
  });
  const enc = new TextEncoder();
  const response = new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } });
  return {
    response,
    push: (event: string, data: unknown, id?: string) =>
      ctl.enqueue(enc.encode(`${id ? `id: ${id}\n` : ""}event: ${event}\ndata: ${JSON.stringify(data)}\n\n`)),
    end: () => ctl.close(),
    fetch: async (_url: string, init?: RequestInit) => {
      init?.signal?.addEventListener("abort", () => {
        try {
          ctl.error(new DOMException("The operation was aborted.", "AbortError"));
        } catch {
          /* already closed */
        }
      });
      return response;
    },
  };
}

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  api.gets = { "/build/sessions/s1": view(), "/build/accounts": new api.ApiError("no such route", 404) };
  api.calls = [];
  localStorage.clear();
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function renderSession() {
  return render(
    <HarnessSession threadId="s1" terminal={terminal} listRunning={false} onChanged={() => {}} onGone={() => {}} />,
  );
}

const box = () => screen.getByTestId("harness-input") as HTMLTextAreaElement;

describe("R2 — leaving and coming back many times leaves one open stream", () => {
  it("five returns: one rejoin each, every earlier one closed, the words never doubled", async () => {
    api.gets["/build/sessions/s1"] = view({
      running: true,
      turn_id: "tr9",
      messages: [{ role: "user", content: "a long job", turnId: "tr9" }],
    });
    const opened: { url: string; signal: AbortSignal | undefined; stream: ReturnType<typeof controlledStream> }[] = [];
    fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
      const st = controlledStream();
      opened.push({ url: String(url), signal: init?.signal ?? undefined, stream: st });
      return st.fetch(url, init);
    });
    let words = "";
    let pos = 0;
    for (let round = 0; round < 5; round += 1) {
      const r = renderSession();
      await waitFor(() => expect(opened.length).toBe(round + 1));
      const cur = opened[round];
      // The first follow is the whole turn; every return asks after what it drew.
      expect(cur.url).toBe(
        round === 0 ? "http://d/chat/turns/tr9/stream?ids=1" : `http://d/chat/turns/tr9/stream?after=0%3A${pos}`,
      );
      const piece = `piece ${round}. `;
      await act(async () => {
        cur.stream.push("resume", { resumed: round > 0, running: true });
        cur.stream.push("caught_up", { id: round === 0 ? "" : `0:${pos}` });
        cur.stream.push("token", { text: piece }, `0:${pos + piece.length}`);
        await new Promise((res) => setTimeout(res, 30));
      });
      words += piece;
      pos += piece.length;
      expect(screen.getByTestId("harness-live-text").textContent).toBe(words.trimEnd());
      r.unmount();
      await act(async () => {
        await new Promise((res) => setTimeout(res, 20));
      });
      // Every stream this window opened is closed once it left.
      expect(opened.filter((o) => o.signal && !o.signal.aborted)).toHaveLength(0);
    }
    expect(opened).toHaveLength(5);
    expect(turnFor("s1")?.lastId).toBe(`0:${pos}`);
  });
});

describe("R1 — the question sent here is on screen at once on return", () => {
  it("a question typed and sent, then left mid-reply, is drawn on the FIRST render back", async () => {
    // An earlier exchange is in the session; the new question is sent HERE.
    const earlier = [
      { role: "user", content: "an earlier ask", turnId: "t0" },
      { role: "assistant", content: "An earlier answer.", turnId: "t0" },
    ];
    api.gets["/build/sessions/s1"] = view({ messages: earlier });
    const s = controlledStream();
    fetchMock.mockImplementation(s.fetch);
    const first = renderSession();
    await screen.findByText("An earlier answer.");
    fireEvent.change(box(), { target: { value: "fix every file please" } });
    fireEvent.keyDown(box(), { key: "Enter" });
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const sent = (fetchMock.mock.calls[0] as unknown[])[0];
    expect(String(sent)).toBe("http://d/build/sessions/s1/turn");
    const turnId = JSON.parse(String(((fetchMock.mock.calls[0] as unknown[])[1] as RequestInit).body)).turn_id;
    await act(async () => {
      s.push("resume", { resumed: false, running: true });
      s.push("caught_up", { id: "" });
      s.push("token", { text: "Working on it" }, "0:13");
      await new Promise((r) => setTimeout(r, 40));
    });
    expect(screen.getAllByTestId("harness-user-bubble").map((b) => b.textContent)).toContain("fix every file please");
    // The user goes elsewhere in the app.
    first.unmount();
    await act(async () => {
      await new Promise((r) => setTimeout(r, 20));
    });
    expect(turnFor("s1")?.turnId).toBe(turnId);
    // Back: the read is HELD, so the first render is drawn from what was kept.
    let release!: () => void;
    const gate = new Promise<void>((r) => {
      release = r;
    });
    api.gets["/build/sessions/s1"] = async () => {
      await gate;
      return view({
        running: true,
        turn_id: turnId,
        messages: [...earlier, { role: "user", content: "fix every file please", turnId }],
      });
    };
    const back = controlledStream();
    fetchMock.mockReset();
    fetchMock.mockImplementation(back.fetch);
    renderSession();
    expect(screen.getByTestId("harness-live-text")).toHaveTextContent("Working on it");
    const bubbles = () => screen.getAllByTestId("harness-user-bubble").map((b) => b.textContent);
    expect(bubbles()).toEqual(["an earlier ask", "fix every file please"]);
    // The read lands: the daemon's copy of the question stands in, never twice.
    await act(async () => {
      release();
      await new Promise((r) => setTimeout(r, 30));
    });
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(bubbles()).toEqual(["an earlier ask", "fix every file please"]);
    await act(async () => {
      back.push("resume", { resumed: true, running: true });
      back.push("caught_up", { id: "0:13" });
      back.push("done", { reply: "Working on it, done." }, "1:0");
      back.end();
      await new Promise((r) => setTimeout(r, 30));
    });
  });
});

describe("R3 — a reload's whole replay counts from the turn's real start", () => {
  it("a rejoin takes the daemon's start; a POST keeps its own clock", async () => {
    const { renderHook } = await import("@testing-library/react");
    const { useChatStream } = await import("@/lib/useChatStream");
    const started = Date.now() - 2 * 3600 * 1000; // two hours in
    const s = controlledStream();
    fetchMock.mockImplementation(s.fetch);
    const { result } = renderHook(() => useChatStream({ textInState: false }));
    let done!: Promise<unknown>;
    act(() => {
      done = result.current.run(undefined, undefined, undefined, { rejoin: "tr1", ids: true });
    });
    await act(async () => {
      s.push("resume", { resumed: false, running: true, started_at: started });
      s.push("token", { text: "Long work" }, "0:9");
      s.push("caught_up", { id: "0:9" });
      await new Promise((r) => setTimeout(r, 30));
    });
    expect(result.current.startedAt).toBe(started);
    expect(result.current.seedOf?.()?.startedAt).toBe(started);
    await act(async () => {
      s.push("done", { reply: "Long work" }, "1:0");
      s.end();
      await done;
    });
    // A POST: the daemon's clock never moves the page's (it may be another
    // machine's — a phone over the network).
    const p = controlledStream();
    fetchMock.mockImplementation(p.fetch);
    const before = Date.now();
    act(() => {
      done = result.current.run({ text: "go", ids: true }, undefined, undefined, { path: "/build/sessions/s1/turn" });
    });
    await act(async () => {
      p.push("resume", { resumed: false, running: true, started_at: before - 50_000 });
      p.push("caught_up", { id: "" });
      await new Promise((r) => setTimeout(r, 30));
    });
    expect(result.current.startedAt).toBeGreaterThanOrEqual(before);
    await act(async () => {
      p.push("done", { reply: "ok" }, "0:0");
      p.end();
      await done;
    });
  });

  it("a start from the future (another machine's clock) is never ahead of now", async () => {
    const { renderHook } = await import("@testing-library/react");
    const { useChatStream } = await import("@/lib/useChatStream");
    const s = controlledStream();
    fetchMock.mockImplementation(s.fetch);
    const { result } = renderHook(() => useChatStream({ textInState: false }));
    let done!: Promise<unknown>;
    act(() => {
      done = result.current.run(undefined, undefined, undefined, { rejoin: "tr1", ids: true });
    });
    const now = Date.now();
    await act(async () => {
      s.push("resume", { resumed: false, running: true, started_at: now + 3_600_000 });
      s.push("caught_up", { id: "" });
      await new Promise((r) => setTimeout(r, 30));
    });
    expect(result.current.startedAt).toBeLessThanOrEqual(Date.now());
    await act(async () => {
      s.end();
      await done;
    });
  });
});
