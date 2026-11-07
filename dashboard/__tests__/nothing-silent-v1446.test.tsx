/**
 * v1.446.0 — ease-of-use review items 20, 21, 22, the bell's half.
 *
 *  20  a rule that could not start (`reflex.failed`) is an ATTENTION row in
 *      the daemon's plain words, opening the Reflex page;
 *  21  a folder watcher's new suggestion (`autonomy.proposed` with
 *      `source: "sentinel"`) is a QUIET row — never a ping or the badge — and
 *      the pulse's own proposals stay off the bell, as before;
 *  22  a workflow that finished with a failed step it was told to skip
 *      (`completed_with_failures`) rings like a failed one, folds its step
 *      rows in, and reads "Part failed" (amber) in Run history.
 *
 * The same rows are in fixtures/one-notice-v13600.json, which the daemon's
 * notifier and desktop/main.js read too (tests/test_one_notice_v13600.py).
 */
import { describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", () => ({
  ApiError: class extends Error {},
  get: vi.fn(async () => ({})),
  post: vi.fn(async () => ({})),
  put: vi.fn(async () => ({})),
  patch: vi.fn(async () => ({})),
  del: vi.fn(async () => ({})),
  API_BASE: "",
  ijToken: () => "",
  sseUrl: (p: string) => p,
}));

import {
  BELL_ACTIVITY_TYPES,
  BELL_EVENT_TYPES,
  oneRowPerWorkflow,
  toActivity,
} from "@/components/NotificationBell";
import { EVENT_WORDS } from "@/lib/eventWords";
import { statusWords } from "@/lib/statusWords";
import { WORKFLOW_RUN_TERMINAL, type IJEvent } from "@/lib/types";
import { isLiveRun } from "@/components/workflow/starters";

const ev = (type: string, payload: Record<string, unknown>, extra: Partial<IJEvent> = {}): IJEvent =>
  ({ id: `e_${type}_${Math.random()}`, type, ts: "2026-11-07T03:00:00", session_id: null, payload, ...extra }) as IJEvent;

describe("20: a rule that could not start", () => {
  it("is an attention row in the daemon's words, opening the Reflex page", () => {
    const row = toActivity(
      ev("reflex.failed", {
        rule: "invoice-run",
        reason: "the workflow “invoice-flow” it starts is no longer saved",
      }),
    )!;
    expect(row.title).toBe("Your rule invoice-run couldn't start");
    expect(row.body).toBe("the workflow “invoice-flow” it starts is no longer saved");
    expect(row.href).toBe("/reflex");
    expect(row.attention).toBe(true);
    expect(row.quiet).toBeFalsy();
  });

  it("a nameless rule still reads as a sentence", () => {
    expect(toActivity(ev("reflex.failed", {}))!.title).toBe("A rule couldn't start");
  });

  it("the bell reads it, and remembers it across a closed window", () => {
    expect(BELL_ACTIVITY_TYPES).toContain("reflex.failed");
    expect(BELL_EVENT_TYPES).toContain("reflex.failed");
    expect(EVENT_WORDS["reflex.failed"]).toBe("A rule couldn't start");
  });
});

describe("21: a folder watcher's new suggestion", () => {
  it("is a QUIET row naming the watcher, opening Autonomy", () => {
    const row = toActivity(
      ev("autonomy.proposed", { source: "sentinel", sentinel: "scans", rationale: "Changed: K-1 2025.pdf." }),
    )!;
    expect(row.title).toBe("Your watcher scans noticed changes · Review");
    expect(row.body).toBe("Changed: K-1 2025.pdf.");
    expect(row.href).toBe("/autonomy");
    expect(row.quiet).toBe(true);
    expect(row.attention).toBeFalsy();
  });

  it("the pulse's own proposals stay off the bell", () => {
    expect(toActivity(ev("autonomy.proposed", { proposal_id: "p", title: "Tidy the inbox" }))).toBeNull();
    expect(BELL_ACTIVITY_TYPES).toContain("autonomy.proposed");
  });
});

describe("22: a workflow that finished with a skipped failure", () => {
  const partFailed = () =>
    toActivity(
      ev("workflow.completed", {
        workflow: "Weekly report",
        status: "completed_with_failures",
        sessions: ["s_a", "s_b"],
        failed_session: "s_a",
      }),
    )!;

  it("rings like a failure, in plain words", () => {
    const row = partFailed();
    expect(row.title).toBe("Your workflow Weekly report finished, but part of it failed");
    expect(row.body).toBe("The Workflows page shows which step failed.");
    expect(row.attention).toBe(true);
    expect(row.covers).toEqual(["s_a", "s_b"]);
    expect(row.endedBy).toBe("s_a");
  });

  it("is ONE row: the failed step's own row folds into it", () => {
    const step = toActivity(
      ev(
        "session.completed",
        { status: "failed", ok: false, origin: "workflow:Weekly report", summary: "The run failed: x" },
        { session_id: "s_a" },
      ),
    )!;
    expect(step.session).toBe("s_a");
    const wf = partFailed();
    const out = oneRowPerWorkflow([wf, step]);
    expect(out.map((r) => r.id)).toEqual([wf.id]);
    expect(out[0].standsInFor).toEqual([step.id]);
  });

  it("names the steps it went on past, by name", () => {
    const one = toActivity(
      ev("workflow.completed", { workflow: "w", status: "completed_with_failures", failed_steps: ["Fetch"] }),
    )!;
    expect(one.body).toBe("Its step “Fetch” failed. The Workflows page shows why.");
    const two = toActivity(
      ev("workflow.completed", { workflow: "w", status: "completed_with_failures", failed_steps: ["A", "B"] }),
    )!;
    expect(two.body).toBe("Its steps “A”, “B” failed. The Workflows page shows why.");
  });

  it("a clean run stays a quiet row", () => {
    const row = toActivity(ev("workflow.completed", { workflow: "w", status: "completed" }))!;
    expect(row.quiet).toBe(true);
  });

  it("a part-failed run is FINISHED: Run history stops polling, a chat card settles", () => {
    expect(WORKFLOW_RUN_TERMINAL.has("completed_with_failures")).toBe(true);
    expect(isLiveRun({ status: "completed_with_failures" })).toBe(false);
  });

  it("Run history reads it 'Part failed' in amber, never as an unknown word", () => {
    expect(statusWords("completed_with_failures")).toEqual({ word: "Part failed", tone: "amber" });
    // The same object the session outcome and the schedule row say.
    expect(statusWords("completed_with_failures")).toBe(statusWords("completed", "completed_with_failures"));
  });
});
