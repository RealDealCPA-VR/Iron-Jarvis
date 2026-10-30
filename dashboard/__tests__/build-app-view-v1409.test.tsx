/**
 * v1.409.0 — Build's Code view (components/buildCode/BuildCodeView): the REAL
 * Claude Code / Codex, driven from a GUI. Driven through the real view, its
 * conversation, its question card and lib/buildApp; only the network
 * (lib/api), the stream reader (lib/useChatStream's streamSSE) and the folder
 * tree are stand-ins.
 *
 *   - the sessions list: grouped by folder, each row its tool and account,
 *     a session found in the tool's own saved sessions says so;
 *   - New session: a tool not installed / signed out is greyed with the
 *     reason; Send sends the choice; Full access needs two presses and
 *     `confirm: true` (v1.412.0: moved on purpose to the start composer —
 *     the composer is there at once, no form first; its own pins are
 *     build-code-start-v1412.test.tsx);
 *   - every event kind of THE CONTRACT draws (the contract fixture — the
 *     daemon's real one when present, else the hand-written draft);
 *   - approvals and questions answered from the composer's place; Enter
 *     allows only an ordinary card, never in Full access or a once-only one;
 *     Esc denies;
 *   - Stop interrupts; a running turn is followed again on open (no
 *     duplicate question); Open in terminal hands the terminal on.
 */

import React from "react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, createEvent, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const H = vi.hoisted(() => ({
  gets: {} as Record<string, unknown>,
  getLog: [] as string[],
  posts: [] as { path: string; body: unknown }[],
  patches: [] as { path: string; body: unknown }[],
  postReply: {} as Record<string, unknown>,
  postFail: {} as Record<string, { status: number; message: string }>,
  streams: [] as { path: string; body: unknown }[],
  /** Frames each stream sends (in order of the streams opened); a
   *  `{yield}` entry is what the reader itself yields (a refusal, a drop). */
  scripts: [] as ({ event: string; data: Record<string, unknown> } | { yield: Record<string, unknown> })[][],
  /** The newest stream's tap: a test sends a frame "later" through it. */
  lastWatch: null as null | { onFrame?: (event: string, data: Record<string, unknown>) => void },
  /** While set, an open stream waits here before it ends. */
  hold: null as Promise<void> | null,
  /** >0: each frame arrives in its own macrotask after this many ms (a real
   *  stream arrives in network chunks; v1.409.2 — the same-tick delivery
   *  let React fold a doubled question away and hid it). */
  frameMs: 0,
  release: null as (() => void) | null,
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
      const r = H.gets[path];
      if (r === undefined) return Promise.reject(new FakeApiError(`unmocked GET ${path}`, 404));
      return Promise.resolve(typeof r === "function" ? (r as () => unknown)() : r);
    },
    post: (path: string, body?: unknown) => {
      H.posts.push({ path, body });
      const fail = H.postFail[path];
      if (fail) return Promise.reject(new FakeApiError(fail.message, fail.status));
      return Promise.resolve(H.postReply[path] ?? {});
    },
    patch: (path: string, body?: unknown) => {
      H.patches.push({ path, body });
      return Promise.resolve(H.postReply[`PATCH ${path}`] ?? {});
    },
    del: () => Promise.resolve({}),
  };
});

vi.mock("@/lib/useChatStream", () => ({
  // The stream reader: every frame goes through the caller's `onFrame` tap,
  // exactly as the real reader taps a raw frame before it decodes it.
  streamSSE: async function* (
    path: string,
    body: unknown,
    signal?: AbortSignal,
    watch?: { onFrame?: (event: string, data: Record<string, unknown>) => void },
  ) {
    H.streams.push({ path, body });
    H.lastWatch = watch ?? null;
    const frames = H.scripts.shift() ?? [];
    for (const f of frames) {
      if (signal?.aborted) return;
      if ("yield" in f) {
        // The real reader ends its stream after every error it yields.
        yield f.yield;
        return;
      }
      watch?.onFrame?.(f.event, f.data);
      if (H.frameMs > 0) await new Promise((r) => setTimeout(r, H.frameMs));
      else await Promise.resolve();
    }
    if (H.hold) await H.hold;
  },
  useLiveText: (s: { text?: string }) => s?.text ?? "",
}));

vi.mock("@/components/terminal/DirectoryTree", () => ({
  DirectoryTree: ({ onSelect, pickTypedPath }: { onSelect: (p: string) => void; pickTypedPath?: boolean }) => (
    <button
      type="button"
      data-testid="stub-pick-folder"
      data-pick-typed={pickTypedPath ? "true" : "false"}
      onClick={() => onSelect("C:\\work\\shop")}
    >
      pick
    </button>
  ),
}));

import { BuildCodeView, FULL_ARM_GUARD_MS, FULL_DISARM_MS } from "@/components/buildCode/BuildCodeView";
import { codeRowDraws } from "@/components/buildCode/CodeConversation";
import {
  firstMessageTitle,
  accountStatusWords,
  cutForRejoin,
  modeLabel,
  codeAskKeyHint,
  decodeCodeEvent,
  enterAllowsCodeAsk,
  foldConversation,
  type CodeEvent,
} from "@/lib/buildApp";

/* ---- the contract fixture ---------------------------------------------- */

const FIX_DIR = join(process.cwd(), "__tests__", "fixtures");
// Written by the REAL daemon parser from recorded Claude Code / Codex output
// (tests/test_build_app_v1409.py, IJ_REGEN_BUILD_APP=1).
const REAL = join(FIX_DIR, "build-app-events-v1409.json");
const fixture = JSON.parse(readFileSync(REAL, "utf8")) as {
  scenarios: Record<string, Record<string, unknown>[]>;
};
const SC = fixture.scenarios;

/* ---- the daemon --------------------------------------------------------- */

const TOOLS = {
  harnesses: [
  {
    id: "claude-code",
    label: "Claude Code",
    installed: true,
    signed_in: true,
    version: "2.1.311",
    modes: [
      { id: "ask", label: "Ask first", dangerous: false },
      { id: "edits", label: "Auto edits", dangerous: false },
      { id: "plan", label: "Plan", dangerous: false },
      { id: "full", label: "Full access", dangerous: true },
    ],
    hidden_modes: [],
    models: [
      { id: "", label: "Claude Code's default" },
      { id: "claude-opus-5-5", label: "Opus 5.5" },
    ],
    efforts: ["low", "medium", "high"],
    accounts: [{ id: "prof_work", name: "Work", status: "ready" }],
    commands: [
      { name: "review", description: "Review the changes" },
      { name: "compact", description: "Summarize the conversation" },
    ],
  },
  {
    id: "codex",
    label: "Codex",
    installed: false,
    signed_in: null,
    version: null,
    modes: [
      { id: "ask", label: "Ask first", dangerous: false },
      { id: "full", label: "Full access", dangerous: true },
    ],
    hidden_modes: [
      { id: "plan", label: "Plan", reason: "Codex has no plan mode." },
      { id: "edits", label: "Auto edits", reason: "Codex cannot run commands in its sandbox on Windows." },
    ],
    models: [{ id: "", label: "Codex's default" }],
    efforts: [],
    accounts: [],
    commands: [],
  },
  { id: "grok", label: "Grok", installed: true, signed_in: false, version: null, modes: [], models: [], efforts: [], accounts: [] },
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
  harness_session_id: "",
  created_at: "2026-10-29T09:00:00Z",
  updated_at: "2026-10-29T09:00:00Z",
  archived_at: null,
  last_error: "",
  ...over,
});

function seed(sessions: Record<string, unknown>[] = []) {
  H.gets = {
    "/build/app/harnesses": TOOLS,
    "/build/app/sessions": { sessions },
    "/editor/options": { editors: [], preferred: "" },
  };
}

function openSession(id: string, view: Record<string, unknown>) {
  H.gets[`/build/app/sessions/${id}`] = view;
}

/** Frames from events (SSE name = the event's own type). */
const frames = (events: Record<string, unknown>[]) => events.map((e) => ({ event: String(e.type), data: e }));

function holdStreams() {
  H.hold = new Promise<void>((res) => {
    H.release = res;
  });
}

beforeEach(() => {
  H.frameMs = 0;
  H.getLog = [];
  H.posts = [];
  H.patches = [];
  H.postReply = {};
  H.postFail = {};
  H.streams = [];
  H.scripts = [];
  H.hold = null;
  H.release = null;
  H.lastWatch = null;
  seed();
});

afterEach(() => {
  H.release?.();
  cleanup();
});

const ARM_MS = 450; // the card's key guard (DOCK_KEY_ARM_MS = 400) plus slack

