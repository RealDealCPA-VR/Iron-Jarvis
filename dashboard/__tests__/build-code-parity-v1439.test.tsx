/**
 * v1.439.0 — Build › Code parity with the coding tools' own screens:
 *
 *   1. a quiet copy icon under each FINISHED reply (hover / keyboard focus,
 *      always on touch) copies the reply's text through the app's own copy
 *      path (copyPlain) and says "Copied"; Pi's /copy note names it;
 *   2. "Export as Markdown" in the session row's ⋯ menu, built from the
 *      events this window already holds (messages, replies, step titles —
 *      never a step's output), saved as <title>.md;
 *   3. Claude Code's plan card names its two allows by the mode each leaves
 *      the session in ("Approve, then ask first" / "Approve and edit
 *      freely"), sending the same answers as before.
 *
 * Fed the events the REAL daemon sends
 * (__tests__/fixtures/build-app-events-v1409.json, IJ_REGEN_BUILD_APP=1).
 */

import React from "react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const H = vi.hoisted(() => ({
  gets: {} as Record<string, unknown>,
  posts: [] as { path: string; body: unknown }[],
  copies: [] as string[],
  copyAnswer: "copied" as "copied" | "unavailable",
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
    return Promise.resolve(H.copyAnswer);
  },
}));

import { BuildCodeView, EXPORT_MARKDOWN } from "@/components/buildCode/BuildCodeView";
import {
  CodeConversation,
  COPIED_WORDS,
  COPY_FAILED_WORDS,
  COPY_REPLY_LABEL,
} from "@/components/buildCode/CodeConversation";
import {
  conversationMarkdown,
  decodeCodeEvent,
  exportFileName,
  foldConversation,
  toolStepWords,
  planAllowLabels,
  planApproveWords,
  type CodeEvent,
  type ConvItem,
} from "@/lib/buildApp";
import { ALLOW_ONCE } from "@/lib/approvalWords";

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

function open(id: string, events: Record<string, unknown>[], running: unknown, others: string[] = []) {
  H.gets = {
    "/build/app/harnesses": TOOLS,
    "/build/app/sessions": { sessions: [sess(id), ...others.map((o) => sess(o))] },
    "/editor/options": { editors: [], preferred: "" },
    [`/build/app/sessions/${id}`]: { session: sess(id), events, running, pending: [] },
  };
}

const upToAsk = (name: string) => SC[name].slice(0, SC[name].findIndex((e) => e.type === "approval") + 1);
const decoded = (rows: Record<string, unknown>[]) =>
  rows.map((r) => decodeCodeEvent(r)).filter((e): e is CodeEvent => e !== null);

