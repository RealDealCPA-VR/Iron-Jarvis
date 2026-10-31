/**
 * v1.417.0 (fix plan wave 1: C2.1) — a coding session is NOT redrawn whole
 * for every streamed word, every keystroke or every unchanged list poll.
 *
 * The REAL IjHarnessView + HarnessSession + chat stream (lib/useChatStream)
 * against a fetch double; three counters, each a pass-through stub that
 * renders the real component (the code-split-live-v1406 way):
 *  - `session`: the box's permission chip — drawn on EVERY render of the
 *    session (so it counts the session's renders);
 *  - `row`: a user message's attachment strip — drawn on every render of a
 *    transcript ROW (rows are memoised: HarnessMessageRow);
 *  - `view`: the session list — drawn on every render of the view.
 *
 *  1. 60 streamed tokens redraw neither the session nor a row (the clock is
 *     out of the session's state: `clockInState: false`);
 *  2. typing in the box redraws the session but never a settled row;
 *  3. three 4 s list polls that answer the same list redraw nothing.
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
  return { ApiError, gets: {} as Record<string, unknown>, calls: [] as { method: string; path: string }[] };
});
const counts = vi.hoisted(() => ({ session: 0, row: 0, view: 0, panes: 0 }));

vi.mock("@/lib/api", () => {
  const answer = (method: string, path: string) => {
    api.calls.push({ method, path });
    if (method !== "GET") return Promise.resolve({});
    const value = api.gets[path];
    if (value instanceof Error) return Promise.reject(value);
    if (value === undefined) return Promise.reject(new api.ApiError(`unmocked ${path}`, 404));
    // A NEW copy of the answer each time, as a real read would be.
    return Promise.resolve(JSON.parse(JSON.stringify(value)));
  };
  return {
    ApiError: api.ApiError,
    API_BASE: "http://d",
    ijToken: () => "",
    flattenDetail: (d: unknown) => String(d),
    get: (p: string) => answer("GET", p),
    post: (p: string) => answer("POST", p),
    put: (p: string) => answer("PUT", p),
    patch: (p: string) => answer("PATCH", p),
    del: (p: string) => answer("DELETE", p),
  };
});
// The real hooks hand back the SAME objects while nothing changed (v1.390.0:
// useModels' stable lists; the daemon provider keeps /health when unchanged).
const stable = vi.hoisted(() => ({
  daemon: {
    online: true,
    checking: false,
    health: { default_provider: "mock", default_model: "mock-1", providers: [] },
    refresh: () => {},
  },
  models: { models: [], usable: [], loading: false },
}));
vi.mock("@/lib/daemon", () => ({ useDaemon: () => stable.daemon }));
vi.mock("@/lib/useModels", () => ({ useModels: () => stable.models }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/chat",
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock("@/components/terminal/FilesPanel", () => ({ FilesPanel: () => <div data-testid="files-panel" /> }));
vi.mock("@/components/terminal/DirectoryTree", () => ({ DirectoryTree: () => <div /> }));
vi.mock("@/components/chat/PermissionChip", async (orig) => {
  const real = await orig<typeof import("@/components/chat/PermissionChip")>();
  const R = await vi.importActual<typeof import("react")>("react");
  return {
    ...real,
    PermissionChip: (p: React.ComponentProps<typeof real.PermissionChip>) => {
      counts.session += 1;
      return R.createElement(real.PermissionChip, p);
    },
  };
});
vi.mock("@/components/harness/HarnessAttachments", async (orig) => {
  const real = await orig<typeof import("@/components/harness/HarnessAttachments")>();
  const R = await vi.importActual<typeof import("react")>("react");
  return {
    ...real,
    SentAttachments: (p: React.ComponentProps<typeof real.SentAttachments>) => {
      counts.row += 1;
      return R.createElement(real.SentAttachments, p);
    },
  };
});
// Review: the panes are memoised too — the Timeline tab's component is drawn
// on EVERY render of HarnessPanes (a pass-through stub, so it counts the
// panes' renders, not the Timeline's own once-a-second clock).
vi.mock("@/components/harness/HarnessTimeline", async (orig) => {
  const real = await orig<typeof import("@/components/harness/HarnessTimeline")>();
  const R = await vi.importActual<typeof import("react")>("react");
  return {
    ...real,
    HarnessTimeline: (p: React.ComponentProps<typeof real.HarnessTimeline>) => {
      counts.panes += 1;
      return R.createElement(real.HarnessTimeline, p);
    },
  };
});
vi.mock("@/components/harness/HarnessSidebar", async (orig) => {
  const real = await orig<typeof import("@/components/harness/HarnessSidebar")>();
  const R = await vi.importActual<typeof import("react")>("react");
  return {
    ...real,
    HarnessSidebar: (p: React.ComponentProps<typeof real.HarnessSidebar>) => {
      counts.view += 1;
      return R.createElement(real.HarnessSidebar, p);
    },
  };
});

import { IjHarnessView, SESSION_LIST_POLL_MS } from "@/components/harness/IjHarnessView";
import { __resetLooking } from "@/lib/buildWatching";
import { __resetSplitMemory } from "@/lib/codeSplit";

function view(running: boolean) {
  return {
    id: "s1",
    title: "Cart",
    updated_at: "2026-10-31T10:00:00",
    running,
    waiting: false,
    archived: false,
    folder: "C:\\work\\shop",
    folder_name: "shop",
    is_project: false,
    project_id: null,
    mode: "act",
    plan: null,
    setup: {},
    messages: running
      ? [{ role: "user", content: "the cart job", turnId: "tr1", at: "2026-10-31T10:00:00" }]
      : [
          { role: "user", content: "the cart job", turnId: "t0", at: "2026-10-31T10:00:00" },
          { role: "assistant", content: "Added the cart.", turnId: "t0", timing: { startedAt: 1, firstTokenAt: 2, endedAt: 3 } },
        ],
    session_id: "build:s1",
    queued: [],
    turn_id: running ? "tr1" : "",
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
    push: (event: string, data: unknown) => ctl.enqueue(enc.encode(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`)),
    end: () => {
      try {
        ctl.close();
      } catch {
        /* closed */
      }
    },
    response: (init?: RequestInit) => {
      init?.signal?.addEventListener("abort", () => {
        try {
          ctl.error(new DOMException("The operation was aborted.", "AbortError"));
        } catch {
          /* closed */
        }
      });
      return new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } });
    },
  };
}

