/**
 * North-star plan 3.4 (v1.503.0, decision D12) and 3.11.
 *
 * A queued job whose run completed while an ask went unanswered, or whose
 * change failed, was a "done" row and the inbox said "Done". The row now
 * carries the ledger's verdict (`outcome`) and says it in the ONE table's
 * words ("Needs you" / "Part failed" / "Did nothing"), with the reason under
 * it and Retry offered. A run that did nothing has its own word everywhere a
 * finished run is worded (the run badge and the Schedules row).
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

vi.mock("@/lib/api", () => {
  class ApiError extends Error {
    status = 500;
  }
  return { ApiError, post: () => Promise.resolve({}), get: () => Promise.resolve({}) };
});
vi.mock("next/link", () => ({
  default: ({ children, href, ...rest }: React.ComponentProps<"a">) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

import { AssignmentRow, healthLine, rowActions } from "@/components/agents/AgentInbox";
import { scheduleRunWords, statusWords } from "@/lib/statusWords";
import type { Assignment } from "@/lib/types";

afterEach(cleanup);

function row(over: Partial<Assignment> & { outcome?: string | null }): Assignment {
  return {
    id: "a1",
    project_id: null,
    assignee: "builder",
    task: "file the receipts",
    title: "file the receipts",
    priority: 0,
    status: "done",
    source: "user",
    reason: "",
    payload: {},
    idempotency_key: null,
    coalesced_count: 0,
    attempts: 1,
    failure_count: 0,
    blocked_reason: null,
    held_reason: null,
    depth: 0,
    session_id: null,
    last_error: null,
    created_at: null,
    claimed_at: null,
    started_at: null,
    finished_at: null,
    updated_at: null,
    ...over,
  } as Assignment;
}

describe("a queued job shows how it really ended (D12)", () => {
  it.each([
    ["needs_you", "Needs you"],
    ["completed_with_failures", "Part failed"],
    ["did_nothing", "Did nothing"],
  ])("a done row whose verdict is %s reads %s, with its reason and Retry", (outcome, word) => {
    render(
      <ul>
        <AssignmentRow
          assignment={row({ outcome, last_error: "Some of its steps failed." })}
          onChanged={() => {}}
        />
      </ul>,
    );
    const li = screen.getByTestId("inbox-done-a1");
    expect(li.textContent).toContain(word);
    expect(li.textContent).not.toContain("Done");
    expect(li.textContent).toContain("Some of its steps failed.");
    expect(screen.getByTestId("inbox-retry-a1")).toBeTruthy();
  });

  it("a done row whose job was done still reads Done, with no Retry", () => {
    render(
      <ul>
        <AssignmentRow assignment={row({ outcome: "completed" })} onChanged={() => {}} />
      </ul>,
    );
    expect(screen.getByTestId("inbox-done-a1").textContent).toContain("Done");
    expect(screen.queryByTestId("inbox-retry-a1")).toBeNull();
  });

  it("rowActions offers Retry on a shortfall only", () => {
    expect(rowActions("done", "needs_you").retry).toBe(true);
    expect(rowActions("done", "completed").retry).toBe(false);
    expect(rowActions("done").retry).toBe(false);
    expect(rowActions("failed").retry).toBe(true);
  });

  it("the health line says did nothing in amber", () => {
    const h = {
      last_run_at: new Date().toISOString(),
      last_outcome: "did_nothing",
      last_error: null,
      last_wake_at: null,
      queued: 0,
      running: 0,
      blocked: 0,
    };
    expect(healthLine(h).tone).toBe("amber");
    expect(healthLine(h).text).toContain("did nothing");
  });
});

describe("a run that did nothing has its own word (3.11)", () => {
  it("on a run's badge and on the Schedules row", () => {
    expect(statusWords("completed", "did_nothing")).toEqual({ word: "Did nothing", tone: "amber" });
    expect(scheduleRunWords("did_nothing")).toEqual({ word: "Did nothing", tone: "amber" });
    // A failed run still says Failed whatever its verdict.
    expect(statusWords("failed", "did_nothing").word).toBe("Failed");
  });
});
