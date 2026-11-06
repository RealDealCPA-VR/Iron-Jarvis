/**
 * v1.441.0 — the review's fixes to Build › Code's v1.439.0 parity pieces:
 *
 *   D1. "Export as Markdown" writes the WHOLE conversation: the pages before
 *       what the pane holds are read first (the "Load earlier" route); a part
 *       the record trimmed, or one that could not be read, is SAID under the
 *       title — never a file that silently starts part way through;
 *   D2. the export is offered only once the session was read, and an empty
 *       session is refused in words (no empty file);
 *   D3. a plan card whose allow leaves the session in Full access is never
 *       approved by Enter, and the key hint names the button Enter presses;
 *   D4. "Copy reply" copies the whole reply (every finished text of the turn,
 *       across its tool steps), shown once per reply, never on a helper's rows.
 */

import React from "react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const H = vi.hoisted(() => ({
  gets: {} as Record<string, unknown>,
  failGets: new Set<string>(),
  getLog: [] as string[],
  posts: [] as { path: string; body: unknown }[],
  copies: [] as string[],
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
      H.getLog.push(path);
      if (H.failGets.has(path)) return Promise.reject(new FakeApiError("Iron Jarvis isn't responding", 0));
      const r = H.gets[path];
      if (r === undefined) return Promise.reject(new FakeApiError(`unmocked GET ${path}`, 404));
      return Promise.resolve(r);
    },
    post: (path: string, body?: unknown) => {
      H.posts.push({ path, body });
      return Promise.resolve({});
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

vi.mock("@/components/settings/MaintenanceTools", () => ({
  copyPlain: (text: string) => {
    H.copies.push(text);
    return Promise.resolve("copied");
  },
}));

import { BuildCodeView } from "@/components/buildCode/BuildCodeView";
import { CodeConversation, replyCopyTexts } from "@/components/buildCode/CodeConversation";
import {
  codeAskKeyHint,
  CODE_CLICK_TO_ALLOW_HINT,
  conversationMarkdown,
  decodeCodeEvent,
  enterAllowsCodeAsk,
  EXPORT_NOTHING,
  exportGapWords,
  type CodeEvent,
  type ConvItem,
} from "@/lib/buildApp";
import { collectAllEvents, eventsPagePath, type EventsPage } from "@/lib/buildAppHistory";

const FIX = join(process.cwd(), "__tests__", "fixtures", "build-app-events-v1409.json");
const SC = (JSON.parse(readFileSync(FIX, "utf8")) as { scenarios: Record<string, Record<string, unknown>[]> }).scenarios;

const TOOLS = {
  harnesses: [
    {
      id: "claude-code",
      label: "Claude Code",
      installed: true,
      signed_in: true,
      modes: [
        { id: "ask", label: "Ask first", dangerous: false },
        { id: "plan", label: "Plan", dangerous: false },
        { id: "full", label: "Full access", dangerous: true },
      ],
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
  mode: "plan",
  harness_session_id: "",
  created_at: "2026-11-06T09:00:00Z",
  updated_at: "2026-11-06T09:00:00Z",
  archived_at: null,
  last_error: "",
  ...over,
});

const SESSIONS = "/build/app/sessions";

function open(id: string, view: Record<string, unknown> | null, others: string[] = []) {
  H.gets = {
    "/build/app/harnesses": TOOLS,
    [SESSIONS]: { sessions: [sess(id), ...others.map((o) => sess(o))] },
    "/editor/options": { editors: [], preferred: "" },
  };
  if (view) H.gets[`${SESSIONS}/${id}`] = { session: sess(id), running: null, pending: [], ...view };
}

const decoded = (rows: Record<string, unknown>[]) =>
  rows.map((r) => decodeCodeEvent(r)).filter((e): e is CodeEvent => e !== null);

/** Catch the file the export hands the browser. */
function catchDownload() {
  const got: { blob: Blob | null; name: string } = { blob: null, name: "" };
  const created = vi.fn((b: Blob) => {
    got.blob = b;
    return "blob:x";
  });
  Object.defineProperty(URL, "createObjectURL", { configurable: true, value: created });
  Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: () => {} });
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
    got.name = this.download;
  });
  const text = () =>
    new Promise<string>((res) => {
      const r = new FileReader();
      r.onload = () => res(String(r.result));
      r.readAsText(got.blob as Blob);
    });
  return { got, created, text };
}

