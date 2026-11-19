/**
 * v1.543.0 — Build › Code: "Rewind to here…" can put the folder's files back
 * (hosted/checkpoints.py; GET …/rewind/preview, POST …/rewind {files},
 * …/rewind/files, …/rewind/undo):
 *
 *   - a message whose checkpoint is kept (`branch.files_at`) opens a PREVIEW
 *     first — nothing is posted until a choice is made in it;
 *   - the preview lists what goes back, what moves to the trash, what is left
 *     and the commits that stay; "Rewind conversation and files" sends the
 *     preview's id back (`expect`), "Conversation only" sends no `files`;
 *   - a tool with no conversation rewind still gets the menu, with the files
 *     only;
 *   - when the files cannot go back right now the reason is said and only the
 *     conversation's own press stays;
 *   - "Undo rewind" (two presses) is there while the daemon says it can be;
 *   - an older Iron Jarvis (no `files_at`) keeps the two-press rewind, with no
 *     preview read at all.
 */

import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const H = vi.hoisted(() => ({
  gets: {} as Record<string, unknown>,
  got: [] as string[],
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
      H.got.push(path);
      const r = H.gets[path];
      if (r === undefined) return Promise.reject(new FakeApiError(`unmocked GET ${path}`, 404));
      if (r instanceof Error) return Promise.reject(r);
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
import { CodeConversation, menuFitsLeftward, type MessageActions } from "@/components/buildCode/CodeConversation";
import { RewindDialog } from "@/components/buildCode/RewindDialog";
import {
  FILES_BACK_TO_HERE,
  REWIND_BOTH,
  REWIND_CONVERSATION_ONLY,
  REWIND_FILES_PRESS,
  REWIND_NOTHING,
  REWIND_TO_HERE,
  REWIND_TO_HERE_ASK,
  UNDO_REWIND,
  UNDO_REWIND_CONFIRM,
  decodeBranch,
  decodeRewindPlan,
  type BranchFacts,
} from "@/lib/buildAppBranch";
import { foldConversation, decodeCodeEvent, type CodeEvent, type ConvItem } from "@/lib/buildApp";

const AT1 = "2026-11-19T10:00:00.100000Z";
const AT2 = "2026-11-19T10:01:00.200000Z";
const EVENTS = [
  { type: "user", at: AT1, text: "first one", files: [] },
  { type: "text_done", at: "2026-11-19T10:00:01Z", item: "a", text: "reply one" },
  { type: "done", at: "2026-11-19T10:00:02Z", stop: "completed" },
  { type: "user", at: AT2, text: "second one", files: [] },
  { type: "text_done", at: "2026-11-19T10:01:01Z", item: "b", text: "reply two" },
  { type: "done", at: "2026-11-19T10:01:02Z", stop: "completed" },
];

const tool = (id: string, label: string) => ({
  id,
  label,
  installed: true,
  signed_in: true,
  modes: [{ id: "ask", label: "Ask first", dangerous: false }],
  models: [{ id: "", label: `${label}'s default` }],
  efforts: [],
  accounts: [],
  commands: [],
});
const TOOLS = { harnesses: [tool("claude-code", "Claude Code"), tool("grok", "Grok")] };

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
  created_at: "2026-11-19T09:00:00Z",
  updated_at: "2026-11-19T09:00:00Z",
  archived_at: null,
  last_error: "",
  source: "app",
  ...over,
});

const PREVIEW = (id: string, at: string) => `/build/app/sessions/${id}/rewind/preview?to=${encodeURIComponent(at)}`;

function open(id: string, branch: unknown, over: Record<string, unknown> = {}) {
  H.gets = {
    "/build/app/harnesses": TOOLS,
    "/build/app/sessions": { sessions: [sess(id, over)] },
    "/editor/options": { editors: [], preferred: "" },
    [`/build/app/sessions/${id}`]: { session: sess(id, over), events: EVENTS, running: null, pending: [], branch },
  };
}

const FULL = { fork: true, fork_at: true, rewind: true, at: [AT1, AT2], files_at: [AT1, AT2], undo_rewind: false };
const PLAN = {
  id: "0123456789abcdef",
  can: true,
  back: ["a.py", "one.py"],
  back_count: 2,
  new: ["two.py"],
  new_count: 1,
  left: [{ rel: "vendor/lib", why: "it is a repository of its own" }],
  left_count: 1,
  problems: [],
  problems_count: 0,
  commits_since: 2,
  too_many: false,
  blocked: "",
  conversation: true,
  to: AT2,
  label: "Claude Code",
};

beforeEach(() => {
  H.posts = [];
  H.got = [];
  H.answers = {};
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const items = (): ConvItem[] =>
  foldConversation(EVENTS.map((e) => decodeCodeEvent(e)).filter((e): e is CodeEvent => e !== null)).items;

function actions(facts: BranchFacts, over: Partial<MessageActions> = {}): MessageActions {
  return { facts, busy: false, onBranch: vi.fn(), onRewind: vi.fn(), onRewindAsk: vi.fn(), ...over };
}

describe("the facts", () => {
  it("reads the kept checkpoints and the undo flag; an older daemon sends neither", () => {
    const f = decodeBranch({ branch: { ...FULL, undo_rewind: true } })!;
    expect([...f.filesAt]).toEqual([AT1, AT2]);
    expect(f.undoRewind).toBe(true);
    const old = decodeBranch({ branch: { fork: true, fork_at: true, rewind: true, at: [AT1] } })!;
    expect(old.filesAt.size).toBe(0);
    expect(old.undoRewind).toBe(false);
  });

  it("reads a preview, and refuses one with no id", () => {
    const p = decodeRewindPlan(PLAN)!;
    expect(p.back).toEqual(["a.py", "one.py"]);
    expect(p.made).toEqual(["two.py"]);
    expect(p.left).toEqual([{ rel: "vendor/lib", why: "it is a repository of its own" }]);
    expect([p.backCount, p.madeCount, p.leftCount, p.commitsSince]).toEqual([2, 1, 1, 2]);
    expect(decodeRewindPlan({ ...PLAN, id: "" })).toBeNull();
    // A count is never less than the names shown.
    expect(decodeRewindPlan({ ...PLAN, back_count: 0 })!.backCount).toBe(2);
  });
});

describe("the message's menu", () => {
  it("a message with a kept checkpoint asks first; one without keeps the two presses", () => {
    const facts = decodeBranch({ branch: { ...FULL, files_at: [AT2] } })!;
    const a = actions(facts);
    render(<CodeConversation items={items()} folder="C:\\work" tool="claude-code" messageActions={a} />);
    fireEvent.click(screen.getAllByTestId("code-user-more")[1]);
    const ask = screen.getByTestId("code-user-rewind-ask");
    expect(ask.textContent).toBe(REWIND_TO_HERE_ASK);
    expect(screen.queryByTestId("code-user-rewind")).toBeNull();
    fireEvent.click(ask);
    expect(a.onRewindAsk).toHaveBeenCalledWith(AT2);
    expect(a.onRewind).not.toHaveBeenCalled();
    // The first message has no checkpoint: the two-press rewind, as before.
    fireEvent.click(screen.getAllByTestId("code-user-more")[0]);
    expect(screen.queryByTestId("code-user-rewind-ask")).toBeNull();
    expect(screen.getByTestId("code-user-rewind").textContent).toBe(REWIND_TO_HERE);
  });

  it("a tool with no conversation rewind still offers the files, and nothing else", () => {
    const facts = decodeBranch({
      branch: { fork: false, fork_at: false, rewind: false, at: [], files_at: [AT2], undo_rewind: false },
    })!;
    const a = actions(facts);
    render(<CodeConversation items={items()} folder="C:\\work" tool="grok" messageActions={a} />);
    expect(screen.getAllByTestId("code-user-more")).toHaveLength(1);
    fireEvent.click(screen.getByTestId("code-user-more"));
    expect(screen.getByTestId("code-user-rewind-ask").textContent).toBe(FILES_BACK_TO_HERE);
    expect(screen.queryByTestId("code-user-branch")).toBeNull();
    expect(screen.queryByTestId("code-user-rewind")).toBeNull();
  });

  it("while a reply runs the ask is not offered", () => {
    const facts = decodeBranch({ branch: FULL })!;
    const a = actions(facts, { busy: true });
    render(<CodeConversation items={items()} folder="C:\\work" tool="claude-code" messageActions={a} />);
    fireEvent.click(screen.getAllByTestId("code-user-more")[1]);
    const ask = screen.getByTestId("code-user-rewind-ask") as HTMLButtonElement;
    expect(ask.disabled).toBe(true);
    fireEvent.click(ask);
    expect(a.onRewindAsk).not.toHaveBeenCalled();
  });
});

describe("where the menu opens", () => {
  const rect = (left: number, right: number) => ({ left, right, top: 0, bottom: 0, width: right - left, height: 0 });

  const placed: HTMLElement[] = [];
  afterEach(() => {
    for (const row of placed.splice(0)) row.remove();
  });

  function place(buttonRight: number, rowLeft: number): HTMLElement {
    const row = document.createElement("div");
    row.setAttribute("data-testid", "code-user");
    const button = document.createElement("button");
    row.appendChild(button);
    document.body.appendChild(row);
    placed.push(row);
    row.getBoundingClientRect = () => rect(rowLeft, rowLeft + 900) as DOMRect;
    button.getBoundingClientRect = () => rect(buttonRight - 24, buttonRight) as DOMRect;
    return button;
  }

  it("beside a wide message (the ⋯ at the left edge) it opens rightward, so its words are not cut", () => {
    // Seen in the real window: the button's right edge 30px into the row, and
    // a 192px menu opening leftward was cut to "…rom here".
    expect(menuFitsLeftward(place(330, 300))).toBe(false);
    // With room on its left it opens leftward, as before.
    expect(menuFitsLeftward(place(700, 300))).toBe(true);
    expect(menuFitsLeftward(place(500, 300))).toBe(true);
    expect(menuFitsLeftward(place(499, 300))).toBe(false);
    expect(menuFitsLeftward(null)).toBe(false);
  });

  it("the open menu says which way it opened", () => {
    const facts = decodeBranch({ branch: FULL })!;
    render(<CodeConversation items={items()} folder="C:\work" tool="claude-code" messageActions={actions(facts)} />);
    fireEvent.click(screen.getAllByTestId("code-user-more")[1]);
    const menu = screen.getByTestId("code-user-menu");
    // No layout here (every measure is 0): rightward, the side that is never cut.
    expect(menu.getAttribute("data-opens")).toBe("right");
    expect(menu.className).toContain("left-0");
    expect(menu.className).not.toContain("right-0");
  });
});

describe("the preview", () => {
  const noop = () => {};
  const base = { busy: false, onClose: noop, onReadAgain: noop, onBoth: noop, onConversation: noop, onFiles: noop };

  it("lists what goes back, what moves, what is left and the commits that stay", () => {
    render(<RewindDialog {...base} plan={decodeRewindPlan(PLAN)} error={null} conversation />);
    expect(screen.getByTestId("code-rewind-back").textContent).toContain("2 files go back");
    expect(screen.getByTestId("code-rewind-back").textContent).toContain("one.py");
    expect(screen.getByTestId("code-rewind-new").textContent).toContain("1 file made since then moves to Iron Jarvis's trash");
    expect(screen.getByTestId("code-rewind-left").textContent).toContain("vendor/lib (it is a repository of its own)");
    expect(screen.getByTestId("code-rewind-commits").textContent).toContain("2 commits made since then stay in git");
    expect(screen.getByTestId("code-rewind-go").textContent).toBe(REWIND_BOTH);
    expect(screen.getByTestId("code-rewind-conversation").textContent).toBe(REWIND_CONVERSATION_ONLY);
    expect(screen.getByTestId("code-rewind-files")).toBeTruthy();
  });

  it("says how many more when the list is cut", () => {
    render(
      <RewindDialog {...base} plan={decodeRewindPlan({ ...PLAN, back_count: 240 })} error={null} conversation />,
    );
    expect(screen.getByTestId("code-rewind-back").textContent).toContain("240 files go back");
    expect(screen.getByTestId("code-rewind-back").textContent).toContain("and 238 more");
  });

  it("when a file is in the way, the files cannot go and only the conversation's press stays", () => {
    const plan = decodeRewindPlan({
      ...PLAN,
      can: false,
      problems: [{ rel: "a.py", why: "it cannot be written right now" }],
      problems_count: 1,
    });
    render(<RewindDialog {...base} plan={plan} error={null} conversation />);
    expect(screen.getByTestId("code-rewind-problems").textContent).toContain("a.py (it cannot be written right now)");
    expect(screen.queryByTestId("code-rewind-go")).toBeNull();
    expect(screen.queryByTestId("code-rewind-files")).toBeNull();
    expect(screen.getByTestId("code-rewind-conversation").textContent).toBe(REWIND_TO_HERE);
  });

  it("says why the files must wait (the daemon's own sentence)", () => {
    const plan = decodeRewindPlan({ ...PLAN, blocked: "Another session is working on a reply in this folder." });
    render(<RewindDialog {...base} plan={plan} error={null} conversation />);
    expect(screen.getByTestId("code-rewind-blocked").textContent).toContain("Another session is working");
    expect(screen.queryByTestId("code-rewind-go")).toBeNull();
  });

  it("with nothing to put back it says so and offers the conversation alone", () => {
    const plan = decodeRewindPlan({ ...PLAN, back: [], back_count: 0, new: [], new_count: 0, left: [], left_count: 0 });
    render(<RewindDialog {...base} plan={plan} error={null} conversation />);
    expect(screen.getByTestId("code-rewind-nothing").textContent).toBe(REWIND_NOTHING);
    expect(screen.queryByTestId("code-rewind-go")).toBeNull();
    expect(screen.getByTestId("code-rewind-conversation").textContent).toBe(REWIND_TO_HERE);
  });

  it("a tool with no conversation rewind has one press: put the files back", () => {
    const onFiles = vi.fn();
    render(
      <RewindDialog {...base} onFiles={onFiles} plan={decodeRewindPlan(PLAN)} error={null} conversation={false} />,
    );
    expect(screen.queryByTestId("code-rewind-conversation")).toBeNull();
    expect(screen.queryByTestId("code-rewind-files")).toBeNull();
    const go = screen.getByTestId("code-rewind-go");
    expect(go.textContent).toBe(REWIND_FILES_PRESS);
    fireEvent.click(go);
    expect(onFiles).toHaveBeenCalledWith(PLAN.id);
  });

  it("a preview that could not be read says why and can be read again", () => {
    const onReadAgain = vi.fn();
    render(
      <RewindDialog
        {...base}
        onReadAgain={onReadAgain}
        plan={null}
        error="This folder is not in a git repository, so its files cannot be put back from here."
        conversation
      />,
    );
    expect(screen.getByTestId("code-rewind-error").textContent).toContain("not in a git repository");
    fireEvent.click(screen.getByTestId("code-rewind-again"));
    expect(onReadAgain).toHaveBeenCalled();
    // The conversation can still be rewound by itself.
    expect(screen.getByTestId("code-rewind-conversation").textContent).toBe(REWIND_TO_HERE);
  });
});

describe("in the Code view", () => {
  async function openMenu(index: number) {
    await screen.findAllByTestId("code-user-more", {}, { timeout: 10_000 });
    fireEvent.click(screen.getAllByTestId("code-user-more")[index]);
  }

  it("the preview is read first and nothing is posted until a choice", async () => {
    open("a1", FULL);
    H.gets[PREVIEW("a1", AT2)] = PLAN;
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    await openMenu(1);
    fireEvent.click(screen.getByTestId("code-user-rewind-ask"));
    await screen.findByTestId("code-rewind-back", {}, { timeout: 10_000 });
    expect(H.got).toContain(PREVIEW("a1", AT2));
    expect(H.posts).toEqual([]);
    fireEvent.click(screen.getByTestId("code-rewind-cancel"));
    await waitFor(() => expect(screen.queryByTestId("code-rewind-dialog")).toBeNull());
    expect(H.posts).toEqual([]);
  });

  it("conversation and files sends the preview's id, then says what the daemon said", async () => {
    open("a1", FULL);
    H.gets[PREVIEW("a1", AT2)] = PLAN;
    H.answers["/build/app/sessions/a1/rewind"] = {
      session: sess("a1"),
      text: "second one",
      files: [],
      note: "The conversation now ends before that message. The folder's files are back: 2 put back.",
      restored: { back: 2, moved: 1, trash: "C:\\home\\trash\\x", words: "" },
    };
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    await openMenu(1);
    const before = H.gets["/build/app/sessions/a1"] as Record<string, unknown>;
    fireEvent.click(screen.getByTestId("code-user-rewind-ask"));
    H.gets["/build/app/sessions/a1"] = {
      ...before,
      events: EVENTS.slice(0, 3),
      branch: { ...FULL, at: [AT1], files_at: [AT1], undo_rewind: true },
    };
    fireEvent.click(await screen.findByTestId("code-rewind-go", {}, { timeout: 10_000 }));
    await waitFor(() => expect(H.posts.some((p) => p.path === "/build/app/sessions/a1/rewind")).toBe(true));
    expect(H.posts.find((p) => p.path === "/build/app/sessions/a1/rewind")!.body).toEqual({
      to: AT2,
      files: true,
      expect: PLAN.id,
    });
    await screen.findByDisplayValue("second one", {}, { timeout: 10_000 });
    expect(screen.getByTestId("code-info").textContent).toContain("2 put back");
    // The way back is on screen, and it takes two presses.
    const undo = () => screen.getByTestId("code-undo-rewind").querySelector("button")!;
    await waitFor(() => expect(undo().textContent).toBe(UNDO_REWIND));
    H.answers["/build/app/sessions/a1/rewind/undo"] = { ok: true, note: "The rewind's file changes were undone." };
    H.gets["/build/app/sessions/a1"] = {
      ...before,
      events: EVENTS.slice(0, 3),
      branch: { ...FULL, at: [AT1], files_at: [AT1], undo_rewind: false },
    };
    fireEvent.click(undo());
    expect(H.posts.some((p) => p.path.endsWith("/rewind/undo"))).toBe(false);
    expect(undo().textContent).toBe(UNDO_REWIND_CONFIRM);
    fireEvent.click(undo());
    await waitFor(() => expect(H.posts.some((p) => p.path === "/build/app/sessions/a1/rewind/undo")).toBe(true));
    await waitFor(() => expect(screen.queryByTestId("code-undo-rewind")).toBeNull());
    expect(screen.getByTestId("code-info").textContent).toContain("file changes were undone");
  });

  it("Conversation only sends no files, exactly as the old press did", async () => {
    open("a1", FULL);
    H.gets[PREVIEW("a1", AT2)] = PLAN;
    H.answers["/build/app/sessions/a1/rewind"] = { session: sess("a1"), text: "second one", files: [], note: "Done." };
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    await openMenu(1);
    fireEvent.click(screen.getByTestId("code-user-rewind-ask"));
    fireEvent.click(await screen.findByTestId("code-rewind-conversation", {}, { timeout: 10_000 }));
    await waitFor(() => expect(H.posts.some((p) => p.path === "/build/app/sessions/a1/rewind")).toBe(true));
    expect(H.posts.find((p) => p.path === "/build/app/sessions/a1/rewind")!.body).toEqual({ to: AT2 });
  });

  it("a tool with no conversation rewind puts the files back through its own route", async () => {
    const branch = { fork: false, fork_at: false, rewind: false, at: [], files_at: [AT1, AT2], undo_rewind: false };
    open("g1", branch, { harness: "grok" });
    H.gets[PREVIEW("g1", AT2)] = { ...PLAN, conversation: false };
    H.answers["/build/app/sessions/g1/rewind/files"] = {
      ok: true,
      note: "The folder's files are back. The conversation was not changed.",
    };
    render(<BuildCodeView initialSession="g1" onOpenedTerminal={() => {}} />);
    await openMenu(1);
    fireEvent.click(screen.getByTestId("code-user-rewind-ask"));
    const go = await screen.findByTestId("code-rewind-go", {}, { timeout: 10_000 });
    expect(go.textContent).toBe(REWIND_FILES_PRESS);
    fireEvent.click(go);
    await waitFor(() => expect(H.posts.some((p) => p.path === "/build/app/sessions/g1/rewind/files")).toBe(true));
    expect(H.posts.find((p) => p.path === "/build/app/sessions/g1/rewind/files")!.body).toEqual({
      to: AT2,
      expect: PLAN.id,
    });
    expect(H.posts.some((p) => p.path === "/build/app/sessions/g1/rewind")).toBe(false);
    await waitFor(() => expect(screen.getByTestId("code-info").textContent).toContain("conversation was not changed"));
    // Both messages are still in the conversation.
    expect(screen.getAllByTestId("code-user")).toHaveLength(2);
  });

  it("a refused rewind says the daemon's sentence", async () => {
    const { ApiError } = (await import("@/lib/api")) as unknown as {
      ApiError: new (m: string, s: number) => Error;
    };
    open("a1", FULL);
    H.gets[PREVIEW("a1", AT2)] = PLAN;
    H.answers["/build/app/sessions/a1/rewind"] = new ApiError(
      "The folder changed after this list was read, so nothing was changed. Read it again.",
      409,
    );
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    await openMenu(1);
    fireEvent.click(screen.getByTestId("code-user-rewind-ask"));
    fireEvent.click(await screen.findByTestId("code-rewind-go", {}, { timeout: 10_000 }));
    await waitFor(() => expect(screen.getByTestId("code-note").textContent).toContain("nothing was changed"));
    expect(screen.getAllByTestId("code-user")).toHaveLength(2);
  });

  it("an older Iron Jarvis (no files_at) reads no preview and keeps the two presses", async () => {
    open("a1", { fork: true, fork_at: true, rewind: true, at: [AT1, AT2] });
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    await openMenu(1);
    expect(screen.queryByTestId("code-user-rewind-ask")).toBeNull();
    expect(screen.getByTestId("code-user-rewind").textContent).toBe(REWIND_TO_HERE);
    expect(screen.queryByTestId("code-undo-rewind")).toBeNull();
    expect(H.got.some((p) => p.includes("/rewind/preview"))).toBe(false);
  });
});
