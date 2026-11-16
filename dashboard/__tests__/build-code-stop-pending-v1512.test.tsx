/**
 * v1.512.0 (second review H1): a coding tool that does not stop when asked.
 *
 * The daemon's Stop answers `stop_pending` (with its own words) when the tool
 * was told to stop and its reply still runs. Build › Code then:
 *
 *   - shows those words EXACTLY as sent, in one quiet line by the composer;
 *   - offers the existing "Close <tool>" (two presses, the same `/close`
 *     route the kept-open line uses) beside them, and shows its refusal;
 *   - drops the line once the reply ends, the session changes, or a Stop
 *     answers without `stop_pending` — it never outlives its reply;
 *   - an ordinary Stop (no `stop_pending`) shows nothing new.
 *
 * Only the network (lib/api), the stream reader and the folder tree are
 * stand-ins; the view, its conversation and lib/buildApp are real.
 */

import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const H = vi.hoisted(() => ({
  running: true,
  posts: [] as string[],
  /** What each POST answers (a function: called with nothing). */
  replies: {} as Record<string, unknown>,
  fails: {} as Record<string, { status: number; message: string }>,
  /** The newest POST's promise (a test awaits its answer). */
  lastPost: Promise.resolve() as Promise<unknown>,
  streams: [] as string[],
  hold: null as Promise<void> | null,
  release: null as (() => void) | null,
}));

vi.mock("@/lib/api", () => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  return {
    ApiError: FakeApiError,
    get: (path: string) => {
      if (path === "/build/app/harnesses") return Promise.resolve(TOOLS);
      if (path === "/build/app/sessions") return Promise.resolve({ sessions: [sess("a1"), sess("a2")] });
      if (path === "/editor/options") return Promise.resolve({ editors: [], preferred: "" });
      if (path === "/build/app/sessions/a1") return Promise.resolve(view("a1"));
      if (path === "/build/app/sessions/a2") return Promise.resolve(view("a2"));
      return Promise.reject(new FakeApiError(`unmocked GET ${path}`, 404));
    },
    post: (path: string) => {
      H.posts.push(path);
      const fail = H.fails[path];
      const r = H.replies[path];
      const p = fail
        ? Promise.reject(new FakeApiError(fail.message, fail.status))
        : Promise.resolve(typeof r === "function" ? (r as () => unknown)() : (r ?? { ok: true }));
      H.lastPost = p.catch(() => undefined);
      return p;
    },
    patch: () => Promise.resolve({}),
    del: () => Promise.resolve({}),
  };
});

vi.mock("@/lib/useChatStream", () => ({
  streamSSE: async function* (
    path: string,
    _body: unknown,
    signal?: AbortSignal,
    watch?: { onFrame?: (event: string, data: Record<string, unknown>) => void },
  ) {
    H.streams.push(path);
    for (const f of TURN) {
      if (signal?.aborted) return;
      watch?.onFrame?.(String(f.type), f);
      await Promise.resolve();
    }
    if (H.hold) await H.hold;
  },
  useLiveText: (s: { text?: string }) => s?.text ?? "",
}));

vi.mock("@/components/terminal/DirectoryTree", () => ({ DirectoryTree: () => null }));

import { BuildCodeView } from "@/components/buildCode/BuildCodeView";
import { closeToolWords } from "@/lib/buildApp";

const TOOLS = {
  harnesses: [
    {
      id: "claude-code",
      label: "Claude Code",
      installed: true,
      signed_in: true,
      version: "2.1.316",
      modes: [{ id: "ask", label: "Ask first", dangerous: false }],
      hidden_modes: [],
      models: [{ id: "", label: "Claude Code's default" }],
      efforts: [],
      accounts: [],
      commands: [],
    },
  ],
};

