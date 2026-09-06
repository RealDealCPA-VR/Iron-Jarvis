/**
 * v1.232.0 Wave 6, task 6D (audit A9) — the bell can answer a batch.
 *
 * Converted from __audit_20260904__/bell-and-result-card.audit.test.tsx D3,
 * RED at v1.226.0: an agent ask in the bell offered Approve once / Deny only,
 * so a 28-file rename answered from the bell was 28 clicks at up to 15 s
 * poll lag each — while the chat card had "Allow for this conversation".
 *
 * The row now carries "Allow for this run", which POSTs decision
 * "conversation" to the SAME /chat/approvals/{id} route the card posts. The
 * daemon (v1.227.0, A2) releases every sibling ask of the same tool on that
 * answer and (v1.232.0, A6) keeps the grant for the run's continues — so
 * one click clears the batch. Pinned here: the button exists, it posts
 * exactly that decision, the row leaves on success, and Approve once still
 * posts "once" (the new answer did not replace the old one).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const { getMock, postMock, notifyMock } = vi.hoisted(() => ({
  getMock: vi.fn(),
  postMock: vi.fn(),
  notifyMock: vi.fn(),
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
  useEvents: () => ({ events: [], connected: true }),
}));
vi.mock("@/lib/useDesktopNotifications", () => ({
  useDesktopNotifications: () => ({
    supported: true,
    permission: "granted" as const,
    requestPermission: async () => "granted" as const,
    notify: notifyMock,
  }),
}));

import { NotificationBell } from "@/components/NotificationBell";

beforeEach(() => {
  getMock.mockReset();
  postMock.mockReset();
  getMock.mockImplementation(async (path: unknown) => {
    if (path === "/computeruse") return { pending_approvals: 0 };
    if (path === "/diagnostics") return { pending_reviews: 0 };
    if (path === "/chat/approvals/pending")
      return {
        approvals: [
          { id: "apr_1", tool: "rename_file", session_id: "session_7e5621fb449b", requested_at: "2026-08-23T01:20:06" },
          { id: "apr_2", tool: "rename_file", session_id: "session_7e5621fb449b", requested_at: "2026-08-23T01:20:06" },
        ],
      };
    return { runs: [] };
  });
  postMock.mockResolvedValue({ ok: true });
});
afterEach(() => cleanup());

async function openBell() {
  render(<NotificationBell />);
  const btn = await screen.findByRole("button", { name: /2 notifications/ });
  fireEvent.click(btn);
  const rows = await screen.findAllByTestId("bell-agent-approval");
  expect(rows.length).toBe(2);
  return rows;
}

describe("A9 — the bell offers a grant wider than one call", () => {
  it("every agent ask row carries 'Allow for this run'", async () => {
    await openBell();
    const wide = screen.getAllByRole("button", { name: /allow for this run/i });
    expect(wide.length).toBe(2);
    // The batch promise is spelled out where the user hovers.
    expect(wide[0].getAttribute("title")).toMatch(/every pending ask for it clears at once/i);
  });

  it("'Allow for this run' posts decision 'conversation' to the card's route and the row leaves", async () => {
    await openBell();
    fireEvent.click(screen.getAllByRole("button", { name: /allow for this run/i })[0]);
    await waitFor(() => {
      expect(postMock).toHaveBeenCalledWith("/chat/approvals/apr_1", {
        decision: "conversation",
      });
    });
    // The answered row unmounts (the sibling stays until the poll refreshes
    // — the daemon released it, the bell does not guess).
    await waitFor(() => {
      expect(screen.getAllByTestId("bell-agent-approval").length).toBe(1);
    });
  });

  it("Approve once still posts 'once' — the new answer did not replace it", async () => {
    await openBell();
    fireEvent.click(screen.getAllByRole("button", { name: /^approve once$/i })[1]);
    await waitFor(() => {
      expect(postMock).toHaveBeenCalledWith("/chat/approvals/apr_2", { decision: "once" });
    });
    expect(postMock).not.toHaveBeenCalledWith("/chat/approvals/apr_2", {
      decision: "conversation",
    });
  });
});