beforeEach(() => {
  H.posts = [];
  H.copies = [];
  H.copyAnswer = "copied";
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

/* ====================================================== 1. copy a reply */

describe("copy a reply", () => {
  const items: ConvItem[] = [
    { kind: "user", key: "u", text: "hi", files: [] },
    { kind: "text", key: "t1", text: "**Done.** The file is `a.txt`.", done: true },
    { kind: "text", key: "t2", text: "still writing", done: false },
  ];

  it("a finished reply has a quiet copy icon; a reply still being written has none", () => {
    render(<CodeConversation items={items} folder="C:\\work" tool="claude-code" />);
    const icons = screen.getAllByTestId("code-reply-copy");
    expect(icons).toHaveLength(1);
    expect(icons[0].getAttribute("aria-label")).toBe(COPY_REPLY_LABEL);
    expect(icons[0].getAttribute("title")).toBe(COPY_REPLY_LABEL);
    // Shown on hover and keyboard focus, always on a touch screen.
    const row = screen.getByTestId("code-reply-actions");
    expect(row.className).toContain("opacity-0");
    expect(row.className).toContain("group-hover/reply:opacity-100");
    expect(row.className).toContain("focus-within:opacity-100");
    expect(row.className).toContain("[@media(hover:none)]:opacity-100");
    expect(row.closest(".group\\/reply")).not.toBeNull();
  });

  it("the press copies the reply's text through copyPlain and says Copied, briefly", async () => {
    render(<CodeConversation items={items} folder="C:\\work" tool="claude-code" />);
    fireEvent.click(screen.getByTestId("code-reply-copy"));
    await waitFor(() => expect(screen.getByTestId("code-reply-copied").textContent).toBe(COPIED_WORDS));
    expect(H.copies).toEqual(["**Done.** The file is `a.txt`."]);
    expect(screen.getByTestId("code-reply-copied").getAttribute("role")).toBe("status");
    // While it says so, the row stays on screen.
    expect(screen.getByTestId("code-reply-actions").className).not.toContain("opacity-0");
    await waitFor(() => expect(screen.getByTestId("code-reply-copied").textContent).toBe(""), { timeout: 4_000 });
  });

  it("when nothing could take the text it says so", async () => {
    H.copyAnswer = "unavailable";
    render(<CodeConversation items={items} folder="C:\\work" tool="claude-code" />);
    fireEvent.click(screen.getByTestId("code-reply-copy"));
    await waitFor(() => expect(screen.getByTestId("code-reply-copied").textContent).toBe(COPY_FAILED_WORDS));
  });

  it("Pi's /copy note names the copy icon that is really there", () => {
    const src = readFileSync(join(process.cwd(), "..", "src", "iron_jarvis", "hosted", "pi.py"), "utf8");
    const line = src.split(/\r?\n/).find((l) => l.trim().startsWith('"copy":')) ?? "";
    expect(line).toContain("copy icon");
    expect(line).toContain(`(${COPY_REPLY_LABEL})`);
    expect(line).not.toContain("copy button under a reply");
    const pi = readFileSync(join(process.cwd(), "__tests__", "fixtures", "build-app-pi-models-v1429.json"), "utf8");
    expect(pi).toContain(`(${COPY_REPLY_LABEL})`);
  });
});

/* ============================================== 2. export as Markdown */

describe("export as Markdown", () => {
  it("the Markdown holds the messages, the replies and the step titles — never a step's output", () => {
    const evs = decoded(SC.claude_tool_approval_edit);
    const md = conversationMarkdown("Fix the build", evs, "Claude Code");
    expect(md.startsWith("# Fix the build\n\n## You\n\n")).toBe(true);
    const user = evs.find((e) => e.type === "user") as Extract<CodeEvent, { type: "user" }>;
    expect(md).toContain(user.text.trim());
    expect(md).toContain("## Claude Code");
    const tools = evs.filter((e): e is Extract<CodeEvent, { type: "tool" }> => e.type === "tool");
    expect(tools.length).toBeGreaterThan(0);
    // Each step once, by its newest title (the conversation's own fold).
    const steps = foldConversation(evs).items.filter((i): i is Extract<ConvItem, { kind: "tool" }> => i.kind === "tool");
    expect(steps.length).toBeGreaterThan(0);
    for (const s of steps) expect(md).toContain(`- ${toolStepWords(s.tool).replace(/\s+/g, " ").trim()}`);
    expect(md.split("\n").filter((l) => l.startsWith("- ")).length).toBe(steps.length);
    for (const t of tools) {
      for (const piece of t.output.split(/\r?\n/).map((l) => l.trim()).filter((l) => l.length > 12)) {
        expect(md).not.toContain(piece);
      }
    }
    const reply = evs.filter((e) => e.type === "text_done").at(-1) as Extract<CodeEvent, { type: "text_done" }> | undefined;
    if (reply?.text.trim()) expect(md).toContain(reply.text.trim());
    expect(md.endsWith("\n")).toBe(true);
  });

  it("the file name is the title, safe on every file system", () => {
    expect(exportFileName("Fix: the a/b build?")).toBe("Fix the a b build.md");
    expect(exportFileName('  <>:"|*  ')).toBe("coding-session.md");
    expect(exportFileName("Notes...")).toBe("Notes.md");
    expect(exportFileName("x".repeat(200))).toBe(`${"x".repeat(80)}.md`);
  });

  it("the open session's ⋯ menu saves <title>.md from the events already loaded", async () => {
    let saved: Blob | null = null;
    let name = "";
    const created = vi.fn((b: Blob) => {
      saved = b;
      return "blob:x";
    });
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: created });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: () => {} });
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
      name = this.download;
    });
    open("a1", SC.claude_tool_approval_edit, null, ["b2"]);
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    await screen.findAllByTestId("code-text", {}, { timeout: 10_000 });
    const gets = Object.keys(H.gets).length;
    fireEvent.click(await screen.findByTestId("code-row-more-a1"));
    const item = await screen.findByTestId("code-row-export-a1");
    expect(item.textContent).toBe(EXPORT_MARKDOWN);
    expect(item.getAttribute("role")).toBe("menuitem");
    fireEvent.click(item);
    expect(created).toHaveBeenCalledTimes(1);
    expect(name).toBe("Session a1.md");
    const text = await new Promise<string>((res) => {
      const r = new FileReader();
      r.onload = () => res(String(r.result));
      r.readAsText(saved as unknown as Blob);
    });
    expect(text).toBe(conversationMarkdown("Session a1", decoded(SC.claude_tool_approval_edit), "Claude Code"));
    expect((saved as unknown as Blob).type).toContain("text/markdown");
    // Nothing new was read for it, and the menu closed.
    expect(Object.keys(H.gets).length).toBe(gets);
    expect(screen.queryByTestId("code-row-export-a1")).toBeNull();
  });

  it("a session not open in front offers no export (its events are not loaded)", async () => {
    open("a1", SC.claude_tool_approval_edit, null, ["b2"]);
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    await screen.findAllByTestId("code-text", {}, { timeout: 10_000 });
    fireEvent.click(await screen.findByTestId("code-row-more-b2"));
    await screen.findByTestId("code-row-rename-b2");
    expect(screen.queryByTestId("code-row-export-b2")).toBeNull();
  });
});

