/**
 * v1.350.1: the model menu's per-provider panel stays inside the window.
 * The user's RTX6000ada list was "cut off at the top": the panel grew up from
 * a row near the top of the menu, past the window's top edge, unscrollable.
 */
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { EDGE_PX, FitPanel, fitPlacement } from "@/components/chat/FitPanel";

describe("fitPlacement", () => {
  it("grows up, capped to the room above, when there is room", () => {
    expect(fitPlacement(500, 530, 900, 384)).toEqual({ side: "up", maxHeight: 384 });
    expect(fitPlacement(250, 280, 900, 384)).toEqual({ side: "up", maxHeight: 280 - EDGE_PX });
  });
  it("opens DOWN when a row sits too high and there is more room below", () => {
    expect(fitPlacement(40, 70, 900, 384)).toEqual({ side: "down", maxHeight: 384 });
    expect(fitPlacement(40, 70, 300, 384)).toEqual({ side: "down", maxHeight: 300 - 40 - EDGE_PX });
  });
  it("never reports a negative height", () => {
    expect(fitPlacement(-50, -20, 10, 384).maxHeight).toBeGreaterThanOrEqual(0);
  });
});

describe("FitPanel", () => {
  afterEach(() => vi.restoreAllMocks());

  function anchorAt(top: number, bottom: number) {
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(
      () => ({ top, bottom, left: 0, right: 0, width: 0, height: bottom - top, x: 0, y: top, toJSON: () => ({}) }) as DOMRect,
    );
  }

  it("a row near the top opens its list downward and scrolls inside it", () => {
    anchorAt(40, 70);
    render(
      <div className="relative">
        <FitPanel testId="model-sub" className="absolute right-full w-56">
          <button type="button">brain (RTX)</button>
        </FitPanel>
      </div>,
    );
    const panel = screen.getByTestId("model-sub");
    expect(panel.dataset.side).toBe("down");
    expect(panel.className).toContain("top-0");
    expect(panel.className).not.toContain("bottom-0");
    expect(panel.className).toContain("overflow-y-auto");
    expect(panel.style.maxHeight).toBe("384px");
  });

  it("a row low in the window keeps growing up, capped to the room above", () => {
    anchorAt(250, 280);
    render(
      <div className="relative">
        <FitPanel testId="model-sub" className="absolute right-full w-56">
          <span>m</span>
        </FitPanel>
      </div>,
    );
    const panel = screen.getByTestId("model-sub");
    expect(panel.dataset.side).toBe("up");
    expect(panel.className).toContain("bottom-0");
    expect(panel.style.maxHeight).toBe(`${280 - EDGE_PX}px`);
  });
});
