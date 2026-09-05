/**
 * v1.189.0 — the chat page's half of the session fixes (source-pinned; the
 * daemon's tests drive the behaviour end-to-end, and the page-level idiom
 * here is pinning the call sites — the v1.163.0 lesson).
 *
 * Two seams, each of which failed silently in the measured run:
 *  - the escalation POST must carry `workspace_root` (the folder chat's own
 *    tools were using) — without it the session works in a scratch dir and
 *    every rename is refused as outside-workspace;
 *  - a paused run's `approval.requested` event must render the SAME
 *    ApprovalCard under the agent-mode bubble — without it the only visible
 *    artifact of a blocked run is a capability proposal on the Tools page.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const page = readFileSync(join(process.cwd(), "app", "chat", "page.tsx"), "utf8");

describe("the folder rides the escalation", () => {
  it("both escalation branches send workspace_root", () => {
    const hits = page.match(/workspace_root: workspaceDir/g) ?? [];
    // The POST /sessions branch AND the custom-agent spawn branch — one
    // carrying it and one not would make "which agent answered" decide
    // whether the job can reach its own files.
    expect(hits.length).toBe(2);
  });
});

describe("a paused run's ask reaches the chat", () => {
  it("watches approval events for the awaited session", () => {
    expect(page).toContain('e.type === "approval.requested"');
    expect(page).toContain('e.type === "approval.resolved"');
    // Scoped to the session the user is actually watching.
    expect(page).toMatch(/e\.session_id !== awaitingId/);
  });

  it("renders the same ApprovalCard in the agent-mode bubble — one per pending ask", () => {
    // v1.227.0 (wave 1, A1): the single `sessionApproval` slot became a fold
    // over the events — the runtime asks in parallel batches, and one slot
    // showed one card of N. Behaviour is driven end-to-end in
    // wave1-approvals.test.tsx; this pins the call site.
    expect(page).toMatch(/sessionAsks\.map\(\(ask\) => \(\s*<ApprovalCard/);
    // A resolution closes only ITS request — a stale one must not eat a newer
    // question (same rule as the stream hook's), and a request whose id has
    // already resolved never becomes a card.
    expect(page).toMatch(/resolved\.add\(String\(e\.payload\?\.approval_id/);
    expect(page).toMatch(/resolved\.has\(id\)/);
  });
});
