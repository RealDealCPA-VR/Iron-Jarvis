/**
 * v1.418.0 (fix plan wave 2, C4.2-C4.5) — Build › Code shows what the coding
 * tool is doing, drawn from the events the REAL daemon sends
 * (__tests__/fixtures/build-app-events-v1409.json, IJ_REGEN_BUILD_APP=1,
 * from tests/test_build_app_v1409.py):
 *
 *   - C4.2: a helper's (Task / Agent) steps and answer fold INSIDE its row —
 *     closed by default with a count and the helper's answer; a row whose
 *     input is still being written says how much has come;
 *   - C4.3: thinking reads "Thinking…" while it is the newest thing the turn
 *     said, then how long it took; Claude Code's own system messages (hooks,
 *     compacting, a refused request) are notice rows;
 *   - C4.4: a command's title is the tool's own description, the command
 *     under it; a cut output offers "Show all N characters", which asks
 *     `onExpand(item)` and shows what it gives back;
 *   - C4.5: the recorded Full-access helper's edit shows its change.
 */

import React from "react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { CodeConversation } from "@/components/buildCode/CodeConversation";
import {
  decodeCodeEvent,
  decodeItemText,
  foldConversation,
  helperSummary,
  reuseItems,
  showAllWords,
  type CodeEvent,
  type ConvItem,
} from "@/lib/buildApp";

const GOLDEN = JSON.parse(
  readFileSync(join(__dirname, "fixtures", "build-app-events-v1409.json"), "utf8").replace(/\r\n/g, "\n"),
) as { scenarios: Record<string, unknown[]> };

function scenario(name: string): CodeEvent[] {
  return GOLDEN.scenarios[name].map((e) => decodeCodeEvent(e)).filter((e): e is CodeEvent => !!e);
}

const AGENT = "toolu_01C2ykXYSx2B2DMzAqfHZEGc";

afterEach(cleanup);

describe("C4.2 a helper's work folds inside its row", () => {
  it("the recorded helper holds its Write and its answer; neither is a top-level row", () => {
    const { items } = foldConversation(scenario("claude_full_mode_helper"));
    const top = items.filter((i): i is Extract<ConvItem, { kind: "tool" }> => i.kind === "tool");
    expect(top.map((t) => t.tool.name)).toEqual(["Read", "Edit", "Agent"]);
    const helper = top.find((t) => t.tool.item === AGENT)!;
    expect(helper.tool.title).toBe("Helper: Create new.txt containing hi");
    const kids = helper.children ?? [];
    expect(kids.map((k) => k.kind)).toEqual(["tool", "text"]);
    expect(helperSummary(kids)).toEqual({ steps: 1, finalText: "created" });
    expect(items.some((i) => i.kind === "text" && i.text === "created")).toBe(false);
  });

  it("is closed by default with its count and answer, and opens onto its steps", () => {
    const { items } = foldConversation(scenario("claude_full_mode_helper"));
    render(<CodeConversation items={items} folder="C:\\work\\demo" tool="claude-code" />);
    const rows = screen.getAllByTestId("code-tool");
    const helperRow = rows.find((r) => r.dataset.kind === "task")!;
    expect(within(helperRow).getByTestId("code-helper-count").textContent).toContain("1 step");
    expect(within(helperRow).getByTestId("code-helper-final").textContent).toBe("created");
    expect(within(helperRow).queryByTestId("code-helper-children")).toBeNull();
    // Only the three top-level steps are drawn while it is closed.
    expect(rows).toHaveLength(3);
    fireEvent.click(within(helperRow).getByTestId("work-row"));
    const kids = within(helperRow).getByTestId("code-helper-children");
    const nested = within(kids).getAllByTestId("code-tool");
    expect(nested.map((n) => n.dataset.kind)).toEqual(["write"]);
    // C4.5: the helper's Write came with no tool_use_result: its change shows.
    expect(within(nested[0]).getByTestId("code-tool-diff")).toBeTruthy();
    expect(within(kids).getByTestId("code-text").textContent).toContain("created");
  });

  it("a helper whose own step is not in the list still gets a row to hold its work", () => {
    const evs: CodeEvent[] = [
      { type: "user", at: "2026-10-31T10:00:00Z", text: "go", files: [] },
      { type: "text_done", at: "2026-10-31T10:00:01Z", item: "m:t0", text: "done inside", parent: "T9" },
    ];
    const { items } = foldConversation(evs);
    const row = items.find((i) => i.kind === "tool") as Extract<ConvItem, { kind: "tool" }>;
    expect(row.tool.item).toBe("T9");
    expect(row.children?.map((c) => c.kind)).toEqual(["text"]);
  });

  it("a step still being written says how much has come", () => {
    const partial = decodeCodeEvent({
      type: "tool", at: "2026-10-31T10:00:00Z", item: "t1", kind: "write", name: "Write", title: "Write notes.md",
      input: "…line", status: "running", partial: true, input_chars: 1234,
    })!;
    expect(partial.type === "tool" && partial.partial).toBe(true);
    const { items } = foldConversation([
      { type: "user", at: "2026-10-31T10:00:00Z", text: "go", files: [] },
      partial,
    ]);
    render(<CodeConversation items={items} folder="" tool="claude-code" />);
    const row = screen.getByTestId("code-tool");
    expect(row.dataset.partial).toBe("true");
    expect(within(row).getByTestId("code-tool-partial").textContent).toContain("1,234 characters so far");
  });
});

