/**
 * v1.187.0 — the mid-turn approval card (chat asks, then proceeds).
 *
 * The daemon pauses a turn on an ask-tier tool and emits an `approval` frame;
 * this card is the deciding surface. What must hold:
 *
 *  - the user SEES the payload before deciding — for shell that means the
 *    exact command, verbatim, rendered as a block (approving a call you
 *    cannot read is not a decision);
 *  - exactly one decision has a write path: one click POSTs, the card
 *    disables itself, and only a FAILED post re-enables it;
 *  - "Allow for this conversation" also hands the tool to `onConversation` —
 *    the persistence half of that button's promise (the page adds it to the
 *    composer's armed set); "once" and "deny" must NOT;
 *  - the stream hook holds the approval while paused, clears it on the
 *    matching resolution, and never lets a stale resolution eat a newer
 *    question.
 */

import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const apiState = {
  posts: [] as { path: string; body: unknown }[],
  failNext: false,
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
    if (apiState.failNext) {
      apiState.failNext = false;
      throw new Error("daemon unreachable");
    }
    return { ok: true };
  },
}));

import { ApprovalCard } from "@/components/chat/ApprovalCard";
import { sseEventFrom, type SSEEvent } from "@/lib/useChatStream";

const APPROVAL = {
  id: "apr_1",
  callId: "c1",
  tool: "shell",
  args: { command: "git status" },
};

beforeEach(() => {
  apiState.posts = [];
  apiState.failNext = false;
});
afterEach(() => cleanup());

describe("ApprovalCard", () => {
  it("shows the exact command before asking for a decision", () => {
    render(<ApprovalCard approval={APPROVAL} />);
    expect(screen.getByTestId("chat-approval-card")).toBeInTheDocument();
    expect(screen.getByText("shell")).toBeInTheDocument();
    // The payload, verbatim, in a block — not a summary of it.
    expect(screen.getByText("git status")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /allow once/i })).toBeEnabled();
    expect(
      screen.getByRole("button", { name: /allow for this conversation/i }),
    ).toBeEnabled();
    expect(screen.getByRole("button", { name: /deny/i })).toBeEnabled();
  });

  it("POSTs exactly one decision and disables itself", async () => {
    render(<ApprovalCard approval={APPROVAL} />);
    fireEvent.click(screen.getByRole("button", { name: /allow once/i }));
    // A second click while in flight must not produce a second decision.
    fireEvent.click(screen.getByRole("button", { name: /deny/i }));
    await waitFor(() => expect(apiState.posts.length).toBe(1));
    expect(apiState.posts[0].path).toBe("/chat/approvals/apr_1");
    expect(apiState.posts[0].body).toEqual({ decision: "once" });
  });

  it("hands the tool to onConversation ONLY on the conversation grant", async () => {
    const onConversation = vi.fn();
    const { unmount } = render(
      <ApprovalCard approval={APPROVAL} onConversation={onConversation} />,
    );
    fireEvent.click(
      screen.getByRole("button", { name: /allow for this conversation/i }),
    );
    await waitFor(() => expect(onConversation).toHaveBeenCalledWith("shell"));
    unmount();

    // "once" must not arm the tool for later turns — that is the entire
    // difference between the two allow buttons.
    render(<ApprovalCard approval={APPROVAL} onConversation={onConversation} />);
    fireEvent.click(screen.getByRole("button", { name: /allow once/i }));
    await waitFor(() => expect(apiState.posts.length).toBe(2));
    expect(onConversation).toHaveBeenCalledTimes(1);
  });

  it("a failed POST re-enables the buttons instead of stranding the pause", async () => {
    apiState.failNext = true;
    render(<ApprovalCard approval={APPROVAL} />);
    fireEvent.click(screen.getByRole("button", { name: /deny/i }));
    await waitFor(() =>
      expect(screen.getByText(/daemon unreachable/i)).toBeInTheDocument(),
    );
    expect(screen.getByRole("button", { name: /deny/i })).toBeEnabled();
  });
});

describe("the stream frames", () => {
  it("decodes approval and approval_resolved", () => {
    const ap = sseEventFrom("approval", {
      id: "apr_9",
      call_id: "c9",
      tool: "repl",
      args: { code: "print(1)" },
      timeout_s: 180,
    }) as Extract<SSEEvent, { type: "approval" }>;
    expect(ap.type).toBe("approval");
    expect(ap.tool).toBe("repl");
    expect(ap.args).toEqual({ code: "print(1)" });

    const res = sseEventFrom("approval_resolved", {
      id: "apr_9",
      call_id: "c9",
      tool: "repl",
      decision: "deny",
    }) as Extract<SSEEvent, { type: "approval_resolved" }>;
    expect(res.type).toBe("approval_resolved");
    expect(res.decision).toBe("deny");
    // An unknown decision string normalises to "timeout", never to a grant.
    const odd = sseEventFrom("approval_resolved", {
      id: "x",
      call_id: "c",
      tool: "shell",
      decision: "yes-please",
    }) as Extract<SSEEvent, { type: "approval_resolved" }>;
    expect(odd.decision).toBe("timeout");
  });
});
