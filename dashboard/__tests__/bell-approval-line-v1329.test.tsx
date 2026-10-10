/**
 * v1.329.0, calm chat wave 8 (J5 review round): the bell's permission row is
 * two plain sentences.
 *
 * It used to render "It wants to run edit_file — the run is waiting for you."
 * (a lone dash between two {" "} pieces, which the first cut of the copy guard
 * read as a placeholder). Now: "It wants to run edit_file. The run is waiting
 * for you." and, with a clock, "... The run waits up to 5 min for you.". The
 * batch count keeps its own element (bell-approval-count).
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

const { getMock } = vi.hoisted(() => ({ getMock: vi.fn() }));

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
    post: vi.fn(async () => ({})),
    put: vi.fn(async () => ({})),
    patch: vi.fn(async () => ({})),
    del: vi.fn(async () => ({})),
    API_BASE: "",
    ijToken: () => "",
  };
});

vi.mock("@/lib/useEvents", () => ({
  useEvents: () => ({ events: [], connected: true }),
}));

vi.mock("@/lib/useDesktopNotifications", () => ({
  useDesktopNotifications: () => ({
    supported: true,
    permission: "granted" as const,
    requestPermission: async () => "granted" as const,
    notify: vi.fn(),
  }),
}));

import { NotificationBell } from "@/components/NotificationBell";

const PENDING_PATH = "/chat/approvals/pending";

function mockPending(approvals: unknown[]) {
  getMock.mockImplementation(async (path: unknown) => {
    if (path === PENDING_PATH) return { approvals };
    if (path === "/computeruse") return { pending_approvals: 0 };
    if (path === "/diagnostics") return { pending_reviews: 0 };
    if (typeof path === "string" && path.startsWith("/workflows/runs")) return { runs: [] };
    return {};
  });
}

/** The row's sentence, with whitespace runs folded the way a browser shows it. */
async function askLine(): Promise<string> {
  fireEvent.click(await screen.findByRole("button", { name: /notification/i }));
  const row = await screen.findByTestId("bell-agent-approval");
  const p = row.querySelector("p")!;
  return (p.textContent ?? "").replace(/\s+/g, " ").trim();
}

const ASK = { id: "apr_1", tool: "edit_file", session_id: "s1", requested_at: "2026-10-10T10:00:00" };

beforeEach(() => {
  getMock.mockReset();
});
afterEach(() => cleanup());

describe("the bell's permission row reads as two sentences", () => {
  it("no clock: 'It wants to run edit_file. The run is waiting for you.'", async () => {
    mockPending([ASK]);
    render(<NotificationBell />);
    const line = await askLine();
    expect(line).toBe("It wants to run edit_file. The run is waiting for you.");
    expect(line).not.toMatch(/(^|\s)[—–](\s|$)/);
  });

  it("a clock and a batch: the count stays its own element, then the wait", async () => {
    mockPending([{ ...ASK, count: 3, timeout_s: 300 }]);
    render(<NotificationBell />);
    const line = await askLine();
    expect(line).toBe("It wants to run edit_file × 3 (one answer covers all). The run waits up to 5 min for you.");
    expect(screen.getByTestId("bell-approval-count").textContent).toBe(" × 3 (one answer covers all)");
  });
});
