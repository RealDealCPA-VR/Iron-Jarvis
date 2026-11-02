/**
 * v1.417.0 review of "helpers at work are said, and the report appears by
 * itself". While helpers work after the reply ended, the Code view reads the
 * session again every few seconds — and a helper can ask the user (its tool
 * call needs an OK) exactly then. A read that was out when the user answered
 * carries the card as still open: it must never put the answered card back
 * (a second press would then answer a card that is gone, and the line would
 * read "cancelled" for an Allow).
 */

import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const H = vi.hoisted(() => ({
  view: null as null | (() => Record<string, unknown>),
  reads: 0,
  /** When set, the next session read waits for `release`. */
  holdNext: false,
  release: null as null | (() => void),
  posts: [] as { path: string; body: unknown }[],
  /** A new turn's stream stays open until this is called. */
  endTurn: null as null | (() => void),
  /** Every session read, by session id. */
  readsOf: new Map<string, number>(),
  intervals: new Map<number, { fn: () => void; enabled: boolean }>(),
}));

vi.mock("@/lib/useVisibleInterval", async () => {
  const React = await import("react");
  return {
    useVisibleInterval: (fn: () => void, ms: number, enabled = true) => {
      const last = React.useRef<number | null>(null);
      if (last.current !== null && last.current !== ms) H.intervals.delete(last.current);
      last.current = ms;
      H.intervals.set(ms, { fn, enabled: enabled && ms > 0 });
    },
  };
});

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
      if (path === "/build/app/sessions") return Promise.resolve({ sessions: [sess(), sess({ id: "q1", title: "Quiet" })] });
      if (path === "/build/app/sessions/q1") {
        H.readsOf.set("q1", (H.readsOf.get("q1") ?? 0) + 1);
        return Promise.resolve({ session: sess({ id: "q1", title: "Quiet" }), events: [], running: null, turn_id: null, pending: [] });
      }
      if (path === "/editor/options") return Promise.resolve({ editors: [], preferred: "" });
      if (path === "/build/app/sessions/a1" && H.view) {
        H.reads += 1;
        // The answer is what the daemon held WHEN the read was made.
        const answer = H.view();
        if (H.holdNext) {
          H.holdNext = false;
          return new Promise((resolve) => {
            H.release = () => resolve(answer);
          });
        }
        return Promise.resolve(answer);
      }
      return Promise.reject(new FakeApiError(`unmocked GET ${path}`, 404));
    },
    post: (path: string, body: unknown) => {
      H.posts.push({ path, body });
      return Promise.resolve({});
    },
    patch: () => Promise.resolve({}),
    del: () => Promise.resolve({}),
  };
});

vi.mock("@/lib/useChatStream", () => ({
  streamSSE: async function* (path: string) {
    if (path.endsWith("/turn")) await new Promise<void>((resolve) => (H.endTurn = resolve));
  },
  useLiveText: (s: { text?: string }) => s?.text ?? "",
}));

vi.mock("@/components/terminal/DirectoryTree", () => ({ DirectoryTree: () => null }));

import { BuildCodeView } from "@/components/buildCode/BuildCodeView";
import { CODE_BACKGROUND_REREAD_MS } from "@/lib/buildApp";

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

function sess(over: Record<string, unknown> = {}) {
  return {
    id: "a1",
    harness: "claude-code",
    folder: "C:\\work\\shop",
    title: "Teams",
    account: "",
    model: "",
    effort: "",
    mode: "ask",
    harness_session_id: "h1",
    created_at: "2026-10-31T09:00:00Z",
    updated_at: "2026-10-31T09:00:00Z",
    archived_at: null,
    last_error: "",
    running: false,
    process: true,
    waiting: false,
    background: 0,
    background_helpers: 0,
    self_reply: false,
    ...over,
  };
}

const MINE = [
  { type: "user", text: "build it with teams", turn: "t1" },
  { type: "text_done", item: "m1:0", text: "Two teams are on it. I'll report as teams finish.", turn: "t1" },
  { type: "done", stop: "completed", turn: "t1" },
];
/** A helper's tool call asks for an OK while no reply of the user's runs. */
const ASK = { type: "approval", request_id: "ask-h1", kind: "command", title: "Run npm test", command: "npm test", can_session: true };

async function tick(ms: number): Promise<boolean> {
  const it = H.intervals.get(ms);
  if (!it || !it.enabled) return false;
  await act(async () => {
    it.fn();
  });
  return true;
}

beforeEach(() => {
  H.view = null;
  H.reads = 0;
  H.holdNext = false;
  H.release = null;
  H.posts = [];
  H.endTurn = null;
  H.readsOf = new Map();
  H.intervals = new Map();
});

afterEach(() => {
  cleanup();
});

