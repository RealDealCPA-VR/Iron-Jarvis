/**
 * v1.351.0, wave 1 lane D (G07): the folded work line shows failures.
 *
 * A live chat read "✓ … read 8 web pages, 7 failed" when ONE page was read:
 * the fold always drew a check, and lib/stepWords.stepCounts counted a failed
 * attempt as work done. Now a failed step counts only as failed, and a turn
 * with any failed step folds under a warning triangle in the theme's warn
 * token (the same quiet row, no box). An all-successful turn keeps its check.
 *
 * Driven through the REAL component and the REAL counter.
 */

import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { LiveToolRows, WorkLine, liveSteps, liveWorkSummary, workSummary } from "@/components/chat/WorkLine";
import { failedSteps, stepCounts } from "@/lib/stepWords";
import type { ToolCard } from "@/lib/useChatStream";

afterEach(cleanup);

const TIMING = { startedAt: 0, firstTokenAt: 9_000, endedAt: 12_000 };

/** One good page read and seven that failed (the live chat's turn). */
function oneGoodSevenBad() {
  return [
    { name: "web_fetch", ok: true, ms: 800, target: "example.com" },
    ...Array.from({ length: 7 }, (_, i) => ({ name: "web_fetch", ok: false, ms: 300, target: `site${i}.com` })),
  ];
}

describe("the counter counts a failed step only as failed", () => {
  it("one read page and seven failed reads say 'read 1 web page, 7 failed'", () => {
    expect(stepCounts(oneGoodSevenBad())).toEqual(["read 1 web page", "7 failed"]);
    expect(failedSteps(oneGoodSevenBad())).toBe(7);
  });

  it("an all-successful turn is counted as before", () => {
    const ok = [
      { name: "read_file", ok: true },
      { name: "read_file", ok: true },
      { name: "web_search", ok: true },
    ];
    expect(stepCounts(ok)).toEqual(["read 2 files", "searched the web"]);
    expect(failedSteps(ok)).toBe(0);
  });

  it("a step with no recorded outcome (an older chat) still counts as done", () => {
    expect(stepCounts([{ name: "read_file", ok: null }, { name: "read_file" }])).toEqual(["read 2 files"]);
  });
});

describe("the folded line", () => {
  it("a turn with failures folds to a warning-toned 'Worked for … · read 1 web page, 7 failed'", () => {
    render(<WorkLine steps={oneGoodSevenBad()} timing={TIMING} />);
    const summary = screen.getByTestId("work-summary");
    expect(summary.textContent).toBe("Worked for 12 s · read 1 web page, 7 failed");
    // The warning triangle, in the theme's warn token, replaces the check.
    const warn = screen.getByTestId("work-line-warn");
    expect(warn.getAttribute("class")).toContain("text-tone-warn");
    expect(warn.getAttribute("aria-hidden")).toBe("true");
    expect(screen.queryByTestId("work-line-check")).toBeNull();
    // The failure words carry the same tone; the rest of the line stays grey.
    const failed = screen.getByTestId("work-summary-failed");
    expect(failed.textContent).toBe("7 failed");
    expect(failed.className).toContain("text-tone-warn");
    const button = screen.getByRole("button", { name: /Worked for 12 s/ });
    expect(button.getAttribute("data-outcome")).toBe("some-failed");
    expect(button.className).toContain("text-zinc-500");
  });

  it("an all-successful turn keeps the check and no warning", () => {
    render(
      <WorkLine
        steps={[
          { name: "web_fetch", ok: true, ms: 800, target: "example.com" },
          { name: "read_file", ok: true, ms: 10, target: "harbor.xlsx" },
        ]}
        timing={TIMING}
      />,
    );
    expect(screen.getByTestId("work-summary").textContent).toBe("Worked for 12 s · read 1 file, read 1 web page");
    expect(screen.getByTestId("work-line-check")).toBeTruthy();
    expect(screen.queryByTestId("work-line-warn")).toBeNull();
    expect(screen.queryByTestId("work-summary-failed")).toBeNull();
    expect(screen.getByRole("button", { name: /Worked for/ }).getAttribute("data-outcome")).toBe("ok");
  });

  it("a turn where everything failed says so, with no work claimed", () => {
    expect(workSummary({ steps: [{ name: "write_file", ok: false, ms: 5 }], timing: TIMING })).toBe(
      "Worked for 12 s · 1 failed",
    );
    render(<WorkLine steps={[{ name: "write_file", ok: false, ms: 5 }]} timing={TIMING} />);
    expect(screen.getByTestId("work-line-warn")).toBeTruthy();
    expect(screen.getByTestId("work-summary").textContent).not.toMatch(/made/);
  });

  it("opening the fold still lists every step, the failed ones marked failed", () => {
    render(<WorkLine steps={oneGoodSevenBad()} timing={TIMING} />);
    fireEvent.click(screen.getByRole("button", { name: /Worked for/ }));
    const rows = screen.getAllByTestId("work-row");
    expect(rows).toHaveLength(8);
    expect(rows.filter((r) => r.getAttribute("data-state") === "failed")).toHaveLength(7);
    expect(rows.filter((r) => r.getAttribute("data-state") === "done")).toHaveLength(1);
  });
});

describe("the live line shares the counter", () => {
  // No client clock on the cards: the live line then reads "Working · …".
  function card(id: string, ok: boolean): ToolCard {
    return {
      id,
      name: "web_fetch",
      status: "done",
      ok,
      args: { url: `https://${id}.com` },
    } as unknown as ToolCard;
  }

  it("says 'read 1 web page, 2 failed' while the turn runs", () => {
    const cards = [card("a", true), card("b", false), card("c", false)];
    expect(liveWorkSummary(liveSteps(cards), 5_000)).toBe("Working for 5 s · read 1 web page, 2 failed");
    render(<LiveToolRows cards={cards} since={null} />);
    expect(screen.getByTestId("live-work-words").textContent).toBe("Working · read 1 web page, 2 failed");
  });
});
