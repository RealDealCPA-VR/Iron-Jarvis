/**
 * v1.449.0 (plan 0h.5 item 10, Wave B "Did it work?, everywhere") — Build ›
 * Code's receipt reads like every other receipt in the app:
 *
 *   - the duration is the app's own words (lib/durationWords.secondsText:
 *     "9.0 s", "2 min 14 s"), never "2 m 14 s" or "1 h 3 m";
 *   - the collapsed line LEADS with the outcome's glyph in its tone — a ✓
 *     (text-tone-success) for a reply that ran to its end with no failed
 *     step, a ⚠ (text-tone-warn) for a stopped, failed or cut-off reply or one
 *     with a step that failed — the way WorkLine marks a chat reply;
 *   - a failed step is said apart ("4 actions · 1 failed"), and the usage
 *     line stays folded under the summary.
 *
 * The view is the REAL BuildCodeView fed hand-made events in the daemon's own
 * shape; only the network (lib/api), the stream and the folder tree are doubles.
 */

import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";

const H = vi.hoisted(() => ({
  gets: {} as Record<string, unknown>,
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
    post: () => Promise.resolve({}),
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

import { BuildCodeView } from "@/components/buildCode/BuildCodeView";
import { decodeCodeEvent, foldConversation, receiptDuration, receiptLine, receiptOutcome, type CodeEvent } from "@/lib/buildApp";
import { secondsText } from "@/lib/durationWords";

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
      accounts: [{ id: "prof_work", name: "Work", status: "ready" }],
      commands: [],
    },
  ],
};

const sess = (id: string) => ({
  id,
  harness: "claude-code",
  folder: "C:\\work\\shop",
  title: `Session ${id}`,
  account: "prof_work",
  account_name: "Work",
  model: "",
  effort: "",
  mode: "ask",
  harness_session_id: "",
  created_at: "2026-10-29T09:00:00Z",
  updated_at: "2026-10-29T09:00:00Z",
  archived_at: null,
  last_error: "",
});

function open(id: string, events: Record<string, unknown>[]) {
  H.gets = {
    "/build/app/harnesses": TOOLS,
    "/build/app/sessions": { sessions: [sess(id)] },
    "/editor/options": { editors: [], preferred: "" },
    [`/build/app/sessions/${id}`]: { session: sess(id), events, running: null, pending: [] },
  };
}

/** A reply: the question, the tool's session, its steps, its usage, its end. */
function reply(steps: Record<string, unknown>[], stop: string, endAt = "2026-10-31T10:02:14Z"): Record<string, unknown>[] {
  return [
    { type: "user", at: "2026-10-31T10:00:00Z", text: "go" },
    { type: "session", at: "2026-10-31T10:00:01Z", harness: "claude-code", harness_session_id: "x", model: "claude-opus-5-5", cwd: "", mode: "ask" },
    ...steps,
    { type: "usage", at: "2026-10-31T10:02:13Z", input: 1200, output: 300, cost_usd: 0.02, list_price: true, cost_reported_by: "Claude Code" },
    { type: "done", at: endAt, stop },
  ];
}

const OK_STEPS = [
  { type: "tool", at: "2026-10-31T10:00:02Z", item: "1", kind: "edit", name: "Edit", title: "Edit a", status: "running", path: "a.py" },
  { type: "tool", at: "2026-10-31T10:00:05Z", item: "1", kind: "edit", name: "Edit", title: "Edit a", status: "done", path: "a.py" },
  { type: "tool", at: "2026-10-31T10:00:06Z", item: "2", kind: "command", name: "Bash", title: "Run the tests", status: "done" },
];

afterEach(() => cleanup());
beforeEach(() => {
  H.gets = {};
});

describe("the duration is the app's own words", () => {
  it("receiptDuration speaks lib/durationWords, never 'm'", () => {
    expect(receiptDuration(134)).toBe("2 min 14 s");
    expect(receiptDuration(134)).toBe(secondsText(134_000));
    expect(receiptDuration(9)).toBe("9.0 s");
    expect(receiptDuration(14)).toBe("14 s");
    expect(receiptDuration(3780)).toBe("63 min");
    expect(receiptDuration(-4)).toBe("0.0 s");
    expect(receiptDuration(134)).not.toMatch(/\d m \d/);
  });

  it("the fold keeps a reply's length to the millisecond, as the chat's clock does", () => {
    const evs = reply(OK_STEPS, "completed", "2026-10-31T10:00:05.300Z").map((e) => decodeCodeEvent(e)!) as CodeEvent[];
    const done = foldConversation(evs).items.at(-1);
    if (done?.kind !== "done" || !done.receipt) throw new Error("no receipt");
    expect(done.receipt.seconds).toBeCloseTo(5.3, 3);
    expect(receiptLine(done.receipt)).toBe("answered by claude-opus-5-5 · 5.3 s · 2 actions · 1 file changed");
  });
});

