/**
 * v1.168.0 P4 — workflow runs parked on an `ask` step join the notification
 * bell.
 *
 * The mechanism under test: NotificationBell polls GET /workflows/runs
 * (same 15s polled-source pattern as /computeruse and /diagnostics), treats
 * `status === "waiting"` rows as work that WAITS ON THE USER (badge + tab
 * title + desktop ping), and renders each one in the dropdown with the actual
 * question from `waiting_json` ({index, step, question} — workflows/engine.py)
 * plus an INLINE answer box that POSTs the existing
 * /workflows/runs/{id}/answer. The atomic waiting→resuming claim can 409 when
 * the same ask was answered from another surface (chat card / Workflows page)
 * — that must surface honestly, never retry, never vanish silently.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";

const { getMock, postMock, notifyMock, eventsRef } = vi.hoisted(() => ({
  getMock: vi.fn(),
  postMock: vi.fn(),
  notifyMock: vi.fn(),
  eventsRef: { current: [] as unknown[] },
}));

vi.mock("@/lib/api", () => {
  class MockApiError extends Error {
    status: number;
    constructor(message: string, status: number) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  return {
    ApiError: MockApiError,
    get: getMock,
    post: postMock,
    put: vi.fn(async () => ({})),
    patch: vi.fn(async () => ({})),
    del: vi.fn(async () => ({})),
    API_BASE: "",
    ijToken: () => "",
  };
});

vi.mock("@/lib/useEvents", () => ({
  useEvents: () => ({ events: eventsRef.current, connected: true }),
}));

vi.mock("@/lib/useDesktopNotifications", () => ({
  useDesktopNotifications: () => ({
    supported: true,
    permission: "granted" as const,
    requestPermission: async () => "granted" as const,
    notify: notifyMock,
  }),
}));

import { ApiError } from "@/lib/api";
import { NotificationBell } from "@/components/NotificationBell";

// ---- fixtures ---------------------------------------------------------------

// The exact polled path (v1.168.0 coordinator integration): server-side
// status=waiting so a long-parked question can NEVER fall out of a
// newest-first page — the exact "count that lies" failure the bell exists to
// prevent — and slim=true because this poll runs on every page and only needs
// waiting_json (the question), never the steps/outputs blobs. limit stays at
// the route's clamp max as belt-and-braces.
const RUNS_PATH = "/workflows/runs?status=waiting&slim=true&limit=200";

const WAITING_RUN = {
  id: "wfrun-abc123",
  workflow_name: "monthly-close",
  status: "waiting",
  waiting_json: JSON.stringify({
    index: 2,
    step: "confirm send",
    question: "Send the summary email to the client?",
  }),
  started_at: "2026-08-12T10:00:00",
};

const COMPLETED_RUN = {
  id: "wfrun-done",
  workflow_name: "old-job",
  status: "completed",
  waiting_json: "",
};

const RUNNING_RUN = {
  id: "wfrun-live",
  workflow_name: "live-job",
  status: "running",
  waiting_json: "",
};

/** Route the mocked `get` like the daemon's three bell sources. */
function mockDaemon({
  runs = [] as unknown[],
  approvals = 0,
  reviews = 0,
} = {}) {
  getMock.mockImplementation(async (path: unknown) => {
    if (path === "/computeruse") return { pending_approvals: approvals };
    if (path === "/diagnostics") return { pending_reviews: reviews };
    if (path === RUNS_PATH) return { runs };
    return {};
  });
}

async function openBell() {
  const trigger = await screen.findByRole("button", { name: /notification/i });
  fireEvent.click(trigger);
  return trigger;
}

const runsCalls = () =>
  getMock.mock.calls.filter((c: unknown[]) => c[0] === RUNS_PATH).length;

beforeEach(() => {
  getMock.mockReset();
  postMock.mockReset();
  notifyMock.mockReset();
  eventsRef.current = [];
  document.title = "Iron Jarvis";
});