describe("C4.3 thinking live; Claude Code's own messages", () => {
  const user: CodeEvent = { type: "user", at: "2026-10-31T10:00:00Z", text: "go", files: [] };
  const think: CodeEvent = { type: "thinking", at: "2026-10-31T10:00:01Z", item: "m:0", delta: "Let me see" };

  it("reads Thinking… while it is the newest thing the turn said", () => {
    const { items } = foldConversation([user, think]);
    const t = items.find((i) => i.kind === "thinking") as Extract<ConvItem, { kind: "thinking" }>;
    expect(t.live).toBe(true);
    render(<CodeConversation items={items} folder="" tool="claude-code" />);
    expect(screen.getByTestId("thinking-summary").textContent).toBe("Thinking…");
  });

  it("then says how long it took", () => {
    const text: CodeEvent = { type: "text", at: "2026-10-31T10:00:08Z", item: "m:1", delta: "Here." };
    const { items } = foldConversation([user, think, text]);
    const t = items.find((i) => i.kind === "thinking") as Extract<ConvItem, { kind: "thinking" }>;
    expect(t).toMatchObject({ live: false, seconds: 7 });
    render(<CodeConversation items={items} folder="" tool="claude-code" />);
    expect(screen.getByTestId("thinking-summary").textContent).toBe("Thought for 7 s");
  });

  it("a finished turn's thinking is never live", () => {
    const done: CodeEvent = { type: "done", at: "2026-10-31T10:00:03Z", stop: "completed", result: "" };
    const { items } = foldConversation([user, think, done]);
    expect(items.find((i) => i.kind === "thinking")).toMatchObject({ live: false, seconds: 2 });
    // A record with no turn around it (nothing running) never says live.
    expect(foldConversation([think]).items[0]).toMatchObject({ kind: "thinking", live: false, seconds: null });
  });

  it("a recorded hook run, a refused hook callback and a failed compaction are notice rows", () => {
    const { items } = foldConversation(scenario("claude_hooks_and_long_output"));
    render(<CodeConversation items={items} folder="" tool="claude-code" />);
    const notices = screen.getAllByTestId("code-notice").map((n) => n.textContent);
    expect(notices).toContain("Hook SessionStart:startup (SessionStart) ran: Loaded the team's notes.");
    expect(notices).toContain("Hook PostToolUse:Bash (PostToolUse) failed: lint: 2 warnings in file_0003.py");
    expect(notices.some((n) => n?.startsWith("Claude Code asked Iron Jarvis to answer one of its hooks"))).toBe(true);
    expect(notices).toContain("Claude Code is compacting the conversation to make room…");
    expect(notices.filter((n) => n?.includes("compacting"))).toHaveLength(1);
  });
});