describe("the outcome's glyph", () => {
  it("a reply that ran to its end with no failed step is 'ok'; a stop, a failure, a cut-off or a failed step is 'warn'", () => {
    const none = { failed: 0 };
    expect(receiptOutcome("completed", none)).toBe("ok");
    expect(receiptOutcome("completed", null)).toBe("ok");
    expect(receiptOutcome("completed", { failed: 1 })).toBe("warn");
    expect(receiptOutcome("interrupted", none)).toBe("warn");
    expect(receiptOutcome("failed", none)).toBe("warn");
    expect(receiptOutcome("max_turns", none)).toBe("warn");
  });

  it("a failed step is counted apart on the line and never counted as landed once it fails", () => {
    const steps = [
      ...OK_STEPS,
      { type: "tool", at: "2026-10-31T10:00:07Z", item: "3", kind: "command", name: "Bash", title: "Run lint", status: "running" },
      { type: "tool", at: "2026-10-31T10:00:09Z", item: "3", kind: "command", name: "Bash", title: "Run lint", status: "failed" },
      // A step that failed and then LANDED (the tool retried it) is not a failure.
      { type: "tool", at: "2026-10-31T10:00:10Z", item: "4", kind: "read", name: "Read", title: "Read b", status: "failed" },
      { type: "tool", at: "2026-10-31T10:00:11Z", item: "4", kind: "read", name: "Read", title: "Read b", status: "done" },
      // A plan update is neither a step nor a failure.
      { type: "tool", at: "2026-10-31T10:00:12Z", item: "5", kind: "todo", name: "TodoWrite", title: "Plan", status: "failed" },
    ];
    const evs = reply(steps, "completed").map((e) => decodeCodeEvent(e)!) as CodeEvent[];
    const done = foldConversation(evs).items.at(-1);
    if (done?.kind !== "done" || !done.receipt) throw new Error("no receipt");
    expect(done.receipt.actions).toBe(4);
    expect(done.receipt.failed).toBe(1);
    expect(receiptLine(done.receipt, "Work")).toBe("answered by claude-opus-5-5 on Work · 2 min 14 s · 4 actions · 1 failed · 1 file changed");
    expect(receiptOutcome("completed", done.receipt)).toBe("warn");
  });

  it("on screen: the collapsed line leads with a ✓ in the success tone, the usage folded under it", async () => {
    open("a1", reply(OK_STEPS, "completed"));
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    const receipt = await screen.findByTestId("code-receipt", {}, { timeout: 10_000 });
    expect(receipt.getAttribute("data-outcome")).toBe("ok");
    expect(receipt).not.toHaveAttribute("open");
    const summary = receipt.querySelector("summary")!;
    const check = within(summary).getByTestId("code-receipt-check");
    expect(check.getAttribute("class")).toContain("text-tone-success");
    expect(check.getAttribute("aria-hidden")).toBe("true");
    expect(within(summary).queryByTestId("code-receipt-warn")).toBeNull();
    // The glyph comes FIRST, then the words.
    expect(summary.firstElementChild).toBe(check);
    expect(within(summary).getByTestId("code-receipt-line").textContent).toBe("answered by Opus 5.5 on Work · 2 min 14 s · 2 actions · 1 file changed");
    // The usage line is inside the fold, said once.
    expect(screen.getAllByTestId("code-usage")).toHaveLength(1);
    expect(within(receipt).getByTestId("code-usage").textContent).toContain("1,200 in");
  });

  it("on screen: a stopped reply leads with a ⚠ in the warning tone", async () => {
    open("a1", reply(OK_STEPS, "interrupted"));
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    const receipt = await screen.findByTestId("code-receipt", {}, { timeout: 10_000 });
    expect(receipt.getAttribute("data-outcome")).toBe("warn");
    const warn = within(receipt).getByTestId("code-receipt-warn");
    expect(warn.getAttribute("class")).toContain("text-tone-warn");
    expect(within(receipt).queryByTestId("code-receipt-check")).toBeNull();
    // The stop's own words still stand above the receipt.
    expect(screen.getByTestId("code-done").textContent).toBe("Stopped.");
  });

  it("on screen: a reply that finished with a failed step leads with a ⚠ and says '1 failed'", async () => {
    const steps = [
      ...OK_STEPS,
      { type: "tool", at: "2026-10-31T10:00:09Z", item: "3", kind: "command", name: "Bash", title: "Run lint", status: "failed" },
    ];
    open("a1", reply(steps, "completed"));
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    const receipt = await screen.findByTestId("code-receipt", {}, { timeout: 10_000 });
    expect(receipt.getAttribute("data-outcome")).toBe("warn");
    expect(within(receipt).getByTestId("code-receipt-warn")).toBeTruthy();
    expect(within(receipt).getByTestId("code-receipt-line").textContent).toContain("3 actions · 1 failed");
  });
});
