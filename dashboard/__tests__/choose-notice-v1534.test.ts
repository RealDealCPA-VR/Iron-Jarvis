/**
 * Fourth review, group 26, convenience C-CS-3: the attempts notice says
 * "choose one".
 *
 * Before: a daytime "Try on several models" turn that ended with one or more
 * attempts finished reached the bell as outcome "needs_you" and read "Your
 * coding session finished, but something needs you", which does not say
 * what. The daemon now sends outcome "choose" (routes/build
 * finished_notice_outcome), and the bell says "Your attempts are ready:
 * choose the one to keep" with the warning sign (never the finished check),
 * opening the session where the attempts card is. The notifier and the tray
 * say the same words (tests/test_fourth_review_choose_notice_v1534.py).
 */
import { describe, expect, it } from "vitest";
import { AlertTriangle, CheckCircle2 } from "lucide-react";
import { toActivity } from "@/components/NotificationBell";
import { buildFinishedHeadline } from "@/lib/buildLinks";
import { eventHeadline } from "@/lib/eventWords";
import type { IJEvent } from "@/lib/types";

const CHOOSE_TITLE = "Your attempts are ready: choose the one to keep";

function finished(outcome: string): IJEvent {
  return {
    id: `evt_${outcome}`,
    type: "build.turn_finished",
    session_id: "build:bs_try",
    ts: "2026-11-18T09:00:00",
    payload: { thread_id: "bs_try", turn_id: "bt_try", title: "Write the notes", seconds: 150, outcome },
  } as unknown as IJEvent;
}

describe("the bell's row for an attempts turn with a finished attempt", () => {
  it("asks the user to choose, with the warning sign, and opens the session", () => {
    const row = toActivity(finished("choose"));
    expect(row?.title).toBe(CHOOSE_TITLE);
    expect(row?.body).toBe("Write the notes · took 2 min 30 s");
    expect(row?.href).toBe("/chat?view=code&session=bs_try");
    expect(row?.icon).toBe(AlertTriangle);
    expect(row?.icon).not.toBe(CheckCircle2);
    expect(row?.attention).toBe(true);
  });

  it("gives every ending its own headline (none falls through to finished)", () => {
    const titles = ["done", "failed", "limit", "needs_you", "choose"].map((o) => buildFinishedHeadline(o));
    expect(new Set(titles).size).toBe(titles.length);
    expect(buildFinishedHeadline("choose")).toBe(CHOOSE_TITLE);
  });

  it("says choose in the activity list, never the bare outcome word", () => {
    const line = eventHeadline("build.turn_finished", { title: "t", outcome: "choose" });
    expect(line).toBe("A coding session's reply finished · t · Choose the one to keep");
  });
});
