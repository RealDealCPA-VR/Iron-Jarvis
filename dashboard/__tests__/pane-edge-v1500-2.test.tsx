/**
 * v1.500.2 (the owner, on Chat › Code's and Build › Code's right-hand panel:
 * "should be draggable so when the left is hidden the user can focus on the
 * task at hand easier") — components/PaneEdge: the panel's left edge is a
 * drag handle (a vertical separator). Dragging left widens the panel, the
 * arrow keys move it in steps, a double-click goes back to the standard
 * width, and the chosen width is remembered per view.
 */
import React, { useState } from "react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import {
  MAIN_MIN,
  PANE_MAX,
  PANE_MIN,
  PANE_STEP,
  PANE_WIDTH_CLASS,
  PaneEdge,
  clampPaneWidth,
  paneWidthStyle,
  readPaneWidth,
  usePaneWidth,
  writePaneWidth,
} from "@/components/PaneEdge";

beforeEach(() => localStorage.clear());
afterEach(() => cleanup());

describe("the width rules", () => {
  it("keeps the panel between its least and most, and leaves the conversation room", () => {
    expect(clampPaneWidth(100)).toBe(PANE_MIN);
    expect(clampPaneWidth(5000)).toBe(PANE_MAX);
    expect(clampPaneWidth(500)).toBe(500);
    // A 1200 px row leaves the conversation at least MAIN_MIN.
    expect(clampPaneWidth(1000, 1200)).toBe(1200 - MAIN_MIN);
    // A row too narrow for both: the least width still wins.
    expect(clampPaneWidth(500, 500)).toBe(PANE_MIN);
    expect(clampPaneWidth(Number.NaN)).toBe(PANE_MIN);
  });

  it("is remembered per view; a bad value reads as none", () => {
    writePaneWidth("k1", 512);
    expect(readPaneWidth("k1")).toBe(512);
    expect(readPaneWidth("k2")).toBeNull();
    writePaneWidth("k1", null);
    expect(readPaneWidth("k1")).toBeNull();
    localStorage.setItem("k3", "wide");
    expect(readPaneWidth("k3")).toBeNull();
    localStorage.setItem("k4", "99999");
    expect(readPaneWidth("k4")).toBe(PANE_MAX);
  });

  it("the style carries the width only when one was chosen", () => {
    expect(paneWidthStyle(null)).toBeUndefined();
    expect(paneWidthStyle(400)).toEqual({ "--side-w": "400px" });
    expect(PANE_WIDTH_CLASS).toContain("xl:w-[var(--side-w)]");
  });
});

function Panel({ fallback = 320, storeKey = "pane.test" }: { fallback?: number; storeKey?: string }) {
  const [width, setWidth] = usePaneWidth(storeKey);
  const [commits, setCommits] = useState(0);
  return (
    <div>
      <aside data-testid="panel" data-width={width ?? ""} style={paneWidthStyle(width)}>
        <PaneEdge
          width={width}
          fallback={fallback}
          onCommit={(w) => {
            setWidth(w);
            setCommits((n) => n + 1);
          }}
        />
      </aside>
      <span data-testid="commits">{commits}</span>
    </div>
  );
}

describe("the edge", () => {
  it("is a named vertical separator with its range", () => {
    render(<Panel />);
    const edge = screen.getByRole("separator");
    expect(edge).toHaveAttribute("aria-orientation", "vertical");
    expect(edge.getAttribute("aria-label")).toMatch(/resize/i);
    expect(edge).toHaveAttribute("aria-valuenow", "320");
    expect(edge).toHaveAttribute("aria-valuemin", String(PANE_MIN));
    expect(edge).toHaveAttribute("aria-valuemax", String(PANE_MAX));
  });

  it("dragging LEFT widens the panel, live while dragging, kept when let go", () => {
    render(<Panel />);
    const edge = screen.getByRole("separator");
    const panel = screen.getByTestId("panel");
    fireEvent.pointerDown(edge, { clientX: 1000, button: 0, pointerId: 1 });
    fireEvent.pointerMove(edge, { clientX: 900, pointerId: 1 });
    // Live: the panel follows the pointer before anything is saved.
    expect(panel.style.width).toBe("420px");
    expect(screen.getByTestId("commits")).toHaveTextContent("0");
    fireEvent.pointerUp(edge, { clientX: 900, pointerId: 1 });
    expect(panel).toHaveAttribute("data-width", "420");
    expect(panel.style.width).toBe("");
    expect(panel.style.getPropertyValue("--side-w")).toBe("420px");
    expect(readPaneWidth("pane.test")).toBe(420);
    // Dragging RIGHT narrows it, never under the least width.
    fireEvent.pointerDown(edge, { clientX: 900, button: 0, pointerId: 1 });
    fireEvent.pointerMove(edge, { clientX: 1500, pointerId: 1 });
    fireEvent.pointerUp(edge, { clientX: 1500, pointerId: 1 });
    expect(panel).toHaveAttribute("data-width", String(PANE_MIN));
  });

  it("a press that never moves saves nothing", () => {
    render(<Panel />);
    const edge = screen.getByRole("separator");
    fireEvent.pointerDown(edge, { clientX: 1000, button: 0, pointerId: 1 });
    fireEvent.pointerUp(edge, { clientX: 1000, pointerId: 1 });
    expect(screen.getByTestId("commits")).toHaveTextContent("0");
    expect(readPaneWidth("pane.test")).toBeNull();
  });

  it("the arrow keys move it a step; a double-click goes back to the standard width", () => {
    render(<Panel />);
    const edge = screen.getByRole("separator");
    fireEvent.keyDown(edge, { key: "ArrowLeft" });
    expect(screen.getByTestId("panel")).toHaveAttribute("data-width", String(320 + PANE_STEP));
    fireEvent.keyDown(edge, { key: "ArrowRight" });
    fireEvent.keyDown(edge, { key: "ArrowRight" });
    expect(screen.getByTestId("panel")).toHaveAttribute("data-width", String(320 - PANE_STEP));
    fireEvent.doubleClick(edge);
    expect(screen.getByTestId("panel")).toHaveAttribute("data-width", "");
    expect(readPaneWidth("pane.test")).toBeNull();
  });

  it("a remembered width is used on the first frame", () => {
    writePaneWidth("pane.test", 600);
    render(<Panel />);
    expect(screen.getByTestId("panel")).toHaveAttribute("data-width", "600");
    expect(screen.getByRole("separator")).toHaveAttribute("aria-valuenow", "600");
  });
});

// The two views draw it on their panel (wiring pinned in their own files:
// ij-harness-panes-toolbar-v1420, build-code-panes-v1414).
