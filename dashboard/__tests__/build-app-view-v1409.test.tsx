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
 *     reason; Start sends the choice; Full access needs two presses and
 *     `confirm: true`;
 *   - every event kind of THE CONTRACT draws (the contract fixture — the
 *     daemon's real one when present, else the hand-written draft);
 *   - approvals and questions answered from the composer's place; Enter
 *     allows only an ordinary card, never in Full access or a once-only one;
 *     Esc denies;
 *   - Stop interrupts; a running turn is followed again on open (no
 *     duplicate question); Open in terminal hands the terminal on.
 */

import React from "react";
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const H = vi.hoisted(() => ({
  gets: {} as Record<string, unknown>,
  posts: [] as { path: string; body: unknown }[],
  patches: [] as { path: string; body: unknown }[],
  postReply: {} as Record<string, unknown>,
  postFail: {} as Record<string, { status: number; message: string }>,
  streams: [] as { path: string; body: unknown }[],
  /** Frames each stream sends (in order of the streams opened). */
  scripts: [] as { event: string; data: Record<string, unknown> }[][],
  /** While set, an open stream waits here before it ends. */
  hold: null as Promise<void> | null,
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
    const frames = H.scripts.shift() ?? [];
    for (const f of frames) {
      if (signal?.aborted) return;
      watch?.onFrame?.(f.event, f.data);
      await Promise.resolve();
    }
    if (H.hold) await H.hold;
    yield* [];
  },
  useLiveText: (s: { text?: string }) => s?.text ?? "",
}));

vi.mock("@/components/terminal/DirectoryTree", () => ({
  DirectoryTree: ({ onSelect }: { onSelect: (p: string) => void }) => (
    <button type="button" data-testid="stub-pick-folder" onClick={() => onSelect("C:\\work\\shop")}>
      pick
    </button>
  ),
}));

import { BuildCodeView } from "@/components/buildCode/BuildCodeView";
import {
  codeAskKeyHint,
  decodeCodeEvent,
  enterAllowsCodeAsk,
  foldConversation,
  type CodeEvent,
} from "@/lib/buildApp";

/* ---- the contract fixture ---------------------------------------------- */

const FIX_DIR = join(process.cwd(), "__tests__", "fixtures");
const REAL = join(FIX_DIR, "build-app-events-v1409.json");
const DRAFT = join(FIX_DIR, "build-app-events-draft-v1409.json");
const fixture = JSON.parse(readFileSync(existsSync(REAL) ? REAL : DRAFT, "utf8")) as {
  scenarios: Record<string, Record<string, unknown>[]>;
};
const SC = fixture.scenarios;

/* ---- the daemon --------------------------------------------------------- */

const TOOLS = [
  {
    id: "claude-code",
    label: "Claude Code",
    installed: true,
    signed_in: true,
    version: "2.1.311",
    modes: [
      { id: "default", label: "Ask first", dangerous: false },
      { id: "acceptEdits", label: "Auto edits", dangerous: false },
      { id: "plan", label: "Plan", dangerous: false },
      { id: "bypassPermissions", label: "Full access", dangerous: true },
    ],
    models: [{ id: "claude-opus-5-5", label: "Opus 5.5" }],
    efforts: ["low", "medium", "high"],
    accounts: [{ id: "prof_work", name: "Work", status: "ready" }],
    commands: [
      { name: "review", description: "Review the changes" },
      { name: "compact", description: "Summarize the conversation" },
    ],
  },
  { id: "codex", label: "Codex", installed: false, signed_in: null, version: "", modes: [], models: [], efforts: [], accounts: [] },
  { id: "grok", label: "Grok", installed: true, signed_in: false, version: "", modes: [], models: [], efforts: [], accounts: [] },
];

const sess = (id: string, over: Record<string, unknown> = {}) => ({
  id,
  harness: "claude-code",
  folder: "C:\\work\\shop",
  title: `Session ${id}`,
  account: "",
  model: "",
  effort: "",
  mode: "default",
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
  H.posts = [];
  H.patches = [];
  H.postReply = {};
  H.postFail = {};
  H.streams = [];
  H.scripts = [];
  H.hold = null;
  H.release = null;
  seed();
});

