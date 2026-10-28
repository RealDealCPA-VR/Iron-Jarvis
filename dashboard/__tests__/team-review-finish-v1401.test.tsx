/**
 * v1.401.0 — TEAM REVIEW AT THE FINISH, on the screen.
 *
 * With "Hand work to my team first" on and a member holding the Review role,
 * Iron Jarvis hands a Build reply's change to the team's reviewer before the
 * reply ends (the daemon's harness/finish). The reply line's `finish` record
 * then carries `review {state, member, model, verdict[, why]}`.
 *
 *  1. The words: the work line's one sentence ends with "reviewed by codex:
 *     changes needed" / "reviewed by codex: approved" / "reviewed by codex"
 *     (no verdict counted) / "review failed" / "review stopped" / "review not
 *     run"; a "changes needed" is never a
 *     warning (the reply answered it). The hover names the reviewer and its
 *     model, or why no review ran.
 *  2. The decoder whitelists it (lib/buildSessions `decodeFinishReview`, the
 *     twin of harness/finish.saved_review); anything else is dropped.
 *  3. A record with no review reads EXACTLY as before.
 */
import React from "react";
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import { WorkLine } from "@/components/chat/WorkLine";
import { decodeFinish, decodeFinishReview } from "@/lib/buildSessions";
import {
  finishTitle,
  finishWarn,
  finishWords,
  reviewWords,
  type FinishRecord,
  type FinishReview,
} from "@/lib/stepWords";

const rec = (over: Partial<FinishRecord> = {}): FinishRecord => ({
  tests: "passed",
  command: "python -m unittest -q test_calc",
  removed: 0,
  kept: 0,
  points: 3,
  unmet: 0,
  after: "",
  ...over,
});

const review = (over: Partial<FinishReview> = {}): FinishReview => ({
  state: "done",
  member: "codex",
  model: "gpt-6-astra",
  verdict: "changes_needed",
  ...over,
});

afterEach(() => cleanup());

describe("the review's words on the work line", () => {
  it.each([
    [review(), "reviewed by codex: changes needed"],
    [review({ verdict: "approved" }), "reviewed by codex: approved"],
    [review({ verdict: "" }), "reviewed by codex"],
    [review({ member: "", verdict: "approved" }), "reviewed by your team: approved"],
    [review({ state: "not_run", member: "", model: "", verdict: "", why: "no member" }), "review not run"],
    // Review: a reviewer that started and failed / was stopped never reads
    // "not run" (it ran).
    [review({ state: "failed", verdict: "" }), "review failed"],
    [review({ state: "stopped", verdict: "" }), "review stopped"],
  ])("%j reads %s", (r, words) => {
    expect(reviewWords(r)).toBe(words);
  });

  it("ends the finish sentence", () => {
    expect(finishWords(rec({ review: review() }))).toBe("Checked: tests passed, reviewed by codex: changes needed");
    expect(finishWords(rec({ tests: "none", command: "", review: review({ verdict: "approved" }) }))).toBe(
      "Checked: no tests found, reviewed by codex: approved",
    );
    expect(
      finishWords(rec({ removed: 1, review: review({ state: "not_run", member: "", model: "", verdict: "" }) })),
    ).toBe("Checked: tests passed, 1 scratch file removed, review not run");
  });

  it("a 'changes needed' is not a warning: the reply answered it", () => {
    expect(finishWarn(rec({ review: review() }))).toBe(false);
    expect(finishWarn(rec({ tests: "failed", review: review({ verdict: "approved" }) }))).toBe(true);
  });

  it("its hover names the reviewer and its model, or why no review ran", () => {
    expect(finishTitle(rec({ review: review() }))).toBe(
      "Ran: python -m unittest -q test_calc\nReviewed by codex on gpt-6-astra",
    );
    expect(
      finishTitle(
        rec({
          tests: "none",
          command: "",
          review: review({ state: "not_run", member: "", model: "", verdict: "", why: "no member on this network" }),
        }),
      ),
    ).toBe("Review: no member on this network");
  });

  it("with no review the sentence and the hover are EXACTLY as before", () => {
    expect(finishWords(rec())).toBe("Checked: tests passed");
    expect(finishTitle(rec())).toBe("Ran: python -m unittest -q test_calc");
  });

  it("the work line shows it under itself", () => {
    const steps = [{ name: "write_file", ok: true, ms: 12, target: "app.py" }];
    const timing = { startedAt: 1_000, firstTokenAt: 1_500, endedAt: 4_000 };
    render(<WorkLine steps={steps} timing={timing} finish={rec({ review: review() })} />);
    const line = screen.getByTestId("work-line-finish");
    expect(line).toHaveTextContent("Checked: tests passed, reviewed by codex: changes needed");
    expect(line.getAttribute("data-outcome")).toBe("ok");
    expect(line.getAttribute("title")).toContain("Reviewed by codex on gpt-6-astra");
  });
});

describe("the decoder", () => {
  it("keeps the review's known keys and drops the rest", () => {
    const got = decodeFinish({
      ...rec(),
      review: { state: "done", member: "codex", model: "gpt-6-astra", verdict: "changes_needed",
                why: "never for a finished review", report: "never on the screen" },
    });
    expect(got?.review).toEqual(review());
    expect(
      decodeFinishReview({ state: "not_run", member: 3, model: null, verdict: "great", why: "x".repeat(500) }),
    ).toEqual({ state: "not_run", member: "", model: "", verdict: "", why: "x".repeat(200) });
  });

  it("drops a review that is not a record or has an unknown state", () => {
    for (const raw of [null, undefined, "done", 3, [], { state: "lovely" }, { member: "codex" }]) {
      expect(decodeFinishReview(raw)).toBeNull();
      expect(decodeFinish({ ...rec(), review: raw })?.review).toBeUndefined();
    }
  });

  it("an older daemon's record (no review) decodes exactly as before", () => {
    expect(decodeFinish(rec())).toEqual(rec());
  });
});
