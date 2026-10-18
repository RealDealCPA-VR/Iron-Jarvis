/**
 * v1.350.0 integrate: the bell says the plan ask in words and offers only
 * Approve once and Deny (lane C2 review: "the bell shows the raw tool name").
 *
 * The row is the REAL daemon's listing row for a plan ask, read from the
 * contract fixture (`__tests__/fixtures/plan-contract-v1350.json`, generated
 * by tests/test_plan_contract_v1350.py), so a change to the listing's shape
 * fails here or there. Any other ask keeps its v1.329.0 sentence and its
 * "Allow for this run" (the control).
 */

import { readFileSync } from "node:fs";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";

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
vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: [], connected: true }) }));
vi.mock("@/lib/useDesktopNotifications", () => ({
  useDesktopNotifications: () => ({
    supported: true,
    permission: "granted" as const,
    requestPermission: async () => "granted" as const,
    notify: vi.fn(),
  }),
}));

import { NotificationBell } from "@/components/NotificationBell";

const ROW = (
  JSON.parse(
    readFileSync(path.join(__dirname, "fixtures", "plan-contract-v1350.json"), "utf8").replace(/\r\n/g, "\n"),
  ) as { allow: { pending_row: Record<string, unknown> } }
).allow.pending_row;

function mockPending(approvals: unknown[]) {
  getMock.mockImplementation(async (p: unknown) => {
    if (p === "/chat/approvals/pending") return { approvals };
    if (p === "/computeruse") return { pending_approvals: 0 };
    if (p === "/diagnostics") return { pending_reviews: 0 };
    if (typeof p === "string" && p.startsWith("/workflows/runs")) return { runs: [] };
    return {};
  });
}

async function openRow(): Promise<HTMLElement> {
  fireEvent.click(await screen.findByRole("button", { name: /notification/i }));
  return screen.findByTestId("bell-agent-approval");
}
const sentence = (row: HTMLElement) => (row.querySelector("p")!.textContent ?? "").replace(/\s+/g, " ").trim();

beforeEach(() => getMock.mockReset());
afterEach(() => cleanup());

describe("the bell's plan ask", () => {
  it("reads 'It wants you to approve its plan' and offers Approve once and Deny only", async () => {
    expect(ROW.tool).toBe("approve_plan");
    expect(ROW.once_only).toBe(true);
    mockPending([ROW]);
    render(<NotificationBell />);
    const row = await openRow();
    expect(sentence(row)).toBe("It wants you to approve its plan. The run is waiting for you.");
    expect(within(row).getByRole("button", { name: "Approve once" })).toBeTruthy();
    expect(within(row).getByRole("button", { name: "Deny" })).toBeTruthy();
    expect(within(row).queryByRole("button", { name: "Allow for this run" })).toBeNull();
  });

  it("a plan ask is known by its name too (a row without the flag)", async () => {
    const { once_only: _flag, ...bare } = ROW;
    mockPending([bare]);
    render(<NotificationBell />);
    const row = await openRow();
    expect(sentence(row)).toBe("It wants you to approve its plan. The run is waiting for you.");
    expect(within(row).queryByRole("button", { name: "Allow for this run" })).toBeNull();
  });

  it("the daemon's once_only flag alone takes the run-wide answer away", async () => {
    mockPending([{ id: "apr_9", tool: "web_fetch", session_id: "s1", requested_at: "2026-10-17T12:00:00", once_only: true }]);
    render(<NotificationBell />);
    const row = await openRow();
    expect(sentence(row)).toBe("It wants to run web_fetch. The run is waiting for you.");
    expect(within(row).queryByRole("button", { name: "Allow for this run" })).toBeNull();
  });

  it("control: any other ask is exactly as before", async () => {
    mockPending([{ id: "apr_1", tool: "edit_file", session_id: "s1", requested_at: "2026-10-10T10:00:00" }]);
    render(<NotificationBell />);
    const row = await openRow();
    expect(sentence(row)).toBe("It wants to run edit_file. The run is waiting for you.");
    expect(within(row).getByRole("button", { name: "Allow for this run" })).toBeTruthy();
  });
});
