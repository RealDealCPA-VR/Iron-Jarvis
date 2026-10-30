/**
 * v1.407.2: an edit of Git's own files or of Iron Jarvis's / Claude Code's own
 * rules (`.git/**`, `.ironjarvis/**`, `.claude/**` …) asks under Ask first,
 * Allow once only (src/iron_jarvis/core/auto_approve.py `guarded_edit`). Its
 * card names that reason (never the local-only line), shows the daemon's own
 * "why" line (`cost_note`), and Enter never allows it: only a click does.
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

const WHY = "Write .git/config: it changes Git's own settings, which Git can run as programs";
const ASK = {
  callId: "c1",
  approvalId: "a1",
  tool: "write_file",
  args: { path: ".git/config", content: "[core]" },
  onceOnly: true,
  onceReason: "guarded",
  costNote: WHY,
  cost_note: WHY,
} as unknown as PendingApproval;

describe("a guarded-file edit card", () => {
  it("knows the guarded reason", () => {
    expect(onceReasonOf("guarded")).toBe("guarded");
  });

  it("says why it asks every time, and is not the local-only line", () => {
    render(<ApprovalCard approval={ASK} scope="chat" />);
    const line = screen.getByTestId("approval-guarded-once").textContent ?? "";
    expect(line).toMatch(/Git's own files/);
    expect(line).toMatch(/Iron Jarvis's or Claude Code's own rules/);
    expect(line).toMatch(/Don't ask/);
    expect(screen.queryByTestId("approval-local-only")).toBeNull();
  });

  it("is never allowed by Enter", () => {
    expect(enterAllows({ kind: "approval", id: "a1", approval: ASK })).toBe(false);
  });
});
