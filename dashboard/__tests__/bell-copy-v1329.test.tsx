/**
 * v1.329.0, calm chat wave 8 (J5): the notification bell says it plainly.
 *
 * The bell still hung clauses on dashes after the mission screen had moved
 * to plain words: "Your objective finished — something needs you" beside the
 * mission headline's "Finished, but something needs you"; "— check what it
 * did"; "builder — needs a key" (and "builder — " with no reason); and
 * "Computer-use run finished — failed". These pin the plain sentences, read
 * through the bell's own event mapper.
 */

import { describe, expect, it } from "vitest";
import { toActivity } from "@/components/NotificationBell";
import { missionHeadline, type MissionView } from "@/lib/mission";
import type { IJEvent } from "@/lib/types";

const ASIDE = /(^|\s)[—–](\s|$)/;

function ev(type: string, payload: Record<string, unknown>, session_id: string | null = null): IJEvent {
  return { id: `e-${type}`, type, ts: "2026-10-10T12:00:00Z", session_id, payload } as unknown as IJEvent;
}

function mission(outcome: string | null) {
  return ev("session.completed", { origin: "job:mission", status: "completed", ok: true, outcome }, "m1");
}

describe("a finished mission reads like the mission screen", () => {
  it("needs_you: the same 'but something needs you' the headline says", () => {
    const item = toActivity(mission("needs_you"))!;
    expect(item.title).toBe("Your objective finished, but something needs you");
    const head = missionHeadline({
      session: { status: "completed", outcome: "needs_you" },
    } as unknown as MissionView);
    expect(head).toBe("Finished, but something needs you");
    expect(item.title.endsWith(head.replace(/^Finished/, "finished"))).toBe(true);
  });

  it("an outcome the bell does not name: two plain sentences", () => {
    expect(toActivity(mission("something_else"))!.title).toBe("Your objective finished. Check what it did");
  });

  it("controls: done, failures and no outcome keep their words", () => {
    expect(toActivity(mission("completed"))!.title).toBe("Your objective is done");
    expect(toActivity(mission("completed_with_failures"))!.title).toBe(
      "Your objective finished, but part of it failed",
    );
    expect(toActivity(mission(null))!.title).toBe("Your objective finished");
  });
});

describe("other bell rows carry no dash aside", () => {
  it("a blocked job: the agent and the reason, or the agent alone", () => {
    const withWhy = toActivity(
      ev("assignment.blocked", { id: "a1", assignee: "builder", title: "Rename files", blocked_reason: "needs a key" }),
    )!;
    expect(withWhy.body).toBe("builder: needs a key");
    const noWhy = toActivity(ev("assignment.blocked", { id: "a2", assignee: "builder", title: "Rename files" }))!;
    expect(noWhy.body).toBe("builder");
  });

  it("a computer-use run says finished or failed in its title", () => {
    const ok = toActivity(ev("computeruse.run_finished", { run_id: "r1", status: "completed", steps: 3 }))!;
    expect(ok.title).toBe("Computer-use run finished");
    const bad = toActivity(ev("computeruse.run_finished", { run_id: "r2", status: "failed", steps: 1 }))!;
    expect(bad.title).toBe("Computer-use run failed");
  });

  it("none of these titles or bodies holds a spaced dash", () => {
    const items = [
      toActivity(mission("needs_you")),
      toActivity(mission("something_else")),
      toActivity(ev("assignment.blocked", { id: "a1", assignee: "builder", title: "x", blocked_reason: "y" })),
      toActivity(ev("assignment.blocked", { id: "a2", assignee: "builder", title: "x" })),
      toActivity(ev("computeruse.run_finished", { run_id: "r1", status: "failed" })),
    ];
    for (const it of items) {
      expect(it).not.toBeNull();
      expect(it!.title, it!.title).not.toMatch(ASIDE);
      expect(it!.body ?? "", it!.body).not.toMatch(ASIDE);
    }
  });
});
