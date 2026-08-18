/**
 * v1.188.0 — the approval-posture dropdown (source-pinned).
 *
 * The chat page is not rendered in any test (it is the app's largest
 * component and every suite that touches it extracts components instead), so
 * this follows the established idiom for page-level seams: pin the SOURCE
 * (the draftFromFence / v1.163.0 lesson — a seam whose only guard is a
 * rendered test goes green the day the call site is deleted).
 *
 * The daemon's tests already prove the wire behaviour of all three modes;
 * what the frontend can silently lose is (a) the control itself, (b) the
 * vocabulary drifting from the daemon's, (c) the body no longer carrying the
 * pick, (d) the pick no longer persisting with the thread or restoring from
 * it. Each is pinned here.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const page = readFileSync(join(process.cwd(), "app", "chat", "page.tsx"), "utf8");

describe("the posture vocabulary", () => {
  it("matches the daemon's, value for value", () => {
    // Lock-step with chat_turn.APPROVAL_MODES — a renamed value here would
    // send a string the daemon coerces to the default, and the dropdown
    // would silently stop doing anything.
    for (const value of ["always_ask", "approve_for_me", "yolo"]) {
      expect(page).toContain(`value: "${value}"`);
    }
    // The labels the user asked for, verbatim intent.
    expect(page).toContain("Ask for approval");
    expect(page).toContain("Approve for me");
    expect(page).toContain("Auto-approve");
    // Unknown → the DEFAULT, never yolo (mirrors normalize_approval_mode).
    expect(page).toMatch(/asApprovalMode[\s\S]{0,200}"approve_for_me"/);
  });
});

describe("the control and its wiring", () => {
  it("renders as a labelled select in the composer", () => {
    expect(page).toContain('aria-label="Approval mode"');
    // The window spans the select's onChange handler (persist + snapshot),
    // which sits between the tag and its label in source order.
    expect(page).toMatch(/<select[\s\S]{0,1500}aria-label="Approval mode"/);
  });

  it("rides the chat body — only the non-default", () => {
    // A pre-v1.188.0 daemon must keep seeing a body it already understands.
    expect(page).toMatch(
      /approvalMode !== "approve_for_me"[\s\S]{0,120}approval_mode: approvalMode/,
    );
  });

  it("persists with the thread setup and restores from it", () => {
    expect(page).toContain("approval_mode: approvalMode");
    expect(page).toMatch(/setApprovalMode\(asApprovalMode\(setup\.approval_mode\)\)/);
  });

  it("remembers the user's default and returns to it on New chat", () => {
    expect(page).toContain('const APPROVAL_MODE_KEY = "ij_chat_approval_mode"');
    expect(page).toMatch(
      /localStorage\.setItem\(APPROVAL_MODE_KEY, mode\)/,
    );
    // The new-chat reset re-reads the DEFAULT rather than carrying the
    // previous thread's posture — a YOLO grant is per-conversation consent.
    const reset = page.indexOf("setSelectedTools([]); // armed tools are per-conversation");
    expect(reset).toBeGreaterThan(-1);
    expect(page.slice(reset, reset + 700)).toContain(
      "localStorage.getItem(APPROVAL_MODE_KEY)",
    );
  });

  it("marks YOLO visibly as the dangerous position", () => {
    expect(page).toMatch(/approvalMode === "yolo"[\s\S]{0,80}text-amber-300/);
  });
});
