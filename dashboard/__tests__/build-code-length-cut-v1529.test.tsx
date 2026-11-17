/**
 * Owner decision 5 after the third fix plan (RB10's open half): a coding
 * reply that did not finish never wears the finished check mark, and the
 * activity list says "stopped at its limit" in plain words.
 *
 * Before: the bell drew `build.turn_finished` with CheckCircle2 for every
 * outcome but "failed" — a reply stopped at its limit ("limit") and one that
 * ended with something left for the user ("needs_you") had the same check as
 * a finished one. The activity list showed the raw outcome as "Limit".
 *
 * A Build › Code reply the tool cut at the model's length limit reaches the
 * bell as "failed" (hosted drivers, tests/test_build_code_length_cut_v1529.py):
 * its row is "could not finish" with the warning sign.
 */
import { describe, expect, it } from "vitest";
import { AlertTriangle, CheckCircle2, PauseCircle } from "lucide-react";
import { toActivity } from "@/components/NotificationBell";
import { eventHeadline } from "@/lib/eventWords";
import type { IJEvent } from "@/lib/types";

function finished(outcome: string, app = true): IJEvent {
  return {
    id: `evt_${outcome}`,
    type: "build.turn_finished",
    session_id: app ? "build_app:app_x" : "build:bs_x",
    ts: "2026-11-17T10:00:00",
    payload: {
      thread_id: app ? "app_x" : "bs_x",
      turn_id: "t1",
      title: "Tidy the notes",
      seconds: 300,
      outcome,
      ...(app ? { surface: "build_app", harness: "opencode" } : {}),
    },
  } as unknown as IJEvent;
}

describe("the bell's finished row", () => {
  it("keeps the check mark for a reply that finished", () => {
    const row = toActivity(finished("done"));
    expect(row?.icon).toBe(CheckCircle2);
    expect(row?.title).toBe("Your coding session finished");
  });

  it("never draws the finished check for a reply stopped at its limit", () => {
    for (const app of [true, false]) {
      const row = toActivity(finished("limit", app));
      expect(row?.title).toBe("Your coding session stopped at its limit and needs you");
      expect(row?.icon).not.toBe(CheckCircle2);
      expect(row?.icon).toBe(PauseCircle);
    }
  });

  it("never draws the finished check for a reply that left something for the user", () => {
    const row = toActivity(finished("needs_you"));
    expect(row?.icon).not.toBe(CheckCircle2);
    expect(row?.icon).toBe(AlertTriangle);
  });

  it("draws a reply cut at the model's length limit (failed) as could not finish", () => {
    const row = toActivity(finished("failed"));
    expect(row?.title).toBe("Your coding session could not finish");
    expect(row?.icon).toBe(AlertTriangle);
  });
});

describe("the activity list's words for how a coding reply ended", () => {
  it("says stopped at its limit, never the bare word Limit", () => {
    const line = eventHeadline("build.turn_finished", { title: "t", outcome: "limit" });
    expect(line).toBe("A coding session's reply finished · t · Stopped at its limit");
    expect(line).not.toMatch(/· Limit$/);
  });

  it("says Failed for a reply cut at its length, as before", () => {
    expect(eventHeadline("build.turn_finished", { title: "t", outcome: "failed" })).toBe(
      "A coding session's reply finished · t · Failed",
    );
  });
});
