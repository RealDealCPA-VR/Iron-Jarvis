import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";

/**
 * v1.327.0 (Calm chat W2-2, review round): the permission menu can never be
 * cut off by the chat page.
 *
 * The first cut placed the menu absolutely inside the chip's wrapper, and the
 * chat page holds the composer inside
 * `<section data-testid="chat-card" class="... overflow-y-auto">`, which clips
 * anything placed inside it at ITS edges. On a 390x844 phone's new chat the
 * section's top is 166; the menu opened above the chip at 116, so the
 * "Ask first" row sat under the section's edge. A real tap there landed on the
 * page, counted as a press outside, and closed the menu with nothing picked.
 * The old native select's list could never be clipped.
 *
 * Now the menu is drawn on the page's top layer: a portal into
 * `document.body`, `position: fixed` at the chip. Pinned here:
 *  - the open menu is NOT inside the clipping section (or the chip's wrapper);
 *    it is a fixed box at the chip's edges, above or below;
 *  - a press on a row is not "a press outside": mousedown on the row leaves the
 *    menu open and the click picks (the exact live failure);
 *  - the menu follows the chip on a scroll, and closes when the chip goes away
 *    (hidden for a question in the composer's place);
 *  - a window too short for the menu on either side caps it to the roomier
 *    side with its own scroll, so every row stays reachable.
 *
 * The live check (every row's centre hit-tested with elementFromPoint at
 * 390x844, 360x740 and short desk windows, and a real click on "Ask first")
 * runs on the scratch stack; it is not part of this suite.
 */

import {
  PermissionChip,
  permissionMenuPlacement,
  permissionMenuPosition,
  PERMISSION_MENU_GAP,
} from "@/components/chat/PermissionChip";

afterEach(() => {
  cleanup();
});

type Box = { top: number; bottom: number; left: number; right: number; width: number; height: number };

/** Stubs boxes by data-testid and the window size; restores in a finally. */
function withLayout(rects: Record<string, Box>, size: { width: number; height: number }, run: () => void) {
  const spy = vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (this: HTMLElement) {
    const r = rects[this.dataset.testid ?? ""];
    const base = { x: 0, y: 0, top: 0, left: 0, bottom: 0, right: 0, width: 0, height: 0, toJSON() {} };
    return { ...base, ...(r ?? {}) } as DOMRect;
  });
  const prevW = window.innerWidth;
  const prevH = window.innerHeight;
  Object.defineProperty(window, "innerWidth", { configurable: true, writable: true, value: size.width });
  Object.defineProperty(window, "innerHeight", { configurable: true, writable: true, value: size.height });
  try {
    run();
  } finally {
    spy.mockRestore();
    Object.defineProperty(window, "innerWidth", { configurable: true, writable: true, value: prevW });
    Object.defineProperty(window, "innerHeight", { configurable: true, writable: true, value: prevH });
  }
}

const PHONE = { width: 390, height: 844 };
/** The live numbers: the chat section starts at 166; the chip sits at 348-378. */
const SECTION: Box = { top: 166, bottom: 563, left: 0, right: 390, width: 390, height: 397 };
const CHIP: Box = { top: 348, bottom: 378, left: 120, right: 150, width: 30, height: 30 };
const MENU_BOX: Box = { top: 0, left: 0, bottom: 226, right: 288, width: 288, height: 226 };

/** The chip inside a clipping section, as the chat page holds it. */
function renderInSection(onChange = vi.fn()) {
  render(
    <section data-testid="chat-card" style={{ overflowY: "auto", position: "relative" }}>
      <div className="toolbar">
        <PermissionChip id="chat-approval-mode" value="approve_for_me" onChange={onChange} iconOnlyOnPhone />
      </div>
    </section>,
  );
  return onChange;
}

