/**
 * v1.390.0 (the long-jobs plan's Phase 1): the Timeline tab of a RUNNING
 * coding reply, driven through the REAL session screen (HarnessSession +
 * HarnessPanes) and the REAL chat stream (lib/useChatStream) against a fetch
 * double that sends the frames of a REAL recorded reply
 * (fixtures/build-timeline-v1390.json).
 *
 *  - "Timeline" sits beside Plan, Terminal, Changes, Browser and Files;
 *  - CALM AND CHEAP: the running reply is folded from its frames with no
 *    React state; the tab reads it at most about once a second, and NEVER
 *    while another tab is shown — a burst of hundreds of word frames costs it
 *    nothing;
 *  - live, the tab says "Live" and draws the steps as they come; when the
 *    reply ends, the saved line draws the same picture.
 */
import React from "react";
import { readFileSync } from "node:fs";
import path from "node:path";
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

// How many times the Timeline READ the running reply (every read is one
// `snapshot` of the store the session folds frames into).
const reads = vi.hoisted(() => ({ n: 0 }));

vi.mock("@/lib/buildTimeline", async (importOriginal) => {
  const mod = await importOriginal<typeof import("@/lib/buildTimeline")>();
  return {
    ...mod,
    createLiveTimeline: (initial?: unknown) => {
      const t = mod.createLiveTimeline(initial);
      return {
        ...t,
        snapshot: (now: number) => {
          reads.n += 1;
          return t.snapshot(now);
        },
      };
    },
  };
});

vi.mock("@/lib/api", () => {
  const answer = (method: string, p: string, body?: unknown) => {
    api.calls.push({ method, path: p, body });
    const hit = method === "GET" ? api.gets[p] : undefined;
    const value = typeof hit === "function" ? (hit as (x: string) => unknown)(p) : hit;
    if (value instanceof Error) return Promise.reject(value);
    if (value === undefined && method === "GET") return Promise.reject(new api.ApiError(`unmocked ${p}`, 404));
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
    health: { default_provider: "anthropic", default_model: "claude-sonnet-4-5", providers: [] },
    refresh: () => {},
  }),
}));
vi.mock("@/lib/useModels", () => ({ useModels: () => ({ models: [], usable: [], loading: false }) }));
vi.mock("@/components/terminal/FilesPanel", () => ({
  FilesPanel: ({ folder }: { folder: string | null }) => <div data-testid="files-panel">{folder}</div>,
}));

import { HarnessSession } from "@/components/harness/HarnessSession";
import { PANE_TABS, type SessionTerminalApi } from "@/components/harness/HarnessPanes";
import { createLiveTimeline } from "@/lib/buildTimeline";
import { turnFor } from "@/lib/turnCache";

const FIXTURE = JSON.parse(
  readFileSync(path.join(__dirname, "fixtures", "build-timeline-v1390.json"), "utf8").replace(/\r\n/g, "\n"),
) as { question: Record<string, unknown>; line: Record<string, unknown>; frames: [string, Record<string, unknown>][] };

const terminal: SessionTerminalApi = {
  find: () => null,
  open: async () => null,
  render: () => <div data-testid="term-body" />,
  close: () => {},
};

function view(over: Record<string, unknown> = {}) {
  return {
    id: "s1",
    title: "Timeline",
    updated_at: "2026-10-25T10:00:00",
    running: false,
    waiting: false,
    archived: false,
    folder: "C:\\work\\shop",
    folder_name: "shop",
    is_project: false,
    project_id: null,
    mode: "act",
    plan: null,
    setup: { provider: "anthropic", model: "claude-sonnet-4-5" },
    messages: [],
    session_id: "build:s1",
    queued: [],
    turn_id: "",
    ...over,
  };
}

function controlledStream() {
  let ctl!: ReadableStreamDefaultController<Uint8Array>;
  const body = new ReadableStream<Uint8Array>({
    start(c) {
      ctl = c;
    },
  });
  const enc = new TextEncoder();
  return {
    response: new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } }),
    push: (event: string, data: unknown, id?: string) =>
      ctl.enqueue(enc.encode(`${id ? `id: ${id}\n` : ""}event: ${event}\ndata: ${JSON.stringify(data)}\n\n`)),
    end: () => ctl.close(),
  };
}

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  api.gets = { "/build/sessions/s1": view(), "/build/accounts": new api.ApiError("no such route", 404) };
  api.calls = [];
  reads.n = 0;
  localStorage.clear();
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const box = () => screen.getByTestId("harness-input") as HTMLTextAreaElement;
async function send(text: string) {
  fireEvent.change(box(), { target: { value: text } });
  fireEvent.keyDown(box(), { key: "Enter" });
}

