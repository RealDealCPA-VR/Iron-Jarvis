/**
 * v1.289.0 — an Overview tile never leaves the screen, and pushing one past
 * the edge opens that module in its own window.
 *
 * Before: sortable's transform followed the pointer without limit, so a tile
 * could be dragged clean off the window (the auto-scroller chasing it) and the
 * Overview was wrecked until a reload. Now the drag is clamped to the viewport,
 * and in the desktop app a drop past the edge is the v1.283.0 pop-out of that
 * module — the arrangement untouched, because that was not a rearrange.
 *
 * The geometry is pinned on the pure helpers; the gesture is driven through
 * the REAL grid with dnd-kit's PointerSensor (pointer events on jsdom, tile
 * rectangles stubbed to a known size), asserting the tile's rendered
 * transform, the hint, the pop-out call and the saved order.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import { AppGrid } from "@/components/overview/AppGrid";
import { clampToWindow, offscreenEdge } from "@/lib/tileDrag";

const TILE = { left: 100, top: 100, width: 80, height: 80 };
const WIN = { width: 1000, height: 800 };

describe("tile drag geometry", () => {
  it("clamps the translate so the tile stays inside the window", () => {
    expect(clampToWindow({ x: 5000, y: 0 }, TILE, WIN)).toEqual({ x: 820, y: 0 });
    expect(clampToWindow({ x: -5000, y: -5000 }, TILE, WIN)).toEqual({ x: -100, y: -100 });
    expect(clampToWindow({ x: 0, y: 3000 }, TILE, WIN)).toEqual({ x: 0, y: 620 });
    // Inside the window: untouched.
    expect(clampToWindow({ x: 12, y: -7 }, TILE, WIN)).toEqual({ x: 12, y: -7 });
  });

  it("reads the edge from the RAW translate: more than half the tile out", () => {
    expect(offscreenEdge({ x: 0, y: 0 }, TILE, WIN)).toBeNull();
    // Exactly half out is not yet an intent; one more pixel is.
    expect(offscreenEdge({ x: 860, y: 0 }, TILE, WIN)).toBeNull();
    expect(offscreenEdge({ x: 861, y: 0 }, TILE, WIN)).toBe("right");
    expect(offscreenEdge({ x: -141, y: 0 }, TILE, WIN)).toBe("left");
    expect(offscreenEdge({ x: 0, y: -141 }, TILE, WIN)).toBe("top");
    expect(offscreenEdge({ x: 0, y: 661 }, TILE, WIN)).toBe("bottom");
    // A corner: the larger overshoot names the edge (right 40px, bottom 60px).
    expect(offscreenEdge({ x: 900, y: 720 }, TILE, WIN)).toBe("bottom");
    expect(offscreenEdge({ x: 920, y: 700 }, TILE, WIN)).toBe("right");
  });
});

// --- the gesture, through the real grid --------------------------------------

const open = vi.fn(async (path: string) => ({ ok: true, path }));

function installBridge() {
  (window as unknown as { ironjarvis?: unknown }).ironjarvis = {
    isDesktop: true,
    popout: {
      isPopout: false,
      path: "",
      open,
      list: vi.fn(async () => []),
      focus: vi.fn(async () => null),
      close: vi.fn(async () => null),
    },
  };
}

function stubGeometry() {
  Object.defineProperty(window, "innerWidth", { value: WIN.width, configurable: true });
  Object.defineProperty(window, "innerHeight", { value: WIN.height, configurable: true });
  // Every tile reports the same rectangle: enough for the sensor and the
  // modifier, which only need the DRAGGED tile's size and origin.
  vi.spyOn(Element.prototype, "getBoundingClientRect").mockReturnValue({
    ...TILE,
    right: TILE.left + TILE.width,
    bottom: TILE.top + TILE.height,
    x: TILE.left,
    y: TILE.top,
    toJSON: () => ({}),
  } as DOMRect);
}

async function pickUp(tile: HTMLElement) {
  fireEvent.pointerDown(tile, { clientX: 140, clientY: 140, pointerId: 1, button: 0, isPrimary: true });
  // Past the 6px intent threshold: the drag starts.
  fireEvent.pointerMove(document, { clientX: 160, clientY: 140, pointerId: 1 });
  await waitFor(() => expect(tile.getAttribute("style") ?? "").toContain("translate3d"));
}

beforeEach(() => {
  window.localStorage.clear();
  open.mockClear();
  stubGeometry();
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  delete (window as unknown as { ironjarvis?: unknown }).ironjarvis;
});

describe("the Overview grid", () => {
  it("clamps a tile dragged far past the right edge to the screen", async () => {
    render(<AppGrid />);
    const tile = await screen.findByTestId("tile-chat");
    await pickUp(tile);
    fireEvent.pointerMove(document, { clientX: 5000, clientY: 140, pointerId: 1 });
    await waitFor(() => {
      // 1000 − 100 − 80: pinned at the window's right edge, not 4860px out.
      expect(tile.getAttribute("style")).toContain("translate3d(820px");
    });
  });

  it("in the desktop app, releasing a tile past the edge pops the module out and keeps the arrangement", async () => {
    installBridge();
    render(<AppGrid />);
    const tile = await screen.findByTestId("tile-chat");
    await pickUp(tile);
    expect(screen.queryByTestId("tile-edge-hint")).toBeNull();

    fireEvent.pointerMove(document, { clientX: 5000, clientY: 140, pointerId: 1 });
    const hint = await screen.findByTestId("tile-edge-hint");
    expect(hint.getAttribute("data-edge")).toBe("right");
    expect(hint.textContent).toContain("Release to open");
    expect(hint.textContent).toContain("in its own window");
    expect(tile.getAttribute("data-armed")).toBe("true");

    fireEvent.pointerUp(document, { clientX: 5000, clientY: 140, pointerId: 1 });
    await waitFor(() => expect(open).toHaveBeenCalledWith("/chat"));
    expect(open).toHaveBeenCalledTimes(1);
    // Not a rearrange: no order was written, the hint is gone, nothing armed.
    expect(window.localStorage.getItem("ironjarvis.overview.order")).toBeNull();
    await waitFor(() => expect(screen.queryByTestId("tile-edge-hint")).toBeNull());
    expect(tile.getAttribute("data-armed")).toBeNull();
  });

  it("pulling the tile back inside disarms the drop", async () => {
    installBridge();
    render(<AppGrid />);
    const tile = await screen.findByTestId("tile-chat");
    await pickUp(tile);
    fireEvent.pointerMove(document, { clientX: 5000, clientY: 140, pointerId: 1 });
    await screen.findByTestId("tile-edge-hint");
    fireEvent.pointerMove(document, { clientX: 300, clientY: 140, pointerId: 1 });
    await waitFor(() => expect(screen.queryByTestId("tile-edge-hint")).toBeNull());
    fireEvent.pointerUp(document, { clientX: 300, clientY: 140, pointerId: 1 });
    await new Promise((r) => setTimeout(r, 20));
    expect(open).not.toHaveBeenCalled();
  });

  it("in a browser there is no window to open: the tile stays put and nothing breaks", async () => {
    render(<AppGrid />);
    const tile = await screen.findByTestId("tile-chat");
    await pickUp(tile);
    fireEvent.pointerMove(document, { clientX: 5000, clientY: 140, pointerId: 1 });
    const hint = await screen.findByTestId("tile-edge-hint");
    expect(hint.textContent).toContain("stay on this screen");
    fireEvent.pointerUp(document, { clientX: 5000, clientY: 140, pointerId: 1 });
    await waitFor(() => expect(screen.queryByTestId("tile-edge-hint")).toBeNull());
    expect(open).not.toHaveBeenCalled();
    expect(window.localStorage.getItem("ironjarvis.overview.order")).toBeNull();
  });
});