describe("the menu is drawn outside the chat page's clipping section", () => {
  it("is a fixed box on the page's top layer, not inside the section or the chip's wrapper", () => {
    withLayout({ "chat-card": SECTION, "permission-chip": CHIP, "permission-menu": MENU_BOX }, PHONE, () => {
      renderInSection();
      fireEvent.click(screen.getByTestId("permission-chip"));
      const menu = screen.getByTestId("permission-menu");
      expect(screen.getByTestId("chat-card").contains(menu)).toBe(false);
      expect(screen.getByTestId("permission-chip-wrap").contains(menu)).toBe(false);
      expect(menu.parentElement).toBe(document.body);
      expect(menu.style.position).toBe("fixed");
      // Window above the chip: 348 - 8 = 340 >= 226, so it opens above, its
      // bottom edge 6px over the chip: 844 - 348 + 6 = 502 from the bottom.
      expect(menu.dataset.side).toBe("above");
      expect(menu.style.bottom).toBe(`${844 - 348 + PERMISSION_MENU_GAP}px`);
      // The box it covers: 844 - 502 - 226 = 116 to 342, which the section
      // (top 166) used to cut. Fixed to the window, nothing cuts it now.
      const top = 844 - parseFloat(menu.style.bottom) - MENU_BOX.height;
      expect(top).toBeGreaterThanOrEqual(8);
      // The chip keeps saying which menu it controls.
      expect(screen.getByTestId("permission-chip").getAttribute("aria-controls")).toBe(menu.id);
    });
  });

  it("a press on a row is not a press outside: mousedown keeps the menu, the click picks", () => {
    withLayout({ "chat-card": SECTION, "permission-chip": CHIP, "permission-menu": MENU_BOX }, PHONE, () => {
      const onChange = renderInSection();
      fireEvent.click(screen.getByTestId("permission-chip"));
      const ask = screen
        .getAllByTestId("permission-item")
        .find((i) => i.getAttribute("data-mode") === "always_ask")!;
      // A real tap: mousedown, then click. The menu sits outside the wrapper,
      // so the outside-press check must count the menu as inside.
      fireEvent.mouseDown(ask);
      expect(screen.queryByTestId("permission-menu")).not.toBeNull();
      fireEvent.click(ask);
      expect(onChange).toHaveBeenCalledWith("always_ask");
      expect(screen.queryByTestId("permission-menu")).toBeNull();
    });
  });

  it("a press elsewhere on the page still closes it", () => {
    withLayout({ "chat-card": SECTION, "permission-chip": CHIP, "permission-menu": MENU_BOX }, PHONE, () => {
      const onChange = renderInSection();
      fireEvent.click(screen.getByTestId("permission-chip"));
      fireEvent.mouseDown(screen.getByTestId("chat-card"));
      expect(screen.queryByTestId("permission-menu")).toBeNull();
      expect(onChange).not.toHaveBeenCalled();
    });
  });

  it("follows the chip when the page scrolls", () => {
    const rects = { "chat-card": SECTION, "permission-chip": { ...CHIP }, "permission-menu": MENU_BOX };
    withLayout(rects, PHONE, () => {
      renderInSection();
      fireEvent.click(screen.getByTestId("permission-chip"));
      const menu = screen.getByTestId("permission-menu");
      expect(menu.style.bottom).toBe(`${844 - 348 + PERMISSION_MENU_GAP}px`);
      // The page scrolls the chip 100px up.
      rects["permission-chip"] = { ...CHIP, top: 248, bottom: 278 };
      act(() => {
        fireEvent.scroll(screen.getByTestId("chat-card"));
      });
      expect(screen.getByTestId("permission-menu").style.bottom).toBe(`${844 - 248 + PERMISSION_MENU_GAP}px`);
    });
  });

  it("closes when the chip goes away (a question takes the composer's place)", () => {
    const observers: { cb: ResizeObserverCallback }[] = [];
    class FakeRO {
      cb: ResizeObserverCallback;
      constructor(cb: ResizeObserverCallback) {
        this.cb = cb;
        observers.push(this);
      }
      observe() {}
      unobserve() {}
      disconnect() {}
    }
    const prev = (globalThis as { ResizeObserver?: unknown }).ResizeObserver;
    (globalThis as { ResizeObserver?: unknown }).ResizeObserver = FakeRO;
    const rects = { "chat-card": SECTION, "permission-chip": { ...CHIP }, "permission-menu": MENU_BOX };
    try {
      withLayout(rects, PHONE, () => {
        renderInSection();
        fireEvent.click(screen.getByTestId("permission-chip"));
        expect(screen.queryByTestId("permission-menu")).not.toBeNull();
        expect(observers.length).toBeGreaterThan(0);
        // The composer is hidden: the chip has no box any more.
        rects["permission-chip"] = { top: 0, bottom: 0, left: 0, right: 0, width: 0, height: 0 };
        act(() => {
          for (const o of observers) o.cb([], o as unknown as ResizeObserver);
        });
        expect(screen.queryByTestId("permission-menu")).toBeNull();
      });
    } finally {
      (globalThis as { ResizeObserver?: unknown }).ResizeObserver = prev;
    }
  });
});