afterEach(() => {
  cleanup();
});

// ---- badge / count ----------------------------------------------------------

describe("bell badge with waiting runs", () => {
  it("counts ONLY status=waiting runs and sums with reviews + approvals", async () => {
    mockDaemon({
      runs: [WAITING_RUN, COMPLETED_RUN, RUNNING_RUN],
      approvals: 1,
      reviews: 2,
    });
    render(<NotificationBell />);
    // 2 reviews + 1 approval + exactly 1 waiting run = 4 (finished/running
    // runs must NOT inflate the badge).
    const trigger = await screen.findByRole("button", { name: "4 notifications" });
    expect(trigger.textContent).toContain("4");
    await waitFor(() => expect(document.title).toBe("(4) Iron Jarvis"));
  });

  it("polls the runs list on the exact bell path and titles the tab for one waiting run", async () => {
    mockDaemon({ runs: [WAITING_RUN] });
    render(<NotificationBell />);
    await screen.findByRole("button", { name: "1 notifications" });
    await waitFor(() => expect(document.title).toBe("(1) Iron Jarvis"));
    expect(getMock).toHaveBeenCalledWith(RUNS_PATH);
    // Regression pin: the poll asks for the route's clamp max (200), never the
    // old 50-row window that let a long-parked run fall off the badge.
    expect(getMock).not.toHaveBeenCalledWith("/workflows/runs?limit=50");
    for (const [path] of getMock.mock.calls as [unknown][]) {
      if (typeof path === "string" && path.startsWith("/workflows/runs")) {
        expect(path).toBe(RUNS_PATH);
      }
    }
  });

  it("pings the desktop with the waiting-question count on the upward transition", async () => {
    mockDaemon({ runs: [WAITING_RUN] });
    render(<NotificationBell />);
    await waitFor(() => expect(notifyMock).toHaveBeenCalled());
    const [title, body] = notifyMock.mock.calls[0] as [string, string];
    expect(title).toBe("Iron Jarvis — 1 pending");
    expect(body).toContain("1 workflow question waiting");
  });
});

// ---- dropdown row -----------------------------------------------------------