/** The daemon's own words (hosted/words.STOP_ASKED for Claude Code). */
const ASKED = "Claude Code has been asked to stop and has not stopped yet. Press Stop again to close it now.";
const PENDING = { ok: true, running: true, stop_pending: true, words: ASKED };
const STOP = "/build/app/sessions/a1/interrupt";
const CLOSE = "/build/app/sessions/a1/close";

function sess(id: string) {
  return {
    id,
    harness: "claude-code",
    folder: "C:\\work\\site",
    title: `Session ${id}`,
    account: "",
    model: "",
    effort: "",
    mode: "ask",
    harness_session_id: "h1",
    created_at: "2026-11-01T09:00:00Z",
    updated_at: "2026-11-01T09:00:00Z",
    archived_at: null,
    last_error: "",
    process: true,
  };
}

const TURN: Record<string, unknown>[] = [
  { type: "user", text: "build the website", turn: "t1" },
  { type: "text_done", item: "m1:0", text: "Working on the pages.", turn: "t1" },
];

function view(id: string, running = H.running) {
  return {
    session: { ...sess(id), running },
    events: running ? [] : [...TURN, { type: "done", stop: "interrupted", turn: "t1" }],
    running: running ? { turn_id: "t1" } : null,
    pending: [],
  };
}

beforeEach(() => {
  H.running = true;
  H.posts = [];
  H.replies = {};
  H.fails = {};
  H.lastPost = Promise.resolve();
  H.streams = [];
  holdStreams();
});

/** Every stream opened from now on waits here before it ends. */
function holdStreams() {
  H.hold = new Promise<void>((res) => {
    H.release = res;
  });
}

afterEach(() => {
  H.release?.();
  cleanup();
});

/** The reply ends: the record says so, then the stream closes. */
async function endReply() {
  H.running = false;
  await act(async () => {
    H.release?.();
  });
}

async function pressStop() {
  await act(async () => {
    fireEvent.click(screen.getByTestId("code-stop"));
  });
  await act(async () => {
    await H.lastPost;
  });
}

async function openRunning(initial = "a1") {
  const r = render(<BuildCodeView initialSession={initial} onOpenedTerminal={() => {}} />);
  await screen.findByTestId("code-stop");
  // The reply's text is drawn (nothing more arrives until the test says).
  await waitFor(() => expect(screen.getByTestId("code-conversation").textContent).toContain("Working on the pages."));
  return r;
}

