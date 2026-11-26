/**
 * The intelligence plan, release 4: "not checked" is on the result line.
 *
 * Plain words: when a step of a larger task was done but could not be
 * double-checked (the check itself did not run), the run's write-up said so
 * and everything a person looks at FIRST still read as a plain finish: the
 * result card's headline said "Done", the status badge said "Done", the
 * plan's checklist ticked the step, the chat's settled row said nothing and
 * the bell said "finished".
 *
 * What carries weight here, each from the daemon's real shapes
 * (agents/outcome.session_result, plan.step_completed, Session.unchecked_steps,
 * the session.completed payload):
 *  - the headline: "Done. 1 step could not be checked." and the note that
 *    names the step and opens the run;
 *  - the badge every session list draws: "Done, 1 step not checked", amber;
 *  - the checklist: the step is its own state, never a tick, counted as
 *    "could not be checked", through the live fold, the daemon's copy and
 *    the saved form;
 *  - the chat's settled row and the bell's line;
 *  - and that a run with nothing unchecked reads exactly as it did.
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

vi.mock("@/lib/api", () => ({
  get: vi.fn(async () => ({})),
  post: vi.fn(async () => ({})),
  put: vi.fn(async () => ({})),
  del: vi.fn(async () => ({})),
  ApiError: class ApiError extends Error {},
}));

import {
  hasUncheckedSteps,
  resultHeadline,
  uncheckedNote,
  type RunResult,
} from "@/components/chat/RunResultCard";
import { PlanChecklist } from "@/components/chat/PlanChecklist";
import { settledRunParts, verdictTone } from "@/components/chat/AgentRunRow";
import { stepLabel } from "@/components/chat/stepLabel";
import { SessionStatusBadge, uncheckedChip } from "@/components/sessions/SessionStatusBadge";
import { toActivity } from "@/components/NotificationBell";
import { makeAgentRunStamp, readAgentRun, runEndWords, AGENT_RUN_KEY } from "@/lib/agentRun";
import {
  foldPlanEvents,
  isPlanProgressLine,
  planCountWords,
  readPlanResponse,
  readSavedPlan,
  STATE_WORDS,
  toSavedPlan,
  type PlanView,
} from "@/lib/planChecklist";
import { doneNotCheckedWords, uncheckedWords } from "@/lib/statusWords";
import type { IJEvent } from "@/lib/types";

afterEach(cleanup);

/** agents/outcome.session_result for a run that used tools and finished. */
function result(over: Partial<RunResult> = {}): RunResult {
  return {
    found: true,
    session_id: "sess_abc123",
    status: "completed",
    task: "write the greeting and summarize",
    summary: "hello.txt was written and the work was summarized.",
    steps: 4,
    tools_used: [{ tool: "write_file", count: 1 }],
    tools_failed: [],
    files_created: ["hello.txt"],
    files_changed: [],
    errors: [],
    revertable: 1,
    duration_s: 12,
    outcome: "completed",
    ...over,
  };
}

const UNCHECKED = {
  plan_steps_unchecked: 1,
  unchecked_steps: [{ step: 2, goal: "summarize what was done" }],
};

describe("the words", () => {
  it("count the steps and say nothing for none", () => {
    expect(uncheckedWords(1)).toBe("1 step could not be checked.");
    expect(uncheckedWords(3)).toBe("3 steps could not be checked.");
    for (const none of [0, -1, null, undefined, "2", NaN]) expect(uncheckedWords(none)).toBe("");
    expect(doneNotCheckedWords(1)).toEqual({ word: "Done, 1 step not checked", tone: "amber" });
    expect(doneNotCheckedWords(2)?.word).toBe("Done, 2 steps not checked");
    expect(doneNotCheckedWords(0)).toBeNull();
  });
});

describe("the result card's first line", () => {
  it("is never a plain Done over a step nobody could check", () => {
    expect(resultHeadline(result(UNCHECKED))).toBe("Done. 1 step could not be checked.");
    expect(resultHeadline(result({ plan_steps_unchecked: 3 }))).toBe("Done. 3 steps could not be checked.");
    expect(hasUncheckedSteps(result(UNCHECKED))).toBe(true);
  });

  it("reads exactly as before when every step was checked", () => {
    expect(resultHeadline(result())).toBe("Done");
    expect(resultHeadline(result({ plan_steps_unchecked: 0, unchecked_steps: [] }))).toBe("Done");
    expect(hasUncheckedSteps(result())).toBe(false);
  });

  it("lets a run that fell short say the more important thing", () => {
    expect(resultHeadline(result({ ...UNCHECKED, outcome: "completed_with_failures" }))).toBe("Part failed");
    expect(resultHeadline(result({ ...UNCHECKED, outcome: "needs_you" }))).toBe("Needs you");
    expect(resultHeadline(result({ ...UNCHECKED, status: "failed" }))).toBe("Failed");
    expect(resultHeadline(result({ ...UNCHECKED, status: "cancelled" }))).toBe("Stopped");
    expect(hasUncheckedSteps(result({ ...UNCHECKED, outcome: "needs_you" }))).toBe(false);
  });

  it("names the step in the note under it", () => {
    expect(uncheckedNote(UNCHECKED)).toBe(
      "Iron Jarvis did step 2 (summarize what was done) but could not double-check it: the check itself did not run. Open the run to see the step.",
    );
    expect(uncheckedNote({ plan_steps_unchecked: 1 })).toContain("did one step but could not double-check it");
    const many = uncheckedNote({
      plan_steps_unchecked: 5,
      unchecked_steps: [1, 2, 3, 4, 5].map((n) => ({ step: n, goal: `goal ${n}` })),
    });
    expect(many).toContain("step 1 (goal 1), step 2 (goal 2), step 3 (goal 3) and 2 more");
    expect(many).toContain("could not double-check them");
    // No engine words reach the note.
    for (const word of ["verif", "judge", "unverified"]) expect(many.toLowerCase()).not.toContain(word);
  });
});

