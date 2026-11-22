/**
 * 22-b (R3.4): Commit and Open a pull request are drawn under the newest
 * SETTLED Build › Code reply, in the real BuildCodeView, and only there: not
 * while the reply runs, a card waits, Iron Jarvis checks the reply, or the
 * check's first-time card asks (one main action at a time, G3).
 *
 * The view is the REAL BuildCodeView fed the daemon's own shapes; only the
 * network (lib/api), the stream and the folder tree are doubles. ReplyGit is
 * the real one, wrapped so the test also sees each time it is placed: the
 * conversation and the row are drawn in ONE commit, so once the reply's words
 * are on screen, a row that is not placed then is not placed at all.
 */

import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";

const H = vi.hoisted(() => ({
  gets: {} as Record<string, unknown>,
  asked: [] as string[],
  placed: [] as string[],
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
      H.asked.push(path);
      const r = H.gets[path];
      if (r === undefined) return Promise.reject(new FakeApiError(`unmocked GET ${path}`, 404));
      return Promise.resolve(r);
    },
    post: () => Promise.resolve({}),
    put: () => Promise.resolve({}),
    patch: () => Promise.resolve({}),
    del: () => Promise.resolve({}),
  };
});

vi.mock("@/lib/useChatStream", () => ({
  streamSSE: async function* () {},
  useLiveText: (s: { text?: string }) => s?.text ?? "",
}));

vi.mock("@/components/terminal/DirectoryTree", () => ({
  DirectoryTree: () => <span />,
}));

vi.mock("@/components/buildCode/ReplyGit", async (orig) => {
  const real = await orig<typeof import("@/components/buildCode/ReplyGit")>();
  const Placed = (p: React.ComponentProps<typeof real.ReplyGit>) => {
    H.placed.push(p.readKey);
    return <real.ReplyGit {...p} />;
  };
  return { ...real, ReplyGit: Placed, default: Placed };
});

import { BuildCodeView } from "@/components/buildCode/BuildCodeView";

const TOOLS = {
  harnesses: [
    {
      id: "codex",
      label: "Codex",
      installed: true,
      signed_in: true,
      modes: [{ id: "ask", label: "Ask first", dangerous: false }],
      models: [{ id: "", label: "Codex's default" }],
      efforts: [],
      accounts: [],
      commands: [],
    },
  ],
};

const sess = (id: string, over: Record<string, unknown> = {}) => ({
  id,
  harness: "codex",
  folder: "C:\\work\\shop",
  title: `Session ${id}`,
  account: "",
  model: "",
  effort: "",
  mode: "ask",
  harness_session_id: "x",
  created_at: "2026-11-22T09:00:00Z",
  updated_at: "2026-11-22T09:00:00Z",
  archived_at: null,
  last_error: "",
  ...over,
});

const REPLY = "Added the limit.";
const TURN = [
  { type: "user", at: "2026-11-22T10:00:00Z", text: "add a rate limit", files: [], turn: "t1" },
  { type: "text_done", at: "2026-11-22T10:00:03Z", item: "a1", text: REPLY, turn: "t1" },
  { type: "done", at: "2026-11-22T10:00:04Z", stop: "completed", turn: "t1" },
];

function proof(over: Record<string, unknown>): Record<string, unknown> {
  return {
    type: "proof",
    at: "2026-11-22T10:00:05Z",
    for_turn: "t1",
    state: "done",
    tests: "passed",
    why: "ran",
    command: "npm test",
    ran: 3,
    passed: 3,
    failed: 0,
    new_files: [],
    new_source: "git",
    by: "iron_jarvis",
    line: "Checked by Iron Jarvis: 3 tests ran, 3 passed.",
    ...over,
  };
}

const GIT = "/build/app/sessions/a1/git";
const FOLDER_GIT = {
  kind: "folder",
  branch: "feature",
  main: "main",
  on_main: false,
  files: [{ rel: "api/limits.py", status: "created", ticked: true }],
  more: 0,
  uncommitted: 1,
  ahead: 0,
  draft: "Add a rate limit\n\nFiles:\n- api/limits.py",
  can_pr: true,
  pr_note: "",
  pr_draft: { title: "", body: "" },
  gh: { installed: true, signed_in: true, note: "" },
};

