/**
 * v1.406.0 (Brain Profiles): a structural change to a note in the memory
 * base (`brain_change` — archive, delete, rename, move) asks EVERY time with
 * Allow once only. Its card names that reason (never the local-only line),
 * and Enter never allows it: only a click does.
 */
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", async (orig) => {
  const real = await orig<typeof import("@/lib/api")>();
  return { ...real, post: vi.fn(async () => ({})) };
});

import { ApprovalCard } from "@/components/chat/ApprovalCard";
import { enterAllows } from "@/lib/dockAsk";
import { onceReasonOf } from "@/lib/onceReason";
import type { PendingApproval } from "@/lib/useChatStream";

afterEach(() => cleanup());

const ASK = {
  callId: "c1",
  approvalId: "a1",
  tool: "brain_change",
  args: { action: "delete", ref: "old idea" },
  onceOnly: true,
  onceReason: "memory",
} as unknown as PendingApproval;

describe("a memory change card", () => {
  it("knows the memory reason", () => {
    expect(onceReasonOf("memory")).toBe("memory");
  });

  it("says why it asks every time, and is not the local-only line", () => {
    render(<ApprovalCard approval={ASK} scope="chat" />);
    expect(screen.getByTestId("approval-memory-once").textContent).toMatch(/asks you every time/);
    expect(screen.queryByTestId("approval-local-only")).toBeNull();
  });

  it("is never allowed by Enter", () => {
    expect(enterAllows({ kind: "approval", id: "a1", approval: ASK })).toBe(false);
  });
});
