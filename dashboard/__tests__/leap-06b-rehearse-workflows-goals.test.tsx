/**
 * Leap 06-b: rehearse a workflow and a goal, and the honest limits.
 *
 * - Workflows: each saved workflow has Rehearse beside Load; its report shows
 *   under its row; a kept rehearsal reads "Rehearsed <date>" on the row.
 * - Autonomy: an active or paused goal has Rehearse; its report shows on the
 *   goal's card. A finished, stopped or tripped goal has none (a real try
 *   would be refused, so would its rehearsal).
 * - The report: a workflow that stopped at its question says what was not
 *   rehearsed; a read after a held change says the real run may differ
 *   (also when the rehearsal could not finish). Both lines are the daemon's.
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const api = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  return {
    gets: [] as string[],
    posts: [] as { path: string; body: unknown }[],
    responses: {} as Record<string, unknown>,
    postResponses: {} as Record<string, unknown>,
    FakeApiError,
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: api.FakeApiError,
  API_BASE: "http://api.test",
  ijToken: () => "tok",
  wsUrl: (p: string) => `ws://api.test${p}`,
  api: () => Promise.resolve({}),
  get: (path: string) => {
    api.gets.push(path);
    const r = api.responses[path];
    if (r === undefined) return Promise.reject(new api.FakeApiError(`unmocked GET ${path}`, 0));
    return Promise.resolve(r);
  },
  post: (path: string, body?: unknown) => {
    api.posts.push({ path, body });
    const r = api.postResponses[path];
    return Promise.resolve(r === undefined ? {} : r);
  },
  patch: () => Promise.resolve({}),
  put: () => Promise.resolve({}),
  del: () => Promise.resolve({}),
}));

vi.mock("@/lib/useEvents", () => ({
  useEvents: () => ({ events: [], connected: true }),
}));

vi.mock("next/link", async () => {
  const { createElement } = await import("react");
  return {
    default: ({ href, children, ...rest }: { href: string; children?: React.ReactNode }) =>
      createElement("a", { href, ...rest }, children),
  };
});

import AutonomyPage from "@/app/autonomy/page";
import { RehearsalReport } from "@/components/RehearsalReport";
import { SavedWorkflows, rehearsedWords } from "@/components/workflow/SavedWorkflows";

afterEach(() => {
  cleanup();
  api.gets = [];
  api.posts = [];
  api.responses = {};
  api.postResponses = {};
});

const STOPPED = "It stopped where it would ask you, so what comes after was not rehearsed: “Archive”.";
const GUESSED = "Steps after the first change were worked out without it, so the real run may differ.";

function workflowReport(over: Record<string, unknown> = {}) {
  return {
    id: "reh_wf",
    subject: { kind: "workflow", id: "wf_1", name: "Monday summary" },
    status: "ready",
    sentence: "This would write 1 file, send 1 message and ask you 1 question. Nothing was changed.",
    counts: { write: 1, send: 1, ask: 1 },
    would: [
      { kind: "write", words: "write summary.txt", would: "run" },
      { kind: "send", words: "send the message “The summary is ready.” to this PC", would: "run" },
      { kind: "ask", words: "ask you: Send it to the team?", would: "run" },
    ],
    would_total: 3,
    refused: [],
    reads: 0,
    guessed_after: false,
    guessed_words: "",
    stopped_words: STOPPED,
    error: "",
    detail: "",
    sample: "",
    kept_at: null,
    ...over,
  };
}

describe("the report's honest limits", () => {
  it("names the workflow and all three items, and says what was not rehearsed", () => {
    render(<RehearsalReport id="reh_wf" initial={workflowReport() as never} />);
    expect(screen.getByText("Rehearsal of the workflow “Monday summary”")).toBeInTheDocument();
    const items = within(screen.getByTestId("rehearsal-items-reh_wf"));
    expect(items.getByText("Would write summary.txt")).toBeInTheDocument();
    expect(items.getByText("Would send the message “The summary is ready.” to this PC")).toBeInTheDocument();
    expect(items.getByText("Would ask you: Send it to the team?")).toBeInTheDocument();
    expect(screen.getByTestId("rehearsal-stopped-reh_wf")).toHaveTextContent(STOPPED);
    expect(screen.queryByTestId("rehearsal-guessed-reh_wf")).toBeNull();
  });

  it("says the real run may differ, also when the rehearsal could not finish", () => {
    render(
      <RehearsalReport
        id="reh_wf"
        initial={workflowReport({
          status: "failed",
          error: "The step “Read it back” failed: no such file",
          guessed_after: true,
          guessed_words: GUESSED,
          stopped_words: "",
        }) as never}
      />,
    );
    expect(screen.getByTestId("rehearsal-error-reh_wf")).toHaveTextContent(
      "The step “Read it back” failed: no such file Nothing was changed.",
    );
    expect(screen.getByTestId("rehearsal-guessed-reh_wf")).toHaveTextContent(GUESSED);
  });
});

describe("Workflows: Rehearse on a saved workflow", () => {
  it("starts a rehearsal of that workflow and shows its report under the row", async () => {
    api.responses["/workflows"] = {
      workflows: [
        { id: "wf_1", name: "Monday summary", steps_json: "[{}, {}, {}]", rehearsed_at: null },
        { id: "wf_2", name: "Kept one", steps_json: "[{}]", rehearsed_at: "2026-11-20T10:00:00Z" },
      ],
    };
    api.responses["/rehearsals?kind=workflow"] = { rehearsals: [] };
    api.postResponses["/rehearsals"] = workflowReport({ status: "running", sentence: "Rehearsing." });
    api.responses["/rehearsals/reh_wf"] = workflowReport();
    render(<SavedWorkflows />);
    const press = await screen.findByTestId("workflow-rehearse-Monday summary");
    expect(screen.getByTestId("workflow-rehearsed-Kept one")).toHaveTextContent(
      rehearsedWords("2026-11-20T10:00:00Z"),
    );
    expect(screen.queryByTestId("workflow-rehearsed-Monday summary")).toBeNull();
    fireEvent.click(press);
    await waitFor(() => expect(screen.getByTestId("rehearsal-report-reh_wf")).toBeInTheDocument());
    expect(api.posts).toEqual([{ path: "/rehearsals", body: { kind: "workflow", id: "Monday summary" } }]);
  });

  it("shows a report still open again after a reload", async () => {
    api.responses["/workflows"] = {
      workflows: [{ id: "wf_1", name: "Monday summary", steps_json: "[{}]" }],
    };
    api.responses["/rehearsals?kind=workflow"] = { rehearsals: [workflowReport()] };
    render(<SavedWorkflows />);
    expect(await screen.findByTestId("rehearsal-report-reh_wf")).toBeInTheDocument();
    expect(api.posts).toEqual([]);
  });
});

function primeAutonomy(goals: unknown[]) {
  api.responses["/goals/digest?hours=24"] = { digest: { goals: [], since: "2026-11-27T00:00:00Z" } };
  api.responses["/autonomy"] = {
    enabled: true, level: "suggest", dry_run: false, kill_switch: false, tick_seconds: 900,
    max_actions_per_day: 20, max_tokens_per_day: 200000, used_actions_24h: 0, used_tokens_24h: 0,
    active_goals: 1, pending_proposals: 0,
  };
  api.responses["/autonomy/goals"] = { goals: [] };
  api.responses["/proposals?status=pending"] = { proposals: [] };
  api.responses["/autonomy/briefing"] = {
    text: "quiet", active_goals: 0, recent_actions: 0, pending_proposals: 0, pushed: null,
  };
  api.responses["/goals"] = { goals };
  api.responses["/rehearsals?kind=goal"] = { rehearsals: [] };
}

function goal(id: string, name: string, state: string) {
  return {
    id, name, state, contract_text: "Write plan.txt.", schedule: "", budget: { max_tokens: 1000 },
    spent: { tokens: 0, dollars: 0, wallclock_s: 0, iterations: 0 }, last_run_at: null,
    project_id: null, verifier: { kind: "manual", checks: [] },
  };
}

describe("Autonomy: Rehearse on a goal", () => {
  it("is offered on an active or paused goal only, and shows the report on its card", async () => {
    primeAutonomy([
      goal("g_active", "Weekly plan", "active"),
      goal("g_paused", "Nightly recap", "paused"),
      goal("g_stopped", "Old crawler", "stopped"),
      goal("g_tripped", "Ad optimizer", "tripped"),
    ]);
    api.postResponses["/rehearsals"] = {
      ...workflowReport(),
      id: "reh_goal",
      subject: { kind: "goal", id: "g_active", name: "Weekly plan" },
      status: "running",
      sentence: "Rehearsing.",
      stopped_words: "",
    };
    api.responses["/rehearsals/reh_goal"] = {
      ...workflowReport(),
      id: "reh_goal",
      subject: { kind: "goal", id: "g_active", name: "Weekly plan" },
      sentence: "This would write 1 file. Nothing was changed.",
      would: [{ kind: "write", words: "write plan.txt", would: "run" }],
      stopped_words: "",
    };
    render(<AutonomyPage />);
    const active = within(await screen.findByTestId("goal-card-g_active"));
    expect(within(screen.getByTestId("goal-card-g_paused")).getByRole("button", { name: "Rehearse" }))
      .toBeInTheDocument();
    expect(within(screen.getByTestId("goal-card-g_stopped")).queryByRole("button", { name: "Rehearse" }))
      .toBeNull();
    expect(within(screen.getByTestId("goal-card-g_tripped")).queryByRole("button", { name: "Rehearse" }))
      .toBeNull();
    fireEvent.click(active.getByRole("button", { name: "Rehearse" }));
    await waitFor(() => expect(active.getByTestId("rehearsal-report-reh_goal")).toBeInTheDocument());
    expect(api.posts).toEqual([{ path: "/rehearsals", body: { kind: "goal", id: "g_active" } }]);
    expect(screen.getByText("Rehearsal of the goal “Weekly plan”")).toBeInTheDocument();
  });
});
