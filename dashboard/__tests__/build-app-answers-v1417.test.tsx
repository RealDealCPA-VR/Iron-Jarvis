/**
 * v1.417.0 (fix plan wave 1) — Build › Code's cards, failures and receipts,
 * driven through the real view (components/buildCode/BuildCodeView), its
 * question card and lib/buildApp, fed the events the REAL daemon sends
 * (__tests__/fixtures/build-app-events-v1409.json, IJ_REGEN_BUILD_APP=1):
 *
 *   - C1.1: a command card offers "Allow all commands this session" (the
 *     answer `allow_commands`); the tool's own allow then reads "Allow
 *     similar commands"; a card without it keeps "Allow for this session";
 *   - C4.1: Claude Code's plan card shows the plan; "No, tell it what to do
 *     instead" sends the user's words as the deny `message`;
 *   - C1.7: Enter allows ONCE on every card except a safety once-only one and
 *     Full access (Chat › Code's rule);
 *   - C4.9: a failure names the tool and offers Retry (the same message);
 *     each reply ends with a folded receipt ("answered by … · N actions").
 */

import React from "react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const H = vi.hoisted(() => ({
  gets: {} as Record<string, unknown>,
  posts: [] as { path: string; body: unknown }[],
  streams: [] as { path: string; body: unknown }[],
  scripts: [] as { event: string; data: Record<string, unknown> }[][],
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
  streamSSE: async function* (
    path: string,
    body: unknown,
    signal?: AbortSignal,
    watch?: { onFrame?: (event: string, data: Record<string, unknown>) => void },
  ) {
    H.streams.push({ path, body });
    for (const f of H.scripts.shift() ?? []) {
      if (signal?.aborted) return;
      watch?.onFrame?.(f.event, f.data);
      await Promise.resolve();
    }
  },
  useLiveText: (s: { text?: string }) => s?.text ?? "",
}));

vi.mock("@/components/terminal/DirectoryTree", () => ({
  DirectoryTree: () => <span />,
}));

import { BuildCodeView } from "@/components/buildCode/BuildCodeView";
import { ALLOW_ALL_COMMANDS, ALLOW_SIMILAR, REDIRECT_WORDS } from "@/components/buildCode/CodeAskCard";
import {
  codeAskKeyHint,
  decodeCodeEvent,
  enterAllowsCodeAsk,
  foldConversation,
  receiptLine,
  resolvedWords,
  type CodeEvent,
} from "@/lib/buildApp";

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
        { id: "full", label: "Full access", dangerous: true },
      ],
      models: [{ id: "", label: "Claude Code's default" }],
      efforts: [],
      accounts: [{ id: "prof_work", name: "Work", status: "ready" }],
      commands: [],
    },
    {
      id: "codex",
      label: "Codex",
      installed: true,
      signed_in: true,
      modes: [
        { id: "ask", label: "Ask first", dangerous: false },
        { id: "full", label: "Full access", dangerous: true },
      ],
      models: [],
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
  harness_session_id: "",
  created_at: "2026-10-29T09:00:00Z",
  updated_at: "2026-10-29T09:00:00Z",
  archived_at: null,
  last_error: "",
  ...over,
});

function open(id: string, events: Record<string, unknown>[], running: unknown, over: Record<string, unknown> = {}) {
  H.gets = {
    "/build/app/harnesses": TOOLS,
    "/build/app/sessions": { sessions: [sess(id, over)] },
    "/editor/options": { editors: [], preferred: "" },
    [`/build/app/sessions/${id}`]: { session: sess(id, over), events, running, pending: [] },
  };
}

/** The scenario up to (and with) its first approval: a turn waiting. */
const upToAsk = (name: string) => SC[name].slice(0, SC[name].findIndex((e) => e.type === "approval") + 1);

const ARM_MS = 450;
const pause = (ms: number) => act(() => new Promise<void>((r) => setTimeout(r, ms)));

beforeEach(() => {
  H.posts = [];
  H.streams = [];
  H.scripts = [];
});
afterEach(() => cleanup());

const answers = () => H.posts.filter((p) => p.path.endsWith("/answer")).map((p) => p.body);

/* ======================================================== the contract */

