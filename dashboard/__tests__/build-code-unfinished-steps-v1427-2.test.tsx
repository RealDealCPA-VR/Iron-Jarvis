/**
 * v1.427.2 (a USER BUG, with a screenshot) — Build › Code drew spinners on
 * steps for days: Claude Code was working on its own when Iron Jarvis closed,
 * and its log kept six steps "running". The daemon now closes every step its
 * tool never finished with a `stopped` row (tests/test_build_app_unfinished_
 * steps_v14272.py, the same shape as the real log). Here: the screen reads
 * that status and draws "did not finish", never a spinner, and a closing row
 * the app wrote is not counted as the reply's work.
 */

import React from "react";
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";

import { CodeConversation } from "@/components/buildCode/CodeConversation";
import {
  STEP_STOPPED_WORDS,
  decodeCodeEvent,
  foldConversation,
  type CodeEvent,
  type ConvItem,
} from "@/lib/buildApp";

afterEach(cleanup);

function decode(rows: unknown[]): CodeEvent[] {
  return rows.map((e) => decodeCodeEvent(e)).filter((e): e is CodeEvent => !!e);
}

const tool = (item: string, status: string, at: string, extra: Record<string, unknown> = {}) => ({
  type: "tool", at, item, kind: "command", name: "Bash", title: `Run ${item}`, input: "{}", status, ...extra,
});

/** The end of the user's real log, then the rows the daemon now adds. */
const LOG = [
  { type: "user", at: "2026-10-31T20:05:26Z", text: "keep going", turn: "t1" },
  { type: "text_done", at: "2026-10-31T20:18:58Z", item: "m1:t0", text: "Five teams are building.", turn: "t1" },
  { type: "done", at: "2026-10-31T20:18:59Z", stop: "completed", turn: "t1" },
  tool("vitest", "running", "2026-10-31T20:20:56Z", { input: "{\"command\": \"npx vitest run\"}" }),
  { type: "text_done", at: "2026-10-31T20:21:00Z", item: "m2:t0", text: "Let me write a probe test." },
  tool("grep", "running", "2026-10-31T20:21:03Z"),
  // What the daemon's boot (or the tool's exit) appends:
  { type: "tool", at: "2026-10-31T20:21:03Z", item: "vitest", kind: "command", name: "Bash", title: "Run vitest",
    input: "", status: "stopped" },
  { type: "tool", at: "2026-10-31T20:21:03Z", item: "grep", kind: "command", name: "Bash", title: "Run grep",
    input: "", status: "stopped" },
  { type: "notice", at: "2026-10-31T20:21:03Z",
    text: "Iron Jarvis closed while Claude Code was working on its own, so 2 steps did not finish. Send a message to carry on." },
];

function tools(items: readonly ConvItem[]) {
  return items.filter((i): i is Extract<ConvItem, { kind: "tool" }> => i.kind === "tool");
}

describe("a step the tool never finished", () => {
  it("decodes as stopped; an unknown status still reads as running", () => {
    expect(decodeCodeEvent(tool("a", "stopped", "2026-10-31T20:00:00Z"))).toMatchObject({ status: "stopped" });
    expect(decodeCodeEvent(tool("a", "weird", "2026-10-31T20:00:00Z"))).toMatchObject({ status: "running" });
  });

  it("folds into its own row, keeping what the row said before", () => {
    const rows = tools(foldConversation(decode(LOG)).items);
    expect(rows.map((r) => [r.tool.item, r.tool.status])).toEqual([["vitest", "stopped"], ["grep", "stopped"]]);
    expect(rows[0].tool.input).toContain("npx vitest run");
  });

  it("is drawn 'did not finish', with no spinner, and the notice says why", () => {
    const { items } = foldConversation(decode(LOG));
    const { container } = render(<CodeConversation items={items} folder="C:\\work" tool="claude-code" />);
    const rows = screen.getAllByTestId("code-tool");
    expect(rows).toHaveLength(2);
    for (const row of rows) {
      expect(row.getAttribute("data-status")).toBe("stopped");
      expect(within(row).getByTestId("code-tool-stopped").textContent).toContain(STEP_STOPPED_WORDS);
    }
    expect(container.querySelector(".animate-spin")).toBeNull();
    expect(screen.getByText(/so 2 steps did not finish/)).toBeTruthy();
  });

  it("a still-running step keeps its spinner (control)", () => {
    const { items } = foldConversation(decode(LOG.slice(0, 4)));
    const { container } = render(<CodeConversation items={items} folder="C:\\work" tool="claude-code" />);
    expect(screen.getByTestId("code-tool").getAttribute("data-status")).toBe("running");
    expect(container.querySelector(".animate-spin")).not.toBeNull();
    expect(screen.queryByTestId("code-tool-stopped")).toBeNull();
  });

  it("a closing row written hours later never stretches the cut reply's time", () => {
    const { items } = foldConversation(decode([
      { type: "user", at: "2026-11-03T10:00:00Z", text: "go", turn: "t2" },
      tool("s1", "running", "2026-11-03T10:00:30Z", { turn: "t2" }),
      // A boot that closed it much later (an older daemon's time, say).
      { type: "tool", at: "2026-11-03T18:00:00Z", item: "s1", kind: "command", name: "Bash", title: "Run s1",
        input: "", status: "stopped", turn: "t2" },
      { type: "error", at: "2026-11-03T18:00:00Z", title: "Iron Jarvis stopped while this was working", fix: "", technical: "", turn: "t2" },
      { type: "done", at: "2026-11-03T18:00:00Z", stop: "interrupted", turn: "t2" },
    ]));
    const done = items.find((i): i is Extract<ConvItem, { kind: "done" }> => i.kind === "done")!;
    expect(done.receipt?.seconds).toBe(30);
  });
});