const pause = (ms: number) => act(() => new Promise<void>((r) => setTimeout(r, ms)));

/* ======================================================= the list */

describe("the sessions list", () => {
  it("groups by folder; every row names its tool and its account; a saved one says so; archived are hidden", async () => {
    seed([
      sess("a1", { account: "prof_work", updated_at: "2026-10-29T10:00:00Z" }),
      sess("a2", { harness: "codex", folder: "C:\\work\\api", updated_at: "2026-10-29T11:00:00Z" }),
      sess("a3", { source: "disk", folder: "c:/work/shop/", title: "From disk" }),
      sess("a4", { archived_at: "2026-10-29T12:00:00Z" }),
    ]);
    render(<BuildCodeView initialSession={null} onOpenedTerminal={() => {}} />);
    await screen.findByTestId("code-row-a1");
    const groups = screen.getAllByTestId("code-folder-group");
    expect(groups.map((g) => g.querySelector("h3")?.textContent)).toEqual(["api", "shop"]);
    // one folder, however it is spelled
    expect(within(groups[1]).getByTestId("code-row-a3")).toBeInTheDocument();
    expect(screen.getByTestId("code-row-line-a1").textContent).toBe("Claude Code · Work");
    expect(screen.getByTestId("code-row-line-a2").textContent).toBe("Codex · this PC's sign-in");
    expect(screen.getByTestId("code-row-line-a3").textContent).toContain("saved by Claude Code");
    expect(screen.getByTestId("code-row-a3").dataset.source).toBe("disk");
    expect(screen.queryByTestId("code-row-a4")).toBeNull();
  });

  it("a row's ⋯ offers Open in terminal and hands the terminal on", async () => {
    seed([sess("a1")]);
    H.postReply["/build/app/sessions/a1/open-terminal"] = { terminal_id: "t9" };
    const opened = vi.fn();
    render(<BuildCodeView initialSession={null} onOpenedTerminal={opened} />);
    fireEvent.click(await screen.findByTestId("code-row-more-a1"));
    fireEvent.click(screen.getByTestId("code-row-open-terminal-a1"));
    await waitFor(() => expect(opened).toHaveBeenCalledWith("t9"));
    expect(H.posts.map((p) => p.path)).toContain("/build/app/sessions/a1/open-terminal");
  });
});

/* ======================================================= New session */

describe("New session (v1.412.0: the start composer, moved on purpose)", () => {
  it("greys a tool that is not installed or signed out, with the reason", async () => {
    render(<BuildCodeView initialSession={null} onOpenedTerminal={() => {}} />);
    await waitFor(() => expect(screen.getByTestId("code-start-tool").textContent).toContain("Claude Code"));
    fireEvent.click(screen.getByTestId("code-start-tool"));
    const codex = await screen.findByTestId("code-start-tool-codex");
    expect(codex.dataset.available).toBe("false");
    expect(codex).toBeDisabled();
    expect(screen.getByTestId("code-start-tool-why-codex").textContent).toBe("Codex is not installed on this PC.");
    expect(screen.getByTestId("code-start-tool-why-grok").textContent).toContain("Grok is signed out.");
    const claude = screen.getByTestId("code-start-tool-claude-code");
    expect(claude.dataset.available).toBe("true");
    expect(claude.getAttribute("aria-checked")).toBe("true");
    // Nothing typed yet: Send waits.
    expect(screen.getByTestId("code-start-send")).toBeDisabled();
  });

  it("Send sends the tool, the folder and the mode, opens the session and sends the words", async () => {
    H.postReply["/build/app/sessions"] = sess("n1");
    openSession("n1", { session: sess("n1"), events: [], running: false, pending: [] });
    H.scripts.push([]);
    render(<BuildCodeView initialSession={null} onOpenedTerminal={() => {}} />);
    await waitFor(() => expect(screen.getByTestId("code-start-tool").textContent).toContain("Claude Code"));
    fireEvent.click(screen.getByTestId("code-start-folder-choose"));
    fireEvent.click(await screen.findByTestId("stub-pick-folder"));
    fireEvent.click(screen.getByTestId("code-start-folder-tree-use"));
    fireEvent.change(screen.getByTestId("code-start-box"), { target: { value: "fix the cart" } });
    fireEvent.click(screen.getByTestId("code-start-send"));
    await screen.findByTestId("code-session");
    const made = H.posts.find((p) => p.path === "/build/app/sessions");
    // One login (Work): no account question, the daemon decides.
    expect(made?.body).toEqual({ harness: "claude-code", folder: "C:\\work\\shop", mode: "ask" });
    await waitFor(() => expect(H.streams.map((s) => s.path)).toEqual(["/build/app/sessions/n1/turn"]));
    expect((H.streams[0].body as { text?: string }).text).toBe("fix the cart");
  });

  it("Full access takes two presses on the chip, and is sent with confirm: true", async () => {
    H.postReply["/build/app/sessions"] = sess("n2", { mode: "full" });
    openSession("n2", { session: sess("n2", { mode: "full" }), events: [], running: false, pending: [] });
    H.scripts.push([]);
    render(<BuildCodeView initialSession={null} onOpenedTerminal={() => {}} />);
    await waitFor(() => expect(screen.getByTestId("code-start-mode-button").textContent).toContain("Ask first"));
    fireEvent.click(screen.getByTestId("code-start-mode-button"));
    fireEvent.click(screen.getByTestId("code-start-mode-option-full"));
    await pause(FULL_ARM_GUARD_MS + 50);
    // One press only arms it.
    expect(screen.getByTestId("code-start-mode-option-full").dataset.armed).toBe("true");
    expect(screen.getByTestId("code-start-mode-button").textContent).toContain("Ask first");
    fireEvent.click(screen.getByTestId("code-start-mode-option-full"));
    expect(screen.getByTestId("code-start-mode-button").textContent).toContain("Full access");
    fireEvent.change(screen.getByTestId("code-start-box"), { target: { value: "go" } });
    fireEvent.click(screen.getByTestId("code-start-send"));
    await screen.findByTestId("code-session");
    const made = H.posts.find((p) => p.path === "/build/app/sessions");
    expect(made?.body).toEqual({ harness: "claude-code", mode: "full", confirm: true });
  });
});

/* ======================================================= every event kind */