describe("the status badge every session list draws", () => {
  it("says Done, 1 step not checked, in the warn tone", () => {
    render(<SessionStatusBadge session={{ status: "completed", outcome: "completed", unchecked_steps: 1 }} />);
    const chip = screen.getByTestId("session-unchecked-chip");
    expect(chip.textContent).toBe("Done, 1 step not checked");
    expect(chip.getAttribute("title")).toBe("1 step could not be checked.");
  });

  it("stays a plain Done when there is nothing unchecked", () => {
    for (const none of [null, undefined, 0]) {
      cleanup();
      const { container } = render(
        <SessionStatusBadge session={{ status: "completed", outcome: "completed", unchecked_steps: none }} />,
      );
      expect(screen.queryByTestId("session-unchecked-chip")).toBeNull();
      expect(container.textContent).toBe("Done");
    }
  });

  it("never replaces Needs you, Part failed, Failed or Stopped", () => {
    expect(uncheckedChip({ status: "completed", outcome: "needs_you", unchecked_steps: 1 })).toBeNull();
    expect(uncheckedChip({ status: "completed", outcome: "completed_with_failures", unchecked_steps: 1 })).toBeNull();
    expect(uncheckedChip({ status: "failed", unchecked_steps: 1 })).toBeNull();
    expect(uncheckedChip({ status: "cancelled", unchecked_steps: 1 })).toBeNull();
    expect(uncheckedChip({ status: "active", unchecked_steps: 1 })).toBeNull();
  });
});

describe("the plan's checklist", () => {
  const created: IJEvent = {
    id: "e1", ts: "2026-11-26T10:00:00Z", type: "plan.created", session_id: "s1",
    payload: { run_id: "r1", steps: ["write hello.txt", "summarize what was done"] },
  } as IJEvent;
  const done = (index: number, extra: Record<string, unknown> = {}): IJEvent =>
    ({
      id: `d${index}`, ts: "2026-11-26T10:00:05Z", type: "plan.step_completed", session_id: "s1",
      payload: { run_id: "r1", index, ok: true, ...extra },
    }) as IJEvent;

  it("marks the step as it finishes, from the daemon's own event", () => {
    const view = foldPlanEvents(null, [created, done(0), done(1, { checked: false })]);
    expect(view?.steps.map((s) => s.state)).toEqual(["done", "unchecked"]);
    expect(planCountWords(view as PlanView)).toBe("2 of 2 done, 1 could not be checked");
    // A checked step's event is read exactly as before.
    expect(foldPlanEvents(null, [created, done(0), done(1)])?.steps.map((s) => s.state)).toEqual(["done", "done"]);
    // A failed step is failed, whatever else its payload says.
    const failed = foldPlanEvents(null, [created, done(0, { ok: false, checked: false })]);
    expect(failed?.steps[0].state).toBe("failed");
  });

  it("reads the daemon's copy and keeps the state in the saved form", () => {
    const view = readPlanResponse({
      steps: [
        { goal: "write hello.txt", state: "done" },
        { goal: "summarize what was done", state: "unchecked" },
      ],
    });
    expect(view?.steps.map((s) => s.state)).toEqual(["done", "unchecked"]);
    const saved = toSavedPlan(view);
    expect(saved?.steps[1]).toEqual({ goal: "summarize what was done", state: "unchecked" });
    expect(readSavedPlan(JSON.parse(JSON.stringify(saved)))?.steps[1].state).toBe("unchecked");
  });

  it("draws it as its own row: a warn mark, the words, and no strike-through", () => {
    render(
      <PlanChecklist
        plan={{
          total: 2,
          steps: [
            { index: 0, goal: "write hello.txt", state: "done" },
            { index: 1, goal: "summarize what was done", state: "unchecked" },
          ],
        }}
      />,
    );
    const rows = screen.getAllByTestId("plan-step");
    expect(rows[1].getAttribute("data-state")).toBe("unchecked");
    expect(rows[1].textContent).toContain(STATE_WORDS.unchecked);
    expect(screen.getByTestId("plan-step-unchecked").textContent).toBe("could not be checked");
    const goals = screen.getAllByTestId("plan-step-goal");
    expect(goals[0].className).toContain("line-through");
    expect(goals[1].className).not.toContain("line-through");
    expect(screen.getByRole("list").getAttribute("aria-label")).toBe(
      "The plan: 2 of 2 done, 1 could not be checked",
    );
    expect(STATE_WORDS.unchecked).toBe("Done, but could not be checked");
  });

  it("says it on the step's progress line, which the checklist still stands in for", () => {
    const line = stepLabel({ ...done(1, { checked: false }), payload: { run_id: "r1", index: 1, ok: true, checked: false } } as IJEvent);
    expect(line).toMatch(/^Step 2.* done, but could not be checked$/);
    expect(isPlanProgressLine(line as string)).toBe(true);
    expect(stepLabel(done(1))).toMatch(/^Step 2.* done$/);
  });
});

