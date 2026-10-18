/**
 * v1.351.0, wave 1 lane D (G09): the mission headline says when part of it
 * failed.
 *
 * lib/mission.missionHeadline said "Finished" for a mission whose outcome was
 * completed_with_failures, while the bell said "Your objective finished, but
 * part of it failed". Now the screen says the bell's words, in the warn
 * token, and so does a mission whose worklist kept failed items (the screen
 * already offered "Retry the N failed items" under a plain "Finished").
 *
 * Driven through the REAL helpers, the REAL bell mapper and the REAL panel.
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

vi.mock("next/link", () => ({
  default: ({ children, href, ...rest }: React.ComponentProps<"a">) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

import { toActivity } from "@/components/NotificationBell";
import { MissionOutput } from "@/components/agents/mission/MissionOutput";
import {
  PART_FAILED_HEADLINE,
  missionFinish,
  missionHeadline,
  missionHeadlineWarn,
  type MissionView,
  type WorklistSummary,
} from "@/lib/mission";
import type { MissionLiveStore } from "@/lib/useMission";
import type { IJEvent } from "@/lib/types";

afterEach(cleanup);

function view(
  status: string,
  outcome: string | null,
  worklist: WorklistSummary | null = null,
): MissionView {
  return {
    session: {
      id: "m1",
      task: "Sort the receipts",
      objective: "Sort the receipts",
      status,
      outcome,
      project_id: null,
      created_at: "2026-10-18T10:00:00",
      finished_at: status === "active" ? null : "2026-10-18T10:05:00",
      interrupted: false,
      continued_as: null,
      provider: "",
      model: "",
      route_note: "",
    },
    coordinator: { name: "Jarvis", status: status === "active" ? "working" : "done", waiting_on: null, steps: 3 },
    members: [],
    progress: { done: 1, total: 2 },
    activity: [],
    deliverable: { text: "", documents: [], worklist },
  } as MissionView;
}

// One stable snapshot: useSyncExternalStore needs the same object each read.
const EMPTY = { coordinator: "", coordinatorPhase: "", coordinatorNarration: "", members: {}, latest: null };
const LIVE: MissionLiveStore = {
  get: () => EMPTY,
  subscribe: () => () => {},
};

function show(v: MissionView) {
  render(
    <MissionOutput view={v} liveStore={LIVE} objective="Sort the receipts" onChanged={() => {}} onOpen={() => {}} />,
  );
  return screen.getByTestId("mission-headline");
}

function bellTitle(outcome: string | null): string {
  const e = {
    id: "e1",
    type: "session.completed",
    ts: "2026-10-18T10:05:00Z",
    session_id: "m1",
    payload: { origin: "job:mission", status: "completed", ok: true, outcome },
  } as unknown as IJEvent;
  return toActivity(e)!.title;
}

describe("the headline words", () => {
  it("completed_with_failures reads 'Finished, but part of it failed', the bell's words", () => {
    const v = view("completed", "completed_with_failures");
    expect(missionHeadline(v)).toBe("Finished, but part of it failed");
    expect(PART_FAILED_HEADLINE).toBe("Finished, but part of it failed");
    expect(missionFinish(v)).toBe("part_failed");
    // The bell and the screen say the same thing.
    expect(bellTitle("completed_with_failures")).toBe(
      `Your objective ${missionHeadline(v).replace(/^Finished/, "finished")}`,
    );
  });

  it("a worklist that kept failed items reads the same, though the ledger said completed", () => {
    const v = view("completed", "completed", { total: 5, done: 3, failed: 2, pending: 0, doing: 0 });
    expect(missionHeadline(v)).toBe("Finished, but part of it failed");
    expect(missionHeadlineWarn(v)).toBe(true);
  });

  it("needs_you still wins, as it does in the bell", () => {
    const v = view("completed", "needs_you", { total: 5, done: 3, failed: 2, pending: 0, doing: 0 });
    expect(missionHeadline(v)).toBe("Finished, but something needs you");
    expect(missionHeadlineWarn(v)).toBe(true);
  });

  it("controls: a clean finish is one word; failed, stopped and running keep their words", () => {
    expect(missionHeadline(view("completed", "completed"))).toBe("Finished");
    expect(missionHeadline(view("completed", "completed", { total: 3, done: 3, failed: 0, pending: 0, doing: 0 }))).toBe(
      "Finished",
    );
    expect(missionHeadlineWarn(view("completed", "completed"))).toBe(false);
    expect(missionHeadline(view("failed", null))).toBe("Could not finish");
    expect(missionHeadline(view("cancelled", null))).toBe("Stopped");
    expect(missionFinish(view("active", null))).toBeNull();
    // A view with no deliverable (the bell copy test's shape) never throws.
    expect(missionHeadline({ session: { status: "completed", outcome: null } } as unknown as MissionView)).toBe(
      "Finished",
    );
  });
});

describe("the headline on the mission screen", () => {
  it("part of it failed: the bell's words in the warn token", () => {
    const head = show(view("completed", "completed_with_failures"));
    expect(head.textContent).toBe("Finished, but part of it failed");
    expect(head.className).toContain("text-tone-warn");
    expect(head.className).not.toContain("text-zinc-400");
    expect(head.getAttribute("data-tone")).toBe("warn");
  });

  it("something needs you: warn too", () => {
    const head = show(view("completed", "needs_you"));
    expect(head.textContent).toBe("Finished, but something needs you");
    expect(head.className).toContain("text-tone-warn");
  });

  it("control: a clean finish stays the quiet grey line", () => {
    const head = show(view("completed", "completed"));
    expect(head.textContent).toBe("Finished");
    expect(head.className).toContain("text-zinc-400");
    expect(head.className).not.toContain("text-tone-warn");
    expect(head.getAttribute("data-tone")).toBe("plain");
  });
});