beforeEach(() => {
  H.posts = [];
  H.copies = [];
  H.getLog = [];
  H.failGets = new Set();
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

/* ==================================================== D1. the whole record */

const EARLY: Record<string, unknown>[] = [
  { type: "user", at: "2026-11-06T08:00:00Z", text: "the very first question" },
  { type: "text_done", at: "2026-11-06T08:00:05Z", item: "m1:0", text: "the very first answer" },
];
const LATE: Record<string, unknown>[] = [
  { type: "user", at: "2026-11-06T09:00:00Z", text: "a later question" },
  { type: "text_done", at: "2026-11-06T09:00:05Z", item: "m2:0", text: "a later answer" },
];

describe("D1: Export as Markdown reads the whole conversation", () => {
  it("collectAllEvents reads every page before what is held, oldest first", async () => {
    const pages: Record<string, EventsPage> = {
      c2: { events: decoded(LATE.slice(0, 1)), older: "c1", earlier: 2 },
      c1: { events: decoded(EARLY), older: null, earlier: 0 },
    };
    const asked: string[] = [];
    const got = await collectAllEvents(decoded(LATE.slice(1)), "c2", async (c) => {
      asked.push(c);
      return pages[c];
    });
    expect(asked).toEqual(["c2", "c1"]);
    expect(got.unread).toBe(false);
    expect(got.events.map((e) => (e as { text?: string }).text)).toEqual([
      "the very first question",
      "the very first answer",
      "a later question",
      "a later answer",
    ]);
  });

  it("a page that cannot be read, moves nothing, or runs past the bound is said, never silent", async () => {
    const held = decoded(LATE);
    expect((await collectAllEvents(held, "c1", async () => Promise.reject(new Error("x")))).unread).toBe(true);
    expect((await collectAllEvents(held, "c1", async () => ({ events: [], older: "c0", earlier: 1 }))).unread).toBe(true);
    expect((await collectAllEvents(held, "c1", async () => ({ events: decoded(EARLY), older: "c1", earlier: 1 }))).unread).toBe(true);
    let n = 0;
    const endless = await collectAllEvents(held, "c0", async () => ({ events: decoded(EARLY), older: `c${++n}`, earlier: 1 }), 3);
    expect(endless.unread).toBe(true);
    expect(n).toBe(3);
    expect((await collectAllEvents(held, null, async () => Promise.reject(new Error("never")))).unread).toBe(false);
  });

  it("the file holds the earlier pages too, and says what the record trimmed under the title", async () => {
    const dl = catchDownload();
    open("a1", { events: LATE, older: "s777", events_total: 4, dropped: 3 }, ["b2"]);
    H.gets[eventsPagePath(SESSIONS, "a1", "s777", 2000)] = { events: EARLY, older: null, earlier: 0 };
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    await screen.findAllByTestId("code-text", {}, { timeout: 10_000 });
    fireEvent.click(await screen.findByTestId("code-row-more-a1"));
    fireEvent.click(await screen.findByTestId("code-row-export-a1"));
    await waitFor(() => expect(dl.created).toHaveBeenCalledTimes(1));
    const md = await dl.text();
    expect(md).toBe(
      conversationMarkdown("Session a1", decoded([...EARLY, ...LATE]), "Claude Code", exportGapWords(3, false)),
    );
    const lines = md.split("\n");
    expect(lines[0]).toBe("# Session a1");
    expect(lines[2]).toBe("_3 older steps were trimmed from this session's record and are not in this file._");
    expect(md.indexOf("the very first question")).toBeLessThan(md.indexOf("a later question"));
    expect(H.getLog).toContain(eventsPagePath(SESSIONS, "a1", "s777", 2000));
  });

  it("an earlier part that cannot be read is said under the title", async () => {
    const dl = catchDownload();
    open("a1", { events: LATE, older: "s777", events_total: 4 }, []);
    H.failGets.add(eventsPagePath(SESSIONS, "a1", "s777", 2000));
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    await screen.findAllByTestId("code-text", {}, { timeout: 10_000 });
    fireEvent.click(await screen.findByTestId("code-row-more-a1"));
    fireEvent.click(await screen.findByTestId("code-row-export-a1"));
    await waitFor(() => expect(dl.created).toHaveBeenCalledTimes(1));
    const md = await dl.text();
    expect(md.split("\n")[2]).toBe("_The earlier part of this session could not be read, so it is not in this file._");
    expect(md).toContain("a later answer");
  });
});

/* ======================================================= D2. offered when */

describe("D2: the export waits for the session to be read, and refuses nothing", () => {
  it("a session whose read failed offers no export", async () => {
    open("a1", null, []);
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    fireEvent.click(await screen.findByTestId("code-row-more-a1", {}, { timeout: 10_000 }));
    await screen.findByTestId("code-row-rename-a1");
    await waitFor(() => expect(H.getLog).toContain(`${SESSIONS}/a1`));
    expect(screen.queryByTestId("code-row-export-a1")).toBeNull();
  });

  it("an empty session is refused in words, with no file", async () => {
    const dl = catchDownload();
    open("a1", { events: [] }, []);
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    await waitFor(() => expect(H.getLog).toContain(`${SESSIONS}/a1`));
    fireEvent.click(await screen.findByTestId("code-row-more-a1", {}, { timeout: 10_000 }));
    fireEvent.click(await screen.findByTestId("code-row-export-a1", {}, { timeout: 10_000 }));
    expect((await screen.findByTestId("code-row-note")).textContent).toBe(EXPORT_NOTHING);
    expect(dl.created).not.toHaveBeenCalled();
  });
});

/* ===================================================== D3. Enter on a plan */

const planAsk = (over: Record<string, unknown>) =>
  decodeCodeEvent({ type: "approval", at: "x", request_id: "r1", kind: "plan", title: "Start on the plan",
    plan: "1. Do it", can_session: true, once_only: false, ...over }) as Extract<CodeEvent, { type: "approval" }>;

describe("D3: Enter never approves a plan into Full access", () => {
  it("a plan whose allow leaves Full access takes a click; the hint names the button", () => {
    const full = planAsk({ then_mode: "full" });
    expect(enterAllowsCodeAsk(full, false)).toBe(false);
    expect(codeAskKeyHint(full, false)).toBe("Click “Approve, then full access” to approve the plan · Esc to deny");
    const sets = planAsk({ then_mode: "ask", sets_mode: "full" });
    expect(enterAllowsCodeAsk(sets, false)).toBe(false);
  });

  it("any other plan: Enter allowed, and the hint says which button it presses", () => {
    const ask = planAsk({ then_mode: "ask" });
    expect(enterAllowsCodeAsk(ask, false)).toBe(true);
    expect(codeAskKeyHint(ask, false)).toBe("Enter to approve, then ask first · Esc to deny");
    const same = planAsk({ then_mode: "edits", sets_mode: "edits" });
    expect(codeAskKeyHint(same, false)).toBe("Enter to approve the plan · Esc to deny");
    const plain = decodeCodeEvent({ type: "approval", at: "x", request_id: "r2", kind: "edit", title: "Change a.txt" });
    expect(codeAskKeyHint(plain as Extract<CodeEvent, { type: "approval" }>, false)).toBe(
      "Enter to allow once · Esc to deny",
    );
    expect(codeAskKeyHint(plain as Extract<CodeEvent, { type: "approval" }>, true)).toBe(CODE_CLICK_TO_ALLOW_HINT);
  });

  it("through the view: Enter on a plan into Full access sends nothing", async () => {
    const rows = SC.claude_plan_deny_with_words.slice(
      0,
      SC.claude_plan_deny_with_words.findIndex((e) => e.type === "approval") + 1,
    ).map((r) => (r.type === "approval" ? { ...r, then_mode: "full" } : r));
    open("p1", { events: rows, running: "turn_1" }, []);
    render(<BuildCodeView initialSession="p1" onOpenedTerminal={() => {}} />);
    const card = await screen.findByTestId("code-ask", {}, { timeout: 10_000 });
    expect(within(card).getByTestId("code-ask-keys").textContent).toBe(
      "Click “Approve, then full access” to approve the plan · Esc to deny",
    );
    await act(async () => {
      await new Promise((r) => setTimeout(r, 600));
    });
    fireEvent.keyDown(document.activeElement ?? document.body, { key: "Enter" });
    await act(async () => {
      await new Promise((r) => setTimeout(r, 50));
    });
    expect(H.posts.filter((p) => p.path.endsWith("/answer"))).toEqual([]);
  });
});

/* ======================================================= D4. Copy reply */

describe("D4: Copy reply copies the whole reply, once per reply", () => {
  const items: ConvItem[] = [
    { kind: "user", key: "u1", text: "fix it", files: [] },
    { kind: "text", key: "t1", text: "First I will look.", done: true },
    {
      kind: "tool",
      key: "s1",
      tool: decodeCodeEvent({ type: "tool", at: "x", item: "i1", kind: "task", name: "Task", title: "Helper", status: "done" }) as Extract<CodeEvent, { type: "tool" }>,
      children: [{ kind: "text", key: "h1", text: "the helper's own words", done: true }],
    },
    { kind: "text", key: "t2", text: "Done: the file is fixed.", done: true },
    { kind: "user", key: "u2", text: "thanks", files: [] },
    { kind: "text", key: "t3", text: "You're welcome.", done: true },
    { kind: "text", key: "t4", text: "still writing", done: false },
  ];

  it("one copy per reply, on its last finished text, holding every finished text of the turn", () => {
    const got = replyCopyTexts(items);
    expect([...got.entries()]).toEqual([
      ["t2", "First I will look.\n\nDone: the file is fixed."],
      ["t3", "You're welcome."],
    ]);
  });

  it("a message Iron Jarvis sent to carry the reply on is not a new reply", () => {
    const carried: ConvItem[] = [
      { kind: "user", key: "u1", text: "go", files: [] },
      { kind: "text", key: "t1", text: "part one", done: true },
      { kind: "user", key: "c1", text: "carry on", files: [], carry: { why: "restart" } as never },
      { kind: "text", key: "t2", text: "part two", done: true },
    ];
    expect([...replyCopyTexts(carried).entries()]).toEqual([["t2", "part one\n\npart two"]]);
  });

  it("through the view: one button per reply, it copies the whole reply, and a helper's rows have none", async () => {
    render(<CodeConversation items={items} folder="C:\\work" tool="claude-code" />);
    expect(screen.getAllByTestId("code-reply-copy")).toHaveLength(2);
    fireEvent.click(screen.getAllByTestId("code-reply-copy")[0]);
    await waitFor(() => expect(H.copies).toEqual(["First I will look.\n\nDone: the file is fixed."]));
    // Open the helper: its own finished text draws no copy button.
    fireEvent.click(screen.getByTestId("work-row"));
    const kids = await screen.findByTestId("code-helper-children");
    expect(within(kids).getByText("the helper's own words")).toBeTruthy();
    expect(within(kids).queryByTestId("code-reply-copy")).toBeNull();
    expect(screen.getAllByTestId("code-reply-copy")).toHaveLength(2);
  });
});
