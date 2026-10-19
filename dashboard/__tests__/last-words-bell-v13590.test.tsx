/**
 * v1.359.0 (wave 9, lane 9B) — the bell names a run by its task, never by
 * its id.
 *
 * The re-audit (and wave 8's Known list) found the bell's agent-ask row
 * linking the run by its short run id as the link's visible words
 * ("session_7b32f7…"), with the task on the line above. Now the task IS the
 * link (its words: the task the row already carries), and the run's id
 * lives only in the title, the record. With no task the link says "Open
 * run", the words the answered row already uses. The workflow ask row and
 * the review row printed the id beside their time the same way: the id moved
 * into the time's title.
 *
 * The REAL NotificationBell, its data seams mocked (the bell-approvals-v1200
 * harness).
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const { getMock, postMock, eventsRef } = vi.hoisted(() => ({
  getMock: vi.fn(),
  postMock: vi.fn(),
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
    notify: vi.fn(),
  }),
}));

import { NotificationBell, runLinkTitle } from "@/components/NotificationBell";

const PENDING_PATH = "/chat/approvals/pending";
const RUNS_PATH = "/workflows/runs?status=waiting&slim=true&limit=200";

/** A run id long enough that the old short form ("session_7b32f7…") shows. */
const SID = "session_7b32f746bc3a";
const TASK = "Write the nightly brief";

function mockDaemon({ approvalsPending = [] as unknown[], runs = [] as unknown[] } = {}) {
  getMock.mockImplementation(async (path: unknown) => {
    if (path === "/computeruse") return { pending_approvals: 0 };
    if (path === "/diagnostics") return { pending_reviews: 0 };
    if (path === RUNS_PATH) return { runs };
    if (path === PENDING_PATH) return { approvals: approvalsPending };
    return {};
  });
}

async function openBell() {
  fireEvent.click(await screen.findByRole("button", { name: /notification/i }));
}

/** No piece of the run's id on screen: neither the whole id nor its cut. */
function expectNoIdIn(text: string | null, id: string) {
  expect(text ?? "").not.toContain(id);
  expect(text ?? "").not.toContain(id.slice(0, 12));
}

beforeEach(() => {
  getMock.mockReset();
  postMock.mockReset();
  eventsRef.current = [];
});
afterEach(() => cleanup());

describe("the agent-ask row names its run by its task", () => {
  it("the task is the link to the run; the id is only its title", async () => {
    mockDaemon({
      approvalsPending: [
        { id: "apr_1", tool: "write_file", session_id: SID, requested_at: "2026-10-19T10:00:00", task: TASK },
      ],
    });
    render(<NotificationBell />);
    await openBell();
    const row = await screen.findByTestId("bell-agent-approval");
    const link = within(row).getByTestId("bell-approval-run");
    expect(link.tagName).toBe("A");
    expect(link.textContent).toBe(TASK);
    expect(link.getAttribute("href")).toBe(`/sessions/${SID}`);
    expect(link.getAttribute("title")).toBe(runLinkTitle(TASK, SID));
    expect(link.getAttribute("title")).toContain(SID);
    // The line keeps its words ("for: <task>") and no id is visible anywhere.
    expect(within(row).getByTestId("bell-approval-task").textContent).toBe(`for: ${TASK}`);
    expectNoIdIn(row.textContent, SID);
    // One link to the run, not two.
    expect(row.querySelectorAll(`a[href="/sessions/${SID}"]`)).toHaveLength(1);
  });

  it("no task: the link says 'Open run', never the id", async () => {
    mockDaemon({
      approvalsPending: [{ id: "apr_2", tool: "shell", session_id: SID, requested_at: "2026-10-19T10:00:00" }],
    });
    render(<NotificationBell />);
    await openBell();
    const row = await screen.findByTestId("bell-agent-approval");
    const link = within(row).getByTestId("bell-approval-run");
    expect(link.textContent).toBe("Open run");
    expect(link.getAttribute("href")).toBe(`/sessions/${SID}`);
    expect(link.getAttribute("title")).toBe(`Run ${SID}`);
    expectNoIdIn(row.textContent, SID);
  });

  it("CONTROL: an ask with no run has no run link", async () => {
    mockDaemon({ approvalsPending: [{ id: "apr_3", tool: "shell", session_id: "", requested_at: "" }] });
    render(<NotificationBell />);
    await openBell();
    const row = await screen.findByTestId("bell-agent-approval");
    expect(within(row).queryByTestId("bell-approval-run")).toBeNull();
  });
});

describe("the workflow ask and the review rows keep the id off the screen", () => {
  it("a parked workflow names itself; its run id is the time's title", async () => {
    const RUN = "wfrun-9c1d2e3f4a5b";
    mockDaemon({
      runs: [
        {
          id: RUN,
          workflow_name: "monthly-close",
          status: "waiting",
          started_at: "2026-10-19T09:00:00",
          waiting_json: JSON.stringify({ index: 1, step: "ask", question: "Send it?" }),
        },
      ],
    });
    render(<NotificationBell />);
    await openBell();
    const row = await screen.findByTestId("bell-waiting-run");
    expect(row.textContent).toContain("Workflow “monthly-close” needs an answer");
    expectNoIdIn(row.textContent, RUN);
    expect(within(row).getByTestId("bell-waiting-run-when").getAttribute("title")).toContain(RUN);
  });

  it("a review row shows its time; the run's id is the title", async () => {
    eventsRef.current = [
      {
        id: "ev_review_1",
        type: "review.requested",
        ts: "2026-10-19T10:05:00",
        session_id: SID,
        payload: { summary: "Changes are ready for your review." },
      },
    ];
    mockDaemon();
    render(<NotificationBell />);
    await openBell();
    const when = await screen.findByTestId("bell-review-when");
    expect(when.getAttribute("title")).toBe(`Run ${SID}`);
    const row = when.closest("li") as HTMLElement;
    await waitFor(() => expect(row.textContent).toContain("Review requested"));
    expectNoIdIn(row.textContent, SID);
  });
});