describe("waiting-run row", () => {
  it("shows the workflow name, the ACTUAL question, and an inline answer box", async () => {
    mockDaemon({ runs: [WAITING_RUN] });
    render(<NotificationBell />);
    await screen.findByRole("button", { name: "1 notifications" });
    await openBell();

    const row = await screen.findByTestId("bell-waiting-run");
    expect(row.textContent).toContain("Workflow “monthly-close” needs an answer");
    expect(row.textContent).toContain("Send the summary email to the client?");

    const input = screen.getByLabelText("Answer workflow monthly-close");
    const submit = screen.getByRole("button", { name: "Answer" });
    expect(submit).toBeDisabled(); // nothing typed yet
    fireEvent.change(input, { target: { value: "yes" } });
    expect(submit).not.toBeDisabled();
  });

  it("falls back to an honest generic question when waiting_json is corrupt", async () => {
    mockDaemon({
      runs: [{ ...WAITING_RUN, waiting_json: "not json {" }],
    });
    render(<NotificationBell />);
    await screen.findByRole("button", { name: "1 notifications" });
    await openBell();
    const row = await screen.findByTestId("bell-waiting-run");
    expect(row.textContent).toContain("This run needs your answer.");
  });

  it("submits the TRIMMED answer to POST /workflows/runs/{id}/answer and the row leaves", async () => {
    mockDaemon({ runs: [WAITING_RUN] });
    postMock.mockResolvedValue({ id: WAITING_RUN.id, status: "running", answered: true });
    render(<NotificationBell />);
    await screen.findByRole("button", { name: "1 notifications" });
    await openBell();
    await screen.findByTestId("bell-waiting-run");

    fireEvent.change(screen.getByLabelText("Answer workflow monthly-close"), {
      target: { value: "  yes, send it  " },
    });
    const before = runsCalls();
    fireEvent.click(screen.getByRole("button", { name: "Answer" }));

    await waitFor(() =>
      expect(postMock).toHaveBeenCalledWith("/workflows/runs/wfrun-abc123/answer", {
        answer: "yes, send it",
      }),
    );
    expect(postMock).toHaveBeenCalledTimes(1);

    // The row leaves immediately (local suppression) even though the mocked
    // daemon still lists the run as waiting — and the list is refetched.
    await waitFor(() =>
      expect(screen.queryByTestId("bell-waiting-run")).toBeNull(),
    );
    await waitFor(() => expect(runsCalls()).toBeGreaterThan(before));
    // Nothing else pends, so the badge and tab title clear to the base state.
    await waitFor(() => expect(document.title).toBe("Iron Jarvis"));
  });

  it("URL-encodes the run id in the POST path (ids arrive from a polled response)", async () => {
    // Server-generated ids are wfrun-<hex> today, but the id is untrusted
    // polled data — a "/" or "?" must not silently reroute the POST. Same
    // encodeURIComponent idiom as the Workflows page / canvas callers.
    mockDaemon({ runs: [{ ...WAITING_RUN, id: "wfrun-a/b?c" }] });
    postMock.mockResolvedValue({ answered: true });
    render(<NotificationBell />);
    await screen.findByRole("button", { name: "1 notifications" });
    await openBell();
    await screen.findByTestId("bell-waiting-run");

    fireEvent.change(screen.getByLabelText("Answer workflow monthly-close"), {
      target: { value: "yes" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Answer" }));

    await waitFor(() =>
      expect(postMock).toHaveBeenCalledWith("/workflows/runs/wfrun-a%2Fb%3Fc/answer", {
        answer: "yes",
      }),
    );
  });

  it("Enter in the input submits, exactly like the chat card's answer box", async () => {
    mockDaemon({ runs: [WAITING_RUN] });
    postMock.mockResolvedValue({ answered: true });
    render(<NotificationBell />);
    await screen.findByRole("button", { name: "1 notifications" });
    await openBell();
    await screen.findByTestId("bell-waiting-run");

    const input = screen.getByLabelText("Answer workflow monthly-close");
    fireEvent.change(input, { target: { value: "option B" } });
    fireEvent.keyDown(input, { key: "Enter" });
    await waitFor(() =>
      expect(postMock).toHaveBeenCalledWith("/workflows/runs/wfrun-abc123/answer", {
        answer: "option B",
      }),
    );
  });

  it("renders one-tap option buttons when waiting_json carries options", async () => {
    mockDaemon({
      runs: [
        {
          ...WAITING_RUN,
          waiting_json: JSON.stringify({
            index: 2,
            step: "gate",
            question: "Approve the draft?",
            options: ["approve", "reject"],
          }),
        },
      ],
    });
    postMock.mockResolvedValue({ answered: true });
    render(<NotificationBell />);
    await screen.findByRole("button", { name: "1 notifications" });
    await openBell();
    await screen.findByTestId("bell-waiting-run");

    fireEvent.click(screen.getByRole("button", { name: "approve" }));
    await waitFor(() =>
      expect(postMock).toHaveBeenCalledWith("/workflows/runs/wfrun-abc123/answer", {
        answer: "approve",
      }),
    );
  });
});

// ---- conflict + failure honesty ---------------------------------------------

describe("answer failures", () => {
  it("409 (answered elsewhere): honest note with the server's detail, row leaves, no retry", async () => {
    mockDaemon({ runs: [WAITING_RUN] });
    postMock.mockRejectedValue(
      new ApiError("run is running, not waiting — it may already be answered", 409),
    );
    render(<NotificationBell />);
    await screen.findByRole("button", { name: "1 notifications" });
    const trigger = await openBell();
    await screen.findByTestId("bell-waiting-run");

    fireEvent.change(screen.getByLabelText("Answer workflow monthly-close"), {
      target: { value: "yes" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Answer" }));

    const note = await screen.findByTestId("bell-waiting-conflict");
    expect(note.textContent).toContain("Couldn't answer “monthly-close”");
    expect(note.textContent).toContain(
      "run is running, not waiting — it may already be answered",
    );
    expect(screen.queryByTestId("bell-waiting-run")).toBeNull();
    expect(postMock).toHaveBeenCalledTimes(1); // never retried

    // Closing the dropdown clears the note; reopening does NOT resurrect the
    // already-conflicted ask even though the mock still lists it as waiting.
    fireEvent.mouseDown(document.body);
    await waitFor(() => expect(screen.queryByTestId("bell-waiting-conflict")).toBeNull());
    fireEvent.click(trigger);
    await screen.findByText(/all caught up/i);
    expect(screen.queryByTestId("bell-waiting-run")).toBeNull();
    expect(screen.queryByTestId("bell-waiting-conflict")).toBeNull();
  });

  it("non-409 failure keeps the row answerable and shows the error inline", async () => {
    mockDaemon({ runs: [WAITING_RUN] });
    postMock.mockRejectedValue(new ApiError("daemon offline", 0));
    render(<NotificationBell />);
    await screen.findByRole("button", { name: "1 notifications" });
    await openBell();
    await screen.findByTestId("bell-waiting-run");

    fireEvent.change(screen.getByLabelText("Answer workflow monthly-close"), {
      target: { value: "yes" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Answer" }));

    await screen.findByText("daemon offline");
    expect(screen.getByTestId("bell-waiting-run")).toBeInTheDocument();
    // Not stuck in "Sending…": the user can fix connectivity and retry.
    expect(screen.getByRole("button", { name: "Answer" })).not.toBeDisabled();
  });
});

// ---- live-event refresh -----------------------------------------------------

describe("workflow.waiting live event", () => {
  it("triggers ONE immediate refetch of the runs list (no 15s lag, no loop)", async () => {
    mockDaemon({ runs: [] });
    eventsRef.current = [
      {
        id: "ev-park-1",
        ts: "2026-08-12T10:00:01",
        type: "workflow.waiting",
        payload: { run_id: "wfrun-abc123", workflow: "monthly-close", question: "Q?" },
      },
    ];
    render(<NotificationBell />);
    // initial poll fetch + the event-triggered reload = exactly 2.
    await waitFor(() => expect(runsCalls()).toBe(2));
    await new Promise((r) => setTimeout(r, 50));
    expect(runsCalls()).toBe(2); // dedupe by event id — no refetch storm
  });
});

// ---- surrounding chrome -----------------------------------------------------

describe("bell chrome", () => {
  it("empty state mentions workflow questions", async () => {
    mockDaemon({ runs: [] });
    render(<NotificationBell />);
    await openBell();
    await screen.findByText(/all caught up/i);
    expect(
      screen.getByText(/Reviews, approvals, and workflow questions that need you/),
    ).toBeInTheDocument();
  });

  it("footer points at the Workflows page when ONLY parked runs are pending", async () => {
    mockDaemon({ runs: [WAITING_RUN] });
    render(<NotificationBell />);
    await screen.findByRole("button", { name: "1 notifications" });
    await openBell();
    const link = await screen.findByRole("link", { name: /Open the Workflows page/ });
    expect(link.getAttribute("href")).toBe("/workflows");
  });

  it("footer keeps the review board when reviews/approvals are pending too", async () => {
    mockDaemon({ runs: [WAITING_RUN], approvals: 1 });
    render(<NotificationBell />);
    await screen.findByRole("button", { name: "2 notifications" });
    await openBell();
    const link = await screen.findByRole("link", { name: /Open the review board/ });
    expect(link.getAttribute("href")).toBe("/kanban");
  });
});
