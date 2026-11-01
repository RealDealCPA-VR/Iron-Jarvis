/**
 * v1.422.0 (Wave 2, B5) REVIEW: a limit card that WAITS by itself carries the
 * reply on at the reset with nobody pressing anything — so a page left open
 * on that session read the session again only when something else made it,
 * and kept saying "Waiting for Work to reset at 3:45 PM — it carries on by
 * itself then." long after the reply had carried on. While a wait is shown,
 * the page reads the session again (CODE_WAIT_REREAD_MS), so the carried-on
 * reply appears as it runs. Driven through the REAL BuildCodeView.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen } from "@testing-library/react";

const H = vi.hoisted(() => ({
  gets: {} as Record<string, unknown>,
  reads: [] as string[],
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
    API_BASE: "http://d",
    ijToken: () => "",
    get: (path: string) => {
      H.reads.push(path);
      const r = H.gets[path];
      if (r === undefined) return Promise.reject(new FakeApiError(`unmocked GET ${path}`, 404));
      return Promise.resolve(typeof r === "function" ? (r as () => unknown)() : r);
    },
    post: () => Promise.resolve({ ok: true }),
    patch: () => Promise.resolve({}),
    del: () => Promise.resolve({}),
  };
});

// The window is on screen (jsdom's own document reads as hidden).
vi.mock("@/lib/useDocumentVisible", () => ({ useDocumentVisible: () => true }));

vi.mock("@/lib/useChatStream", () => ({
  streamSSE: async function* () {
    // no frames
  },
  useLiveText: (s: { text?: string }) => s?.text ?? "",
}));

import { BuildCodeView, CODE_WAIT_REREAD_MS } from "@/components/buildCode/BuildCodeView";

const CLAUDE = {
  id: "claude-code", label: "Claude Code", installed: true, signed_in: true, version: "2.1.311",
  modes: [{ id: "ask", label: "Ask first", dangerous: false }], hidden_modes: [],
  models: [{ id: "", label: "Claude Code's default" }], efforts: [], accounts: [], commands: [],
};

const sess = {
  id: "a1", harness: "claude-code", folder: "C:\work\shop", title: "Fix the cart", account: "p1",
  account_name: "Work", model: "", effort: "", mode: "ask",
  harness_session_id: "6b1c2f0e-5d4a-4c3b-9a8e-7f6d5c4b3a21", created_at: "2026-11-01T09:00:00Z",
  updated_at: "2026-11-01T09:00:00Z", archived_at: null, last_error: "",
};

const limitEvents = [
  { type: "user", at: "2026-11-01T10:00:00Z", text: "fix the cart", turn: "t1" },
  { type: "error", at: "2026-11-01T10:00:02Z", title: "Claude Code hit this account's limit",
    fix: "Press Continue on another account, or wait until it resets.", technical: "",
    fix_kind: "account_limit", account: "p1", turn: "t1" },
  { type: "done", at: "2026-11-01T10:00:02Z", stop: "failed", turn: "t1" },
];

function seed(extra: Record<string, unknown>) {
  H.gets = {
    "/build/app/harnesses": { harnesses: [CLAUDE] },
    "/build/app/sessions": { sessions: [sess] },
    "/editor/options": { editors: [], preferred: "" },
    "/build/app/sessions/a1": { session: sess, running: false, pending: [], events: limitEvents, ...extra },
  };
}

/** React runs a render's effects on a (faked) timer: let them run before
 *  the clock is moved, so the interval exists when it is measured. */
async function settle() {
  await act(async () => {
    vi.advanceTimersByTime(20);
  });
}

const viewReads = () => H.reads.filter((p) => p === "/build/app/sessions/a1").length;

beforeEach(() => {
  H.reads = [];
  vi.useFakeTimers({ shouldAdvanceTime: true });
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("a limit that waits by itself", () => {
  it("is read again while it waits, so the reply it carries on shows here", async () => {
    seed({ account_wait: { account: "p1", account_name: "Work", resets_at: "2026-11-01T15:30:00Z", group: "" } });
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    await screen.findByTestId("code-limit-waiting");
    await settle();
    const before = viewReads();
    await act(async () => {
      vi.advanceTimersByTime(CODE_WAIT_REREAD_MS + 50);
    });
    expect(viewReads()).toBeGreaterThan(before);
  });

  it("a card with no wait is not read again on a timer", async () => {
    seed({});
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    await screen.findByTestId("code-continue-on");
    await settle();
    const before = viewReads();
    await act(async () => {
      vi.advanceTimersByTime(CODE_WAIT_REREAD_MS * 3);
    });
    expect(viewReads()).toBe(before);
  });
});
