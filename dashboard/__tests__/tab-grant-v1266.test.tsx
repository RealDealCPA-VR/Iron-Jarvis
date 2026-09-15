/**
 * v1.266.0 — the chat page's approval card offers "Allow for this tab" for a
 * browser action, and the stream decoder keeps the `tab` decision.
 *
 *  - A browser action shows the button; it POSTs exactly `{decision: "tab"}`
 *    and disables the card like every other decision.
 *  - A tool with no tab (shell) does not show it: a button that would grant a
 *    tab to a shell command is a promise about nothing.
 *  - The card says what the grant reaches and what it never covers.
 *  - `sseEventFrom` decodes `approval_resolved {decision: "tab"}` as "tab",
 *    not as the "timeout" every unknown word becomes.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const apiState = {
  posts: [] as { path: string; body: unknown }[],
};

vi.mock("@/lib/api", () => ({
  ApiError: class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 500) {
      super(message);
      this.status = status;
    }
  },
  post: async (path: string, body: unknown) => {
    apiState.posts.push({ path, body });
    return { ok: true };
  },
}));

import { ApprovalCard } from "@/components/chat/ApprovalCard";
import { sseEventFrom, type SSEEvent } from "@/lib/useChatStream";

const BROWSER = { id: "apr_b", callId: "c1", tool: "browser_click", args: { element_id: 7 } };
const SHELL = { id: "apr_s", callId: "c2", tool: "shell", args: { command: "git status" } };

beforeEach(() => {
  apiState.posts = [];
});
afterEach(() => cleanup());

describe("Allow for this tab on the chat page (v1.266.0)", () => {
  it("a browser action offers it and POSTs the tab decision once", async () => {
    render(<ApprovalCard approval={BROWSER} />);
    const button = screen.getByTestId("approval-tab");
    expect(button.textContent).toContain("Allow for this tab");
    fireEvent.click(button);
    fireEvent.click(button);
    await waitFor(() => expect(apiState.posts).toHaveLength(1));
    expect(apiState.posts[0].path).toBe("/chat/approvals/apr_b");
    expect(apiState.posts[0].body).toEqual({ decision: "tab" });
    // Every button is disabled once a decision is in flight.
    expect(screen.getByRole("button", { name: /allow once/i })).toHaveProperty("disabled", true);
  });

  it("a tool with no tab does not offer it", () => {
    render(<ApprovalCard approval={SHELL} />);
    expect(screen.queryByTestId("approval-tab")).toBeNull();
    expect(screen.queryByTestId("approval-tab-note")).toBeNull();
  });

  it("says what the grant reaches and what still asks", () => {
    render(<ApprovalCard approval={BROWSER} />);
    const note = screen.getByTestId("approval-tab-note").textContent ?? "";
    expect(note).toContain("until you close it");
    expect(note).toMatch(/still ask/);
    expect(note.toLowerCase()).not.toContain("extension");
  });

  it("the stream decoder keeps the tab decision", () => {
    const res = sseEventFrom("approval_resolved", {
      id: "apr_b",
      call_id: "c1",
      tool: "browser_click",
      decision: "tab",
    }) as Extract<SSEEvent, { type: "approval_resolved" }>;
    expect(res.type).toBe("approval_resolved");
    expect(res.decision).toBe("tab");
  });
});