describe("a Stop the tool has not acted on", () => {
  it("shows the daemon's words exactly, offers Close beside them, and Close calls the close route", async () => {
    H.replies[STOP] = PENDING;
    await openRunning();
    fireEvent.click(screen.getByTestId("code-stop"));
    const line = await screen.findByTestId("code-stop-pending");
    expect(line.querySelector("p")?.textContent).toBe(ASKED);
    expect(H.posts).toEqual([STOP]);
    // The Stop button stays (a second press is the documented way).
    expect(screen.getByTestId("code-stop")).toBeInTheDocument();
    const press = () => screen.getByTestId("code-stop-pending-close").querySelector("button")!;
    expect(press().textContent).toBe(closeToolWords("Claude Code").idle);
    fireEvent.click(press());
    expect(H.posts.filter((p) => p === CLOSE)).toEqual([]);
    expect(press().textContent).toBe(closeToolWords("Claude Code").armed);
    H.replies[CLOSE] = { ok: true, closed: true, stopped: [] };
    await act(async () => {
      fireEvent.click(press());
    });
    await waitFor(() => expect(H.posts).toContain(CLOSE));
    // Closed: the line goes (the reply then ends through its stream).
    await waitFor(() => expect(screen.queryByTestId("code-stop-pending")).toBeNull());
  });

  it("a refused Close says the route's words, as today", async () => {
    H.replies[STOP] = PENDING;
    H.fails[CLOSE] = { status: 409, message: "Claude Code is answering a message." };
    await openRunning();
    fireEvent.click(screen.getByTestId("code-stop"));
    await screen.findByTestId("code-stop-pending");
    const press = () => screen.getByTestId("code-stop-pending-close").querySelector("button")!;
    fireEvent.click(press());
    await act(async () => {
      fireEvent.click(press());
    });
    await waitFor(() => expect(screen.getByTestId("code-note").textContent).toBe("Claude Code is answering a message."));
  });

  it("the line is gone once the reply ends, and the next reply starts without it", async () => {
    H.replies[STOP] = PENDING;
    await openRunning();
    fireEvent.click(screen.getByTestId("code-stop"));
    await screen.findByTestId("code-stop-pending");
    await endReply();
    await waitFor(() => expect(screen.queryByTestId("code-stop-pending")).toBeNull());
    // A new message: a new reply runs (its Stop shows) — the old line stays gone.
    await waitFor(() => expect(screen.queryByTestId("code-stop")).toBeNull());
    holdStreams();
    const box = screen.getByTestId("code-box");
    fireEvent.change(box, { target: { value: "add a contact page" } });
    fireEvent.keyDown(box, { key: "Enter" });
    await screen.findByTestId("code-stop");
    expect(screen.queryByTestId("code-stop-pending")).toBeNull();
    expect(screen.queryByText(ASKED)).toBeNull();
  });

  it("a later Stop that answers without stop_pending clears it", async () => {
    H.replies[STOP] = PENDING;
    await openRunning();
    fireEvent.click(screen.getByTestId("code-stop"));
    await screen.findByTestId("code-stop-pending");
    H.replies[STOP] = { ok: true, running: true, closed: true };
    fireEvent.click(screen.getByTestId("code-stop"));
    await waitFor(() => expect(screen.queryByTestId("code-stop-pending")).toBeNull());
    expect(H.posts.filter((p) => p === STOP)).toHaveLength(2);
  });

  it("another session on screen (its own reply running): the line does not follow it", async () => {
    H.replies[STOP] = PENDING;
    await openRunning();
    fireEvent.click(screen.getByTestId("code-stop"));
    await screen.findByTestId("code-stop-pending");
    fireEvent.click(await screen.findByTestId("code-row-a2"));
    // a2's reply is followed (its own stream) and its Stop shows.
    await waitFor(() => expect(H.streams.some((p) => p.startsWith("/build/app/sessions/a2/"))).toBe(true));
    await screen.findByTestId("code-stop");
    expect(screen.queryByTestId("code-stop-pending")).toBeNull();
    expect(screen.queryByText(ASKED)).toBeNull();
  });

  it("a pending answer that arrives after the reply ended shows nothing", async () => {
    let answer: (v: unknown) => void = () => {};
    H.replies[STOP] = () => new Promise((res) => (answer = res));
    await openRunning();
    fireEvent.click(screen.getByTestId("code-stop"));
    await endReply();
    await waitFor(() => expect(screen.queryByTestId("code-stop")).toBeNull());
    await act(async () => {
      answer(PENDING);
      await H.lastPost;
    });
    expect(screen.queryByTestId("code-stop-pending")).toBeNull();
    // ...nor on the next reply.
    holdStreams();
    const box = screen.getByTestId("code-box");
    fireEvent.change(box, { target: { value: "add a contact page" } });
    fireEvent.keyDown(box, { key: "Enter" });
    await screen.findByTestId("code-stop");
    expect(screen.queryByTestId("code-stop-pending")).toBeNull();
    expect(screen.queryByText(ASKED)).toBeNull();
  });
});

describe("an ordinary Stop", () => {
  it("shows nothing new", async () => {
    H.replies[STOP] = { ok: true, running: true };
    await openRunning();
    const before = screen.getByTestId("code-session").textContent;
    await pressStop();
    expect(H.posts).toEqual([STOP]);
    expect(screen.queryByTestId("code-stop-pending")).toBeNull();
    expect(screen.queryByTestId("code-note")).toBeNull();
    expect(screen.getByTestId("code-session").textContent).toBe(before);
  });
});
