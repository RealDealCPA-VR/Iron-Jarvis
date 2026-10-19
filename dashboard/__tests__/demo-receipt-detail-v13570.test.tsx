/**
 * v1.357.0 (product readiness G76, review round): the chat receipt's
 * expanded route line says the demo model's ONE name.
 *
 * Under a demo reply the collapsed receipt already read "Demo answer. No AI
 * model ran." (lib/demoWords), but pressing it opened "Demo model (scripted)
 * (mock) · mock-1 (mock)": the provider id the canon retired, twice, on the
 * most-used surface a new user has. The line now says "Demo model
 * (scripted)" alone and the raw record ("mock · mock-1 (mock)") rides its
 * title, the rule the mission card's demo line follows (36c91ced). A real
 * model's line keeps its raw ids exactly as before (v1.314.0: a record for
 * support).
 *
 * Real component, no mocks.
 */

import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { TurnReceipt } from "@/components/chat/TurnReceipt";
import { DEMO_ANSWER, DEMO_MODEL_LABEL } from "@/lib/demoWords";

afterEach(() => cleanup());

function expandedDetail(): HTMLElement {
  fireEvent.click(screen.getByRole("button", { expanded: false }));
  return screen.getByTestId("turn-receipt-detail");
}

describe("the demo's receipt detail names the demo one way", () => {
  it("a demo turn's expanded line reads 'Demo model (scripted)' with no 'mock' in its words", () => {
    render(<TurnReceipt route={{ requested: "", provider: "mock", model: "mock-1", reason: "mock" }} />);
    // The collapsed line is the demo's sentence (unchanged).
    expect(screen.getByRole("button", { expanded: false }).textContent).toContain(DEMO_ANSWER);
    const detail = expandedDetail();
    const name = Array.from(detail.querySelectorAll("span")).find((s) => s.textContent === DEMO_MODEL_LABEL);
    expect(name).toBeTruthy();
    // The route line's words: the demo's one label and nothing that names it
    // another way.
    const line = (name as HTMLElement).parentElement as HTMLElement;
    expect(line.textContent).toBe(DEMO_MODEL_LABEL);
    expect(detail.textContent ?? "").not.toMatch(/\bmock\b/i);
    // The record for support is still reachable, in the title.
    expect((name as HTMLElement).getAttribute("title")).toBe("mock · mock-1 (mock)");
  });

  it("a demo turn with no model or reason keeps a short record", () => {
    render(<TurnReceipt route={{ provider: "mock", reason: "" }} />);
    const detail = expandedDetail();
    const name = Array.from(detail.querySelectorAll("span")).find((s) => s.textContent === DEMO_MODEL_LABEL);
    expect((name as HTMLElement).getAttribute("title")).toBe("mock");
    expect(detail.textContent ?? "").not.toMatch(/\bmock\b/i);
  });

  it("control: a real model's expanded line keeps its raw ids and reason, with no title", () => {
    render(<TurnReceipt route={{ requested: "", provider: "claude-cli", model: "claude-fable-5", reason: "default" }} />);
    const detail = expandedDetail();
    expect(detail.textContent).toContain("Claude Code (claude-cli) · claude-fable-5 (default)");
    const name = Array.from(detail.querySelectorAll("span")).find((s) => s.textContent === "Claude Code");
    expect((name as HTMLElement).getAttribute("title")).toBeNull();
  });
});