/** v1.390.0 review: the running reply is DRAWN once a second (LIVE_READ_MS),
 *  so a wait for rows the frames brought must outlast that tick — waitFor's
 *  default 1 s bound is the tick itself, and a loaded runner lost the race
 *  ("expected 1 to be greater than or equal to 5"; a 1.2 s tick reproduces it
 *  on an idle machine). Far under vitest's 15 s budget, so a miss still
 *  reports what it waited for. */
const TICK_WAIT = { timeout: 5_000 };

/** The recorded frames, up to (not including) the first `done`. */
const BODY = FIXTURE.frames.filter(([e]) => e !== "done");

describe("the Timeline tab sits beside Plan", () => {
  it("is the second tab, and opens the reply on one time axis", async () => {
    // v1.404.0: Session joined after Files (session-rail-v1404).
    expect(PANE_TABS.map((t) => t.label)).toEqual(["Plan", "Timeline", "Terminal", "Changes", "Browser", "Files", "Session"]);
    api.gets["/build/sessions/s1"] = view({
      messages: [FIXTURE.question, FIXTURE.line],
    });
    render(<HarnessSession threadId="s1" terminal={terminal} listRunning={false} onChanged={() => {}} onGone={() => {}} />);
    await screen.findByText(/Read both, asked a helper/);
    fireEvent.click(screen.getByTestId("harness-pane-tab-timeline"));
    expect(await screen.findByTestId("pane-timeline")).toBeInTheDocument();
    expect(screen.getByTestId("timeline-totals")).toHaveTextContent("model calls");
    // A saved reply is not live.
    expect(screen.queryByTestId("timeline-live")).toBeNull();
  });
});