describe("every event kind draws (the contract fixture)", () => {
  it("decodes every scenario's every event", () => {
    for (const [name, events] of Object.entries(SC)) {
      for (const e of events) expect(decodeCodeEvent(e), `${name}: ${String(e.type)}`).not.toBeNull();
    }
  });

  it("a whole settled session from the real parser: steps, diffs, replies, settled asks, usage, notices, errors", async () => {
    const events = [
      ...SC.claude_tool_approval_edit,
      ...SC.claude_interrupt,
      ...SC.codex_command_and_file_change,
      ...SC.claude_ask_user_question,
      ...SC.claude_sign_in,
    ];
    seed([sess("a1")]);
    openSession("a1", { session: sess("a1"), events, running: null, turn_id: null, pending: [] });
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    await screen.findByTestId("code-conversation");
    expect(screen.getAllByTestId("code-user")).toHaveLength(5);
    // Steps: one row per item (running -> done merged), each kind.
    const tools = screen.getAllByTestId("code-tool");
    expect(tools.map((t) => `${t.dataset.kind}:${t.dataset.status}`)).toEqual([
      "read:done",
      "edit:done",
      "command:done",
      "write:done",
      "other:done",
    ]);
    expect(screen.getAllByTestId("code-tool-diff").length).toBeGreaterThanOrEqual(2);
    // A step opens to its input and output.
    fireEvent.click(within(tools[0]).getByTestId("work-row"));
    expect(within(tools[0]).getByTestId("step-detail").textContent).toContain("hi");
    // The reply is the finished text, once (not the deltas twice).
    const texts = screen.getAllByTestId("code-text").map((t) => t.textContent);
    expect(texts[0]).toBe('I replaced "hi" with "hello" in hello.txt.');
    expect(texts.filter((t) => t?.includes("I replaced"))).toHaveLength(1);
    // Settled asks: an approval reads Allowed; the question reads Answered.
    const resolved = screen.getAllByTestId("code-resolved");
    expect(resolved.map((r) => r.dataset.decision)).toEqual(["allow", "allow", "allow_session", "allow"]);
    expect(resolved[3].textContent).toContain("Answered: Which colour do you prefer?");
    expect(screen.getAllByTestId("code-usage")[0].textContent).toBe("109,870 in · 239 out · about $0.18 at list price");
    expect(screen.getAllByTestId("code-done").find((d) => d.dataset.stop === "interrupted")?.textContent).toBe("Stopped.");
    const err = screen.getByTestId("code-error");
    expect(err.dataset.fixKind).toBe("sign_in");
    expect(err.textContent).toContain("Claude Code isn't signed in");
    // The newest failure offers the ONE sign-in press.
    expect(within(err).getByTestId("code-sign-in-claude-cli")).toBeInTheDocument();
    // Nothing is asked: every ask was settled.
    expect(screen.queryByTestId("code-ask")).toBeNull();
    expect(screen.getByTestId("code-composer")).not.toHaveAttribute("hidden");
  });

  it("thinking and the plan (in the contract; no recording carried them) draw too", async () => {
    const events = [
      { type: "user", text: "Plan it", turn: "t" },
      { type: "thinking", item: "k1", delta: "Think ", turn: "t" },
      { type: "thinking", item: "k1", delta: "first.", turn: "t" },
      { type: "plan", steps: [{ text: "Read", status: "in_progress" }, { text: "Fix", status: "pending" }], turn: "t" },
      { type: "plan", steps: [{ text: "Read", status: "completed" }, { text: "Fix", status: "in_progress" }], turn: "t" },
      { type: "done", stop: "completed", turn: "t" },
    ];
    seed([sess("a1")]);
    openSession("a1", { session: sess("a1"), events, running: null, pending: [] });
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    await screen.findByTestId("code-conversation");
    expect(screen.getByTestId("code-thinking")).toBeInTheDocument();
    // One plan per turn, its NEWEST steps.
    expect(screen.getAllByTestId("code-plan")).toHaveLength(1);
    expect(screen.getByTestId("code-plan").querySelector('[data-state="running"]')?.textContent).toContain("Fix");
  });

  it("a Codex deny and a Codex interrupt from the real parser draw as Denied and Stopped", async () => {
    seed([sess("a1", { harness: "codex" })]);
    openSession("a1", {
      session: sess("a1", { harness: "codex" }),
      events: [...SC.codex_deny, ...SC.codex_interrupt, ...SC.codex_sign_in],
      running: null,
      pending: [],
    });
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    await screen.findByTestId("code-conversation");
    expect(screen.getByTestId("code-resolved").textContent).toContain("Denied");
    expect(screen.getAllByTestId("code-tool").map((t) => t.dataset.status)).toEqual(["failed"]);
    expect(screen.getAllByTestId("code-done").find((d) => d.dataset.stop === "interrupted")?.textContent).toBe("Stopped.");
    expect(screen.getByTestId("code-notice").textContent).toContain("retrying");
    expect(within(screen.getByTestId("code-error")).getByTestId("code-sign-in-codex-cli")).toBeInTheDocument();
  });
});

/* ======================================================= approvals */

/** The real Claude edit scenario up to (and with) its approval: a turn waiting. */
function waitingOnEdit(id = "a1", over: Record<string, unknown> = {}, running = "turn_7") {
  const upTo = SC.claude_tool_approval_edit.findIndex((e) => e.type === "approval");
  const events = SC.claude_tool_approval_edit.slice(0, upTo + 1);
  seed([sess(id, over)]);
  openSession(id, { session: sess(id, over), events, running, pending: [] });
  // The rejoin replays the turn from its question and stays open.
  const turnStart = events.findIndex((e) => e.type === "user");
  H.scripts = [frames(events.slice(turnStart))];
  holdStreams();
}

describe("an approval, in the composer's place", () => {
  it("shows the change and the tool's own session-wide allow; Allow once posts the answer and the card goes", async () => {
    waitingOnEdit();
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    const card = await screen.findByTestId("code-ask");
    expect(card.dataset.kind).toBe("approval");
    expect(within(card).getByTestId("code-ask-diff")).toBeInTheDocument();
    expect(within(card).getByTestId("code-ask-allow-session")).toBeInTheDocument();
    // The composer stays mounted, hidden and inert (a draft survives).
    const composer = screen.getByTestId("code-composer");
    expect(composer).toHaveAttribute("hidden");
    expect(composer).toHaveAttribute("inert");
    fireEvent.click(within(card).getByTestId("code-ask-allow"));
    await waitFor(() => expect(screen.queryByTestId("code-ask")).toBeNull());
    expect(H.posts.find((p) => p.path === "/build/app/sessions/a1/answer")?.body).toEqual({
      request_id: "ask-1",
      decision: "allow",
    });
    expect(screen.getByTestId("code-resolved").dataset.decision).toBe("allow");
  });

  it("Allow for this session posts allow_session", async () => {
    waitingOnEdit();
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    fireEvent.click(await screen.findByTestId("code-ask-allow-session"));
    await waitFor(() =>
      expect(H.posts.find((p) => p.path === "/build/app/sessions/a1/answer")?.body).toEqual({
        request_id: "ask-1",
        decision: "allow_session",
      }),
    );
  });

  it("no session-wide allow when the tool offers none", async () => {
    // The real once-only command card (no session-wide allow offered).
    const two = SC.claude_two_approvals;
    const events = two.slice(0, two.findIndex((e) => e.type === "approval") + 1);
    seed([sess("a1")]);
    openSession("a1", { session: sess("a1"), events, running: true, pending: [] });
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    const card = await screen.findByTestId("code-ask");
    expect(within(card).queryByTestId("code-ask-allow-session")).toBeNull();
    expect(within(card).getByTestId("code-ask-command").textContent).toContain("Get-ChildItem -Force");
    // Asked once every time: Enter never allows it.
    expect(within(card).getByTestId("code-ask-keys").textContent).toBe("Click Allow once to allow it · Esc to deny");
  });

  it("Enter allows an ordinary card (after the first moment); Esc denies", async () => {
    waitingOnEdit();
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    await screen.findByTestId("code-ask");
    (document.activeElement as HTMLElement | null)?.blur();
    // A key in the first moment does nothing.
    fireEvent.keyDown(window, { key: "Enter" });
    expect(H.posts.filter((p) => p.path.endsWith("/answer"))).toHaveLength(0);
    await act(() => new Promise((r) => setTimeout(r, ARM_MS)));
    expect(screen.getByTestId("code-ask-keys").textContent).toBe("Enter to allow once · Esc to deny");
    fireEvent.keyDown(window, { key: "Enter" });
    await waitFor(() =>
      expect(H.posts.find((p) => p.path.endsWith("/answer"))?.body).toEqual({ request_id: "ask-1", decision: "allow" }),
    );
  });

  it("Esc denies", async () => {
    waitingOnEdit();
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    await screen.findByTestId("code-ask");
    (document.activeElement as HTMLElement | null)?.blur();
    await act(() => new Promise((r) => setTimeout(r, ARM_MS)));
    fireEvent.keyDown(window, { key: "Escape" });
    await waitFor(() =>
      expect(H.posts.find((p) => p.path.endsWith("/answer"))?.body).toEqual({ request_id: "ask-1", decision: "deny" }),
    );
  });

  it("Enter NEVER allows while the session runs in Full access; Esc still denies", async () => {
    waitingOnEdit("a1", { mode: "full" });
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    const card = await screen.findByTestId("code-ask");
    await act(() => new Promise((r) => setTimeout(r, ARM_MS)));
    expect(screen.getByTestId("code-ask-keys").textContent).toBe("Click Allow once to allow it · Esc to deny");
    // From the card itself and from the page body.
    fireEvent.keyDown(card, { key: "Enter" });
    (document.activeElement as HTMLElement | null)?.blur();
    fireEvent.keyDown(window, { key: "Enter" });
    await act(() => new Promise((r) => setTimeout(r, 30)));
    expect(H.posts.filter((p) => p.path.endsWith("/answer"))).toHaveLength(0);
    fireEvent.keyDown(window, { key: "Escape" });
    await waitFor(() =>
      expect(H.posts.find((p) => p.path.endsWith("/answer"))?.body).toEqual({ request_id: "ask-1", decision: "deny" }),
    );
  });

  it("Enter NEVER allows a card that asks once every time", () => {
    const ask = decodeCodeEvent({ type: "approval", request_id: "r", once_only: true }) as Extract<CodeEvent, { type: "approval" }>;
    expect(enterAllowsCodeAsk(ask, false)).toBe(false);
    expect(codeAskKeyHint(ask, false)).toBe("Click Allow once to allow it · Esc to deny");
    const plain = decodeCodeEvent({ type: "approval", request_id: "r" }) as Extract<CodeEvent, { type: "approval" }>;
    expect(enterAllowsCodeAsk(plain, false)).toBe(true);
    expect(enterAllowsCodeAsk(plain, true)).toBe(false);
    // A question keeps Enter = Send in every mode.
    const q = decodeCodeEvent(SC.claude_ask_user_question.find((e) => e.type === "question"));
    expect(enterAllowsCodeAsk(q as Extract<CodeEvent, { type: "question" }>, true)).toBe(true);
  });

  it("an answer that comes back 404 (answered elsewhere) closes the card without an error", async () => {
    waitingOnEdit();
    H.postFail["/build/app/sessions/a1/answer"] = { status: 404, message: "not found" };
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    fireEvent.click(await screen.findByTestId("code-ask-allow"));
    await waitFor(() => expect(screen.queryByTestId("code-ask")).toBeNull());
    expect(screen.getByTestId("code-resolved").textContent).toContain("No longer asked");
  });

  it("an answer that fails otherwise keeps the card and says why", async () => {
    waitingOnEdit();
    H.postFail["/build/app/sessions/a1/answer"] = { status: 500, message: "The coding tool stopped answering." };
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    fireEvent.click(await screen.findByTestId("code-ask-allow"));
    expect((await screen.findByTestId("code-ask-error")).textContent).toBe("The coding tool stopped answering.");
    expect(screen.getByTestId("code-ask")).toBeInTheDocument();
  });
});

