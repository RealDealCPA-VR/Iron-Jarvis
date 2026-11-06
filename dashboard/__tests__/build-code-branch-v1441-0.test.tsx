/**
 * v1.441.0 — Build › Code's Branch from here and Rewind to here
 * (hosted/branching.py; POST /build/app/sessions/{id}/fork and …/rewind):
 *
 *   - a quiet ⋯ on each of the user's own messages the tool can act from
 *     (the view's `branch.at`), listing ONLY what the tool can do;
 *   - "Rewind to here" takes two presses; neither runs while a reply runs;
 *   - a branch opens the new session with the message in its box; a rewind
 *     reads the session again, puts the message back in the box and says the
 *     daemon's line;
 *   - "Branch" in the session row's ⋯ only where `can_branch` says so;
 *   - an older Iron Jarvis (no `branch`, no `can_branch`) shows none of it.
 */

import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const H = vi.hoisted(() => ({
  gets: {} as Record<string, unknown>,
  posts: [] as { path: string; body: unknown }[],
  answers: {} as Record<string, unknown>,
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
      const r = H.gets[path];
      if (r === undefined) return Promise.reject(new FakeApiError(`unmocked GET ${path}`, 404));
      return Promise.resolve(r);
    },
    post: (path: string, body?: unknown) => {
      H.posts.push({ path, body });
      const r = H.answers[path];
      if (r instanceof Error) return Promise.reject(r);
      return Promise.resolve(r ?? {});
    },
    patch: () => Promise.resolve({}),
    del: () => Promise.resolve({}),
  };
});

vi.mock("@/lib/useChatStream", () => ({
  streamSSE: async function* () {
    /* nothing streams in these tests */
  },
  useLiveText: (s: { text?: string }) => s?.text ?? "",
}));

vi.mock("@/components/terminal/DirectoryTree", () => ({
  DirectoryTree: () => <span />,
}));

import { BuildCodeView } from "@/components/buildCode/BuildCodeView";
import { CodeConversation, type MessageActions } from "@/components/buildCode/CodeConversation";
import {
  BRANCH_FROM_HERE,
  BRANCH_ROW,
  REWIND_CONFIRM,
  REWIND_TO_HERE,
  decodeBranch,
  type BranchFacts,
} from "@/lib/buildAppBranch";
import { foldConversation, decodeCodeEvent, type CodeEvent, type ConvItem } from "@/lib/buildApp";

const AT1 = "2026-11-06T10:00:00.100000Z";
const AT2 = "2026-11-06T10:01:00.200000Z";
const EVENTS = [
  { type: "user", at: AT1, text: "first one", files: [] },
  { type: "text_done", at: "2026-11-06T10:00:01Z", item: "a", text: "reply one" },
  { type: "done", at: "2026-11-06T10:00:02Z", stop: "completed" },
  { type: "user", at: AT2, text: "second one", files: [] },
  { type: "text_done", at: "2026-11-06T10:01:01Z", item: "b", text: "reply two" },
  { type: "done", at: "2026-11-06T10:01:02Z", stop: "completed" },
];

const TOOLS = {
  harnesses: [
    {
      id: "claude-code",
      label: "Claude Code",
      installed: true,
      signed_in: true,
      modes: [{ id: "ask", label: "Ask first", dangerous: false }],
      models: [{ id: "", label: "Claude Code's default" }],
      efforts: [],
      accounts: [],
      commands: [],
    },
  ],
};

const sess = (id: string, over: Record<string, unknown> = {}) => ({
  id,
  harness: "claude-code",
  folder: "C:\\work\\shop",
  title: `Session ${id}`,
  account: "",
  model: "",
  effort: "",
  mode: "ask",
  harness_session_id: `hid-${id}`,
  created_at: "2026-11-06T09:00:00Z",
  updated_at: "2026-11-06T09:00:00Z",
  archived_at: null,
  last_error: "",
  source: "app",
  ...over,
});

