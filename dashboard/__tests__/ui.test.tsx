import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { Badge, StatusDot, Empty } from "@/components/ui";

/**
 * Smoke render of a few shared ui.tsx primitives — proves they mount in jsdom
 * and encode their core contract (Badge shows its value; a live status gets the
 * pulse class). Cheap regression net for the most-reused presentational bits.
 */
describe("ui.tsx primitives", () => {
  it("Badge renders its value text", () => {
    render(<Badge value="completed" />);
    expect(screen.getByText("completed")).toBeInTheDocument();
  });

  it("StatusDot pulses for in-flight (running) states", () => {
    const { container } = render(<StatusDot status="running" />);
    const dot = container.querySelector("span");
    expect(dot).toBeTruthy();
    expect(dot?.className).toContain("animate-pulse-glow");
  });

  it("StatusDot does NOT pulse for a terminal (completed) state", () => {
    const { container } = render(<StatusDot status="completed" />);
    expect(container.querySelector("span")?.className).not.toContain("animate-pulse-glow");
  });

  it("Empty renders its message", () => {
    render(<Empty>Nothing here yet.</Empty>);
    expect(screen.getByText("Nothing here yet.")).toBeInTheDocument();
  });
});