let stream: ReturnType<typeof controlledStream>;
const terminal = { find: () => null, open: async () => null, render: () => <div />, close: () => {} };

function setUp(running: boolean) {
  api.gets = {
    "/build/sessions": {
      folders: [{ path: "C:\\work\\shop", name: "shop", sessions: [{ id: "s1", title: "Cart", running }] }],
    },
    "/build/sessions/s1": view(running),
    "/build/accounts": new api.ApiError("no such route", 404),
  };
}

beforeEach(() => {
  counts.session = 0;
  counts.row = 0;
  counts.view = 0;
  counts.panes = 0;
  __resetLooking();
  __resetSplitMemory();
  localStorage.clear();
  window.sessionStorage.clear();
  window.history.replaceState(null, "", "/chat?view=code&session=s1");
  api.calls = [];
  stream = controlledStream();
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      if (/\/chat\/turns\/tr1\/stream/.test(String(url))) return stream.response(init);
      return new Response("{}", { status: 200 });
    }),
  );
});

afterEach(() => {
  stream.end();
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const openView = () =>
  render(<IjHarnessView initialSession="s1" terminals={[]} onOpenTerminal={() => {}} defaultFolder={null} terminal={terminal} />);
const reset = () => {
  counts.session = 0;
  counts.row = 0;
  counts.view = 0;
  counts.panes = 0;
};

describe("C2.1 a coding session is not redrawn per word, per keystroke or per unchanged poll", () => {
  it("60 streamed tokens redraw neither the session nor a transcript row", async () => {
    setUp(true);
    openView();
    await waitFor(
      () => expect(fetch).toHaveBeenCalledWith(expect.stringContaining("/chat/turns/tr1/stream"), expect.anything()),
      { timeout: 10_000 },
    );
    await act(async () => {
      stream.push("resume", { resumed: false, running: true });
      stream.push("caught_up", { id: "" });
      stream.push("token", { text: "w0 " });
      await new Promise((r) => setTimeout(r, 40));
    });
    await waitFor(() => expect(screen.getByTestId("harness-live-text")).toHaveTextContent("w0"));
    expect(counts.row).toBeGreaterThan(0); // the row IS counted
    reset();
    for (let i = 1; i <= 60; i += 1) {
      await act(async () => {
        stream.push("token", { text: `w${i} ` });
        await new Promise((r) => setTimeout(r, 4));
      });
    }
    await waitFor(() => expect(screen.getByTestId("harness-live-text")).toHaveTextContent("w60"));
    expect(counts.session).toBeLessThanOrEqual(1);
    expect(counts.row).toBeLessThanOrEqual(1);
    expect(counts.view).toBe(0);
  });

  it("typing in the box redraws the session but never a settled row", async () => {
    setUp(false);
    openView();
    expect(await screen.findByText("Added the cart.", {}, { timeout: 10_000 })).toBeInTheDocument();
    await act(async () => {
      await new Promise((r) => setTimeout(r, 30));
    });
    reset();
    const box = screen.getByTestId("harness-input");
    for (const v of ["a", "ad", "add", "add a", "add a f"]) fireEvent.change(box, { target: { value: v } });
    expect(counts.session).toBeGreaterThanOrEqual(5); // the session does redraw
    expect(counts.row).toBe(0);
  });

  it("neither streamed tokens nor typing redraw the panes", async () => {
    setUp(true);
    // The Timeline tab is the one shown (HarnessPanes reads it at mount).
    localStorage.setItem("ij.build.pane", "timeline");
    openView();
    await waitFor(
      () => expect(fetch).toHaveBeenCalledWith(expect.stringContaining("/chat/turns/tr1/stream"), expect.anything()),
      { timeout: 10_000 },
    );
    await act(async () => {
      stream.push("resume", { resumed: false, running: true });
      stream.push("caught_up", { id: "" });
      stream.push("token", { text: "w0 " });
      await new Promise((r) => setTimeout(r, 40));
    });
    await waitFor(() => expect(screen.getByTestId("harness-live-text")).toHaveTextContent("w0"));
    expect(counts.panes).toBeGreaterThan(0); // the panes ARE counted
    reset();
    for (let i = 1; i <= 30; i += 1) {
      await act(async () => {
        stream.push("token", { text: `w${i} ` });
        await new Promise((r) => setTimeout(r, 4));
      });
    }
    await waitFor(() => expect(screen.getByTestId("harness-live-text")).toHaveTextContent("w30"));
    const box = screen.getByTestId("harness-input");
    for (const v of ["n", "no", "not", "note"]) fireEvent.change(box, { target: { value: v } });
    expect(counts.session).toBeGreaterThanOrEqual(4); // the session does redraw (the box)
    expect(counts.panes).toBe(0);
  });

  it("three list polls that answer the same list redraw neither the view nor the session", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    setUp(false);
    openView();
    expect(await screen.findByText("Added the cart.", {}, { timeout: 10_000 })).toBeInTheDocument();
    await act(async () => {
      await new Promise((r) => setTimeout(r, 30));
    });
    const reads = () => api.calls.filter((c) => c.method === "GET" && c.path === "/build/sessions").length;
    const before = reads();
    reset();
    for (let i = 0; i < 3; i += 1) {
      await act(async () => {
        vi.advanceTimersByTime(SESSION_LIST_POLL_MS);
        await new Promise((r) => setTimeout(r, 20));
      });
    }
    expect(reads()).toBe(before + 3);
    expect(counts.view).toBe(0);
    expect(counts.session).toBe(0);
  });
});