describe("the contract from the real daemon", () => {
  it("a command card says it takes allow_commands; a plan card carries the plan", () => {
    const ask = decodeCodeEvent(upToAsk("claude_allow_all_commands").at(-1)) as Extract<CodeEvent, { type: "approval" }>;
    expect(ask.canAllCommands).toBe(true);
    expect(ask.detail).toContain(`${ALLOW_SIMILAR} always allows PowerShell(Get-ChildItem *)`);
    const plan = decodeCodeEvent(upToAsk("claude_plan_deny_with_words").at(-1)) as Extract<CodeEvent, { type: "approval" }>;
    expect(plan.canAllCommands).toBe(false);
    expect(plan.plan).toContain("# Plan");
    expect(plan.title).toBe("Start working on this plan");
    const resolved = SC.claude_allow_all_commands.find((e) => e.type === "resolved");
    expect(resolved?.decision).toBe("allow_commands");
    expect(resolvedWords("allow_commands")).toBe("Allowed all commands for this session");
  });

  it("a failure no rule knows names the tool (never Iron Jarvis)", () => {
    const err = SC.claude_failed_generic.find((e) => e.type === "error");
    expect(err?.title).toBe("Claude Code couldn't finish this answer");
    expect(String(err?.fix)).toMatch(/^Press Retry\./);
  });
});

/* ===================================================== C1.1 the answers */

describe("Allow all commands this session (C1.1)", () => {
  it("the card offers it beside the tool's own, renamed; the press posts allow_commands", async () => {
    open("a1", upToAsk("claude_allow_all_commands"), "turn_1");
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    const card = await screen.findByTestId("code-ask");
    expect(within(card).getByTestId("code-ask-allow-commands").textContent).toBe(ALLOW_ALL_COMMANDS);
    expect(within(card).getByTestId("code-ask-allow-session").textContent).toBe(ALLOW_SIMILAR);
    fireEvent.click(within(card).getByTestId("code-ask-allow-commands"));
    await waitFor(() => expect(answers()).toEqual([{ request_id: "ask-1", decision: "allow_commands" }]));
    expect((await screen.findByTestId("code-resolved")).textContent).toContain("Allowed all commands for this session");
  });

  it("a card that does not offer it keeps 'Allow for this session' and shows no third button", async () => {
    open("a1", upToAsk("claude_tool_approval_edit"), "turn_1");
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    const card = await screen.findByTestId("code-ask");
    expect(within(card).queryByTestId("code-ask-allow-commands")).toBeNull();
    const session = within(card).queryByTestId("code-ask-allow-session");
    if (session) expect(session.textContent).toBe("Allow for this session");
  });
});

/* ========================================== C4.1 the plan, a no with words */

describe("the plan card (C4.1)", () => {
  it("shows the plan, and 'No, tell it what to do instead' sends the words as the deny message", async () => {
    open("a1", upToAsk("claude_plan_deny_with_words"), "turn_1");
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    const card = await screen.findByTestId("code-ask");
    const plan = within(card).getByTestId("code-ask-plan");
    expect(within(plan).getByRole("heading", { name: "Plan" })).toBeInTheDocument();
    expect(plan.textContent).toContain("Write b.txt with its words reversed.");
    expect(within(card).getByTestId("code-ask-redirect").textContent).toBe(REDIRECT_WORDS);
    fireEvent.click(within(card).getByTestId("code-ask-redirect"));
    fireEvent.change(within(card).getByTestId("code-ask-redirect-input"), { target: { value: "Plan it in one file." } });
    fireEvent.click(within(card).getByTestId("code-ask-redirect-send"));
    await waitFor(() =>
      expect(answers()).toEqual([{ request_id: "ask-1", decision: "deny", message: "Plan it in one file." }]),
    );
  });

  it("the words go with Enter (and only words: an empty box sends nothing)", async () => {
    open("a1", upToAsk("claude_plan_deny_with_words"), "turn_1");
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    const card = await screen.findByTestId("code-ask");
    await pause(ARM_MS);
    fireEvent.click(within(card).getByTestId("code-ask-redirect"));
    const box = within(card).getByTestId("code-ask-redirect-input");
    await waitFor(() => expect(document.activeElement).toBe(box));
    fireEvent.keyDown(box, { key: "Enter" });
    await pause(30);
    expect(answers()).toEqual([]);
    fireEvent.change(box, { target: { value: "Write it in one file instead." } });
    fireEvent.keyDown(box, { key: "Enter" });
    await waitFor(() =>
      expect(answers()).toEqual([{ request_id: "ask-1", decision: "deny", message: "Write it in one file instead." }]),
    );
  });

  it("Codex reads no words with a no: its card offers no such press", async () => {
    open("c1", upToAsk("codex_deny"), "turn_1", { harness: "codex" });
    render(<BuildCodeView initialSession="c1" onOpenedTerminal={() => {}} />);
    const card = await screen.findByTestId("code-ask");
    expect(within(card).queryByTestId("code-ask-redirect")).toBeNull();
  });
});