function open(id: string, branch: unknown, rowOver: Record<string, unknown> = {}) {
  H.gets = {
    "/build/app/harnesses": TOOLS,
    "/build/app/sessions": { sessions: [sess(id, rowOver)] },
    "/editor/options": { editors: [], preferred: "" },
    [`/build/app/sessions/${id}`]: {
      session: sess(id),
      events: EVENTS,
      running: null,
      pending: [],
      ...(branch === undefined ? {} : { branch }),
    },
  };
}

const FULL = { fork: true, fork_at: true, rewind: true, at: [AT1, AT2] };

beforeEach(() => {
  H.posts = [];
  H.answers = {};
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const items = (): ConvItem[] =>
  foldConversation(EVENTS.map((e) => decodeCodeEvent(e)).filter((e): e is CodeEvent => e !== null)).items;

function actions(facts: BranchFacts, over: Partial<MessageActions> = {}): MessageActions {
  return { facts, busy: false, onBranch: vi.fn(), onRewind: vi.fn(), ...over };
}

describe("the user's own messages", () => {
  it("each message the tool can act from has a ⋯ with only what the tool can do", () => {
    const facts = decodeBranch({ branch: { ...FULL, at: [AT2] } })!;
    render(<CodeConversation items={items()} folder="C:\\work" tool="claude-code" messageActions={actions(facts)} />);
    // Only the second message is one the tool keeps a reference for.
    expect(screen.getAllByTestId("code-user-more")).toHaveLength(1);
    fireEvent.click(screen.getByTestId("code-user-more"));
    expect(screen.getByTestId("code-user-branch").textContent).toBe(BRANCH_FROM_HERE);
    expect(screen.getByTestId("code-user-rewind").textContent).toBe(REWIND_TO_HERE);
  });

  it("a tool that branches only the whole conversation offers nothing on a message", () => {
    const facts = decodeBranch({ branch: { fork: true, fork_at: false, rewind: false, at: [AT1, AT2] } })!;
    render(<CodeConversation items={items()} folder="C:\\work" tool="opencode" messageActions={actions(facts)} />);
    expect(screen.queryByTestId("code-user-more")).toBeNull();
  });

  it("an older Iron Jarvis (no branch facts) shows no menu", () => {
    expect(decodeBranch({ events: [] })).toBeNull();
    render(<CodeConversation items={items()} folder="C:\\work" tool="claude-code" />);
    expect(screen.queryByTestId("code-user-more")).toBeNull();
  });

  it("Branch from here names the message; Rewind to here takes two presses", async () => {
    const facts = decodeBranch({ branch: FULL })!;
    const a = actions(facts);
    render(<CodeConversation items={items()} folder="C:\\work" tool="claude-code" messageActions={a} />);
    fireEvent.click(screen.getAllByTestId("code-user-more")[1]);
    fireEvent.click(screen.getByTestId("code-user-branch"));
    expect(a.onBranch).toHaveBeenCalledWith(AT2);
    fireEvent.click(screen.getAllByTestId("code-user-more")[0]);
    const rewind = () => screen.getByTestId("code-user-rewind").querySelector("button")!;
    fireEvent.click(rewind());
    expect(a.onRewind).not.toHaveBeenCalled();
    expect(rewind().textContent).toBe(REWIND_CONFIRM);
    fireEvent.click(rewind());
    await waitFor(() => expect(a.onRewind).toHaveBeenCalledWith(AT1));
  });

  it("while a reply runs, neither acts", async () => {
    const facts = decodeBranch({ branch: FULL })!;
    const a = actions(facts, { busy: true });
    render(<CodeConversation items={items()} folder="C:\\work" tool="claude-code" messageActions={a} />);
    fireEvent.click(screen.getAllByTestId("code-user-more")[0]);
    expect((screen.getByTestId("code-user-branch") as HTMLButtonElement).disabled).toBe(true);
    const rewind = screen.getByTestId("code-user-rewind").querySelector("button")!;
    fireEvent.click(rewind);
    fireEvent.click(rewind);
    await new Promise((r) => setTimeout(r, 30));
    expect(a.onRewind).not.toHaveBeenCalled();
  });
});

describe("in the Code view", () => {
  it("Branch from here posts the message and opens the branch with it in the box", async () => {
    open("a1", FULL);
    H.answers["/build/app/sessions/a1/fork"] = {
      session: sess("b2"),
      text: "second one",
      files: [],
      note: "A branch of this conversation.",
      from_end: false,
    };
    H.gets["/build/app/sessions/b2"] = { session: sess("b2"), events: EVENTS.slice(0, 3), running: null, pending: [] };
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    await screen.findAllByTestId("code-user-more", {}, { timeout: 10_000 });
    fireEvent.click(screen.getAllByTestId("code-user-more")[1]);
    fireEvent.click(screen.getByTestId("code-user-branch"));
    await waitFor(() => expect(H.posts.some((p) => p.path === "/build/app/sessions/a1/fork")).toBe(true));
    expect(H.posts.find((p) => p.path === "/build/app/sessions/a1/fork")!.body).toEqual({ at: AT2 });
    const box = (await screen.findByDisplayValue("second one", {}, { timeout: 10_000 })) as HTMLTextAreaElement;
    expect(box.tagName).toBe("TEXTAREA");
    expect((await screen.findByTestId("code-info")).textContent).toContain("A branch of this conversation.");
  });

  it("Rewind to here posts the message, reads the session again and puts the message back", async () => {
    open("a1", FULL);
    H.answers["/build/app/sessions/a1/rewind"] = {
      session: sess("a1"),
      text: "second one",
      files: [],
      note: "The conversation now ends before that message. Your files were not changed.",
    };
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    await screen.findAllByTestId("code-user-more", {}, { timeout: 10_000 });
    // The daemon's answer after the rewind: the conversation before it.
    const before = H.gets["/build/app/sessions/a1"] as Record<string, unknown>;
    fireEvent.click(screen.getAllByTestId("code-user-more")[1]);
    H.gets["/build/app/sessions/a1"] = { ...before, events: EVENTS.slice(0, 3), branch: { ...FULL, at: [AT1] } };
    const rewind = () => screen.getByTestId("code-user-rewind").querySelector("button")!;
    fireEvent.click(rewind());
    fireEvent.click(rewind());
    await waitFor(() => expect(H.posts.some((p) => p.path === "/build/app/sessions/a1/rewind")).toBe(true));
    expect(H.posts.find((p) => p.path === "/build/app/sessions/a1/rewind")!.body).toEqual({ to: AT2 });
    await screen.findByDisplayValue("second one", {}, { timeout: 10_000 });
    await waitFor(() => expect(screen.getAllByTestId("code-user")).toHaveLength(1));
    expect(screen.getByTestId("code-info").textContent).toContain("Your files were not changed");
  });

  it("the row's ⋯ has Branch only where the daemon says the tool can", async () => {
    open("a1", FULL, { can_branch: true });
    H.answers["/build/app/sessions/a1/fork"] = { session: sess("b2"), text: "", files: [], note: "Made." };
    H.gets["/build/app/sessions/b2"] = { session: sess("b2"), events: EVENTS, running: null, pending: [] };
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    fireEvent.click(await screen.findByTestId("code-row-more-a1", {}, { timeout: 10_000 }));
    const item = screen.getByTestId("code-row-branch-a1");
    expect(item.textContent).toBe(BRANCH_ROW);
    fireEvent.click(item);
    await waitFor(() => expect(H.posts.some((p) => p.path === "/build/app/sessions/a1/fork")).toBe(true));
    expect(H.posts.find((p) => p.path === "/build/app/sessions/a1/fork")!.body).toEqual({});
  });

  it("an older Iron Jarvis (no can_branch, no branch) shows neither", async () => {
    open("a1", undefined);
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    await screen.findAllByTestId("code-user", {}, { timeout: 10_000 });
    expect(screen.queryByTestId("code-user-more")).toBeNull();
    fireEvent.click(await screen.findByTestId("code-row-more-a1"));
    expect(screen.queryByTestId("code-row-branch-a1")).toBeNull();
  });
});