describe("the running reply: calm and cheap", () => {
  it("reads the running reply at most about once a second, never per word", async () => {
    const s = controlledStream();
    fetchMock.mockImplementation(async () => s.response);
    render(<HarnessSession threadId="s1" terminal={terminal} listRunning={false} onChanged={() => {}} onGone={() => {}} />);
    await screen.findByTestId("harness-empty");
    fireEvent.click(screen.getByTestId("harness-pane-tab-timeline"));
    await send("read both files, ask a helper, make made.py");
    await act(async () => {
      for (const [e, d] of BODY) s.push(e, d);
    });
    // The steps the frames carried are drawn, live.
    await waitFor(() => expect(screen.getByTestId("timeline-live")).toHaveTextContent("Live"), TICK_WAIT);
    await waitFor(() => expect(screen.getAllByTestId("timeline-row").length).toBeGreaterThanOrEqual(5), TICK_WAIT);
    // A burst of 400 word frames: the tab reads the store no more than the
    // once-a-second tick allows (it never reads per word).
    const before = reads.n;
    const t0 = Date.now();
    await act(async () => {
      for (let i = 0; i < 400; i += 1) s.push("token", { text: "word " });
    });
    // (v1.390.0 review: inside ONE `act` React draws once at its end, so
    // this window cannot tell a 40 ms read from a 1 s one — the read's own
    // pace is pinned with fake timers in harness-timeline-review-v1390, and
    // a word never re-running the tab, one word per `act`, there too.)
    await act(async () => {
      await new Promise((r) => setTimeout(r, 50));
    });
    const elapsed = Date.now() - t0;
    expect(reads.n - before).toBeLessThanOrEqual(Math.ceil(elapsed / 1000) + 1);
    s.end();
  });

  it("never reads the running reply while another tab is shown", async () => {
    const s = controlledStream();
    fetchMock.mockImplementation(async () => s.response);
    render(<HarnessSession threadId="s1" terminal={terminal} listRunning={false} onChanged={() => {}} onGone={() => {}} />);
    await screen.findByTestId("harness-empty");
    fireEvent.click(screen.getByTestId("harness-pane-tab-plan"));
    await send("go");
    await act(async () => {
      for (const [e, d] of BODY) s.push(e, d);
      for (let i = 0; i < 200; i += 1) s.push("token", { text: "w " });
    });
    await act(async () => {
      await new Promise((r) => setTimeout(r, 1300));
    });
    expect(reads.n).toBe(0);
    expect(screen.queryByTestId("pane-timeline")).toBeNull();
    // Opened, it draws everything folded meanwhile.
    fireEvent.click(screen.getByTestId("harness-pane-tab-timeline"));
    await waitFor(() => expect(screen.getAllByTestId("timeline-row").length).toBeGreaterThanOrEqual(5), TICK_WAIT);
    expect(reads.n).toBeGreaterThan(0);
    s.end();
  });

  it("a return to the session carries the Timeline on from where it was (lib/turnCache)", async () => {
    const s = controlledStream();
    fetchMock.mockImplementation(async () => s.response);
    const { unmount } = render(
      <HarnessSession threadId="s1" terminal={terminal} listRunning={false} onChanged={() => {}} onGone={() => {}} />,
    );
    await screen.findByTestId("harness-empty");
    await send("go");
    const half = Math.floor(BODY.length / 2);
    await act(async () => {
      // Tagged with their resume positions, as the daemon sends a Build page.
      BODY.slice(0, half).forEach(([e, d], i) => s.push(e, d, `${i}:0`));
    });
    // The kept reply holds the Timeline as folded so far …
    await waitFor(() => {
      const kept = turnFor("s1")?.page.timeline as { calls?: unknown[]; steps?: unknown[] } | undefined;
      expect(kept?.calls?.length ?? 0).toBeGreaterThan(0);
    });
    unmount();
    // … and a store made from it carries on with the rest of the frames to
    // the same picture as one that saw them all.
    const back = createLiveTimeline(turnFor("s1")?.page.timeline);
    for (const [e, d] of BODY.slice(half)) back.fold(e, d);
    const whole = createLiveTimeline();
    whole.reset(back.turn());
    for (const [e, d] of BODY) whole.fold(e, d);
    expect(back.turn()).not.toBe("");
    expect(back.snapshot(5_000)).toEqual(whole.snapshot(5_000));
    s.end();
  });

  it("when the reply ends, the saved line draws the same rows", async () => {
    const s = controlledStream();
    fetchMock.mockImplementation(async () => s.response);
    render(<HarnessSession threadId="s1" terminal={terminal} listRunning={false} onChanged={() => {}} onGone={() => {}} />);
    await screen.findByTestId("harness-empty");
    fireEvent.click(screen.getByTestId("harness-pane-tab-timeline"));
    await send("read both files, ask a helper, make made.py");
    await act(async () => {
      for (const [e, d] of BODY) s.push(e, d);
    });
    await waitFor(() => expect(screen.getAllByTestId("timeline-row").length).toBeGreaterThanOrEqual(5), TICK_WAIT);
    const label = (el: Element) => el.querySelector("span[title]")?.getAttribute("title");
    // The session now holds the saved line (the daemon writes it before the
    // last frame) under the turn id the screen minted; the screen reads it
    // again when the turn ends.
    const sent = JSON.parse(String((fetchMock.mock.calls[0][1] as RequestInit).body)) as { turn_id: string };
    api.gets["/build/sessions/s1"] = view({
      messages: [{ ...FIXTURE.question, turnId: sent.turn_id }, { ...FIXTURE.line, turnId: sent.turn_id }],
    });
    await act(async () => {
      s.push("done", FIXTURE.frames[FIXTURE.frames.length - 1][1]);
      s.end();
    });
    await waitFor(() => expect(screen.queryByTestId("timeline-live")).toBeNull());
    await waitFor(() =>
      expect((screen.getByTestId("timeline-reply") as HTMLSelectElement).selectedOptions[0]?.text).toContain("read both"),
    );
    const saved = screen.getAllByTestId("timeline-row").map(label);
    expect(saved).toEqual(["Sonnet 4.5", "Read app.py", "Read util.py", "The builder · Sonnet 4.5", "Read util.py", "Made made.py"]);
  });
});