describe("the chat's settled row", () => {
  const row = { created_at: "2026-11-26T10:00:00Z", finished_at: "2026-11-26T10:02:00Z", status: "completed", outcome: "completed" };

  it("ends in the count, in the warn tone", () => {
    const stamp = makeAgentRunStamp({
      session: "sess_abc123", createdAt: row.created_at, finishedAt: row.finished_at, steps: 4,
      end: { status: row.status, outcome: row.outcome, uncheckedSteps: 1 },
    });
    expect(stamp.uncheckedSteps).toBe(1);
    expect(runEndWords(stamp)).toBe("1 step could not be checked");
    expect(verdictTone(stamp)).toBe("amber");
    const parts = settledRunParts("The builder", stamp);
    expect(parts.verdict).toBe("1 step could not be checked");
    expect(parts.tone).toBe("amber");
    // Saved on the message and read back, the count is still there.
    expect(readAgentRun({ [AGENT_RUN_KEY]: JSON.parse(JSON.stringify(stamp)) })?.uncheckedSteps).toBe(1);
  });

  it("reads the saved checklist when the count was not kept", () => {
    const stamp = makeAgentRunStamp({
      session: "sess_abc123", steps: 4,
      plan: { total: 2, steps: [
        { index: 0, goal: "a", state: "done" },
        { index: 1, goal: "b", state: "unchecked" },
      ] },
      end: { status: "completed", outcome: "completed" },
    });
    expect(stamp.uncheckedSteps).toBeUndefined();
    expect(runEndWords(stamp)).toBe("1 step could not be checked");
  });

  it("says nothing more over a clean finish, and keeps the short-run words first", () => {
    const clean = makeAgentRunStamp({ session: "sess_abc123", steps: 4, end: { status: "completed", outcome: "completed", uncheckedSteps: 0 } });
    expect(clean.uncheckedSteps).toBeUndefined();
    expect(runEndWords(clean)).toBeNull();
    expect(settledRunParts("The builder", clean).verdict).toBeNull();
    const short = makeAgentRunStamp({ session: "sess_abc123", end: { status: "completed", outcome: "needs_you", uncheckedSteps: 2 } });
    expect(runEndWords(short)).toBe("needs you");
    const failed = makeAgentRunStamp({ session: "sess_abc123", end: { status: "failed", uncheckedSteps: 2 } });
    expect(runEndWords(failed)).toBe("failed");
    expect(verdictTone(failed)).toBe("red");
  });
});

describe("the bell's line", () => {
  const completed = (payload: Record<string, unknown>): IJEvent =>
    ({ id: "ev1", ts: "2026-11-26T10:02:00Z", type: "session.completed", session_id: "sess_abc123", payload }) as IJEvent;

  it("says it for a run nobody was watching, and stays a quiet row", () => {
    const item = toActivity(
      completed({ status: "completed", outcome: "completed", origin: "schedule:Monday report", summary: "all done", unchecked_steps: 1 }),
    );
    expect(item?.title).toBe("Your schedule Monday report finished. 1 step could not be checked");
    expect(item?.quiet).toBe(true);
  });

  it("says it for a finished objective", () => {
    const item = toActivity(
      completed({ status: "completed", outcome: "completed", origin: "job:mission", summary: "all done", unchecked_steps: 2 }),
    );
    expect(item?.title).toBe("Your objective is done. 2 steps could not be checked");
  });

  it("reads exactly as before when every step was checked", () => {
    expect(
      toActivity(completed({ status: "completed", outcome: "completed", origin: "schedule:Monday report", summary: "x" }))?.title,
    ).toBe("Your schedule Monday report finished");
    expect(
      toActivity(completed({ status: "completed", outcome: "completed", origin: "job:mission", summary: "x" }))?.title,
    ).toBe("Your objective is done");
  });
});
