/**
 * v1.406.0 part 4 — THE NEXT-STEP BOX under the newest settled reply.
 *
 *  1. The TABLE: a reply's record (and the session's branch) → the recap and
 *     at most two buttons (lib/nextStep). Rows use REAL recorded reply shapes
 *     where the repo holds one (the Timeline fixture's saved reply line, the
 *     team seams' finish record — both written by the real daemon) and the
 *     decoders' own shapes otherwise.
 *  2. A coding session's SCREEN: no box for a plain answer; Keep going is
 *     the box's (never twice); "Yes, do it" sends exactly "Yes, go ahead." as
 *     a new turn (the same send the step-limit line used); an offer that is
 *     the user's call only fills the box (no turn is sent); the box shows on
 *     the newest reply only and steps aside while the user types; Commit
 *     opens the Branch view's own Commit press (its draft is asked for, and
 *     nothing is committed by the box).
 */
import React from "react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

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
    gets: {} as Record<string, unknown>,
    posts: {} as Record<string, unknown>,
    calls: [] as { method: string; path: string; body?: unknown }[],
  };
});

vi.mock("@/lib/api", () => {
  const answer = (method: string, path: string, body?: unknown) => {
    api.calls.push({ method, path, body });
    const hit = method === "GET" ? api.gets[path] : api.posts[path];
    if (hit instanceof Error) return Promise.reject(hit);
    if (hit === undefined && method === "GET") return Promise.reject(new api.ApiError(`unmocked ${path}`, 404));
    return Promise.resolve(hit ?? {});
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
import {
  FIX_REVIEW_TEXT,
  FIX_TESTS_TEXT,
  MAX_NEXT_ACTIONS,
  NEXT_KEEP_GOING_TEXT,
  YES_TEXT,
  chatNextStep,
  chatReplyFacts,
  codeNextStep,
  codeReplyFacts,
  gitFacts,
  type GitFacts,
} from "@/lib/nextStep";
import { KEEP_GOING_TEXT } from "@/lib/buildSessions";

const fx = (name: string) =>
  JSON.parse(readFileSync(join(__dirname, "fixtures", name), "utf8").replace(/\r\n/g, "\n"));
/** The real daemon's saved reply line (v1.390.0 Timeline fixture). */
const TIMELINE_LINE = fx("build-timeline-v1390.json").line as Record<string, unknown>;
/** The real daemon's finish record of a team-reviewed reply (v1.403.0). */
const TEAM_FINISH = fx("session-team-seams-v1403.json").finish as Record<string, unknown>;

const GIT_DIRTY: GitFacts = { branch: "ij/cart-3f2a", base: "main", uncommitted: 3, ahead: 0, prUrl: "" };
const GIT_COMMITTED: GitFacts = { branch: "ij/cart-3f2a", base: "main", uncommitted: 0, ahead: 2, prUrl: "" };
const GIT_PR: GitFacts = { ...GIT_COMMITTED, prUrl: "https://github.com/o/r/pull/4" };

const finish = (over: Record<string, unknown> = {}) => ({
  tests: "passed", command: "pytest -q", removed: 0, kept: 0, points: 1, unmet: 0, after: "", ...over,
});
const line = (over: Record<string, unknown> = {}) => ({ content: "Done.", documents: ["a.py"], ...over });
const kinds = (s: ReturnType<typeof codeNextStep>) => s?.actions.map((a) => a.kind) ?? null;

/* ------------------------------------------------------------ 1. the table */

describe("the table: a reply's record → the recap and its buttons", () => {
  const rows: {
    name: string;
    reply: Record<string, unknown>;
    git?: GitFacts | null;
    recap: string | null;
    actions: string[] | null;
  }[] = [
    {
      name: "the daemon's own saved line: one file, no tests found — said, nothing to press",
      reply: TIMELINE_LINE,
      recap: "Changed 1 file and no tests found.",
      actions: [],
    },
    {
      name: "a team-reviewed reply (the daemon's finish record) on a branch with work not committed",
      reply: line({ documents: ["a.py", "b.py"], finish: TEAM_FINISH }),
      git: GIT_DIRTY,
      recap: "Changed 2 files, tests pass and the reviewer approved it. The changes on ij/cart-3f2a are not committed yet.",
      actions: ["commit"],
    },
    {
      name: "tests failing → Fix the failing tests",
      reply: line({ finish: finish({ tests: "failed" }) }),
      recap: "Changed 1 file and tests are failing.",
      actions: ["fix_tests"],
    },
    {
      name: "tests failed, then fixed → no fix press",
      reply: line({ finish: finish({ tests: "failed", after: "passed" }) }),
      recap: "Changed 1 file and tests pass after a fix.",
      actions: [],
    },
    {
      name: "the reviewer asked for changes → Fix what the reviewer found",
      reply: line({ finish: finish({ review: { state: "done", member: "codex", model: "x", verdict: "changes_needed" } }) }),
      recap: "Changed 1 file, tests pass and the reviewer asked for changes.",
      actions: ["fix_review"],
    },
    {
      name: "the step limit + failing tests → Keep going first (the reply is not finished), at most two",
      reply: line({
        stepLimit: { limit: 60, safety: false },
        finish: finish({ tests: "failed", review: { state: "done", member: "m", model: "", verdict: "changes_needed" } }),
      }),
      recap: "Changed 1 file, tests are failing and the reviewer asked for changes. It stopped before it finished.",
      actions: ["keep_going", "fix_tests"],
    },
    {
      name: "the session's cap → Keep going (the cap line's own test id)",
      reply: line({ capMet: { kind: "time", limit: 30 } }),
      recap: "Changed 1 file. It stopped at this session's limit before it finished.",
      actions: ["keep_going"],
    },
    {
      name: "an offer of more work → Yes, do it",
      reply: line({ content: "Fixed the cart.\n\nWant me to also add tests?" }),
      recap: "Changed 1 file. Offered next: add tests.",
      actions: ["do_offer"],
    },
    {
      name: "an offer to COMMIT (the user's call) on a dirty branch → the Commit press, once",
      reply: line({ content: "Done. Would you like me to commit these changes?" }),
      git: GIT_DIRTY,
      recap: "Changed 1 file. Offered next: commit these changes. That one is your call.",
      actions: ["commit"],
    },
    {
      name: "an offer to send (the user's call) → named, fills the box",
      reply: line({ content: "The summary is ready. Want me to send this to the client?" }),
      recap: "Changed 1 file. Offered next: send this to the client. That one is your call.",
      actions: ["fill_offer"],
    },
    {
      name: "committed, no pull request → Open a pull request",
      reply: line(),
      git: GIT_COMMITTED,
      recap: "Changed 1 file. ij/cart-3f2a is committed; there is no pull request yet.",
      actions: ["open_pr"],
    },
    {
      name: "a pull request exists → nothing more on the branch",
      reply: line(),
      git: GIT_PR,
      recap: "Changed 1 file.",
      actions: [],
    },
    {
      name: "a plain answer (no files, no check, no offer) → NO box, even with work waiting on the branch",
      reply: { content: "The cart total is computed in cart.py." },
      git: GIT_DIRTY,
      recap: null,
      actions: null,
    },
    {
      name: "points of the request left → said",
      reply: line({ finish: finish({ unmet: 2 }) }),
      recap: "Changed 1 file and tests pass. 2 points of the request still need work.",
      actions: [],
    },
    {
      name: "a failed reply → NO box (its own line has Retry)",
      reply: line({ error: "It failed.", content: "Want me to add tests?" }),
      recap: null,
      actions: null,
    },
    {
      name: "a reply that ended on an account's limit card → NO box (the card is the way out)",
      reply: line({ accountLimit: { account: "a" } }),
      recap: null,
      actions: null,
    },
    {
      name: "a cut-off reply never reads its offer",
      reply: line({ truncated: true, content: "Want me to also add tests?" }),
      recap: "Changed 1 file.",
      actions: [],
    },
    {
      name: "a step-limited reply never reads its offer (Keep going is the press)",
      reply: line({ stepLimit: { limit: 5 }, content: "Want me to also add tests?" }),
      recap: "Changed 1 file. It stopped before it finished.",
      actions: ["keep_going"],
    },
  ];

  it.each(rows.map((r) => [r.name, r] as const))("%s", (_n, r) => {
    const step = codeNextStep(codeReplyFacts(r.reply), r.git ?? null);
    if (r.recap === null) {
      expect(step).toBeNull();
      return;
    }
    expect(step?.recap).toBe(r.recap);
    expect(kinds(step)).toEqual(r.actions);
    expect(step!.actions.length).toBeLessThanOrEqual(MAX_NEXT_ACTIONS);
  });

  it("the presses carry exactly the words a user would type", () => {
    const step = codeNextStep(
      codeReplyFacts(line({ stepLimit: { limit: 9 }, finish: finish({ tests: "failed" }) })),
      null,
    )!;
    expect(step.actions[0]).toMatchObject({ how: "send", text: KEEP_GOING_TEXT, testid: "harness-keep-going" });
    expect(NEXT_KEEP_GOING_TEXT).toBe(KEEP_GOING_TEXT);
    expect(step.actions[1]).toMatchObject({ how: "send", text: FIX_TESTS_TEXT });
    const review = codeNextStep(
      codeReplyFacts(line({ finish: finish({ review: { state: "done", member: "", model: "", verdict: "changes_needed" } }) })),
      null,
    )!;
    expect(review.actions[0]).toMatchObject({ how: "send", text: FIX_REVIEW_TEXT });
    const cap = codeNextStep(codeReplyFacts(line({ capMet: { kind: "cost", limit: 2 } })), null)!;
    expect(cap.actions[0].testid).toBe("harness-cap-keep-going");
    const yes = codeNextStep(codeReplyFacts(line({ content: "Shall I proceed?" })), null)!;
    expect(yes.actions[0]).toMatchObject({ kind: "do_offer", label: "Yes, do it", how: "send", text: YES_TEXT });
  });

  it("an offer that is the user's call NEVER sends: it fills the box or opens a press", () => {
    for (const content of [
      "Want me to push it?",
      "Should I go ahead and merge the PR?",
      "Shall I delete the old files?",
      "Would you like me to pay the invoice?",
      "Want me to email the summary to Dana?",
      "Would you like me to commit these changes?",
      "Want me to open a pull request?",
    ]) {
      for (const git of [null, GIT_DIRTY, GIT_COMMITTED]) {
        const step = codeNextStep(codeReplyFacts(line({ content })), git)!;
        expect(step.actions.some((a) => a.how === "send"), content).toBe(false);
        const chat = chatNextStep(chatReplyFacts({ content }));
        expect(chat?.actions.every((a) => a.how === "fill"), content).toBe(true);
      }
    }
    const fill = chatNextStep(chatReplyFacts({ content: "Want me to email the summary to Dana?" }))!;
    expect(fill.actions[0]).toMatchObject({ label: "Email the summary to Dana…", text: "Yes, email the summary to Dana." });
  });

  it("the branch facts: only a live branch read by the Branch view", () => {
    const g = { branch: "ij/x", base: "main", prUrl: "", removed: false, merged: false };
    expect(gitFacts(g, { uncommitted: 1, ahead: 0 })).toEqual({ branch: "ij/x", base: "main", prUrl: "", uncommitted: 1, ahead: 0 });
    expect(gitFacts({ ...g, removed: true }, { uncommitted: 1, ahead: 0 })).toBeNull();
    expect(gitFacts({ ...g, merged: true }, { uncommitted: 1, ahead: 0 })).toBeNull();
    expect(gitFacts(g, null)).toBeNull();
    expect(gitFacts(null, { uncommitted: 1, ahead: 0 })).toBeNull();
  });

  it("chat: an offer, Continue after a connection, and nothing else", () => {
    expect(chatNextStep(chatReplyFacts({ content: "Paris is the capital of France." }))).toBeNull();
    expect(chatNextStep(chatReplyFacts({ content: "Saved it.", documents: ["r.xlsx"] }))).toBeNull();
    const offer = chatNextStep(chatReplyFacts({ content: "Here is the draft. I can also add a summary table if you want." }))!;
    expect(offer.recap).toBe("Offered next: add a summary table.");
    expect(offer.actions.map((a) => [a.kind, a.how, a.text])).toEqual([["do_offer", "send", YES_TEXT]]);
    expect(chatNextStep(chatReplyFacts({ content: "x" }), { connected: true })?.actions.map((a) => a.kind)).toEqual([
      "connect_continue",
    ]);
    expect(chatNextStep(chatReplyFacts({ content: "Want me to add tests?", refused: true }))).toBeNull();
    expect(chatNextStep(chatReplyFacts({ content: "Want me to add tests?", truncated: true }))).toBeNull();
    expect(chatNextStep(chatReplyFacts({ content: "Want me to add tests?", interrupted: true }))).toBeNull();
  });
});

/* ---------------------------------------------------------- 2. the screen */

const terminal: SessionTerminalApi = {
  find: () => null,
  open: async () => null,
  render: () => <div data-testid="term-body" />,
  close: () => {},
};

function view(over: Record<string, unknown> = {}) {
  return {
    id: "s1",
    title: "Cart",
    updated_at: "2026-10-29T10:00:00",
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

function sse(frames: [string, unknown][]): Response {
  const text = frames.map(([e, d]) => `event: ${e}\ndata: ${JSON.stringify(d)}\n\n`).join("");
  return new Response(
    new ReadableStream<Uint8Array>({
      start(c) {
        c.enqueue(new TextEncoder().encode(text));
        c.close();
      },
    }),
    { status: 200, headers: { "Content-Type": "text/event-stream" } },
  );
}

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  api.gets = { "/build/sessions/s1": view(), "/build/accounts": new api.ApiError("no such route", 404) };
  api.posts = {};
  api.calls = [];
  localStorage.clear();
  fetchMock = vi.fn(async () => sse([["done", { reply: "ok" }]]));
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

const q = (turnId: string, content = "fix the cart") => ({ role: "user", content, turnId });
const reply = (over: Record<string, unknown> = {}) => ({
  role: "assistant",
  content: "Fixed the cart.",
  turnId: "t1",
  timing: { startedAt: 1, firstTokenAt: 2, endedAt: 3 },
  ...over,
});
const sentTexts = () =>
  fetchMock.mock.calls
    .filter((c) => String(c[0]).endsWith("/build/sessions/s1/turn"))
    .map((c) => JSON.parse(String((c[1] as RequestInit).body)).text as string);
const box = () => screen.getByTestId("harness-input") as HTMLTextAreaElement;

describe("a coding session's next-step box", () => {
  it("is NOT drawn under a plain answer", async () => {
    api.gets["/build/sessions/s1"] = view({ messages: [q("t1", "where is the total?"), reply({ content: "In cart.py." })] });
    renderSession();
    await screen.findByText("In cart.py.");
    expect(screen.queryByTestId("next-step-box")).toBeNull();
  });

  it("Keep going is the box's ONE press (never twice) and sends exactly what the step-limit line sent", async () => {
    api.gets["/build/sessions/s1"] = view({
      messages: [q("t1"), reply({ stepLimit: { limit: 60, safety: false }, documents: ["cart.py"] })],
    });
    renderSession();
    await screen.findByTestId("harness-step-limit");
    const next = await screen.findByTestId("next-step-box");
    expect(within(next).getByTestId("next-step-recap")).toHaveTextContent("Changed 1 file. It stopped before it finished.");
    expect(screen.getAllByTestId("harness-keep-going")).toHaveLength(1);
    expect(within(next).getByTestId("harness-keep-going")).toHaveAttribute("data-recommended", "true");
    expect(within(screen.getByTestId("harness-step-limit")).queryByRole("button")).toBeNull();
    fireEvent.click(screen.getByTestId("harness-keep-going"));
    await waitFor(() => expect(sentTexts()).toEqual([KEEP_GOING_TEXT]));
  });

  it("Yes, do it sends 'Yes, go ahead.' as a new turn — the same send as typing it", async () => {
    api.gets["/build/sessions/s1"] = view({
      messages: [q("t1"), reply({ content: "Fixed the cart.\n\nWant me to also add tests?", documents: ["cart.py"] })],
    });
    renderSession();
    const next = await screen.findByTestId("next-step-box");
    expect(within(next).getByTestId("next-step-recap")).toHaveTextContent("Offered next: add tests.");
    fireEvent.click(within(next).getByRole("button", { name: "Yes, do it" }));
    await waitFor(() => expect(sentTexts()).toEqual([YES_TEXT]));
  });

  it("an offer that is the user's call only FILLS the box — nothing is sent", async () => {
    api.gets["/build/sessions/s1"] = view({
      messages: [q("t1"), reply({ content: "The notes are ready. Want me to email the summary to Dana?", documents: ["notes.md"] })],
    });
    renderSession();
    const next = await screen.findByTestId("next-step-box");
    fireEvent.click(within(next).getByRole("button", { name: "Email the summary to Dana…" }));
    expect(box().value).toBe("Yes, email the summary to Dana.");
    // Typing (here: the filled words) puts the recap and buttons aside.
    expect(screen.queryByTestId("next-step-box")).toBeNull();
    await new Promise((r) => setTimeout(r, 30));
    expect(sentTexts()).toEqual([]);
  });

  it("shows on the NEWEST reply only, and steps aside while the user types", async () => {
    api.gets["/build/sessions/s1"] = view({
      messages: [
        q("t1"),
        reply({ content: "Want me to also add tests?", documents: ["a.py"] }),
        q("t2", "now the receipt"),
        reply({ turnId: "t2", content: "The receipt prints.", documents: [] }),
      ],
    });
    renderSession();
    await screen.findByText("The receipt prints.");
    // The newest reply is a plain answer: no box (the older offer is not read).
    expect(screen.queryByTestId("next-step-box")).toBeNull();
    cleanup();
    api.gets["/build/sessions/s1"] = view({
      messages: [q("t1"), reply({ content: "Want me to also add tests?", documents: ["a.py"] })],
    });
    renderSession();
    await screen.findByTestId("next-step-box");
    fireEvent.change(box(), { target: { value: "no, " } });
    expect(screen.queryByTestId("next-step-box")).toBeNull();
    fireEvent.change(box(), { target: { value: "" } });
    expect(screen.getByTestId("next-step-box")).toBeInTheDocument();
  });

  it("Commit opens the Branch view's own Commit press (its draft), and commits nothing itself", async () => {
    const git = { branch: "ij/cart-3f2a", base: "main", base_commit: "abc", worktree: "C:\\w", checkout: "C:\\c", repo: "C:\\r" };
    api.gets["/build/sessions/s1"] = view({ git, messages: [q("t1"), reply({ documents: ["cart.py"] })] });
    api.gets["/build/sessions/s1/git"] = { exists: true, ahead: 0, behind: 0, uncommitted: 2, remote: "origin", pushed: false };
    api.gets["/build/sessions/s1/git/diff"] = { files: [] };
    api.posts["/build/sessions/s1/git/draft"] = { drafted: true, message: "Fix the cart total", note: "" };
    renderSession();
    const next = await screen.findByTestId("next-step-box");
    await waitFor(() =>
      expect(within(next).getByTestId("next-step-recap")).toHaveTextContent(
        "Changed 1 file. The changes on ij/cart-3f2a are not committed yet.",
      ),
    );
    fireEvent.click(within(next).getByRole("button", { name: "Commit" }));
    // The Branch view's own form, with its drafted message, waiting for the user.
    const msg = (await screen.findByLabelText("Commit message")) as HTMLTextAreaElement;
    await waitFor(() => expect(msg.value).toBe("Fix the cart total"));
    expect(api.calls.filter((c) => c.method === "POST" && c.path === "/build/sessions/s1/git/draft")).toHaveLength(1);
    expect(api.calls.some((c) => c.method === "POST" && c.path === "/build/sessions/s1/git/commit")).toBe(false);
  });
});
