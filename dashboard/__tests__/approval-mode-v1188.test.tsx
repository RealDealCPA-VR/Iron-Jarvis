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
// v1.327.0: the words and values moved to ONE table the composer's permission
// chip reads; the page imports it instead of keeping its own copy.
const levels = readFileSync(join(process.cwd(), "lib", "permissionLevels.ts"), "utf8");

describe("the posture vocabulary", () => {
  it("matches the daemon's, value for value", () => {
    // Lock-step with chat_turn.APPROVAL_MODES: a renamed value here would
    // send a string the daemon coerces to the default, and the control
    // would silently stop doing anything.
    expect(levels).toContain(
      'export const PERMISSION_MODES: readonly PermissionMode[] = ["always_ask", "approve_for_me", "yolo"];',
    );
    // The page reads that table, never a second copy.
    expect(page).toContain('from "@/lib/permissionLevels"');
    expect(page).not.toContain("const APPROVAL_MODES = [");
    // Plain names for each level (v1.327.0 replaced the v1.188.0 labels).
    expect(levels).toContain('label: "Ask first"');
    expect(levels).toContain('label: "Ask when risky"');
    expect(levels).toContain(`label: "Don't ask"`);
    // Unknown → the DEFAULT, never yolo (mirrors normalize_approval_mode).
    expect(levels).toContain('export const DEFAULT_PERMISSION_MODE: PermissionMode = "approve_for_me";');
    expect(page).toContain("const asApprovalMode: (raw: unknown) => ApprovalMode = asPermissionMode;");
  });
});

describe("the control and its wiring", () => {
  it("renders as the permission chip in the composer, keeping the old id", () => {
    // v1.327.0: the chip replaced the select. The window spans the chip's
    // onChange handler (persist + snapshot).
    expect(page).toMatch(
      /<PermissionChip\s+id="chat-approval-mode"[\s\S]{0,120}value=\{approvalMode\}[\s\S]{0,700}markSetupChanged\(\);/,
    );
    expect(page).not.toContain('aria-label="Approval mode"');
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
    // v1.327.0: the chip reads the level's tone; yolo is the one warn level
    // and warn is the amber tone token (rendered: ux-wave2-chat-words-v1314).
    expect(levels).toMatch(/yolo: \{[\s\S]{0,200}tone: "warn"/);
    const chip = readFileSync(join(process.cwd(), "components", "chat", "PermissionChip.tsx"), "utf8");
    expect(chip).toMatch(/warn\s*\?\s*"text-tone-warn/);
  });
});