/* ============================================ 3. the plan card's words */

describe("Claude Code's plan card names the mode each allow leaves", () => {
  it("the daemon's plan card says where Allow once leaves the session", () => {
    const plan = decodeCodeEvent(upToAsk("claude_plan_deny_with_words").at(-1)) as Extract<CodeEvent, { type: "approval" }>;
    expect(plan.thenMode).toBe("ask");
    expect(plan.setsMode).toBeUndefined();
  });

  it("the words follow the modes; an unknown mode keeps the usual words", () => {
    expect(planApproveWords("ask")).toBe("Approve, then ask first");
    expect(planApproveWords("edits")).toBe("Approve and edit freely");
    expect(planApproveWords("plan")).toBe("");
    expect(planApproveWords(undefined)).toBe("");
    expect(planAllowLabels({ thenMode: "ask", setsMode: "edits" })).toEqual({
      once: "Approve, then ask first",
      session: "Approve and edit freely",
    });
    // Both leave the same mode: Allow once only says it approves.
    expect(planAllowLabels({ thenMode: "edits", setsMode: "edits" }).once).toBe("Approve the plan");
    const bogus = decodeCodeEvent({ type: "approval", at: "x", request_id: "r", then_mode: "plan", sets_mode: "bogus" });
    expect(bogus).not.toHaveProperty("thenMode");
    expect(bogus).not.toHaveProperty("setsMode");
  });

  it("through the view: Allow once reads 'Approve, then ask first' and still sends allow", async () => {
    open("p1", upToAsk("claude_plan_deny_with_words"), "turn_1");
    render(<BuildCodeView initialSession="p1" onOpenedTerminal={() => {}} />);
    const card = await screen.findByTestId("code-ask", {}, { timeout: 10_000 });
    const allow = within(card).getByTestId("code-ask-allow");
    expect(allow.textContent).toBe("Approve, then ask first");
    fireEvent.click(allow);
    await waitFor(() =>
      expect(H.posts.filter((p) => p.path.endsWith("/answer")).map((p) => p.body)).toEqual([
        { request_id: "ask-1", decision: "allow" },
      ]),
    );
  });

  it("with a session-wide allow that switches mode, both buttons say where they leave it", async () => {
    const rows = upToAsk("claude_plan_deny_with_words").map((r) =>
      r.type === "approval" ? { ...r, can_session: true, once_only: false, sets_mode: "edits" } : r,
    );
    open("p2", rows, "turn_1");
    render(<BuildCodeView initialSession="p2" onOpenedTerminal={() => {}} />);
    const card = await screen.findByTestId("code-ask", {}, { timeout: 10_000 });
    expect(within(card).getByTestId("code-ask-allow").textContent).toBe("Approve, then ask first");
    const session = within(card).getByTestId("code-ask-allow-session");
    expect(session.textContent).toBe("Approve and edit freely");
    fireEvent.click(session);
    await waitFor(() =>
      expect(H.posts.filter((p) => p.path.endsWith("/answer")).map((p) => p.body)).toEqual([
        { request_id: "ask-1", decision: "allow_session" },
      ]),
    );
  });

  it("any other card keeps Allow once and Allow for this session", async () => {
    const rows = upToAsk("claude_plan_deny_with_words").map((r) => {
      if (r.type !== "approval") return r;
      const { then_mode: _t, ...rest } = r as Record<string, unknown>;
      void _t;
      return { ...rest, tool: "Edit", title: "Change a.txt", plan: "", can_session: true, once_only: false };
    });
    open("e1", rows, "turn_1");
    render(<BuildCodeView initialSession="e1" onOpenedTerminal={() => {}} />);
    const card = await screen.findByTestId("code-ask", {}, { timeout: 10_000 });
    expect(within(card).getByTestId("code-ask-allow").textContent).toBe(ALLOW_ONCE);
    expect(within(card).getByTestId("code-ask-allow-session").textContent).toBe("Allow for this session");
  });
});

void act;