/* ======================================================= questions */

describe("a question from the tool", () => {
  function waitingOnQuestion() {
    const qs = SC.claude_ask_user_question;
    const events = qs.slice(0, qs.findIndex((e) => e.type === "question") + 1);
    seed([sess("a1")]);
    openSession("a1", { session: sess("a1"), events, running: true, pending: [] });
  }

  it("draws lettered choices; Send posts the answers keyed by the question's own words", async () => {
    waitingOnQuestion();
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    const card = await screen.findByTestId("code-ask");
    expect(card.dataset.kind).toBe("question");
    expect(within(card).getByTestId("code-ask-send")).toBeDisabled();
    const opt = within(card).getByTestId("code-ask-option-0-0");
    expect(opt.dataset.choiceLetter).toBe("A");
    fireEvent.click(opt);
    fireEvent.click(within(card).getByTestId("code-ask-send"));
    await waitFor(() =>
      expect(H.posts.find((p) => p.path.endsWith("/answer"))?.body).toEqual({
        request_id: "ask-1",
        decision: "allow",
        answers: { "Which colour do you prefer?": "Red" },
      }),
    );
  });

  it("a letter key picks (never sends); Skip posts deny", async () => {
    waitingOnQuestion();
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    const card = await screen.findByTestId("code-ask");
    await act(() => new Promise((r) => setTimeout(r, ARM_MS)));
    fireEvent.keyDown(within(card).getByTestId("code-ask-option-0-0"), { key: "b" });
    expect(within(card).getByTestId("code-ask-option-0-1")).toHaveAttribute("aria-checked", "true");
    expect(H.posts.filter((p) => p.path.endsWith("/answer"))).toHaveLength(0);
    fireEvent.click(within(card).getByTestId("code-ask-skip"));
    await waitFor(() =>
      expect(H.posts.find((p) => p.path.endsWith("/answer"))?.body).toEqual({ request_id: "ask-1", decision: "deny" }),
    );
  });
});

/* ======================================================= the composer */

describe("the composer", () => {
  function settled(over: Record<string, unknown> = {}) {
    seed([sess("a1", over)]);
    openSession("a1", { session: sess("a1", over), events: SC.claude_deny, running: false, pending: [] });
  }

  it("Full access on the mode chip takes two presses and is sent with confirm: true; an ordinary mode is one press", async () => {
    settled();
    H.postReply["PATCH /build/app/sessions/a1"] = sess("a1", { mode: "edits" });
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    await screen.findByTestId("code-session");
    fireEvent.click(screen.getByTestId("code-mode-chip-button"));
    fireEvent.click(screen.getByTestId("code-mode-chip-option-edits"));
    await waitFor(() => expect(H.patches).toHaveLength(1));
    expect(H.patches[0]).toEqual({ path: "/build/app/sessions/a1", body: { mode: "edits" } });
    H.postReply["PATCH /build/app/sessions/a1"] = sess("a1", { mode: "full" });
    fireEvent.click(screen.getByTestId("code-mode-chip-button"));
    fireEvent.click(screen.getByTestId("code-mode-chip-option-full"));
    await pause(FULL_ARM_GUARD_MS + 50);
    expect(H.patches).toHaveLength(1);
    fireEvent.click(screen.getByTestId("code-mode-chip-option-full"));
    await waitFor(() => expect(H.patches).toHaveLength(2));
    expect(H.patches[1].body).toEqual({ mode: "full", confirm: true });
    await waitFor(() => expect(screen.getByTestId("code-mode-chip-button").textContent).toContain("Full access"));
  });

  it("the chips: tool · model, effort, account", async () => {
    settled({ account: "prof_work", model: "claude-opus-5-5" });
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    await screen.findByTestId("code-session");
    const model = screen.getByTestId("code-model-chip") as HTMLSelectElement;
    expect(model.selectedOptions[0].textContent).toBe("Claude Code · Opus 5.5");
    expect(screen.getByTestId("code-account-chip").textContent).toBe("Work");
    fireEvent.change(screen.getByTestId("code-effort-chip"), { target: { value: "high" } });
    await waitFor(() => expect(H.patches[0]?.body).toEqual({ effort: "high" }));
  });

  it("Enter sends the message as a new turn; the tool's echo of it is not drawn twice", async () => {
    settled();
    const turn = [
      { type: "user", at: "x", text: "Add a test" },
      { type: "text_done", at: "x", item: "m9", text: "Added one." },
      { type: "done", at: "x", stop: "completed" },
    ];
    H.scripts = [frames(turn)];
    // Once the turn ran, the saved record holds it too.
    H.gets["/build/app/sessions/a1"] = () => ({
      session: sess("a1"),
      events: H.streams.length ? [...SC.claude_deny, ...turn] : SC.claude_deny,
      running: false,
      pending: [],
    });
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    const box = await screen.findByTestId("code-box");
    fireEvent.change(box, { target: { value: "Add a test" } });
    fireEvent.keyDown(box, { key: "Enter" });
    await waitFor(() => expect(screen.getAllByTestId("code-text").at(-1)?.textContent).toContain("Added one."));
    const sent = H.streams[0];
    expect(sent.path).toBe("/build/app/sessions/a1/turn");
    expect((sent.body as { text: string }).text).toBe("Add a test");
    expect(typeof (sent.body as { turn_id: string }).turn_id).toBe("string");
    expect(screen.getAllByTestId("code-user").filter((u) => u.textContent === "Add a test")).toHaveLength(1);
  });

  it("Stop interrupts the tool", async () => {
    waitingOnEdit();
    // Answer the card first so the composer shows its Stop.
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    fireEvent.click(await screen.findByTestId("code-ask-stop"));
    await waitFor(() => expect(H.posts.map((p) => p.path)).toContain("/build/app/sessions/a1/interrupt"));
    fireEvent.click(screen.getByTestId("code-ask-allow"));
    fireEvent.click(await screen.findByTestId("code-stop"));
    await waitFor(() =>
      expect(H.posts.filter((p) => p.path === "/build/app/sessions/a1/interrupt")).toHaveLength(2),
    );
  });

  it("'/' lists the tool's own commands; Enter picks one into the box", async () => {
    settled();
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    const box = await screen.findByTestId("code-box");
    fireEvent.change(box, { target: { value: "/rev" } });
    const menu = await screen.findByTestId("code-slash-menu");
    expect(within(menu).getByTestId("code-command-review")).toBeInTheDocument();
    expect(within(menu).queryByTestId("code-command-compact")).toBeNull();
    fireEvent.keyDown(box, { key: "Enter" });
    expect((box as HTMLTextAreaElement).value).toBe("/review ");
    expect(H.streams).toHaveLength(0);
  });

  it("⋯ → Open in terminal hands the new terminal on", async () => {
    settled();
    H.postReply["/build/app/sessions/a1/open-terminal"] = { terminal_id: "t9" };
    const opened = vi.fn();
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={opened} />);
    fireEvent.click(await screen.findByTestId("code-session-more"));
    fireEvent.click(screen.getByTestId("code-open-terminal"));
    await waitFor(() => expect(opened).toHaveBeenCalledWith("t9"));
  });

  it("Open in terminal refused (a turn still runs) says so in words", async () => {
    settled();
    H.postFail["/build/app/sessions/a1/open-terminal"] = { status: 409, message: "Wait for the reply to finish first." };
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    fireEvent.click(await screen.findByTestId("code-session-more"));
    fireEvent.click(screen.getByTestId("code-open-terminal"));
    expect((await screen.findByTestId("code-note")).textContent).toBe("Wait for the reply to finish first.");
  });
});

/* ======================================================= rejoin */

