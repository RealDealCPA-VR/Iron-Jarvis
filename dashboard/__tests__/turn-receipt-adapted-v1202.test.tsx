/**
 * The receipt's QUIET adapted line (v1.202.0).
 *
 * The capability envelope bent a turn to fit a measured-weak local model
 * (narrowed tool menu) and the daemon disclosed it in the payload's
 * `adapted` object. The receipt must say so WITHOUT expanding — a silent
 * adaptation is a silent degrade — but in the QUIET class: this is the
 * user's own configured hardware being fitted (like prompted-tools /
 * auto-tier), NOT a substitution, so it must never wear amber or a warning
 * icon. Absent/null renders nothing extra.
 */

import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import {
  TurnReceipt,
  adaptedLabel,
  wordChange,
  type TurnRoute,
} from "@/components/chat/TurnReceipt";

afterEach(() => {
  cleanup();
});

const QUIET_ROUTE: TurnRoute = {
  requested: "ollama",
  provider: "ollama",
  model: "qwen3:4b",
  reason: "explicit",
};

describe("wordChange (THE one token renderer)", () => {
  // The narration module imports this SAME function (the draftFromFence
  // one-renderer lesson): the receipt and the progress line must never word
  // the same wire token differently.
  it("tool_cap:<n> reads as 'N tools max'", () => {
    expect(wordChange("tool_cap:4")).toBe("4 tools max");
    expect(wordChange("tool_cap:3")).toBe("3 tools max");
    expect(wordChange("  tool_cap:6  ")).toBe("6 tools max");
  });

  it("decomposed reads as 'running step-by-step'", () => {
    expect(wordChange("decomposed")).toBe("running step-by-step");
  });

  it("an unknown token renders verbatim, never vanishes", () => {
    expect(wordChange("strict_json")).toBe("strict_json");
    expect(wordChange("tool_cap:")).toBe("tool_cap:"); // malformed = verbatim
    expect(wordChange("tool_cap:x")).toBe("tool_cap:x");
  });

  it("adaptedLabel delegates to it (no second copy of the mapping)", () => {
    // If adaptedLabel re-inlined the mapping, this pair could drift apart —
    // assert the label is literally built from wordChange's output.
    expect(
      adaptedLabel({ model: "m", changes: ["tool_cap:5", "decomposed"] }),
    ).toBe(`adapted to m: ${wordChange("tool_cap:5")}, ${wordChange("decomposed")}`);
  });
});

describe("adaptedLabel (the pure wording helper)", () => {
  it("words tool_cap as 'N tools max' with the model named", () => {
    expect(
      adaptedLabel({ model: "qwen3:4b", changes: ["tool_cap:4"] }),
    ).toBe("adapted to qwen3:4b: 4 tools max");
  });

  it("omits the 'to <model>' clause when the model is blank", () => {
    expect(adaptedLabel({ model: "", changes: ["tool_cap:3"] })).toBe(
      "adapted: 3 tools max",
    );
    expect(adaptedLabel({ changes: ["tool_cap:3"] })).toBe(
      "adapted: 3 tools max",
    );
  });

  it("an unknown change token renders verbatim, never vanishes", () => {
    // A new adaptation kind the daemon learns should read oddly here, not
    // silently disappear (the PHASE_LABEL rule).
    expect(
      adaptedLabel({ model: "m", changes: ["tool_cap:4", "strict_json"] }),
    ).toBe("adapted to m: 4 tools max, strict_json");
  });

  it("null / absent / empty-changes all say nothing", () => {
    expect(adaptedLabel(null)).toBeNull();
    expect(adaptedLabel(undefined)).toBeNull();
    expect(adaptedLabel({ model: "m", changes: [] })).toBeNull();
    expect(adaptedLabel({ model: "m" })).toBeNull();
    expect(adaptedLabel({ model: "m", changes: ["", "  "] })).toBeNull();
  });
});

describe("TurnReceipt — the quiet adapted line", () => {
  it("renders the line WITHOUT expanding, on the collapsed strip", () => {
    render(
      <TurnReceipt
        route={QUIET_ROUTE}
        adapted={{ model: "qwen3:4b", changes: ["tool_cap:4"] }}
      />,
    );
    const toggle = screen.getByRole("button", { expanded: false });
    // No expand click — a bent turn must never be invisible.
    expect(toggle.textContent).toContain("adapted to qwen3:4b: 4 tools max");
  });

  it("is styled QUIET: no amber anywhere, no warning wording", () => {
    render(
      <TurnReceipt
        route={QUIET_ROUTE}
        adapted={{ model: "qwen3:4b", changes: ["tool_cap:4"] }}
      />,
    );
    const line = screen.getByText(/adapted to qwen3:4b/);
    expect(line.className).toContain("zinc");
    expect(line.className).not.toContain("amber");
    // The whole strip stays warning-free — same bar the quiet-route test in
    // turn-receipt.test.tsx holds served-as-asked turns to.
    expect(document.querySelector(".text-amber-300")).toBeNull();
    expect(screen.queryByText(/answered by/)).toBeNull();
  });

  it("absent and null render nothing extra (the common case)", () => {
    render(<TurnReceipt route={QUIET_ROUTE} adapted={null} />);
    expect(screen.queryByText(/adapted/)).toBeNull();
    cleanup();
    render(<TurnReceipt route={QUIET_ROUTE} />);
    expect(screen.queryByText(/adapted/)).toBeNull();
  });

  it("an adapted note ALONE still renders (never swallowed by zero-noise)", () => {
    const { container } = render(
      <TurnReceipt adapted={{ model: "tiny", changes: ["tool_cap:3"] }} />,
    );
    expect(container.firstChild).not.toBeNull();
    expect(screen.getByText(/adapted to tiny: 3 tools max/)).toBeTruthy();
  });

  it("a degenerate adapted (empty changes) does not defeat the zero-noise guard", () => {
    const { container } = render(
      <TurnReceipt adapted={{ model: "tiny", changes: [] }} />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("does not warp the amber honesty chip when both are present", () => {
    render(
      <TurnReceipt
        route={{ requested: "", provider: "mock", reason: "mock" }}
        adapted={{ model: "tiny", changes: ["tool_cap:3"] }}
      />,
    );
    // The warning stays the warning; the quiet line stays quiet.
    const chip = screen.getByText(/mock answer — no real model ran/);
    expect(chip.className).toContain("amber");
    const line = screen.getByText(/adapted to tiny/);
    expect(line.className).not.toContain("amber");
  });
});
