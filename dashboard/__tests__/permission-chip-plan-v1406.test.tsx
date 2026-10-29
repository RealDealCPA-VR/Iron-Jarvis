/**
 * v1.406.0 part 2 — ONE switch, Claude Code's shape: the composer's permission
 * chip offers, in plain words and in this order, Ask before every action /
 * Ask first / Accept edits / Auto / (a coding session: Plan) / Don't ask
 * (last, amber). The wire values are the daemon's; the old values keep their
 * meaning, only their NAMES moved ("Ask first" now names the default).
 *
 * Plan is the coding session's `mode`, not a level: picking it keeps the
 * level (onPlanChange(true), no onChange); picking a level while planning
 * turns plan mode off first. A chat (no onPlanChange) never offers Plan.
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { PermissionChip, permissionMenuKeys } from "@/components/chat/PermissionChip";
import { PERMISSION_LEVELS, PLAN_LEVEL, asPermissionMode } from "@/lib/permissionLevels";

afterEach(() => cleanup());

const read = (...parts: string[]) =>
  readFileSync(join(process.cwd(), ...parts), "utf8").replace(/\r\n/g, "\n");

function setup(props: Partial<Parameters<typeof PermissionChip>[0]> = {}) {
  const onChange = vi.fn();
  render(<PermissionChip value="approve_for_me" onChange={onChange} {...props} />);
  const chip = screen.getByTestId("permission-chip");
  return { chip, onChange };
}

function rows() {
  return within(screen.getByTestId("permission-menu")).getAllByTestId("permission-item");
}

describe("the one switch", () => {
  it("a chat lists the five levels, Don't ask last, and no Plan", () => {
    const { chip } = setup();
    fireEvent.click(chip);
    expect(rows().map((r) => r.dataset.mode)).toEqual(["always_ask", "approve_for_me", "accept_edits", "auto", "yolo"]);
    expect(rows().map((r) => r.querySelector("span.block")?.textContent)).toEqual([
      "Ask before every action",
      "Ask first",
      "Accept edits",
      "Auto",
      "Don't ask",
    ]);
  });

  it("a coding session adds Plan just before Don't ask", () => {
    const onPlanChange = vi.fn();
    const { chip } = setup({ onPlanChange });
    fireEvent.click(chip);
    expect(rows().map((r) => r.dataset.mode)).toEqual([
      "always_ask", "approve_for_me", "accept_edits", "auto", "plan", "yolo",
    ]);
    expect(permissionMenuKeys(false)).not.toContain("plan");
  });

  it("picking Plan keeps the level; picking a level while planning turns plan off first", () => {
    const onPlanChange = vi.fn();
    const { chip, onChange } = setup({ value: "auto", onPlanChange });
    fireEvent.click(chip);
    fireEvent.click(rows().find((r) => r.dataset.mode === "plan")!);
    expect(onPlanChange).toHaveBeenCalledWith(true);
    expect(onChange).not.toHaveBeenCalled();
    cleanup();

    const onPlan2 = vi.fn();
    const s2 = setup({ value: "auto", planMode: true, onPlanChange: onPlan2 });
    // Planning: the chip says Plan, and the menu checks Plan.
    expect(s2.chip.textContent).toBe(PLAN_LEVEL.label);
    expect(s2.chip.getAttribute("data-plan")).toBe("true");
    fireEvent.click(s2.chip);
    expect(rows().find((r) => r.getAttribute("aria-checked") === "true")?.dataset.mode).toBe("plan");
    // Re-picking the SAME level turns plan off and changes no level.
    fireEvent.click(rows().find((r) => r.dataset.mode === "auto")!);
    expect(onPlan2).toHaveBeenCalledWith(false);
    expect(s2.onChange).not.toHaveBeenCalled();
    cleanup();

    const onPlan3 = vi.fn();
    const s3 = setup({ value: "auto", planMode: true, onPlanChange: onPlan3 });
    fireEvent.click(s3.chip);
    fireEvent.click(rows().find((r) => r.dataset.mode === "accept_edits")!);
    expect(onPlan3).toHaveBeenCalledWith(false);
    expect(s3.onChange).toHaveBeenCalledWith("accept_edits");
  });

  it("each level has its own shield, and only Don't ask is amber", () => {
    for (const mode of ["always_ask", "approve_for_me", "accept_edits", "auto", "yolo"] as const) {
      cleanup();
      const { chip } = setup({ value: mode });
      expect(chip.getAttribute("data-mode")).toBe(mode);
      expect(chip.getAttribute("data-tone")).toBe(mode === "yolo" ? "warn" : "neutral");
    }
    expect(PERMISSION_LEVELS.always_ask.chip).toBe("Ask every time");
  });

  it("old stored values keep their meaning, and the new ones are known", () => {
    expect(asPermissionMode("accept_edits")).toBe("accept_edits");
    expect(asPermissionMode("auto")).toBe("auto");
    expect(asPermissionMode("ask_when_risky")).toBe("approve_for_me");
  });

  it("the coding session passes Plan to the picker (the one place)", () => {
    const src = read("components", "harness", "HarnessSession.tsx");
    expect(src).toMatch(/<PermissionChip[\s\S]{0,900}planMode=\{view\.mode === "plan"\}[\s\S]{0,300}onPlanChange=/);
  });

  it("Auto's words say what always asks", () => {
    expect(PERMISSION_LEVELS.auto.description).toMatch(/Always asks before anything that deletes, sends, pays/);
    expect(PERMISSION_LEVELS.accept_edits.description).toMatch(/in this folder/);
  });
});