describe("a running turn is followed again on open", () => {
  it("follows the named turn (a GET of its stream) and draws its question once", async () => {
    H.frameMs = 5;
    waitingOnEdit();
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    await screen.findByTestId("code-ask");
    expect(H.streams[0]).toEqual({ path: "/build/app/sessions/a1/turns/turn_7/stream", body: undefined });
    // Once, and it STAYS once (v1.409.2: the record named the turn "turn-1"
    // while the running turn is "turn_7"; the old cut kept the saved question
    // and the replay drew it again, for good).
    for (let k = 0; k < 8; k++) {
      expect(screen.getAllByTestId("code-user")).toHaveLength(1);
      await act(async () => {
        await new Promise((r) => setTimeout(r, 25));
      });
    }
    // The read and the edit, each once (running and done are one row).
    expect(screen.getAllByTestId("code-tool")).toHaveLength(2);
  });

  it("a record that names the running turn is cut by that turn, its question once (frames in their own macrotasks)", async () => {
    H.frameMs = 5;
    waitingOnEdit("a1", {}, "turn-1");
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    await screen.findByTestId("code-ask");
    expect(H.streams[0]?.path).toBe("/build/app/sessions/a1/turns/turn-1/stream");
    for (let k = 0; k < 8; k++) {
      expect(screen.getAllByTestId("code-user")).toHaveLength(1);
      await act(async () => {
        await new Promise((r) => setTimeout(r, 25));
      });
    }
    expect(screen.getAllByTestId("code-tool")).toHaveLength(2);
  });

  it("cutForRejoin: a running turn the record does not name falls back to the last question", () => {
    const evs = SC.claude_tool_approval_edit.map((e) => decodeCodeEvent(e)!).filter(Boolean);
    const named = cutForRejoin(evs, "turn-1");
    expect(named.base.filter((e) => e.type === "user")).toHaveLength(0);
    const other = cutForRejoin(evs, "turn_7");
    expect(other.base.filter((e) => e.type === "user")).toHaveLength(0);
    expect(other.carry?.type).toBe("user");
  });

  it("a replay that does not begin with the question puts the saved question back first", () => {
    const evs = SC.claude_tool_approval_edit.map((e) => decodeCodeEvent(e)!).filter(Boolean);
    const conv = foldConversation(evs);
    expect(conv.items.filter((i) => i.kind === "user")).toHaveLength(1);
    expect(conv.settled).toBe(true);
    expect(conv.toolSessionId).toBe("harness-session-1");
  });

  it("an ask the session read lists (its event trimmed) is still asked", async () => {
    const pendingAsk = SC.codex_command_and_file_change.find((e) => e.type === "approval")!;
    seed([sess("a1")]);
    openSession("a1", {
      session: sess("a1"),
      events: [{ type: "user", at: "x", text: "do it" }],
      running: true,
      pending: [pendingAsk],
    });
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    const card = await screen.findByTestId("code-ask");
    expect(within(card).getByTestId("code-ask-command").textContent).toContain("Get-ChildItem -Force");
  });
});

/* ======================================================= the daemon's shapes */

describe("what the real daemon sends", () => {
  it("Codex on Windows: the modes it does not offer are listed with the reason, never choosable", async () => {
    seed([sess("c1", { harness: "codex" })]);
    openSession("c1", { session: sess("c1", { harness: "codex" }), events: [], running: null, pending: [], commands: [] });
    render(<BuildCodeView initialSession="c1" onOpenedTerminal={() => {}} />);
    await screen.findByTestId("code-session");
    fireEvent.click(screen.getByTestId("code-mode-chip-button"));
    const menu = screen.getByTestId("code-mode-chip-menu");
    expect(within(menu).getByTestId("code-mode-chip-hidden-edits").textContent).toBe(
      "Auto edits is not offered here. Codex cannot run commands in its sandbox on Windows.",
    );
    expect(within(menu).getByTestId("code-mode-chip-hidden-plan").textContent).toContain("Codex has no plan mode.");
    expect(within(menu).queryByTestId("code-mode-chip-option-edits")).toBeNull();
    expect(within(menu).queryByTestId("code-mode-chip-option-plan")).toBeNull();
    expect(within(menu).getAllByRole("menuitemradio").map((b) => b.dataset.testid)).toEqual([
      "code-mode-chip-option-ask",
      "code-mode-chip-option-full",
    ]);
  });

  it("older steps left out of the read are said in one quiet line", async () => {
    seed([sess("a1")]);
    openSession("a1", { session: sess("a1"), events: SC.claude_deny, events_total: 1240, dropped: 1223, running: null, pending: [] });
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    expect((await screen.findByTestId("code-dropped")).textContent).toBe(
      "1,223 older steps were left out here to keep this quick.",
    );
  });

  it("nothing left out: no line", async () => {
    seed([sess("a1")]);
    openSession("a1", { session: sess("a1"), events: SC.claude_deny, dropped: 0, running: null, pending: [] });
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    await screen.findByTestId("code-conversation");
    expect(screen.queryByTestId("code-dropped")).toBeNull();
  });

  it("'/' lists the commands the SESSION read names (what the tool's own process said)", async () => {
    seed([sess("a1")]);
    openSession("a1", {
      session: sess("a1"),
      events: SC.claude_deny,
      running: null,
      pending: [],
      commands: [{ name: "init", description: "Write a CLAUDE.md" }],
    });
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    const box = await screen.findByTestId("code-box");
    fireEvent.change(box, { target: { value: "/" } });
    const menu = await screen.findByTestId("code-slash-menu");
    expect(within(menu).getByTestId("code-command-init")).toBeInTheDocument();
    expect(within(menu).queryByTestId("code-command-review")).toBeNull();
  });

  it("a change applied 'next' says it applies from the next message", async () => {
    seed([sess("a1")]);
    openSession("a1", { session: sess("a1"), events: SC.claude_deny, running: null, pending: [] });
    H.postReply["PATCH /build/app/sessions/a1"] = { session: sess("a1", { effort: "high" }), applied: { effort: "next" } };
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    await screen.findByTestId("code-session");
    fireEvent.change(screen.getByTestId("code-effort-chip"), { target: { value: "high" } });
    expect((await screen.findByTestId("code-info")).textContent).toBe("The effort applies from your next message.");
    expect((screen.getByTestId("code-effort-chip") as HTMLSelectElement).value).toBe("high");
  });

  it("a change applied now says nothing more", async () => {
    seed([sess("a1")]);
    openSession("a1", { session: sess("a1"), events: SC.claude_deny, running: null, pending: [] });
    H.postReply["PATCH /build/app/sessions/a1"] = { session: sess("a1", { mode: "edits" }), applied: { mode: "now" } };
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    await screen.findByTestId("code-session");
    fireEvent.click(screen.getByTestId("code-mode-chip-button"));
    fireEvent.click(screen.getByTestId("code-mode-chip-option-edits"));
    await waitFor(() => expect(screen.getByTestId("code-mode-chip-button").textContent).toContain("Auto edits"));
    expect(screen.queryByTestId("code-info")).toBeNull();
  });

  it("an ask the tool stopped asking (decision 'expired') reads 'No longer asked' and asks nothing", async () => {
    const two = SC.claude_two_approvals;
    const events = [
      ...two.slice(0, two.findIndex((e) => e.type === "approval") + 1),
      { type: "resolved", request_id: "ask-1", decision: "expired", turn: "turn-1" },
    ];
    seed([sess("a1")]);
    openSession("a1", { session: sess("a1"), events, running: null, pending: [] });
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    expect((await screen.findByTestId("code-resolved")).textContent).toContain("No longer asked");
    expect(screen.queryByTestId("code-ask")).toBeNull();
  });
});

