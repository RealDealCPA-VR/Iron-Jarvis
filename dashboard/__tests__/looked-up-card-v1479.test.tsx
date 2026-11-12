/**
 * v1.479.0 (capability search, Phase 2): a tool Jarvis brought into the reply
 * by LOOKING IT UP (src/iron_jarvis/capability_search/join.py) asks first
 * whatever the approval setting is. The user's words did not name the tool,
 * so its card is a safety card: Allow once and Deny only, its own reason
 * (never the local-only line), and Enter never allows it: only a click does.
 */
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", async (orig) => {
  const real = await orig<typeof import("@/lib/api")>();
  return { ...real, post: vi.fn(async () => ({})) };
});

import { ApprovalCard } from "@/components/chat/ApprovalCard";
import { answersOnlyOnce } from "@/lib/approvalWords";
import { enterAllows } from "@/lib/dockAsk";
import { onceReasonOf } from "@/lib/onceReason";
import type { PendingApproval } from "@/lib/useChatStream";

afterEach(() => cleanup());

const ASK = {
  callId: "c1",
  approvalId: "a1",
  tool: "write_file",
  args: { path: "note.txt", content: "hello" },
  onceOnly: true,
  onceReason: "looked_up",
} as unknown as PendingApproval;

describe("a card for a tool Jarvis looked up", () => {
  it("knows the reason", () => {
    expect(onceReasonOf("looked_up")).toBe("looked_up");
  });

  it("says why it asks every time, and is not the local-only line", () => {
    render(<ApprovalCard approval={ASK} scope="chat" />);
    const line = screen.getByTestId("approval-looked-up-once").textContent ?? "";
    expect(line).toMatch(/found this tool by looking it up/);
    expect(line).toMatch(/whatever your approval setting/);
    expect(screen.queryByTestId("approval-local-only")).toBeNull();
  });

  it("offers Allow once and Deny only", () => {
    expect(answersOnlyOnce(ASK)).toBe(true);
    render(<ApprovalCard approval={ASK} scope="chat" />);
    const names = screen.getAllByRole("button").map((b) => b.textContent ?? "");
    expect(names.some((n) => /Allow once/.test(n))).toBe(true);
    expect(names.some((n) => /for this chat|Always/i.test(n))).toBe(false);
  });

  it("is never allowed by Enter", () => {
    expect(enterAllows({ kind: "approval", id: "a1", approval: ASK })).toBe(false);
    // The control: the same card with no safety reason is.
    const plain = { ...ASK, onceOnly: false, onceReason: undefined } as unknown as PendingApproval;
    expect(enterAllows({ kind: "approval", id: "a2", approval: plain })).toBe(true);
  });
});