afterEach(() => {
  H.release?.();
  cleanup();
});

const ARM_MS = 450; // the card's key guard (DOCK_KEY_ARM_MS = 400) plus slack

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

describe("New session", () => {
  it("greys a tool that is not installed or signed out, with the reason", async () => {
    render(<BuildCodeView initialSession={null} onOpenedTerminal={() => {}} />);
    fireEvent.click(await screen.findByTestId("code-new"));
    const codex = await screen.findByTestId("code-new-tool-codex");
    expect(codex.dataset.available).toBe("false");
    expect(within(codex).getByRole("radio")).toBeDisabled();
    expect(screen.getByTestId("code-new-tool-why-codex").textContent).toBe("Codex is not installed on this PC.");
    expect(screen.getByTestId("code-new-tool-why-grok").textContent).toContain("Grok is signed out.");
    const claude = screen.getByTestId("code-new-tool-claude-code");
    expect(claude.dataset.available).toBe("true");
    expect(within(claude).getByRole("radio")).toBeChecked();
    // No folder yet: Start waits.
    expect(screen.getByTestId("code-new-start")).toBeDisabled();
  });

  it("Start sends the tool, the folder, the account and the mode, then opens the session", async () => {
    H.postReply["/build/app/sessions"] = sess("n1", { account: "prof_work" });
    openSession("n1", { session: sess("n1", { account: "prof_work" }), events: [], running: false, pending: [] });
    render(<BuildCodeView initialSession={null} onOpenedTerminal={() => {}} />);
    fireEvent.click(await screen.findByTestId("code-new"));
    await screen.findByTestId("code-new-tool-claude-code");
    fireEvent.click(screen.getByTestId("stub-pick-folder"));
    fireEvent.change(screen.getByTestId("code-new-account"), { target: { value: "prof_work" } });
    fireEvent.click(screen.getByTestId("code-new-start"));
    await screen.findByTestId("code-session");
    const made = H.posts.find((p) => p.path === "/build/app/sessions");
    expect(made?.body).toEqual({ harness: "claude-code", folder: "C:\\work\\shop", account: "prof_work", mode: "default" });
  });

  it("Full access takes two presses in the form, and is sent with confirm: true", async () => {
    H.postReply["/build/app/sessions"] = sess("n2", { mode: "bypassPermissions" });
    openSession("n2", { session: sess("n2", { mode: "bypassPermissions" }), events: [], running: false, pending: [] });
    render(<BuildCodeView initialSession={null} onOpenedTerminal={() => {}} />);
    fireEvent.click(await screen.findByTestId("code-new"));
    await screen.findByTestId("code-new-tool-claude-code");
    fireEvent.click(screen.getByTestId("stub-pick-folder"));
    fireEvent.click(screen.getByTestId("code-new-mode-button"));
    fireEvent.click(screen.getByTestId("code-new-mode-option-bypassPermissions"));
    // One press only arms it.
    expect(screen.getByTestId("code-new-mode-option-bypassPermissions").dataset.armed).toBe("true");
    expect(screen.getByTestId("code-new-mode-button").textContent).toContain("Ask first");
    fireEvent.click(screen.getByTestId("code-new-mode-option-bypassPermissions"));
    expect(screen.getByTestId("code-new-mode-button").textContent).toContain("Full access");
    fireEvent.click(screen.getByTestId("code-new-start"));
    await screen.findByTestId("code-session");
    const made = H.posts.find((p) => p.path === "/build/app/sessions");
    expect(made?.body).toEqual({ harness: "claude-code", folder: "C:\\work\\shop", mode: "bypassPermissions", confirm: true });
  });
});

/* ======================================================= every event kind */