describe("an account at its limit", () => {
  function atLimit() {
    seed([sess("a1", { account: "p1", account_name: "Work" })]);
    openSession("a1", {
      session: sess("a1", { account: "p1", account_name: "Work" }),
      events: SC.claude_account_limit_synthesized,
      running: null,
      pending: [],
    });
  }

  it("offers Continue on another account; the press moves the session and says where", async () => {
    atLimit();
    H.postReply["/build/app/sessions/a1/continue-on"] = {
      session: sess("a1", { account: "p2", account_name: "Personal" }),
      account: "p2",
      account_name: "Personal",
      note: "The conversation was copied to Personal.",
    };
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    const err = await screen.findByTestId("code-error");
    expect(err.dataset.fixKind).toBe("account_limit");
    fireEvent.click(within(err).getByTestId("code-continue-on"));
    await waitFor(() =>
      expect(H.posts.find((p) => p.path === "/build/app/sessions/a1/continue-on")?.body).toEqual({}),
    );
    expect((await screen.findByTestId("code-info")).textContent).toBe(
      "Now on Personal. The conversation was copied to Personal. Send your message again to carry on.",
    );
    expect(screen.getByTestId("code-account-chip").textContent).toBe("Personal");
  });

  it("no other account: the refusal is said in its words", async () => {
    atLimit();
    H.postFail["/build/app/sessions/a1/continue-on"] = { status: 409, message: "There is no other Claude account to move to." };
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    fireEvent.click(within(await screen.findByTestId("code-error")).getByTestId("code-continue-on"));
    expect((await screen.findByTestId("code-note")).textContent).toBe("There is no other Claude account to move to.");
  });

  it("an older failure (the conversation went on after it) offers no press", async () => {
    seed([sess("a1")]);
    openSession("a1", {
      session: sess("a1"),
      events: [...SC.claude_account_limit_synthesized, ...SC.claude_deny],
      running: null,
      pending: [],
    });
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    await screen.findByTestId("code-error");
    expect(screen.queryByTestId("code-continue-on")).toBeNull();
  });
});

describe("the tool's own saved conversations", () => {
  const SHOP = "C:\\work\\shop";
  const API_DIR = "C:\\work\\api";
  const diskPath = (f: string) => `/build/app/sessions?folder=${encodeURIComponent(f)}&disk=1`;

  function withDisk() {
    seed([sess("a1"), sess("a2", { folder: API_DIR, harness: "codex", updated_at: "2026-10-29T08:00:00Z" })]);
    openSession("a1", { session: sess("a1"), events: [], running: null, pending: [] });
    H.gets[diskPath(SHOP)] = {
      sessions: [sess("a1")],
      disk: [
        { source: "disk", harness: "claude-code", harness_session_id: "c-old", title: "Earlier work", folder: SHOP, started: "", ended: "" },
      ],
    };
    H.gets[diskPath(API_DIR)] = {
      sessions: [],
      disk: [{ source: "disk", harness: "codex", harness_session_id: "x-1", title: "Lint run", folder: API_DIR, started: "", ended: "" }],
    };
  }
  const asked = () => H.getLog.filter((p) => p.includes("disk=1"));

  it("the folder in front lists them by itself; another folder only when opened", async () => {
    withDisk();
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    const row = await screen.findByTestId("code-disk-c-old");
    expect(screen.getByTestId("code-disk-line-c-old").textContent).toBe("saved by Claude Code");
    expect(row.textContent).toContain("Earlier work");
    expect(asked()).toEqual([diskPath(SHOP)]);
    expect(screen.queryByTestId("code-disk-x-1")).toBeNull();
    fireEvent.click(screen.getByTestId("code-saved-toggle-c:/work/api"));
    expect(await screen.findByTestId("code-disk-line-x-1")).toHaveTextContent("saved by Codex");
    expect(asked()).toEqual([diskPath(SHOP), diskPath(API_DIR)]);
  });

  it("opening one makes a session on that saved conversation and opens it", async () => {
    withDisk();
    H.postReply["/build/app/sessions"] = sess("n9", { title: "Earlier work", harness_session_id: "c-old" });
    openSession("n9", { session: sess("n9", { title: "Earlier work" }), events: [], running: null, pending: [] });
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    fireEvent.click(await screen.findByTestId("code-disk-c-old"));
    await waitFor(() => expect(screen.getByTestId("code-session").dataset.session).toBe("n9"));
    expect(H.posts.find((p) => p.path === "/build/app/sessions")?.body).toEqual({
      harness: "claude-code",
      folder: SHOP,
      harness_session_id: "c-old",
    });
    expect(screen.queryByTestId("code-disk-c-old")).toBeNull();
  });
});

describe("archived sessions", () => {
  it("the archived list shows them, and Bring back unarchives one", async () => {
    seed([sess("a1")]);
    H.gets["/build/app/sessions?archived=1"] = {
      sessions: [sess("a1"), sess("z9", { archived: true, archived_at: "2026-10-28T09:00:00Z", title: "Old" })],
    };
    H.postReply["/build/app/sessions/z9/unarchive"] = sess("z9", { archived: false, title: "Old" });
    // Once brought back, the daemon lists it with the others again.
    H.gets["/build/app/sessions"] = () => ({
      sessions: H.posts.some((p) => p.path.endsWith("/unarchive"))
        ? [sess("a1"), sess("z9", { archived: false, title: "Old" })]
        : [sess("a1")],
    });
    render(<BuildCodeView initialSession={null} onOpenedTerminal={() => {}} />);
    await screen.findByTestId("code-row-a1");
    expect(screen.queryByTestId("code-row-z9")).toBeNull();
    fireEvent.click(screen.getByTestId("code-archived-toggle"));
    const z9 = await screen.findByTestId("code-row-z9");
    expect(z9.dataset.archived).toBe("true");
    // Only the archived ones are in the archived list.
    expect(screen.queryByTestId("code-row-a1")).toBeNull();
    fireEvent.click(screen.getByTestId("code-row-more-z9"));
    fireEvent.click(screen.getByTestId("code-row-unarchive-z9"));
    await waitFor(() => expect(screen.queryByTestId("code-row-z9")).toBeNull());
    expect(H.posts.map((p) => p.path)).toContain("/build/app/sessions/z9/unarchive");
    expect(screen.getByTestId("code-archived-empty")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("code-archived-toggle"));
    expect(await screen.findByTestId("code-row-z9")).not.toHaveAttribute("data-archived");
  });
});

/* ======================================================= the review's fixes */

function settledSession(over: Record<string, unknown> = {}, events: Record<string, unknown>[] = SC.claude_deny) {
  seed([sess("a1", over)]);
  openSession("a1", { session: sess("a1", over), events, running: null, pending: [] });
}

describe("review: the '/' and '@' menus answer the arrow keys", () => {
  it("'/' then ArrowDown then Enter inserts the SECOND command", async () => {
    settledSession();
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    const box = await screen.findByTestId("code-box");
    fireEvent.change(box, { target: { value: "/" } });
    const menu = await screen.findByTestId("code-slash-menu");
    expect(within(menu).getAllByRole("option").map((o) => o.dataset.testid)).toEqual([
      "code-command-review",
      "code-command-compact",
    ]);
    fireEvent.keyDown(box, { key: "ArrowDown" });
    await waitFor(() => expect(screen.getByTestId("code-command-compact")).toHaveAttribute("aria-selected", "true"));
    fireEvent.keyDown(box, { key: "Enter" });
    expect((box as HTMLTextAreaElement).value).toBe("/compact ");
  });
});

describe("review: the transcript stays where the reader put it", () => {
  const spy = vi.fn();
  beforeEach(() => {
    spy.mockReset();
    (Element.prototype as unknown as { scrollIntoView: unknown }).scrollIntoView = spy;
  });
  afterEach(() => {
    delete (Element.prototype as unknown as { scrollIntoView?: unknown }).scrollIntoView;
  });
  /** The transcript reports a scroll position (jsdom lays nothing out). */
  function scrollTo(el: HTMLElement, top: number) {
    Object.defineProperty(el, "scrollHeight", { configurable: true, value: 2000 });
    Object.defineProperty(el, "clientHeight", { configurable: true, value: 500 });
    Object.defineProperty(el, "scrollTop", { configurable: true, value: top, writable: true });
    fireEvent.scroll(el);
  }

  it("a reader at the bottom follows the newest step; one who scrolled up is left there, with Jump to latest", async () => {
    waitingOnEdit();
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    await screen.findByTestId("code-ask");
    const scroller = screen.getByTestId("code-transcript");
    scrollTo(scroller, 1500); // at the bottom
    spy.mockClear();
    act(() => H.lastWatch?.onFrame?.("notice", { type: "notice", text: "Compacted.", turn: "turn-1" }));
    await waitFor(() => expect(screen.getByText("Compacted.")).toBeInTheDocument());
    await waitFor(() => expect(spy).toHaveBeenCalled());
    // Scrolled up: a new step does not move the reader.
    scrollTo(scroller, 200);
    spy.mockClear();
    act(() => H.lastWatch?.onFrame?.("notice", { type: "notice", text: "Still working.", turn: "turn-1" }));
    // Wait for the step itself to be drawn, THEN check nothing moved.
    await waitFor(() => expect(screen.getByText("Still working.")).toBeInTheDocument());
    await pause(30);
    expect(spy).not.toHaveBeenCalled();
    const jump = await screen.findByTestId("code-jump-to-latest");
    fireEvent.click(jump);
    expect(spy).toHaveBeenCalled();
    expect(screen.queryByTestId("code-jump-to-latest")).toBeNull();
  });
});