/* ================================================== C1.7 Enter, predictably */

describe("Enter is predictable (C1.7)", () => {
  const once = { type: "approval", at: "x", turn: "t", request_id: "ask-9", kind: "command", title: "Run ls", command: "ls", can_session: false, once_only: true };

  it("Enter allows a once-only card ONCE when no safety reason keeps it a click", async () => {
    open("a1", [{ type: "user", at: "x", turn: "t", text: "go" }, once], "t");
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    await screen.findByTestId("code-ask");
    (document.activeElement as HTMLElement | null)?.blur();
    await pause(ARM_MS);
    expect(screen.getByTestId("code-ask-keys").textContent).toBe("Enter to allow once · Esc to deny");
    // The arming clock is the card's own (a re-read of the view can start it
    // again), so a press exactly ARM_MS after the card appeared raced it on
    // the Tests runner (v1.418.1: `expected [] to deeply equal [...]`). Press
    // until the answer is posted — and only while nothing has been posted,
    // so a slow answer is never followed by a second press.
    // v1.422.1: press where a REAL key goes (the focused element, else the
    // page). This fixture's stream ends at once, so the view re-reads every
    // 2 s; a slow re-read briefly drops the card, the message box takes the
    // caret ("the last question answered"), and the card that comes back
    // takes focus from it. A key sent to `window` then never reaches the
    // card (its window listener only answers when nothing is focused) — CI's
    // same error again on v1.422.0's master run. Holding the later re-reads
    // 400 ms reproduces it on the old press; a real key reaches the card.
    await waitFor(() => {
      if (answers().length === 0) fireEvent.keyDown(document.activeElement ?? document.body, { key: "Enter" });
      expect(answers()).toEqual([{ request_id: "ask-9", decision: "allow" }]);
    }, { timeout: 8_000, interval: 100 });
  });

  it("a safety once-only card is a click; Full access is a click", async () => {
    open("a1", [{ type: "user", at: "x", turn: "t", text: "go" }, { ...once, once_reason: "git" }], "t");
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    await screen.findByTestId("code-ask");
    (document.activeElement as HTMLElement | null)?.blur();
    await pause(ARM_MS);
    expect(screen.getByTestId("code-ask-keys").textContent).toBe("Click Allow once to allow it · Esc to deny");
    // v1.422.1: where a real key goes (see the test above) — a press the card
    // never heard would pass this test for the wrong reason.
    fireEvent.keyDown(document.activeElement ?? document.body, { key: "Enter" });
    await pause(30);
    expect(answers()).toEqual([]);
    const plain = decodeCodeEvent({ ...once, once_only: false }) as Extract<CodeEvent, { type: "approval" }>;
    expect(enterAllowsCodeAsk(plain, true)).toBe(false);
    expect(codeAskKeyHint(plain, true)).toBe("Click Allow once to allow it · Esc to deny");
  });
});

/* ===================================================== C4.9 Retry, receipt */