describe("every event kind draws (the contract fixture)", () => {
  it("decodes every scenario's every event", () => {
    for (const [name, events] of Object.entries(SC)) {
      for (const e of events) expect(decodeCodeEvent(e), `${name}: ${String(e.type)}`).not.toBeNull();
    }
  });

  it("a whole settled session: question, thinking, plan, steps, diff, reply, settled asks, usage, notices, errors", async () => {
    const events = [...SC.claude_edit, ...SC.interrupt, ...SC.codex_command, ...SC.sign_in];
    seed([sess("a1")]);
    openSession("a1", { session: sess("a1"), events, running: false, pending: [] });
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    await screen.findByTestId("code-conversation");
    expect(screen.getAllByTestId("code-user").map((u) => u.textContent)).toEqual([
      "Fix the off-by-one in cart.py",
      "Refactor the whole project",
      "Run the linter",
      "Hello",
    ]);
    expect(screen.getByTestId("code-thinking")).toBeInTheDocument();
    // One plan per turn, its NEWEST steps.
    const plan = screen.getByTestId("code-plan");
    expect(screen.getAllByTestId("code-plan")).toHaveLength(1);
    expect(plan.querySelector('[data-state="running"]')?.textContent).toContain("Run the tests");
    // Steps: one row per item (running → done merged), each kind.
    const tools = screen.getAllByTestId("code-tool");
    expect(tools.map((t) => `${t.dataset.kind}:${t.dataset.status}`)).toEqual([
      "read:done",
      "edit:done",
      "command:done",
      "search:running",
      "command:failed",
    ]);
    expect(screen.getAllByTestId("code-tool-diff")).toHaveLength(1);
    // A step opens to its input and output.
    fireEvent.click(within(tools[2]).getByTestId("work-row"));
    expect(within(tools[2]).getByTestId("step-detail").textContent).toContain("3 passed");
    // The reply is the finished text (not the deltas twice).
    const texts = screen.getAllByTestId("code-text").map((t) => t.textContent);
    expect(texts[0]).toContain("Fixed the loop in cart.py. All three tests pass.");
    expect(screen.getAllByTestId("code-resolved").map((r) => r.dataset.decision)).toEqual(["allow", "allow_session"]);
    expect(screen.getAllByTestId("code-usage")[0].textContent).toBe("12,400 in · 310 out · about $0.04 at list price");
    expect(screen.getByTestId("code-notice").textContent).toContain("compacted");
    expect(screen.getAllByTestId("code-done").find((d) => d.dataset.stop === "interrupted")?.textContent).toBe("Stopped.");
    const err = screen.getByTestId("code-error");
    expect(err.dataset.fixKind).toBe("sign_in");
    expect(err.textContent).toContain("Claude Code is signed out.");
    // Nothing is asked: every ask was settled.
    expect(screen.queryByTestId("code-ask")).toBeNull();
    expect(screen.getByTestId("code-composer")).not.toHaveAttribute("hidden");
  });
});

/* ======================================================= approvals */

/** The claude_edit scenario up to (and with) its approval: a turn waiting. */
function waitingOnEdit(id = "a1", over: Record<string, unknown> = {}) {
  const upTo = SC.claude_edit.findIndex((e) => e.type === "approval");
  const events = SC.claude_edit.slice(0, upTo + 1);
  seed([sess(id, over)]);
  openSession(id, { session: sess(id, over), events, running: "turn_7", pending: [] });
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
      request_id: "req-edit-1",
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
        request_id: "req-edit-1",
        decision: "allow_session",
      }),
    );
  });

  it("no session-wide allow when the tool offers none", async () => {
    const events = SC.deny.slice(0, SC.deny.findIndex((e) => e.type === "approval") + 1);
    seed([sess("a1")]);
    openSession("a1", { session: sess("a1"), events, running: true, pending: [] });
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    const card = await screen.findByTestId("code-ask");
    expect(within(card).queryByTestId("code-ask-allow-session")).toBeNull();
    expect(within(card).getByTestId("code-ask-command").textContent).toContain("rm -rf build");
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
      expect(H.posts.find((p) => p.path.endsWith("/answer"))?.body).toEqual({ request_id: "req-edit-1", decision: "allow" }),
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
      expect(H.posts.find((p) => p.path.endsWith("/answer"))?.body).toEqual({ request_id: "req-edit-1", decision: "deny" }),
    );
  });

  it("Enter NEVER allows while the session runs in Full access; Esc still denies", async () => {
    waitingOnEdit("a1", { mode: "bypassPermissions" });
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
      expect(H.posts.find((p) => p.path.endsWith("/answer"))?.body).toEqual({ request_id: "req-edit-1", decision: "deny" }),
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
    const q = decodeCodeEvent(SC.question.find((e) => e.type === "question"));
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
    const events = SC.question.slice(0, SC.question.findIndex((e) => e.type === "question") + 1);
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
        request_id: "req-q-1",
        decision: "allow",
        answers: { "Which test runner should I use?": "pytest" },
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
      expect(H.posts.find((p) => p.path.endsWith("/answer"))?.body).toEqual({ request_id: "req-q-1", decision: "deny" }),
    );
  });
});