describe("review: a refused send leaves no message behind", () => {
  it("a 409 before the first frame: no bubble, the words and the file back in the box, the reason said", async () => {
    settledSession();
    H.scripts = [[{ yield: { type: "error", detail: "Claude Code is still answering the last message.", status: 409 } }]];
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    const box = (await screen.findByTestId("code-box")) as HTMLTextAreaElement;
    const before = screen.getAllByTestId("code-user").length;
    fireEvent.change(box, { target: { value: "Run the tests" } });
    fireEvent.keyDown(box, { key: "Enter" });
    expect((await screen.findByTestId("code-note")).textContent).toBe("Claude Code is still answering the last message.");
    expect(screen.getAllByTestId("code-user")).toHaveLength(before);
    expect(box.value).toBe("Run the tests");
    // Nothing was sent, so nothing is read back as if it had been.
    expect(screen.queryByTestId("code-stop")).toBeNull();
  });

  it("CONTROL: an error frame after the turn began stays in the conversation", async () => {
    settledSession();
    H.scripts = [
      [
        { event: "user", data: { type: "user", text: "Run the tests", turn: "t9" } },
        { yield: { type: "error", detail: "The reply could not be followed.", status: 500 } },
      ],
    ];
    H.gets["/build/app/sessions/a1"] = () => ({
      session: sess("a1"),
      events: H.streams.length ? [...SC.claude_deny, { type: "user", text: "Run the tests", turn: "t9" }] : SC.claude_deny,
      running: null,
      pending: [],
    });
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    const box = await screen.findByTestId("code-box");
    fireEvent.change(box, { target: { value: "Run the tests" } });
    fireEvent.keyDown(box, { key: "Enter" });
    await waitFor(() => expect(screen.getAllByTestId("code-user").at(-1)?.textContent).toBe("Run the tests"));
  });
});

describe("review: an ask settled once is drawn once", () => {
  it("the stream's own line lands before the answer returns: ONE 'Allowed' line", async () => {
    waitingOnEdit();
    let finish: (v: unknown) => void = () => {};
    const slow = new Promise((r) => {
      finish = r;
    });
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    const card = await screen.findByTestId("code-ask");
    H.postReply["/build/app/sessions/a1/answer"] = slow;
    fireEvent.click(within(card).getByTestId("code-ask-allow"));
    // The tool's resolved frame arrives first...
    act(() => H.lastWatch?.onFrame?.("resolved", { type: "resolved", request_id: "ask-1", decision: "allow", turn: "turn-1" }));
    await waitFor(() => expect(screen.getAllByTestId("code-resolved")).toHaveLength(1));
    // ...then the answer returns.
    await act(async () => {
      finish({ ok: true });
      await slow;
    });
    await pause(40);
    expect(screen.getAllByTestId("code-resolved")).toHaveLength(1);
  });
});

describe("review: drawing only what changed (the v1.250.0 rule)", () => {
  it("twenty streamed words redraw only the reply that grows, at most once a frame", async () => {
    waitingOnEdit();
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    const card = await screen.findByTestId("code-ask");
    fireEvent.click(within(card).getByTestId("code-ask-allow"));
    await waitFor(() => expect(screen.queryByTestId("code-ask")).toBeNull());
    await pause(40);
    const rows = screen.getAllByTestId("code-tool").length + screen.getAllByTestId("code-user").length;
    expect(rows).toBeGreaterThan(2);
    const before = { ...codeRowDraws };
    // Twenty frames, each in its own read (as a real stream delivers them),
    // all well inside one animation frame.
    for (let i = 0; i < 20; i += 1) {
      act(() => H.lastWatch?.onFrame?.("text", { type: "text", item: "m-live", delta: `word${i} `, turn: "turn-1" }));
      await act(() => Promise.resolve());
    }
    await waitFor(() => expect(screen.getAllByTestId("code-text").at(-1)?.textContent).toContain("word19"));
    await pause(40);
    // One new row, drawn once for the twenty words (one frame); no other row
    // drew again.
    expect(codeRowDraws.items - before.items).toBeLessThanOrEqual(2);
    expect(codeRowDraws.tools - before.tools).toBe(0);
  });
});

describe("review: Full access cannot be given by a held or doubled key", () => {
  it("a second press right after arming does not count; a later one does", async () => {
    settledSession();
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    await screen.findByTestId("code-session");
    fireEvent.click(screen.getByTestId("code-mode-chip-button"));
    const full = screen.getByTestId("code-mode-chip-option-full");
    fireEvent.click(full);
    fireEvent.click(full);
    await pause(20);
    expect(H.patches).toHaveLength(0);
    await pause(FULL_ARM_GUARD_MS);
    fireEvent.click(screen.getByTestId("code-mode-chip-option-full"));
    await waitFor(() => expect(H.patches[0]?.body).toEqual({ mode: "full", confirm: true }));
  });

  it("a held key's repeat is swallowed", async () => {
    settledSession();
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    await screen.findByTestId("code-session");
    fireEvent.click(screen.getByTestId("code-mode-chip-button"));
    const full = screen.getByTestId("code-mode-chip-option-full");
    const repeat = createEvent.keyDown(full, { key: "Enter", repeat: true });
    fireEvent(full, repeat);
    expect(repeat.defaultPrevented).toBe(true);
    const first = createEvent.keyDown(full, { key: "Enter" });
    fireEvent(full, first);
    expect(first.defaultPrevented).toBe(false);
  });

  it("an armed press is forgotten after a few seconds", async () => {
    settledSession();
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    await screen.findByTestId("code-session");
    fireEvent.click(screen.getByTestId("code-mode-chip-button"));
    fireEvent.click(screen.getByTestId("code-mode-chip-option-full"));
    expect(screen.getByTestId("code-mode-chip-option-full").dataset.armed).toBe("true");
    await pause(FULL_DISARM_MS + 100);
    expect(screen.getByTestId("code-mode-chip-option-full").dataset.armed).toBeUndefined();
    // The next press arms again; nothing was given.
    fireEvent.click(screen.getByTestId("code-mode-chip-option-full"));
    expect(H.patches).toHaveLength(0);
  });

  it("Enter is refused in Full access by the session's own mode, even with no tool list", async () => {
    const upTo = SC.claude_tool_approval_edit.findIndex((e) => e.type === "approval");
    seed([sess("a1", { mode: "full" })]);
    H.gets["/build/app/harnesses"] = { harnesses: [] };
    openSession("a1", {
      session: sess("a1", { mode: "full" }),
      events: SC.claude_tool_approval_edit.slice(0, upTo + 1),
      running: true,
      pending: [],
    });
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    await screen.findByTestId("code-ask");
    (document.activeElement as HTMLElement | null)?.blur();
    await pause(ARM_MS);
    expect(screen.getByTestId("code-ask-keys").textContent).toBe("Click Allow once to allow it · Esc to deny");
    fireEvent.keyDown(window, { key: "Enter" });
    await pause(30);
    expect(H.posts.filter((p) => p.path.endsWith("/answer"))).toHaveLength(0);
  });
});

describe("review: words", () => {
  it("a mode is never shown by its raw id", () => {
    expect(modeLabel("full")).toBe("Full access");
    expect(modeLabel("edits")).toBe("Auto edits");
    expect(modeLabel("bypassPermissions")).toBe("Another mode");
    expect(modeLabel("")).toBe("Ask first");
  });

  it("an account says its state, and one that cannot start a session is greyed", async () => {
    expect(accountStatusWords("needs_sign_in")).toEqual({ words: "needs to sign in again", usable: false });
    expect(accountStatusWords("limit").usable).toBe(false);
    expect(accountStatusWords("off").usable).toBe(false);
    expect(accountStatusWords("ready")).toEqual({ words: "", usable: true });
    const tools = JSON.parse(JSON.stringify(TOOLS)) as typeof TOOLS;
    tools.harnesses[0].accounts = [
      { id: "p1", name: "Work", status: "ready" },
      { id: "p2", name: "Personal", status: "needs_sign_in" },
      { id: "p3", name: "Team", status: "limit" },
    ];
    seed();
    H.gets["/build/app/harnesses"] = tools;
    render(<BuildCodeView initialSession={null} onOpenedTerminal={() => {}} />);
    // v1.412.0 (moved on purpose): the start composer's account chip.
    fireEvent.click(await screen.findByTestId("code-start-account"));
    const menu = screen.getByTestId("code-start-account-menu");
    const opts = within(menu)
      .getAllByRole("menuitemradio")
      .map((o) => [o.textContent, (o as HTMLButtonElement).disabled]);
    expect(opts).toEqual([
      ["Work", false],
      ["Personalneeds to sign in again", true],
      ["Teamat its limit", true],
    ]);
  });
});