describe("C4.4 the tool's own title; the rest of a cut output", () => {
  it("a command's title is its description, the command under it", () => {
    const { items } = foldConversation(scenario("claude_hooks_and_long_output"));
    render(<CodeConversation items={items} folder="" tool="claude-code" />);
    const row = screen.getByTestId("code-tool");
    expect(within(row).getByTestId("work-row").textContent).toContain("List tracked files and remotes");
    expect(within(row).getByTestId("code-tool-subtitle").textContent).toBe("git ls-files && git remote -v");
  });

  it("Show all asks for the step's whole text and shows it", async () => {
    const { items } = foldConversation(scenario("claude_hooks_and_long_output"));
    const tool = items.find((i) => i.kind === "tool") as Extract<ConvItem, { kind: "tool" }>;
    expect(tool.tool.more).toBe(true);
    expect(showAllWords(tool.tool)).toBe("Show all 6,759 characters");
    const whole = Array.from({ length: 260 }, (_, i) => `${String(i).padStart(4, "0")} tracked/file_${String(i).padStart(4, "0")}.py`).join("\n");
    const onExpand = vi.fn(async (item: string) => decodeItemText({ item, input: "{}", output: whole }));
    render(<CodeConversation items={items} folder="" tool="claude-code" onExpand={onExpand} />);
    const row = screen.getByTestId("code-tool");
    expect(within(row).queryByTestId("code-tool-show-all")).toBeNull();
    fireEvent.click(within(row).getByTestId("work-row"));
    expect(within(row).getByTestId("step-detail-output").textContent).not.toContain("0259 tracked/file_0259.py");
    fireEvent.click(within(row).getByTestId("code-tool-show-all"));
    expect(onExpand).toHaveBeenCalledWith("toolu_H1");
    await waitFor(() => expect(within(row).queryByTestId("code-tool-show-all")).toBeNull());
    fireEvent.click(within(row).getByTestId("step-detail-more"));
    expect(within(row).getByTestId("step-detail-output").textContent).toContain("0259 tracked/file_0259.py");
  });

  it("says so when the rest cannot be read, and offers no press with nothing to read it", async () => {
    const { items } = foldConversation(scenario("claude_hooks_and_long_output"));
    const { unmount } = render(
      <CodeConversation items={items} folder="" tool="claude-code" onExpand={async () => null} />,
    );
    fireEvent.click(screen.getByTestId("work-row"));
    fireEvent.click(screen.getByTestId("code-tool-show-all"));
    await waitFor(() => expect(screen.getByTestId("code-tool-show-all-failed")).toBeTruthy());
    unmount();
    render(<CodeConversation items={items} folder="" tool="claude-code" />);
    fireEvent.click(screen.getByTestId("work-row"));
    expect(screen.queryByTestId("code-tool-show-all")).toBeNull();
  });

  it("decodes only the record's own shape", () => {
    expect(decodeItemText({ item: "t", input: "a", output: "b" })).toEqual({ input: "a", output: "b" });
    expect(decodeItemText({ detail: "nope" })).toBeNull();
    expect(decodeItemText(null)).toBeNull();
  });
});

describe("review: a helper's steps keep their objects while it works (the v1.250.0 rule)", () => {
  it("a new frame inside a helper re-creates only the child that changed", () => {
    const evs = scenario("claude_full_mode_helper");
    const first = foldConversation(evs).items;
    const helperAt = first.findIndex((i) => i.kind === "tool" && i.tool.item === AGENT);
    const more: CodeEvent = { type: "text", at: "2026-10-31T00:00:00Z", item: "helper-live", delta: "more", parent: AGENT };
    const next = reuseItems(first, foldConversation([...evs, more]).items);
    const before = (first[helperAt] as Extract<ConvItem, { kind: "tool" }>).children ?? [];
    const after = (next[helperAt] as Extract<ConvItem, { kind: "tool" }>).children ?? [];
    expect(after.length).toBe(before.length + 1);
    // The memoised child rows of an OPEN helper draw again only when their
    // own item changed.
    before.forEach((child, i) => expect(after[i]).toBe(child));
  });
});
