/**
 * v1.473.0: the two cards a tool script leads to.
 *
 * 1. A BATCH (`apply_changes`): many file changes after one approval. The
 *    daemon sends one line per change (never the arguments themselves) and
 *    the card lists every line, so the one Allow covers a list the user can
 *    read in full.
 * 2. A KEPT VALUE (`_from_script`): the model's file call names a value a
 *    script worked out. The card shows a short summary of it, never the
 *    value, and never offers "Always": the same name can hold different data
 *    next time.
 *
 * Both are drawn from ONE fixture the Python suite writes from the real
 * stream lane (`fixtures/script-cards-v1473.json`,
 * `tests/test_script_power_lanes_v1473.py`, IJ_REGEN_SCRIPT_CARDS=1).
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const H = vi.hoisted(() => ({ post: vi.fn() }));

vi.mock("@/lib/api", async (orig) => {
  const real = await orig<typeof import("@/lib/api")>();
  return { ...real, API_BASE: "http://localhost", ijToken: () => "", post: H.post };
});

import { ApprovalCard } from "@/components/chat/ApprovalCard";
import { approvalPreview, BATCH_TOOL, PREVIEW_LINES } from "@/lib/approvalPreview";
import { toolWords } from "@/lib/toolWords";

type Card = { tool: string; args: Record<string, unknown>; can_always: boolean | null };
const FIXTURE: { batch: Card; kept: Card } = JSON.parse(
  readFileSync(join(__dirname, "fixtures", "script-cards-v1473.json"), "utf8").replace(/\r\n/g, "\n"),
);

afterEach(() => {
  cleanup();
  H.post.mockReset();
});

function approval(card: Card, id = "ap1") {
  return { id, callId: "c1", tool: card.tool, args: card.args, canAlways: card.can_always === true };
}

describe("a batch of file changes", () => {
  it("the daemon sends lines, not arguments", () => {
    expect(FIXTURE.batch.tool).toBe(BATCH_TOOL);
    const changes = FIXTURE.batch.args.changes as unknown[];
    expect(changes.length).toBe(4);
    expect(changes.every((c) => typeof c === "string")).toBe(true);
    expect(FIXTURE.batch.args.total).toBe(4);
    expect(FIXTURE.batch.can_always).toBe(false);
  });

  it("the preview is a list of changes, with the count kept out of the lines", () => {
    const model = approvalPreview(BATCH_TOOL, FIXTURE.batch.args);
    expect(model.kind).toBe("batch");
    expect(model.parts).toEqual([
      {
        type: "plan",
        steps: FIXTURE.batch.args.changes,
        more: 0,
        label: "batch",
        unit: ["change", "changes"],
      },
    ]);
  });

  it("the card lists every change and says how many", () => {
    render(<ApprovalCard approval={approval(FIXTURE.batch)} />);
    const preview = screen.getByTestId("approval-preview");
    expect(preview.getAttribute("data-preview")).toBe("batch");
    const block = within(preview).getByTestId("approval-plan");
    expect(block.getAttribute("data-list")).toBe("batch");
    expect(block.querySelector("p")!.textContent).toBe("batch: 4 changes");
    const list = within(preview).getByRole("list", { name: "The batch, 4 changes" });
    expect(within(list).getAllByRole("listitem").map((li) => li.textContent)).toEqual(
      FIXTURE.batch.args.changes,
    );
    // "total" is the list's count, not a line of its own.
    expect(preview.textContent).not.toContain("total");
    expect(screen.getByRole("button", { name: "Allow once" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Deny" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Always/ })).toBeNull();
  });

  it("one change is one change", () => {
    render(<ApprovalCard approval={approval({ tool: BATCH_TOOL, args: { changes: ["Rename a.txt to b.txt"], total: 1 }, can_always: false })} />);
    expect(screen.getByTestId("approval-plan").querySelector("p")!.textContent).toBe("batch: 1 change");
    expect(screen.getByRole("list", { name: "The batch, 1 change" })).toBeTruthy();
  });

  it("a long batch folds, and says what the daemon left out", () => {
    const lines = Array.from({ length: 30 }, (_, i) => `Rename f${i}.txt to g${i}.txt`);
    render(<ApprovalCard approval={approval({ tool: BATCH_TOOL, args: { changes: lines, total: 45 }, can_always: false })} />);
    const block = screen.getByTestId("approval-plan");
    expect(block.querySelector("p")!.textContent).toBe("batch: 45 changes");
    expect(within(block).getAllByTestId("approval-plan-step").length).toBe(PREVIEW_LINES);
    fireEvent.click(within(block).getByRole("button", { name: "Show all 30 changes" }));
    expect(within(block).getAllByTestId("approval-plan-step").length).toBe(30);
    expect(within(block).getByTestId("approval-plan-more").textContent).toBe("…and 15 more changes");
    expect(within(block).getByRole("button", { name: "Show fewer changes" })).toBeTruthy();
  });

  it("an empty or malformed list keeps the plain preview", () => {
    expect(approvalPreview(BATCH_TOOL, { changes: [] }).kind).toBe("args");
    expect(approvalPreview(BATCH_TOOL, { changes: "everything" }).kind).toBe("args");
    // A count smaller than the lines is not believed.
    const part = approvalPreview(BATCH_TOOL, { changes: ["a", "b"], total: 1 }).parts[0];
    expect(part.type === "plan" ? part.more : -1).toBe(0);
  });

  it("the card says what the call does in words", () => {
    expect(toolWords(BATCH_TOOL)).toBe("make a batch of file changes");
    expect(toolWords("skill_update")).toBe("change a skill");
  });

  it("the plan and form lists keep their own words", () => {
    render(<ApprovalCard approval={{ id: "ap2", callId: "c2", tool: "approve_plan", args: { steps: ["a", "b"] } }} />);
    expect(screen.getByTestId("approval-plan").querySelector("p")!.textContent).toBe("plan: 2 steps");
    expect(screen.getByRole("list", { name: "The plan, 2 steps" })).toBeTruthy();
  });
});

describe("a value a script kept", () => {
  it("the card shows the summary, never the reference or the value", () => {
    expect(FIXTURE.kept.args._from_script).toBeUndefined();
    const content = String(FIXTURE.kept.args.content);
    expect(content.startsWith("(from the script's `totals`:")).toBe(true);
    render(<ApprovalCard approval={approval(FIXTURE.kept)} />);
    const preview = screen.getByTestId("approval-preview");
    expect(preview.textContent).toContain("from the script's `totals`");
    expect(preview.textContent).toContain("totals.xlsx");
    expect(preview.textContent).not.toContain("LASTROW");
    expect(preview.textContent).not.toContain("_from_script");
  });

  it("is never offered as an always-allow", () => {
    expect(FIXTURE.kept.can_always).toBe(false);
    render(<ApprovalCard approval={approval(FIXTURE.kept)} />);
    expect(screen.queryByRole("button", { name: /Always/ })).toBeNull();
  });
});
