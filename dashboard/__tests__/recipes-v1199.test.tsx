/**
 * v1.199.0 — the RECIPES row: one-click whole jobs on the chat empty state.
 *
 * The pins that matter:
 *  - RECIPES is exactly 6 whole jobs with non-empty, unique keys, titles and
 *    prompts — a duplicate key breaks React reconciliation silently, and a
 *    duplicate prompt means two tiles that do the same thing.
 *  - No recipe prompt duplicates a CHAT_EXAMPLES chip: recipes are WHOLE
 *    JOBS, the chips are one-liners; if the catalogs converge, one of them
 *    is lying about its purpose.
 *  - Clicking a recipe calls onPick with that recipe's EXACT prompt and
 *    nothing else — prefill-not-send is the suggest-don't-act posture, so
 *    the only observable effect of a click is the callback.
 *  - The schedule and workflow recipes exist (regression pin): they are the
 *    two recipes whose OUTPUT lives on another surface (/schedules, the
 *    workflow canvas). Dropping them quietly would reduce the row to
 *    chat-only jobs.
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import { RECIPES } from "@/lib/recipes";
import { CHAT_EXAMPLES } from "@/components/chat/examples";
import { RecipesRow } from "@/components/chat/RecipesRow";

afterEach(() => {
  cleanup();
});

describe("RECIPES catalog (v1.199.0)", () => {
  it("has exactly 6 recipes with non-empty unique keys, titles, and prompts", () => {
    expect(RECIPES.length).toBe(6);

    for (const r of RECIPES) {
      expect(r.key.trim().length).toBeGreaterThan(0);
      expect(r.title.trim().length).toBeGreaterThan(0);
      expect(r.blurb.trim().length).toBeGreaterThan(0);
      expect(r.prompt.trim().length).toBeGreaterThan(0);
    }

    expect(new Set(RECIPES.map((r) => r.key)).size).toBe(RECIPES.length);
    expect(new Set(RECIPES.map((r) => r.title)).size).toBe(RECIPES.length);
    expect(new Set(RECIPES.map((r) => r.prompt)).size).toBe(RECIPES.length);
  });

  it("no recipe prompt duplicates an example chip (whole jobs vs one-liners)", () => {
    const chips = new Set(CHAT_EXAMPLES.map((e) => e.trim().toLowerCase()));
    for (const r of RECIPES) {
      expect(chips.has(r.prompt.trim().toLowerCase())).toBe(false);
    }
  });

  it("regression pin: the schedule and workflow door recipes exist", () => {
    // These two create things that live on other surfaces (/schedules, the
    // workflow canvas) — the row's proof that the product is bigger than
    // chat. Pin the key AND that the prompt still asks for that thing.
    const schedule = RECIPES.find((r) => r.key === "morning-brief");
    expect(schedule).toBeDefined();
    expect(schedule!.prompt.toLowerCase()).toContain("schedule");

    const workflow = RECIPES.find((r) => r.key === "build-workflow");
    expect(workflow).toBeDefined();
    expect(workflow!.prompt.toLowerCase()).toContain("workflow");
  });
});

describe("RecipesRow (v1.199.0)", () => {
  it("renders all 6 recipes (title + blurb) under the whole-job heading", () => {
    render(<RecipesRow onPick={() => {}} />);

    expect(screen.getByText(/or start a whole job/i)).not.toBeNull();
    for (const r of RECIPES) {
      expect(screen.getByText(r.title)).not.toBeNull();
      expect(screen.getByText(r.blurb)).not.toBeNull();
    }
  });

  it("clicking a recipe calls onPick with that recipe's exact prompt — and does not send", () => {
    const onPick = vi.fn();
    render(<RecipesRow onPick={onPick} />);

    for (const r of RECIPES) {
      fireEvent.click(screen.getByText(r.title));
      // Exact prompt, verbatim — the composer prefill must match the catalog.
      expect(onPick).toHaveBeenLastCalledWith(r.prompt);
    }
    // One callback per click and nothing else: prefill-only is the whole
    // suggest-don't-act contract of this component.
    expect(onPick).toHaveBeenCalledTimes(RECIPES.length);
  });
});