describe("review: the rejoin cut follows the running turn", () => {
  const ev = (type: string, turn: string, extra: Record<string, unknown> = {}) =>
    decodeCodeEvent({ type, turn, ...extra })!;

  it("cuts every event of the running turn (a queued second message included), keeps its question aside", () => {
    const events = [
      ev("user", "t1", { text: "first" }),
      ev("text_done", "t1", { item: "a", text: "done" }),
      ev("done", "t1", { stop: "completed" }),
      ev("user", "t2", { text: "second" }),
      ev("tool", "t2", { item: "x", kind: "read", status: "done" }),
      ev("user", "t2", { text: "and also this" }),
    ];
    const { base, carry } = cutForRejoin(events, "t2");
    expect(base.map((e) => e.turn)).toEqual(["t1", "t1", "t1"]);
    expect(carry && carry.type === "user" ? carry.text : "").toBe("second");
  });

  it("an older record with no turns falls back to the last question", () => {
    const events = [decodeCodeEvent({ type: "user", text: "q" })!, decodeCodeEvent({ type: "notice", text: "n" })!];
    const { base, carry } = cutForRejoin(events, "t9");
    expect(base).toHaveLength(0);
    expect(carry?.type).toBe("user");
  });
});

describe("review: polish", () => {
  it("after the last card is answered, the caret is back in the message box", async () => {
    waitingOnEdit();
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    const card = await screen.findByTestId("code-ask");
    fireEvent.click(within(card).getByTestId("code-ask-allow"));
    await waitFor(() => expect(screen.queryByTestId("code-ask")).toBeNull());
    await waitFor(() => expect(document.activeElement).toBe(screen.getByTestId("code-box")));
  });

  it("a reply whose stream dropped while Iron Jarvis was away is followed again once it is back", async () => {
    settledSession();
    H.scripts = [
      [
        { event: "user", data: { type: "user", text: "Go", turn: "t5" } },
        { yield: { type: "error", detail: "Iron Jarvis isn't responding.", status: 0, offline: true } },
      ],
      [{ event: "user", data: { type: "user", text: "Go", turn: "t5" } }],
    ];
    let back = false;
    H.gets["/build/app/sessions/a1"] = () => {
      if (H.streams.length && !back) return Promise.reject(new Error("offline"));
      return {
        session: sess("a1"),
        events: H.streams.length ? [...SC.claude_deny, { type: "user", text: "Go", turn: "t5" }] : SC.claude_deny,
        running: H.streams.length === 1 ? "t5" : null,
        pending: [],
      };
    };
    holdStreams();
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    const box = await screen.findByTestId("code-box");
    fireEvent.change(box, { target: { value: "Go" } });
    fireEvent.keyDown(box, { key: "Enter" });
    expect(await screen.findByTestId("code-lost")).toBeInTheDocument();
    back = true;
    await waitFor(() => expect(H.streams).toHaveLength(2), { timeout: 8_000 });
    expect(H.streams[1].path).toBe("/build/app/sessions/a1/turns/t5/stream");
    await waitFor(() => expect(screen.queryByTestId("code-lost")).toBeNull());
    // The replay draws the question again (once a frame): wait for IT.
    await waitFor(() => expect(screen.getAllByTestId("code-user").filter((u) => u.textContent === "Go")).toHaveLength(1));
    await pause(60);
    expect(screen.getAllByTestId("code-user").filter((u) => u.textContent === "Go")).toHaveLength(1);
  });

  it("a session read that lands after a newer send never ends that send's reply", async () => {
    settledSession();
    // Turn A ends at once; its closing read is held back.
    let releaseRead: () => void = () => {};
    const held = new Promise<void>((r) => {
      releaseRead = r;
    });
    let reads = 0;
    H.gets["/build/app/sessions/a1"] = () => {
      reads += 1;
      if (reads === 2) return held.then(() => ({ session: sess("a1"), events: SC.claude_deny, running: null, pending: [] }));
      return { session: sess("a1"), events: SC.claude_deny, running: null, pending: [] };
    };
    H.scripts = [[{ event: "done", data: { type: "done", stop: "completed", turn: "tA" } }], []];
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    const box = await screen.findByTestId("code-box");
    fireEvent.change(box, { target: { value: "A" } });
    fireEvent.keyDown(box, { key: "Enter" });
    await waitFor(() => expect(reads).toBe(2));
    // B goes out (its stream stays open) while A's read is still out.
    holdStreams();
    fireEvent.change(box, { target: { value: "B" } });
    fireEvent.keyDown(box, { key: "Enter" });
    expect(await screen.findByTestId("code-stop")).toBeInTheDocument();
    await act(async () => {
      releaseRead();
      await held;
    });
    await pause(40);
    expect(screen.getByTestId("code-stop")).toBeInTheDocument();
    expect(screen.getAllByTestId("code-user").map((u) => u.textContent)).toContain("B");
  });
});

/* ======================================================= found live */

describe("found live: titles, the folder New session starts on, the path box", () => {
  it("an untitled session is titled by its first message, in the list at once", async () => {
    seed([sess("a1", { title: "" })]);
    openSession("a1", { session: sess("a1", { title: "" }), events: [], running: null, pending: [] });
    holdStreams();
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    expect((await screen.findByTestId("code-row-a1")).textContent).toContain("New Claude Code session");
    const box = await screen.findByTestId("code-box");
    fireEvent.change(box, { target: { value: "Fix the loop in cart.py\nthen run the tests" } });
    fireEvent.keyDown(box, { key: "Enter" });
    await waitFor(() => expect(screen.getByTestId("code-row-a1").textContent).toContain("Fix the loop in cart.py"));
    expect(screen.getByTestId("code-row-a1").textContent).not.toContain("then run the tests");
  });

  it("CONTROL: a session with a title keeps it", async () => {
    seed([sess("a1", { title: "Mine" })]);
    openSession("a1", { session: sess("a1", { title: "Mine" }), events: [], running: null, pending: [] });
    holdStreams();
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    const box = await screen.findByTestId("code-box");
    fireEvent.change(box, { target: { value: "Something else" } });
    fireEvent.keyDown(box, { key: "Enter" });
    await waitFor(() => expect(H.streams).toHaveLength(1));
    expect(screen.getByTestId("code-row-a1").textContent).toContain("Mine");
  });

  it("the echo never shows a line that looks like it holds a key", () => {
    expect(firstMessageTitle("use sk-ant-api03-ABCDEFGHIJKLMNOPQRSTUVWX please")).toBe("");
    expect(firstMessageTitle("  \n  Fix   it  ")).toBe("Fix it");
    expect(firstMessageTitle("w ".repeat(50)).length).toBeLessThanOrEqual(60);
  });

  // v1.412.0 (moved on purpose): New session starts with NO folder — the
  // user's choice; "Choose…" picks one.
  it("New session starts with no folder, whatever the session in front", async () => {
    seed([sess("a1"), sess("a2", { folder: "C:\\work\\api", updated_at: "2026-10-29T08:00:00Z" })]);
    openSession("a2", { session: sess("a2", { folder: "C:\\work\\api" }), events: [], running: null, pending: [] });
    render(<BuildCodeView initialSession="a2" startFolder="C:\\elsewhere" onOpenedTerminal={() => {}} />);
    await screen.findByTestId("code-session");
    fireEvent.click(screen.getByTestId("code-new"));
    const chip = await screen.findByTestId("code-start-folder");
    expect(chip.dataset.folder).toBe("");
    expect(chip.textContent).toBe("No folder");
  });

  it("a folder heading's + starts on THAT folder", async () => {
    seed([sess("a1"), sess("a2", { folder: "C:\\work\\api", updated_at: "2026-10-29T08:00:00Z" })]);
    openSession("a1", { session: sess("a1"), events: [], running: null, pending: [] });
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    await screen.findByTestId("code-session");
    fireEvent.click(screen.getByTestId("code-new-in-c:/work/api"));
    expect((await screen.findByTestId("code-start-folder")).dataset.folder).toBe("C:\\work\\api");
    // And on the other folder: the start begins again on it.
    fireEvent.click(screen.getByTestId("code-new-in-c:/work/shop"));
    await waitFor(() => expect(screen.getByTestId("code-start-folder").dataset.folder).toBe("C:\\work\\shop"));
  });

  it("the in-app folder tree (no desktop picker) picks a pasted path (the opt-in)", async () => {
    render(<BuildCodeView initialSession={null} onOpenedTerminal={() => {}} />);
    fireEvent.click(await screen.findByTestId("code-start-folder-choose"));
    expect((await screen.findByTestId("stub-pick-folder")).dataset.pickTyped).toBe("true");
  });
});
