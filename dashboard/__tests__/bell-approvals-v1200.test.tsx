/**
 * v1.200.0 — paused agent asks join the notification bell.
 *
 * The mechanism under test: NotificationBell polls GET /chat/approvals/pending
 * (same 15s polled-source pattern as /computeruse, /diagnostics and the
 * waiting-runs list), counts each pending mid-turn approval toward the badge
 * (a job-origin agent run is genuinely PAUSED on it, and the pause degrades
 * into a silent deny on timeout), and renders each one as a distinct
 * "An agent is asking permission" row with the tool name, a session link, and
 * Approve once / Deny buttons that POST the existing /chat/approvals/{id} —
 * the exact route the chat card posts. Zero approvals → no section at all.
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

const PENDING_PATH = "/chat/approvals/pending";
const RUNS_PATH = "/workflows/runs?status=waiting&slim=true&limit=200";

// The daemon's response shape (routes/chat.py::pending_chat_approvals):
// id + tool + session_id + requested_at — NEVER args (the registry's own
// no-secrets posture; pinned server-side in test_approvals_pending_v1200.py).
const PENDING_ASK = {
  id: "apr_deadbeef01234567",
  tool: "shell",
  session_id: "session_job42",
  requested_at: "2026-08-22T10:00:00",
};

/** Route the mocked `get` like the daemon's bell sources. */
function mockDaemon({
  approvalsPending = [] as unknown[],
  cuApprovals = 0,
  reviews = 0,
  runs = [] as unknown[],
} = {}) {
  getMock.mockImplementation(async (path: unknown) => {
    if (path === "/computeruse") return { pending_approvals: cuApprovals };
    if (path === "/diagnostics") return { pending_reviews: reviews };
    if (path === RUNS_PATH) return { runs };
    if (path === PENDING_PATH) return { approvals: approvalsPending };
    return {};
  });
}

async function openBell() {
  const trigger = await screen.findByRole("button", { name: /notification/i });
  fireEvent.click(trigger);
  return trigger;
}

const pendingCalls = () =>
  getMock.mock.calls.filter((c: unknown[]) => c[0] === PENDING_PATH).length;

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

// ---- render + badge ----------------------------------------------------------

describe("pending agent approvals in the bell", () => {
  it("renders the section with the tool name and a session link", async () => {
    mockDaemon({ approvalsPending: [PENDING_ASK] });
    render(<NotificationBell />);
    await screen.findByRole("button", { name: "1 notifications" });
    await openBell();

    const row = await screen.findByTestId("bell-agent-approval");
    expect(row.textContent).toContain("An agent is asking permission");
    expect(row.textContent).toContain("shell");
    // Session link points at the run the ask came from.
    const link = row.querySelector('a[href="/sessions/session_job42"]');
    expect(link).not.toBeNull();
  });

  it("counts pending asks toward the badge and the tab title", async () => {
    mockDaemon({
      approvalsPending: [PENDING_ASK],
      cuApprovals: 1,
      reviews: 1,
    });
    render(<NotificationBell />);
    // 1 review + 1 computer-use approval + 1 agent ask = 3.
    await screen.findByRole("button", { name: "3 notifications" });
    await waitFor(() => expect(document.title).toBe("(3) Iron Jarvis"));
    expect(getMock).toHaveBeenCalledWith(PENDING_PATH);
  });

  it("mentions the asking agent in the desktop ping", async () => {
    mockDaemon({ approvalsPending: [PENDING_ASK] });
    render(<NotificationBell />);
    await waitFor(() => expect(notifyMock).toHaveBeenCalled());
    const [title, body] = notifyMock.mock.calls[0] as [string, string];
    expect(title).toBe("Iron Jarvis — 1 pending");
    expect(body).toContain("1 agent asking permission");
  });
});

// ---- answering ----------------------------------------------------------------