function open(view: Record<string, unknown>, session: Record<string, unknown> = {}) {
  H.gets = {
    "/build/app/harnesses": TOOLS,
    "/build/app/sessions": { sessions: [sess("a1", session)] },
    "/editor/options": { editors: [], preferred: "" },
    "/build/app/sessions/a1": { session: sess("a1", session), events: TURN, running: null, pending: [], ...view },
    [GIT]: FOLDER_GIT,
  };
}

const view = () => render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);

afterEach(() => cleanup());
beforeEach(() => {
  H.gets = {};
  H.asked = [];
  H.placed = [];
});

describe("Commit and Open a pull request under the reply, in the real view", () => {
  it("a settled reply: the row reads the folder and draws Commit with the message written", async () => {
    open({});
    view();
    await screen.findByText(REPLY, {}, { timeout: 10_000 });
    const box = await screen.findByTestId("code-git", {}, { timeout: 10_000 });
    expect(box.getAttribute("data-kind")).toBe("folder");
    expect((screen.getByTestId("code-git-message") as HTMLTextAreaElement).value).toBe(FOLDER_GIT.draft);
    expect(H.asked).toContain(GIT);
    expect(H.placed.length).toBeGreaterThan(0);
  });

  it("a folder now on another branch than the pull request the view names is offered one of its own (review)", async () => {
    open({}, { pull_request: { url: "https://github.com/acme/shop/pull/7", number: 7, branch: "feature-a" } });
    H.gets[GIT] = {
      ...FOLDER_GIT,
      branch: "feature-b",
      files: [],
      uncommitted: 0,
      ahead: 1,
      pr_url: "",
      pr_draft: { title: "Make x seven", body: "" },
    };
    view();
    await screen.findByText(REPLY, {}, { timeout: 10_000 });
    expect(await screen.findByTestId("code-git-open-pr", {}, { timeout: 10_000 })).toBeTruthy();
    expect(screen.queryByTestId("code-git-pr-link")).toBeNull();
  });

  it("a reply that passed its check: the row is there too", async () => {
    open({ events: [...TURN, proof({})] });
    view();
    await screen.findByTestId("code-proof", {}, { timeout: 10_000 });
    expect(await screen.findByTestId("code-git", {}, { timeout: 10_000 })).toBeTruthy();
  });

  it("while the reply runs: no row", async () => {
    open({ events: TURN.slice(0, 2), running: true });
    view();
    await screen.findByText(REPLY, {}, { timeout: 10_000 });
    expect(H.placed).toEqual([]);
    expect(screen.queryByTestId("code-git")).toBeNull();
    expect(H.asked).not.toContain(GIT);
  });

  it("while a card waits: no row", async () => {
    // The tool's question is open (its turn has not ended) while the read
    // says no reply runs: only the waiting card holds the row back.
    const ask = {
      type: "approval",
      at: "2026-11-22T10:00:03Z",
      request_id: "r1",
      kind: "command",
      title: "Run npm install?",
      command: "npm install",
      turn: "t1",
    };
    open({ events: [...TURN.slice(0, 2), ask], running: null });
    view();
    await screen.findByText(REPLY, {}, { timeout: 10_000 });
    await screen.findByTestId("code-ask", {}, { timeout: 10_000 });
    expect(H.placed).toEqual([]);
    expect(screen.queryByTestId("code-git")).toBeNull();
  });

  it("while Iron Jarvis checks the reply: no row", async () => {
    open(
      { events: [...TURN, proof({ state: "checking", tests: "not_run", line: "Iron Jarvis is checking this reply: running `npm test`." })] },
      { checking: true },
    );
    view();
    await screen.findByTestId("code-proof", {}, { timeout: 10_000 });
    expect(screen.getByTestId("code-proof").getAttribute("data-state")).toBe("checking");
    expect(H.placed).toEqual([]);
    expect(screen.queryByTestId("code-git")).toBeNull();
  });

  it("while the check's first-time card asks: no row", async () => {
    const ask = proof({
      tests: "ask",
      why: "not_confirmed",
      ran: null,
      passed: null,
      failed: null,
      line: "Run `npm test` after each reply in this folder?",
      detail: "Iron Jarvis runs it itself after Codex changes files.",
    });
    open({ events: [...TURN, ask] });
    view();
    await screen.findByTestId("code-proof-ask", {}, { timeout: 10_000 });
    expect(H.placed).toEqual([]);
    expect(screen.queryByTestId("code-git")).toBeNull();
  });
});
