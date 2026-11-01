/**
 * v1.422.0 (Wave 2, B3 + B5) — the Build › Code page wires what carries on by
 * itself to the conversation: the session read's `cut` puts Continue under
 * a reply a restart cut off (POST …/continue, then the running turn is
 * followed), and its `account_wait` puts the waiting line with Cancel under
 * the limit card (POST …/wait/cancel). Driven through the REAL BuildCodeView;
 * only the network and the stream are stand-ins.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const H = vi.hoisted(() => ({
  gets: {} as Record<string, unknown>,
  posts: [] as { path: string; body: unknown }[],
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
    post: (path: string, body?: unknown) => {
      H.posts.push({ path, body });
      return Promise.resolve({ ok: true });
    },
    patch: () => Promise.resolve({}),
    del: () => Promise.resolve({}),
  };
});

vi.mock("@/lib/useChatStream", () => ({
  streamSSE: async function* () {
    // no frames: the followed turn says nothing in this test
  },
  useLiveText: (s: { text?: string }) => s?.text ?? "",
}));

import { BuildCodeView } from "@/components/buildCode/BuildCodeView";

const CLAUDE = {
  id: "claude-code",
  label: "Claude Code",
  installed: true,
  signed_in: true,
  version: "2.1.311",
  modes: [{ id: "ask", label: "Ask first", dangerous: false }],
  hidden_modes: [],
  models: [{ id: "", label: "Claude Code's default" }],
  efforts: [],
  accounts: [],
  commands: [],
};

const sess = {
  id: "a1",
  harness: "claude-code",
  folder: "C:\\work\\shop",
  title: "Fix the cart",
  account: "p1",
  account_name: "Work",
  model: "",
  effort: "",
  mode: "ask",
  harness_session_id: "6b1c2f0e-5d4a-4c3b-9a8e-7f6d5c4b3a21",
  created_at: "2026-11-01T09:00:00Z",
  updated_at: "2026-11-01T09:00:00Z",
  archived_at: null,
  last_error: "",
};

function seed(view: Record<string, unknown>) {
  H.gets = {
    "/build/app/harnesses": { harnesses: [CLAUDE] },
    "/build/app/sessions": { sessions: [sess] },
    "/editor/options": { editors: [], preferred: "" },
    "/build/app/sessions/a1": { session: sess, running: false, pending: [], ...view },
  };
}

beforeEach(() => {
  H.posts = [];
  H.reads = [];
});

afterEach(cleanup);

describe("Build › Code's page", () => {
  it("Continue on a reply a restart cut off posts the carry-on and reads the session again", async () => {
    seed({
      events: [
        { type: "user", at: "2026-11-01T10:00:00Z", text: "fix the cart", turn: "t1" },
        { type: "error", at: "2026-11-01T10:01:00Z", title: "Iron Jarvis stopped while this was working",
          fix: "Send the message again — the conversation is kept.", technical: "", turn: "t1" },
        { type: "done", at: "2026-11-01T10:01:00Z", stop: "interrupted", turn: "t1" },
      ],
      cut: { turn: "t1", resumable: true, why_not: "it was waiting for your answer", waiting: true },
    });
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    const press = await screen.findByTestId("code-continue-cut");
    expect(screen.getByTestId("code-cut-why")).toHaveTextContent("it was waiting for your answer");
    expect(screen.queryByTestId("code-retry")).toBeNull();
    const before = H.reads.filter((p) => p === "/build/app/sessions/a1").length;
    fireEvent.click(press);
    await waitFor(() => expect(H.posts.map((p) => p.path)).toContain("/build/app/sessions/a1/continue"));
    await waitFor(() => expect(H.reads.filter((p) => p === "/build/app/sessions/a1").length).toBeGreaterThan(before));
  });

  it("a limit that waits by itself says so, and Cancel ends the wait", async () => {
    seed({
      events: [
        { type: "user", at: "2026-11-01T10:00:00Z", text: "fix the cart", turn: "t1" },
        { type: "error", at: "2026-11-01T10:00:02Z", title: "Claude Code hit this account's limit",
          fix: "Press Continue on another account, or wait until it resets.", technical: "",
          fix_kind: "account_limit", account: "p1", turn: "t1" },
        { type: "done", at: "2026-11-01T10:00:02Z", stop: "failed", turn: "t1" },
      ],
      account_wait: { account: "p1", account_name: "Work", resets_at: "2026-11-01T15:30:00Z", group: "" },
    });
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    expect(await screen.findByTestId("code-limit-waiting")).toHaveTextContent(/Waiting for Work to reset at/);
    fireEvent.click(screen.getByTestId("code-wait-cancel"));
    await waitFor(() => expect(H.posts.map((p) => p.path)).toContain("/build/app/sessions/a1/wait/cancel"));
    await waitFor(() => expect(screen.queryByTestId("code-limit-waiting")).toBeNull());
    expect(screen.getByTestId("code-continue-on")).toBeInTheDocument();
  });
});