/* ======================================================= the composer */

describe("the composer", () => {
  function settled(over: Record<string, unknown> = {}) {
    seed([sess("a1", over)]);
    openSession("a1", { session: sess("a1", over), events: SC.deny, running: false, pending: [] });
  }

  it("Full access on the mode chip takes two presses and is sent with confirm: true; an ordinary mode is one press", async () => {
    settled();
    H.postReply["PATCH /build/app/sessions/a1"] = sess("a1", { mode: "acceptEdits" });
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    await screen.findByTestId("code-session");
    fireEvent.click(screen.getByTestId("code-mode-chip-button"));
    fireEvent.click(screen.getByTestId("code-mode-chip-option-acceptEdits"));
    await waitFor(() => expect(H.patches).toHaveLength(1));
    expect(H.patches[0]).toEqual({ path: "/build/app/sessions/a1", body: { mode: "acceptEdits" } });
    H.postReply["PATCH /build/app/sessions/a1"] = sess("a1", { mode: "bypassPermissions" });
    fireEvent.click(screen.getByTestId("code-mode-chip-button"));
    fireEvent.click(screen.getByTestId("code-mode-chip-option-bypassPermissions"));
    await act(() => new Promise((r) => setTimeout(r, 20)));
    expect(H.patches).toHaveLength(1);
    fireEvent.click(screen.getByTestId("code-mode-chip-option-bypassPermissions"));
    await waitFor(() => expect(H.patches).toHaveLength(2));
    expect(H.patches[1].body).toEqual({ mode: "bypassPermissions", confirm: true });
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
      events: H.streams.length ? [...SC.deny, ...turn] : SC.deny,
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
    waitingOnEdit();
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    await screen.findByTestId("code-ask");
    expect(H.streams[0]).toEqual({ path: "/build/app/sessions/a1/turns/turn_7/stream", body: undefined });
    expect(screen.getAllByTestId("code-user")).toHaveLength(1);
    expect(screen.getAllByTestId("code-tool")).toHaveLength(1);
  });

  it("a replay that does not begin with the question puts the saved question back first", () => {
    const evs = SC.claude_edit.map((e) => decodeCodeEvent(e)!).filter(Boolean);
    const conv = foldConversation(evs);
    expect(conv.items.filter((i) => i.kind === "user")).toHaveLength(1);
    expect(conv.settled).toBe(true);
    expect(conv.toolSessionId).toBe("5f0c2a1e-1111-4a6b-9f00-000000000001");
  });

  it("an ask the session read lists (its event trimmed) is still asked", async () => {
    const pendingAsk = SC.codex_command.find((e) => e.type === "approval")!;
    seed([sess("a1")]);
    openSession("a1", {
      session: sess("a1"),
      events: [{ type: "user", at: "x", text: "Run the linter" }],
      running: true,
      pending: [pendingAsk],
    });
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    const card = await screen.findByTestId("code-ask");
    expect(within(card).getByTestId("code-ask-command").textContent).toContain("ruff check .");
  });
});
