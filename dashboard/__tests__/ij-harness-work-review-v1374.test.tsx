/**
 * v1.374.0 review (IJ harness part 2, Phase 3) — the meter and the folded
 * task list say only what is true.
 *
 *  - A reading whose window NOBODY knows (no pin, measurement, catalog or
 *    probe: a Codex or an API-key model) is the app's own safe size — the
 *    meter says so, never "what this model can read at once".
 *  - With compaction turned off in Settings the reading still shows, but no
 *    Compact is offered and nothing turns the warning tone (the chat's rule;
 *    the tone is never the only cue, so without the offer there is none).
 *  - A Build reply's plan CARRIES OVER to the next turn: a step the turn did
 *    not reach stays "not started" and the step it was on is not ticked off
 *    (or failed with a failed turn) — "Not attempted" never appears.
 */
import React from "react";
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";

import { ContextMeter } from "@/components/harness/ContextMeter";
import { SettledPlan } from "@/components/harness/HarnessPlan";
import { decodeTurnContext, meterTitle, offersCompact, replyPlan } from "@/lib/buildContext";

afterEach(() => cleanup());

const reading = (extra: Record<string, unknown> = {}) => ({
  window: 32000,
  used: 20000,
  tokens: 25600,
  percent: 80,
  level: "suggest",
  suggest_at: 70,
  ...extra,
});

function meter(raw: Record<string, unknown>) {
  const r = decodeTurnContext(raw)!;
  return render(
    <ContextMeter
      reading={r}
      offer={offersCompact(r, false)}
      summarized={false}
      busy={false}
      onCompact={() => {}}
      onViewSummary={() => {}}
    />,
  );
}

describe("whose window the meter measures by", () => {
  it("a known window is the model's", () => {
    meter(reading());
    expect(screen.getByTestId("harness-context-meter").getAttribute("title")).toBe(
      "This conversation fills about 80% of what this model can read at once.",
    );
  });

  it("a window nobody knows is the app's own safe size, said in those words", () => {
    meter(reading({ window_known: false }));
    const title = screen.getByTestId("harness-context-meter").getAttribute("title") ?? "";
    expect(title).toBe(
      "This conversation fills about 80% of what Iron Jarvis sends this model at once. This model's own limit is not known, so a safe size is used.",
    );
    expect(title).not.toMatch(/can read at once/);
    // The planner really does leave turns out past it: Compact still helps.
    expect(screen.getByTestId("harness-compact")).toBeTruthy();
  });

  it("with compaction turned off: the reading, no offer, no warning tone", () => {
    meter(reading({ level: "auto", percent: 95, disabled: true }));
    const m = screen.getByTestId("harness-context-meter");
    expect(within(m).getByTestId("harness-context-percent").textContent).toBe("95%");
    expect(m.getAttribute("data-state")).toBe("ok");
    expect(screen.queryByTestId("harness-compact")).toBeNull();
    expect(offersCompact(decodeTurnContext(reading({ disabled: true })), false)).toBe(false);
    expect(meterTitle(decodeTurnContext(reading({ disabled: true }))!)).toMatch(/what this model can read/);
  });
});

describe("a Build reply's plan carries over to the next turn", () => {
  const saved = {
    steps: [
      { goal: "read the cart code", state: "done" },
      { goal: "fix the total", state: "running" },
      { goal: "add a test", state: "waiting" },
    ],
    total: 3,
  };
  const states = () => screen.getAllByTestId("plan-step").map((el) => el.getAttribute("data-state"));

  it("a finished turn: the step it was on is not ticked off, the rest not started", () => {
    expect(replyPlan(saved, false)!.steps.map((s) => s.state)).toEqual(["done", "waiting", "waiting"]);
    render(<SettledPlan plan={replyPlan(saved, false)} />);
    expect(screen.getByTestId("harness-reply-plan-count").textContent).toBe("1 of 3 done");
    fireEvent.click(screen.getByRole("button", { name: /Plan/ }));
    expect(states()).toEqual(["done", "waiting", "waiting"]);
    expect(screen.getByTestId("harness-reply-plan").textContent).not.toMatch(/Not attempted|Working on/);
  });

  it("a failed turn: the step it was on failed; the rest are still to come", () => {
    expect(replyPlan(saved, true)!.steps.map((s) => s.state)).toEqual(["done", "failed", "waiting"]);
    render(<SettledPlan plan={replyPlan(saved, true)} />);
    fireEvent.click(screen.getByRole("button", { name: /Plan/ }));
    expect(screen.getByTestId("harness-reply-plan").textContent).not.toMatch(/Not attempted/);
  });

  it("junk is no plan", () => {
    expect(replyPlan({ steps: "nope" }, false)).toBeNull();
    expect(replyPlan(null, false)).toBeNull();
  });
});
