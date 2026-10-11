/**
 * v1.333.0 (Lane G): the busy-runs ask in the bell.
 *
 * The user's decision: no cap by default, but when an unusual number of
 * background runs are going at once, say so and ASK whether to start a
 * queue. The daemon sends ONE `runs.busy` {running, threshold, cap} per
 * episode; the bell shows it as a row that waits on the user (it counts),
 * with "Queue extras" (PUT /settings/values max_concurrent_sessions = cap,
 * the one settings writer, Undo in the ledger) and "Not now" (POST
 * /runs/busy/not-now: the daemon stays quiet a day). The row must look
 * exactly like the bell's existing rows: no new box, border or colour.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const { getMock, postMock, putMock, notifyMock, eventsRef } = vi.hoisted(() => ({
  getMock: vi.fn(),
  postMock: vi.fn(),
  putMock: vi.fn(),
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
    put: putMock,
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
import { BELL_EVENT_TYPES, NotificationBell, parseBusyRuns } from "@/components/NotificationBell";

const BUSY = {
  id: "evt-busy-1",
  type: "runs.busy",
  session_id: null,
  ts: "2026-10-11T03:00:00",
  payload: { running: 5, threshold: 4, cap: 3 },
};

function src(rel: string): string {
  return readFileSync(join(process.cwd(), rel), "utf8").replace(/\r\n/g, "\n");
}

async function openBell() {
  fireEvent.click(await screen.findByRole("button", { name: /notification/i }));
}

beforeEach(() => {
  getMock.mockReset();
  getMock.mockResolvedValue({});
  postMock.mockReset();
  postMock.mockResolvedValue({ ok: true });
  putMock.mockReset();
  putMock.mockResolvedValue({ changed: [] });
  notifyMock.mockReset();
  eventsRef.current = [];
  document.title = "Iron Jarvis";
});

afterEach(() => cleanup());

describe("the busy-runs ask", () => {
  it("is a type the bell subscribes to", () => {
    expect(BELL_EVENT_TYPES).toContain("runs.busy");
  });

  it("shows one plain line with the count and the cap it offers, and counts as waiting on you", async () => {
    const { rerender } = render(<NotificationBell />);
    await screen.findByRole("button", { name: "Notifications" });
    eventsRef.current = [BUSY]; // the event arrives on the live stream
    rerender(<NotificationBell />);
    await screen.findByRole("button", { name: "1 notifications" });
    await openBell();
    const row = await screen.findByTestId("bell-busy-runs");
    expect(row.textContent).toContain("5 background runs are going at once");
    expect(row.textContent).toContain("Queue the extras so only 3 run at a time?");
    expect(screen.getByRole("button", { name: "Queue extras" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Not now" })).toBeTruthy();
    // The desktop gets the bell's usual one ping, naming what is pending.
    await waitFor(() => expect(notifyMock).toHaveBeenCalled());
    expect(notifyMock.mock.calls[0][1]).toContain("5 background runs going at once");
  });

  it("Queue extras saves the cap through the one settings writer, then the row goes", async () => {
    eventsRef.current = [BUSY];
    render(<NotificationBell />);
    await openBell();
    fireEvent.click(await screen.findByRole("button", { name: "Queue extras" }));
    await waitFor(() => expect(screen.queryByTestId("bell-busy-runs")).toBeNull());
    expect(putMock).toHaveBeenCalledTimes(1);
    expect(putMock).toHaveBeenCalledWith("/settings/values", {
      values: { max_concurrent_sessions: 3 },
    });
    expect(postMock).not.toHaveBeenCalled();
  });

  it("Not now tells the daemon to stay quiet, then the row goes", async () => {
    eventsRef.current = [BUSY];
    render(<NotificationBell />);
    await openBell();
    fireEvent.click(await screen.findByRole("button", { name: "Not now" }));
    await waitFor(() => expect(screen.queryByTestId("bell-busy-runs")).toBeNull());
    expect(postMock).toHaveBeenCalledWith("/runs/busy/not-now", {});
    expect(putMock).not.toHaveBeenCalled();
  });

  it("a failed save keeps the row and says why", async () => {
    eventsRef.current = [BUSY];
    putMock.mockRejectedValueOnce(new ApiError("Runs at once must be a number.", 400));
    render(<NotificationBell />);
    await openBell();
    fireEvent.click(await screen.findByRole("button", { name: "Queue extras" }));
    await screen.findByText("Runs at once must be a number.");
    expect(screen.getByTestId("bell-busy-runs")).toBeTruthy();
  });

  it("reads the newest event and falls back to a cap of 3", () => {
    const older = { ...BUSY, id: "old", payload: { running: 9, threshold: 4, cap: 2 } };
    const newer = { ...BUSY, id: "new", payload: { running: 6, threshold: 4 } };
    expect(parseBusyRuns([newer, older] as never)).toMatchObject({ id: "new", running: 6, cap: 3 });
    expect(parseBusyRuns([] as never)).toBeNull();
  });

  it("looks exactly like the bell's existing rows: every class it uses is already used by another row", () => {
    const bell = src("components/NotificationBell.tsx");
    const start = bell.indexOf("function BusyRunsRow(");
    const end = bell.indexOf("\n}\n", start);
    expect(start).toBeGreaterThan(0);
    const row = bell.slice(start, end);
    const others =
      bell.slice(0, start) + bell.slice(end) + src("components/InterruptedJobs.tsx");
    const classes = [...row.matchAll(/className="([^"]+)"/g)].map((m) => m[1]);
    expect(classes.length).toBeGreaterThanOrEqual(7); // anti-vacuity
    expect(classes.filter((c) => !others.includes(`className="${c}"`))).toEqual([]);
  });
});