describe("a failure has a press, and a reply has a receipt (C4.9)", () => {
  it("the newest failure names the tool and Retry sends the same message again", async () => {
    open("a1", SC.claude_failed_generic, null);
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    const err = await screen.findByTestId("code-error");
    expect(err.textContent).toContain("Claude Code couldn't finish this answer");
    fireEvent.click(within(err).getByTestId("code-retry"));
    await waitFor(() => expect(H.streams.at(-1)?.path).toBe("/build/app/sessions/a1/turn"));
    expect((H.streams.at(-1)?.body as { text: string }).text).toBe("do it");
  });

  it("an account's limit keeps its own press (no Retry beside it)", async () => {
    open("a1", SC.claude_account_limit_synthesized, null, { account: "prof_work" });
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    const err = await screen.findByTestId("code-error");
    expect(within(err).queryByTestId("code-retry")).toBeNull();
  });

  it("an older failure (a later reply answered) offers no Retry", async () => {
    open("a1", [...SC.claude_failed_generic, ...SC.claude_allow_all_commands], null);
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    await screen.findAllByTestId("code-receipt");
    expect(screen.queryByTestId("code-retry")).toBeNull();
  });

  it("a finished reply folds its usage into 'answered by … · N actions'", async () => {
    open("a1", SC.claude_allow_all_commands, null, { account: "prof_work", account_name: "Work" });
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    const receipt = await screen.findByTestId("code-receipt");
    expect(within(receipt).getByTestId("code-receipt-line").textContent).toBe("answered by Opus 5.5 on Work · 2 actions");
    // The usage line is inside the fold, said once.
    expect(screen.getAllByTestId("code-usage")).toHaveLength(1);
    expect(within(receipt).getByTestId("code-usage").textContent).toContain("109,914 in");
  });

  it("the receipt reads the turn's own times, steps and changed files", () => {
    const evs = [
      { type: "user", at: "2026-10-31T10:00:00Z", text: "go" },
      { type: "session", at: "2026-10-31T10:00:01Z", harness: "codex", harness_session_id: "x", model: "gpt-6", cwd: "", mode: "ask" },
      { type: "tool", at: "x", item: "1", kind: "edit", name: "Edit", title: "Edit a", status: "running", path: "a.py" },
      { type: "tool", at: "x", item: "1", kind: "edit", name: "Edit", title: "Edit a", status: "done", path: "a.py" },
      { type: "tool", at: "x", item: "2", kind: "write", name: "Write", title: "Write b", status: "done", path: "b.py" },
      { type: "tool", at: "x", item: "3", kind: "command", name: "Bash", title: "Run t", status: "failed" },
      // A change that failed changed nothing.
      { type: "tool", at: "x", item: "5", kind: "edit", name: "Edit", title: "Edit c", status: "failed", path: "c.py" },
      { type: "tool", at: "x", item: "4", kind: "todo", name: "TodoWrite", title: "Update the plan", status: "done" },
      { type: "done", at: "2026-10-31T10:02:14Z", stop: "completed" },
    ].map((e) => decodeCodeEvent(e)!) as CodeEvent[];
    const done = foldConversation(evs).items.at(-1);
    expect(done?.kind).toBe("done");
    if (done?.kind !== "done" || !done.receipt) throw new Error("no receipt");
    expect(receiptLine(done.receipt, "Work")).toBe("answered by gpt-6 on Work · 2 m 14 s · 4 actions · 2 files changed");
    expect(receiptLine({ model: "", seconds: 9, actions: 1, files: 0, usage: null })).toBe("9 s · 1 action");
  });

  it("a turn Iron Jarvis's restart cut off is timed to its last step, never to the next start (review)", () => {
    // The boot reconcile writes the error and the done when the daemon starts
    // AGAIN (service.reconcile_at_boot) — hours later; the receipt must not
    // say the reply took that long.
    const evs = [
      { type: "user", at: "2026-10-31T10:00:00Z", text: "go" },
      { type: "tool", at: "2026-10-31T10:00:40Z", item: "1", kind: "command", name: "Bash", title: "Run t", status: "running" },
      { type: "error", at: "2026-11-01T09:00:00Z", title: "Stopped", fix: "", technical: "" },
      { type: "done", at: "2026-11-01T09:00:00Z", stop: "interrupted" },
    ].map((e) => decodeCodeEvent(e)!) as CodeEvent[];
    const done = foldConversation(evs).items.at(-1);
    if (done?.kind !== "done" || !done.receipt) throw new Error("no receipt");
    expect(done.receipt.seconds).toBe(40);
    // Nothing happened before the stop: no time is claimed at all.
    const bare = foldConversation(
      [
        { type: "user", at: "2026-10-31T10:00:00Z", text: "go" },
        { type: "done", at: "2026-11-01T09:00:00Z", stop: "interrupted" },
      ].map((e) => decodeCodeEvent(e)!) as CodeEvent[],
    ).items.at(-1);
    expect(bare?.kind === "done" ? bare.receipt?.seconds ?? null : "x").toBeNull();
  });

  it("a changed file is counted by the path the step named when it began (review)", () => {
    const evs = [
      { type: "user", at: "2026-10-31T10:00:00Z", text: "go" },
      { type: "tool", at: "x", item: "1", kind: "edit", name: "Edit", title: "Edit a", status: "running", path: "a.py" },
      // Its end names no path of its own.
      { type: "tool", at: "x", item: "1", kind: "edit", name: "Edit", title: "Edit a", status: "done" },
      { type: "done", at: "2026-10-31T10:00:09Z", stop: "completed" },
    ].map((e) => decodeCodeEvent(e)!) as CodeEvent[];
    const done = foldConversation(evs).items.at(-1);
    if (done?.kind !== "done" || !done.receipt) throw new Error("no receipt");
    expect(done.receipt.files).toBe(1);
  });
});
