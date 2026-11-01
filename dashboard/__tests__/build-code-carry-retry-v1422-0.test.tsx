/**
 * v1.422.0 (Wave 2, B3 + B5) REVIEW FOLLOW-UP: Retry on a carry-on that
 * FAILED. The page's Retry sent the newest user message's words again — for
 * a carry-on, those are Iron Jarvis's own words to the tool ("Iron Jarvis
 * restarted while you were working…"), which then showed as the user's own
 * message. Retry on a carry-on now asks the daemon to run the carry-on again,
 * hidden (POST …/continue); its line says the user pressed Retry. Driven
 * through the REAL BuildCodeView and the REAL CodeConversation.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const H = vi.hoisted(() => ({
  gets: {} as Record<string, unknown>,
  posts: [] as { path: string; body: unknown }[],
  streams: [] as { path: string; body: unknown }[],
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
      const r = H.gets[path];
      if (r === undefined) return Promise.reject(new FakeApiError(`unmocked GET ${path}`, 404));
      return Promise.resolve(typeof r === "function" ? (r as () => unknown)() : r);
    },
    post: (path: string, body?: unknown) => {
      H.posts.push({ path, body });
      return Promise.resolve({ ok: true });
    },
    patch: () => Promise.resolve({}),
    del: () => Promise.resolve({}),
  };
});

vi.mock("@/lib/useDocumentVisible", () => ({ useDocumentVisible: () => true }));

vi.mock("@/lib/useChatStream", () => ({
  streamSSE: async function* (path: string, body?: unknown) {
    H.streams.push({ path, body });
  },
  useLiveText: (s: { text?: string }) => s?.text ?? "",
}));

import { BuildCodeView } from "@/components/buildCode/BuildCodeView";
import { carryLineWords } from "@/lib/buildCodeCarry";

const CLAUDE = {
  id: "claude-code", label: "Claude Code", installed: true, signed_in: true, version: "2.1.311",
  modes: [{ id: "ask", label: "Ask first", dangerous: false }], hidden_modes: [],
  models: [{ id: "", label: "Claude Code's default" }], efforts: [], accounts: [], commands: [],
};

const sess = {
  id: "a1", harness: "claude-code", folder: "C:\work\shop", title: "Fix the cart", account: "",
  account_name: "", model: "", effort: "", mode: "ask",
  harness_session_id: "6b1c2f0e-5d4a-4c3b-9a8e-7f6d5c4b3a21", created_at: "2026-11-01T09:00:00Z",
  updated_at: "2026-11-01T09:00:00Z", archived_at: null, last_error: "",
};

const CARRY_WORDS = "Iron Jarvis restarted while you were working on my last message, so your reply was cut off.";

function seed(lastUser: Record<string, unknown>) {
  H.gets = {
    "/build/app/harnesses": { harnesses: [CLAUDE] },
    "/build/app/sessions": { sessions: [sess] },
    "/editor/options": { editors: [], preferred: "" },
    "/build/app/sessions/a1": {
      session: sess, running: false, pending: [],
      events: [
        { type: "user", at: "2026-11-01T10:00:00Z", text: "fix the cart", turn: "t1" },
        { type: "done", at: "2026-11-01T10:01:00Z", stop: "interrupted", turn: "t1" },
        { type: "user", at: "2026-11-01T11:00:00Z", turn: "t2", ...lastUser },
        { type: "error", at: "2026-11-01T11:00:05Z", title: "Claude Code couldn't finish this answer",
          fix: "Press Retry.", technical: "", turn: "t2" },
        { type: "done", at: "2026-11-01T11:00:05Z", stop: "failed", turn: "t2" },
      ],
    },
  };
}

beforeEach(() => {
  H.posts = [];
  H.streams = [];
});

afterEach(cleanup);

describe("Retry", () => {
  it("on a failed carry-on runs the carry-on again, hidden — never its words as the user's message", async () => {
    seed({ text: CARRY_WORDS, carry: { why: "restart" } });
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    fireEvent.click(await screen.findByTestId("code-retry"));
    await waitFor(() => expect(H.posts.map((p) => p.path)).toContain("/build/app/sessions/a1/continue"));
    const sentWords = [...H.posts, ...H.streams].some((p) => JSON.stringify(p.body ?? "").includes("Iron Jarvis restarted"));
    expect(sentWords).toBe(false);
  });

  it("on the user's own failed message still sends that message again", async () => {
    seed({ text: "fix the cart properly" });
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    fireEvent.click(await screen.findByTestId("code-retry"));
    await waitFor(() =>
      expect([...H.posts, ...H.streams].some((p) => JSON.stringify(p.body ?? "").includes("fix the cart properly"))).toBe(true),
    );
    expect(H.posts.map((p) => p.path)).not.toContain("/build/app/sessions/a1/continue");
  });

  it("a carry-on the user re-ran with Retry says so", () => {
    expect(carryLineWords({ why: "reset", fromAccount: "", toAccount: "", accountName: "Work", group: "", pressed: true, retry: true }))
      .toBe("You pressed Retry: carrying the reply on again.");
  });
});