describe("a window too short for the menu on either side", () => {
  it("caps it to the roomier side, with its own scroll", () => {
    // 1440x300: 140 above and 114 below; the 226px menu fits neither.
    withLayout(
      { "permission-chip": { top: 148, bottom: 178, left: 600, right: 720, width: 120, height: 30 }, "permission-menu": MENU_BOX },
      { width: 1440, height: 300 },
      () => {
        render(<PermissionChip value="approve_for_me" onChange={() => {}} />);
        fireEvent.click(screen.getByTestId("permission-chip"));
        const menu = screen.getByTestId("permission-menu");
        expect(menu.dataset.side).toBe("above");
        expect(menu.style.maxHeight).toBe(`${148 - 8}px`);
        expect(menu.style.overflowY).toBe("auto");
        // Hung over the chip, capped: its top is 148 - 6 - 140 = 2, inside the window.
        expect(300 - parseFloat(menu.style.bottom) - 140).toBeGreaterThanOrEqual(0);
      },
    );
  });

  it("re-measures with the menu's whole height, not the capped box", () => {
    // The first guess caps the menu; its drawn box is then the cap (100px)
    // while its rows are 226px. The cap must stay: a 100px box would "fit".
    const sh = Object.getOwnPropertyDescriptor(HTMLElement.prototype, "scrollHeight");
    Object.defineProperty(HTMLElement.prototype, "scrollHeight", {
      configurable: true,
      get(this: HTMLElement) {
        return this.dataset.testid === "permission-menu" ? 226 : 0;
      },
    });
    try {
      withLayout(
        {
          "permission-chip": { top: 148, bottom: 178, left: 600, right: 720, width: 120, height: 30 },
          "permission-menu": { ...MENU_BOX, bottom: 100, height: 100 },
        },
        { width: 1440, height: 300 },
        () => {
          render(<PermissionChip value="approve_for_me" onChange={() => {}} />);
          fireEvent.click(screen.getByTestId("permission-chip"));
          expect(screen.getByTestId("permission-menu").style.maxHeight).toBe("140px");
        },
      );
    } finally {
      if (sh) Object.defineProperty(HTMLElement.prototype, "scrollHeight", sh);
    }
  });
});

describe("permissionMenuPosition", () => {
  const vp = { width: 390, height: 844 };
  const chip = { top: 348, bottom: 378, left: 120, right: 150 };

  it("above hangs the menu's bottom edge over the chip; below puts its top under it", () => {
    expect(permissionMenuPosition(chip, { side: "above", align: "start" }, vp)).toEqual({ bottom: 502, left: 120 });
    expect(permissionMenuPosition(chip, { side: "below", align: "start" }, vp)).toEqual({ top: 384, left: 120 });
  });

  it("end lines up the right edges; shift moves the left edge by the placement's offset", () => {
    expect(permissionMenuPosition(chip, { side: "above", align: "end" }, vp)).toEqual({ bottom: 502, right: 240 });
    expect(permissionMenuPosition(chip, { side: "above", align: "shift", left: -26 }, vp)).toEqual({
      bottom: 502,
      left: 94,
    });
  });

  it("every placement the phone produces keeps the whole menu inside the window", () => {
    for (const width of [320, 360, 390]) {
      for (let top = 60; top <= 800; top += 9) {
        for (let left = 8; left <= width - 60; left += 11) {
          const a = { top, bottom: top + 30, left, right: Math.min(left + 30, width - 8) };
          const v = { width, height: 844 };
          const place = permissionMenuPlacement(a, v);
          const p = permissionMenuPosition(a, place, v);
          const h = Math.min(226, place.maxHeight ?? 226);
          const w = Math.min(288, width - 32);
          const t = p.top ?? v.height - (p.bottom as number) - h;
          const l = p.left ?? v.width - (p.right as number) - w;
          const tag = `${width}px, chip at ${top},${left}`;
          expect(t, tag).toBeGreaterThanOrEqual(0);
          expect(t + h, tag).toBeLessThanOrEqual(844);
          expect(l, tag).toBeGreaterThanOrEqual(8);
          expect(l + w, tag).toBeLessThanOrEqual(width - 8);
        }
      }
    }
  });
});
