/**
 * Harness gaps, fix 5 — the Plan tab shows the plan the reply beside it shows.
 *
 * The user's screenshot (live v1.388.1): the Plan tab said "No plan yet"
 * while the reply right beside it said "Plan · 0 of 11 done". The pane
 * rebuilt the plan ONLY from a `plan_update` step's detail, whose input is
 * cut at 300 characters (chat_turn.step_detail) — an 11-step plan's JSON is
 * longer, so it failed to parse — while the reply line has kept the plan
 * itself (`plan`) since v1.374.0, and the reply reads THAT
 * (lib/buildContext.replyPlan).
 *
 * Now the pane reads the newest reply's saved plan through the SAME
 * `replyPlan` (so the two can never disagree), then the step-detail fallback
 * for a line an older daemon wrote, then the approved plan; a running
 * reply's live plan still wins. The REAL HarnessPanes; the mocks are
 * ij-harness-panes-v1369's.
 */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";

const api = vi.hoisted(() => {
  class ApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
    }
  }
  return { ApiError, gets: {} as Record<string, unknown> };
});
vi.mock("@/lib/api", () => ({
  ApiError: api.ApiError,
  API_BASE: "http://d",
  ijToken: () => "",
  get: (p: string) =>
    p in api.gets ? Promise.resolve(api.gets[p]) : Promise.reject(new api.ApiError(`unmocked ${p}`, 404)),
  post: () => Promise.resolve({ changes: [] }),
  put: () => Promise.resolve({}),
  patch: () => Promise.resolve({}),
  del: () => Promise.resolve({}),
}));
vi.mock("@/components/terminal/FilesPanel", () => ({
  FilesPanel: ({ folder }: { folder: string | null }) => <div data-testid="files-panel">{folder}</div>,
}));

import { HarnessPanes, keptPlan, panePlan, type SessionTerminalApi } from "@/components/harness/HarnessPanes";
import { decodeSessionView, type BuildSessionView } from "@/lib/buildSessions";
import { replyPlan } from "@/lib/buildContext";

const terminal: SessionTerminalApi = {
  find: () => null,
  open: async () => null,
  render: () => <div />,
  close: () => {},
};

function view(over: Record<string, unknown> = {}): BuildSessionView {
  const v = decodeSessionView({
    id: "s1",
    title: "Cart",
    folder: "C:\\work\\shop",
    folder_name: "shop",
    session_id: "build:s1",
    messages: [{ role: "user", content: "Build the shop", at: "2026-10-25T10:00:00" }],
    ...over,
  });
  if (!v) throw new Error("bad fixture");
  return v;
}

/** Eleven steps, as the reply line keeps them (routes/build.saved_plan). */
const ELEVEN = Array.from({ length: 11 }, (_, i) => ({ goal: `Step ${i + 1}: do part ${i + 1} of the shop`, state: "waiting" }));
const SAVED_PLAN = { steps: ELEVEN, total: 11 };
/** The same plan as its plan_update call's input — CUT at 300 characters, as
 *  chat_turn.step_detail keeps it: no longer JSON. */
const CUT_INPUT = JSON.stringify({
  plan: ELEVEN.map((s) => ({ step: s.goal, status: "pending" })),
}).slice(0, 300);

const REPLY = {
  role: "assistant",
  content: "Here is the plan; starting now.",
  turnId: "t1",
  steps: [{ name: "plan_update", ok: true, ms: 3, detail: { input: CUT_INPUT } }],
  plan: SAVED_PLAN,
};

beforeEach(() => {
  api.gets = { "/browser/status": { paired: false } };
  localStorage.clear();
});
afterEach(() => cleanup());

describe("the Plan tab shows the plan the reply shows", () => {
  it("the cut step detail is not a plan (the old reading found nothing)", () => {
    expect(() => JSON.parse(CUT_INPUT)).toThrow();
  });

  it("an 11-step saved plan with a cut plan_update detail renders the checklist in pane-plan", () => {
    const v = view({ messages: [{ role: "user", content: "Build the shop", turnId: "t1" }, REPLY] });
    render(<HarnessPanes view={v} livePlan={null} terminal={terminal} refreshKey={0} undoFor={() => null} onUndo={async () => {}} />);
    const pane = screen.getByTestId("pane-plan");
    expect(pane).not.toHaveTextContent("No plan yet");
    const list = within(pane).getByTestId("plan-checklist");
    expect(within(list).getAllByTestId("plan-step")).toHaveLength(11);
    expect(within(list).getAllByTestId("plan-step-goal")[0].textContent).toBe("Step 1: do part 1 of the shop");
  });

  it("is the SAME reading the reply uses (replyPlan), so the two never disagree", () => {
    const v = view({ messages: [REPLY] });
    expect(panePlan(v, null)).toEqual(replyPlan(REPLY.plan, false));
    // A failed reply's step that was working reads as failed in both.
    const failed = { ...REPLY, error: "It stopped.", plan: { steps: [{ goal: "a", state: "running" }], total: 1 } };
    expect(keptPlan(view({ messages: [failed] }).messages)).toEqual(replyPlan(failed.plan, true));
  });

  it("the NEWEST reply's plan wins; an older daemon's line still reads from its step detail; then the approved plan", () => {
    const older = { ...REPLY, plan: { steps: [{ goal: "old plan", state: "done" }], total: 1 } };
    const newer = { ...REPLY, turnId: "t2", plan: SAVED_PLAN };
    expect(panePlan(view({ messages: [older, newer] }), null)?.steps).toHaveLength(11);
    const whole = JSON.stringify({ plan: [{ step: "from the detail", status: "completed" }] });
    const oldDaemon = { role: "assistant", content: "ok", steps: [{ name: "plan_update", ok: true, ms: 1, detail: { input: whole } }] };
    expect(panePlan(view({ messages: [oldDaemon] }), null)?.steps.map((s) => s.goal)).toEqual(["from the detail"]);
    const approved = view({ plan: { text: "x", steps: [{ step: "approved step", status: "pending" }] } });
    expect(panePlan(approved, null)?.steps.map((s) => s.goal)).toEqual(["approved step"]);
    // A running reply's live plan still wins.
    const live = { steps: [{ index: 0, goal: "live", state: "running" as const }], total: 1 };
    expect(panePlan(view({ messages: [REPLY] }), live)).toBe(live);
  });
});