describe("a helper's card answered while a background read is out", () => {
  it("never comes back, and the session is read again for the truth", async () => {
    let answered = false;
    H.view = () => ({
      session: sess({ background: 2, background_helpers: 2, waiting: !answered }),
      events: answered ? [...MINE, ASK, { type: "resolved", request_id: "ask-h1", decision: "allow" }] : [...MINE, ASK],
      running: null,
      turn_id: null,
      pending: answered ? [] : [ASK],
    });
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    const card = await screen.findByTestId("code-ask", {}, { timeout: 10_000 });
    expect(card.dataset.kind).toBe("approval");

    // A background read goes out (the card still open on the daemon)…
    H.holdNext = true;
    expect(await tick(CODE_BACKGROUND_REREAD_MS)).toBe(true);
    expect(H.release).not.toBeNull();
    // …and the user answers meanwhile.
    fireEvent.click(screen.getByTestId("code-ask-allow"));
    await waitFor(() => expect(H.posts.some((p) => p.path === "/build/app/sessions/a1/answer")).toBe(true));
    await waitFor(() => expect(screen.queryByTestId("code-ask")).toBeNull());
    answered = true;
    const readsBefore = H.reads;

    // The old read lands: it decides nothing about the card…
    await act(async () => {
      H.release?.();
    });
    expect(screen.queryByTestId("code-ask")).toBeNull();
    // …and the session is read again for the truth.
    await waitFor(() => expect(H.reads).toBeGreaterThan(readsBefore));
    await waitFor(() => expect(screen.getByTestId("code-resolved").dataset.decision).toBe("allow"));
    expect(screen.queryByTestId("code-ask")).toBeNull();
    // One answer, never a second press for a card that is gone.
    expect(H.posts.filter((p) => p.path.endsWith("/answer"))).toHaveLength(1);
  });
});

describe("Running now, for a reply the tool started by itself", () => {
  it("never calls Pi's own run a report (the words hold for every tool)", async () => {
    const { buildAppReplyItems } = await import("@/lib/runningNow");
    const [pi] = buildAppReplyItems([
      { turn_id: "bg:p1", thread_id: "p1", surface: "build_app", harness: "pi", started_at: "", waiting: false, title: "Docs", background: true, helpers: 0 },
    ]);
    expect(pi.line).toBe("Coding session (Pi) · replying on its own"); // v1.420.0: the tool in brackets
  });
});

describe("the read once helpers stop being said", () => {
  it("is never made on a send (the reply is followed; an extra read would undo it)", async () => {
    H.view = () => ({ session: sess({ background: 2, background_helpers: 2 }), events: MINE, running: null, turn_id: null, pending: [] });
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    await screen.findByTestId("code-background");
    const before = H.reads;
    fireEvent.change(screen.getByTestId("code-box"), { target: { value: "and now the docs" } });
    fireEvent.click(screen.getByTestId("code-send"));
    await waitFor(() => expect(H.endTurn).not.toBeNull());
    // The line steps aside for the reply the user started…
    await waitFor(() => expect(screen.queryByTestId("code-background")).toBeNull());
    // …and nothing reads the session while that reply is followed.
    await act(async () => {
      await new Promise((r) => setTimeout(r, 30));
    });
    expect(H.reads).toBe(before);
    expect(screen.getByTestId("code-conversation").textContent).toContain("and now the docs");
    await act(async () => {
      H.endTurn?.();
    });
  });

  it("is never made for a session the user left (the new one reads for itself, once)", async () => {
    H.view = () => ({ session: sess({ background: 2, background_helpers: 2 }), events: MINE, running: null, turn_id: null, pending: [] });
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    await screen.findByTestId("code-background");
    const readsA = H.reads;
    fireEvent.click(await screen.findByTestId("code-row-q1"));
    await waitFor(() => expect(H.readsOf.get("q1")).toBe(1));
    await waitFor(() => expect(screen.queryByTestId("code-background")).toBeNull());
    await act(async () => {
      await new Promise((r) => setTimeout(r, 30));
    });
    expect(H.readsOf.get("q1")).toBe(1);
    expect(H.reads).toBe(readsA);
  });
});

describe("the background read", () => {
  it("waits for its own read: a slow answer is never stacked under the next tick (nor overtaken by it)", async () => {
    H.view = () => ({ session: sess({ background: 2, background_helpers: 2 }), events: MINE, running: null, turn_id: null, pending: [] });
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    await screen.findByTestId("code-background");
    const before = H.reads;
    H.holdNext = true;
    expect(await tick(CODE_BACKGROUND_REREAD_MS)).toBe(true);
    expect(H.reads).toBe(before + 1);
    // Iron Jarvis is slow: the next ticks find the read still out.
    expect(await tick(CODE_BACKGROUND_REREAD_MS)).toBe(true);
    expect(await tick(CODE_BACKGROUND_REREAD_MS)).toBe(true);
    expect(H.reads).toBe(before + 1);
    // It answers: the next tick reads again.
    await act(async () => {
      H.release?.();
    });
    expect(await tick(CODE_BACKGROUND_REREAD_MS)).toBe(true);
    await waitFor(() => expect(H.reads).toBe(before + 2));
  });
});