describe("answering an agent ask", () => {
  it("Approve once POSTs {decision:'once'} to /chat/approvals/{id} and the row leaves", async () => {
    mockDaemon({ approvalsPending: [PENDING_ASK] });
    postMock.mockResolvedValue({ ok: true, decision: "once" });
    render(<NotificationBell />);
    await screen.findByRole("button", { name: "1 notifications" });
    await openBell();
    await screen.findByTestId("bell-agent-approval");

    const before = pendingCalls();
    fireEvent.click(screen.getByRole("button", { name: "Approve once" }));

    await waitFor(() =>
      expect(postMock).toHaveBeenCalledWith(
        `/chat/approvals/${PENDING_ASK.id}`,
        { decision: "once" },
      ),
    );
    expect(postMock).toHaveBeenCalledTimes(1);

    // Optimistic removal: the row leaves immediately even though the mocked
    // daemon still lists the ask — and the list is refetched.
    await waitFor(() =>
      expect(screen.queryByTestId("bell-agent-approval")).toBeNull(),
    );
    await waitFor(() => expect(pendingCalls()).toBeGreaterThan(before));
    // Nothing else pends, so the badge and tab title clear to the base state.
    await waitFor(() => expect(document.title).toBe("Iron Jarvis"));
  });

  it("Deny POSTs {decision:'deny'} to the same route", async () => {
    mockDaemon({ approvalsPending: [PENDING_ASK] });
    postMock.mockResolvedValue({ ok: true, decision: "deny" });
    render(<NotificationBell />);
    await screen.findByRole("button", { name: "1 notifications" });
    await openBell();
    await screen.findByTestId("bell-agent-approval");

    fireEvent.click(screen.getByRole("button", { name: "Deny" }));
    await waitFor(() =>
      expect(postMock).toHaveBeenCalledWith(
        `/chat/approvals/${PENDING_ASK.id}`,
        { decision: "deny" },
      ),
    );
    await waitFor(() =>
      expect(screen.queryByTestId("bell-agent-approval")).toBeNull(),
    );
  });

  it("a 404 (answered elsewhere / expired) removes the row without a retry", async () => {
    mockDaemon({ approvalsPending: [PENDING_ASK] });
    postMock.mockRejectedValue(new ApiError("no such pending approval", 404));
    render(<NotificationBell />);
    await screen.findByRole("button", { name: "1 notifications" });
    await openBell();
    await screen.findByTestId("bell-agent-approval");

    fireEvent.click(screen.getByRole("button", { name: "Approve once" }));
    await waitFor(() =>
      expect(screen.queryByTestId("bell-agent-approval")).toBeNull(),
    );
    expect(postMock).toHaveBeenCalledTimes(1); // never retried
  });

  it("a non-404 failure keeps the row answerable with the error inline", async () => {
    mockDaemon({ approvalsPending: [PENDING_ASK] });
    postMock.mockRejectedValue(new ApiError("daemon offline", 0));
    render(<NotificationBell />);
    await screen.findByRole("button", { name: "1 notifications" });
    await openBell();
    await screen.findByTestId("bell-agent-approval");

    fireEvent.click(screen.getByRole("button", { name: "Approve once" }));
    await screen.findByText("daemon offline");
    expect(screen.getByTestId("bell-agent-approval")).toBeInTheDocument();
    // Not stuck in "Approving…": the user can fix connectivity and retry.
    expect(screen.getByRole("button", { name: "Approve once" })).not.toBeDisabled();
    expect(screen.getByRole("button", { name: "Deny" })).not.toBeDisabled();
  });
});

// ---- honest empty + live refresh ----------------------------------------------

describe("empty and live behavior", () => {
  it("zero approvals → no section, no badge", async () => {
    mockDaemon({ approvalsPending: [] });
    render(<NotificationBell />);
    await openBell();
    await screen.findByText(/all caught up/i);
    expect(screen.queryByTestId("bell-agent-approval")).toBeNull();
    expect(screen.queryByText("An agent is asking permission")).toBeNull();
  });

  it("a live approval.requested event triggers ONE immediate refetch", async () => {
    mockDaemon({ approvalsPending: [] });
    eventsRef.current = [
      {
        id: "ev-apr-1",
        ts: "2026-08-22T10:00:01",
        type: "approval.requested",
        payload: { approval_id: "apr_x", tool: "shell" },
        session_id: "session_job42",
      },
    ];
    render(<NotificationBell />);
    // initial poll fetch + the event-triggered reload = exactly 2.
    await waitFor(() => expect(pendingCalls()).toBe(2));
    await new Promise((r) => setTimeout(r, 50));
    expect(pendingCalls()).toBe(2); // dedupe by event id — no refetch storm
  });

  it("footer points at the Agents page when ONLY agent asks are pending", async () => {
    mockDaemon({ approvalsPending: [PENDING_ASK] });
    render(<NotificationBell />);
    await screen.findByRole("button", { name: "1 notifications" });
    await openBell();
    const link = await screen.findByRole("link", { name: /Open the Agents page/ });
    expect(link.getAttribute("href")).toBe("/agents");
  });
});
