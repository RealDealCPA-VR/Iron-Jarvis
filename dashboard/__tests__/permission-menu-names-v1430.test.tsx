import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";

/**
 * v1.430.0 (the user, beside a screenshot of Chat › Code's permission menu):
 * "the descriptions are too verbose. Instead lets make it just the titles …
 * Potentially a popup … of what it means if the user waves the mouse over it
 * for long enough."
 *
 * Pinned here:
 *  - the menu's rows show each level's NAME only (Auto is "Auto");
 *  - each row's meaning stays its accessible description (aria-describedby),
 *    so a screen reader still hears it;
 *  - the meaning shows in a box beside the menu only after the pointer rests
 *    on a row for PERMISSION_HINT_DELAY_MS, and once one shows the next row's
 *    shows at once; leaving the menu hides it; closing the menu hides it;
 *  - the keyboard's highlight resting on a row shows it too (opening does not);
 *  - on a touch screen a finger HELD on a row shows it, and that lift picks
 *    nothing; a plain tap still picks;
 *  - the box stays inside the window: beside the menu when there is room,
 *    under (or over) the row on a phone.
 */

import {
  PermissionChip,
  PERMISSION_HINT_DELAY_MS,
  PERMISSION_HINT_W,
  permissionHintPlacement,
} from "@/components/chat/PermissionChip";
import { PERMISSION_LEVELS, PERMISSION_MODES, PLAN_LEVEL } from "@/lib/permissionLevels";

beforeAll(() => {
  // jsdom has no PointerEvent: React reads `pointerType` off the native event.
  if (typeof window.PointerEvent === "undefined") {
    class FakePointerEvent extends MouseEvent {
      pointerType: string;
      constructor(type: string, init: PointerEventInit = {}) {
        super(type, init);
        this.pointerType = init.pointerType ?? "mouse";
      }
    }
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (window as any).PointerEvent = FakePointerEvent;
  }
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

function setup(props: Partial<React.ComponentProps<typeof PermissionChip>> = {}) {
  const onChange = vi.fn();
  render(<PermissionChip value="approve_for_me" onChange={onChange} {...props} />);
  const chip = screen.getByTestId("permission-chip");
  fireEvent.click(chip);
  const menu = screen.getByTestId("permission-menu");
  const row = (mode: string) =>
    within(menu)
      .getAllByTestId("permission-item")
      .find((r) => r.dataset.mode === mode)!;
  return { onChange, chip, menu, row };
}

const hint = () => screen.queryByTestId("permission-hint");
const wait = (ms: number) =>
  act(() => {
    vi.advanceTimersByTime(ms);
  });

describe("the menu shows names only", () => {
  it("every row reads its name and nothing else", () => {
    const { menu } = setup();
    const rows = within(menu).getAllByTestId("permission-item");
    expect(rows.map((r) => r.dataset.mode)).toEqual([...PERMISSION_MODES]);
    for (const r of rows) {
      const level = PERMISSION_LEVELS[r.dataset.mode as keyof typeof PERMISSION_LEVELS];
      expect(r.textContent).toBe(level.label);
    }
    expect(rows.map((r) => r.textContent)).toEqual([
      "Ask before every action",
      "Ask before commands",
      "Accept edits",
      "Auto",
      "Don't ask",
    ]);
  });

  it("a coding session's Plan row is a name too", () => {
    const { row } = setup({ onPlanChange: () => {} });
    expect(row("plan").textContent).toBe("Plan");
  });

  it("each row keeps its meaning as its accessible description", () => {
    const { menu } = setup({ onPlanChange: () => {} });
    for (const r of within(menu).getAllByTestId("permission-item")) {
      const id = r.getAttribute("aria-describedby");
      expect(id, `${r.dataset.mode} has no description`).toBeTruthy();
      const about = document.getElementById(id!);
      expect(about).not.toBeNull();
      const want = r.dataset.mode === "plan" ? PLAN_LEVEL.description : PERMISSION_LEVELS[r.dataset.mode as "yolo"].description;
      expect(about!.textContent).toBe(want);
      // Not drawn: the eye reads it in the box beside the menu.
      expect(about!.hidden).toBe(true);
    }
  });

  it("the chip's own tooltip still says what the current level does", () => {
    const { chip } = setup();
    expect(chip.getAttribute("title")).toBe(PERMISSION_LEVELS.approve_for_me.description);
  });
});

describe("the meaning shows after a pause over a row", () => {
  it("not at once; after the pause, beside the menu, with that row's meaning", () => {
    vi.useFakeTimers();
    const { row } = setup();
    fireEvent.mouseEnter(row("auto"));
    expect(hint()).toBeNull();
    wait(PERMISSION_HINT_DELAY_MS - 1);
    expect(hint()).toBeNull();
    wait(1);
    const box = hint()!;
    expect(box).not.toBeNull();
    expect(box.dataset.mode).toBe("auto");
    expect(box.textContent).toBe(PERMISSION_LEVELS.auto.description);
    expect(box.getAttribute("aria-hidden")).toBe("true");
    expect(box.className).toContain("pointer-events-none");
    expect(box.style.position).toBe("fixed");
    expect(box.style.visibility).toBe("");
  });

  it("a pointer passing over a row and on shows nothing", () => {
    vi.useFakeTimers();
    const { row, menu } = setup();
    fireEvent.mouseEnter(row("always_ask"));
    wait(200);
    fireEvent.mouseLeave(menu);
    wait(PERMISSION_HINT_DELAY_MS * 2);
    expect(hint()).toBeNull();
  });

  it("once one meaning shows, the next row's shows at once", () => {
    vi.useFakeTimers();
    const { row } = setup();
    fireEvent.mouseEnter(row("accept_edits"));
    wait(PERMISSION_HINT_DELAY_MS);
    expect(hint()!.dataset.mode).toBe("accept_edits");
    fireEvent.mouseEnter(row("yolo"));
    expect(hint()!.dataset.mode).toBe("yolo");
    expect(hint()!.textContent).toBe(PERMISSION_LEVELS.yolo.description);
  });

  it("leaving the menu hides it, and the next row waits again", () => {
    vi.useFakeTimers();
    const { row, menu } = setup();
    fireEvent.mouseEnter(row("auto"));
    wait(PERMISSION_HINT_DELAY_MS);
    expect(hint()).not.toBeNull();
    fireEvent.mouseLeave(menu);
    expect(hint()).toBeNull();
    fireEvent.mouseEnter(row("yolo"));
    expect(hint()).toBeNull();
    wait(PERMISSION_HINT_DELAY_MS);
    expect(hint()!.dataset.mode).toBe("yolo");
  });

  it("closing the menu takes the meaning with it", () => {
    vi.useFakeTimers();
    const { row, menu } = setup();
    fireEvent.mouseEnter(row("auto"));
    wait(PERMISSION_HINT_DELAY_MS);
    fireEvent.keyDown(menu, { key: "Escape" });
    expect(screen.queryByTestId("permission-menu")).toBeNull();
    expect(hint()).toBeNull();
    // Opening again starts with nothing showing (no stale meaning comes back).
    fireEvent.click(screen.getByTestId("permission-chip"));
    expect(screen.getByTestId("permission-menu")).toBeTruthy();
    expect(hint()).toBeNull();
  });

  it("a click still picks, and nothing is left showing", () => {
    vi.useFakeTimers();
    const { row, onChange } = setup();
    fireEvent.mouseEnter(row("auto"));
    wait(PERMISSION_HINT_DELAY_MS);
    fireEvent.click(row("auto"));
    expect(onChange).toHaveBeenCalledWith("auto");
    expect(hint()).toBeNull();
  });
});

describe("the keyboard", () => {
  it("opening shows nothing; the highlight resting on a row shows its meaning", () => {
    vi.useFakeTimers();
    const { menu } = setup();
    wait(PERMISSION_HINT_DELAY_MS * 2);
    expect(hint()).toBeNull();
    fireEvent.keyDown(menu, { key: "ArrowDown" });
    expect(hint()).toBeNull();
    wait(PERMISSION_HINT_DELAY_MS);
    expect(hint()!.dataset.mode).toBe("accept_edits");
    // Moving on while one shows: the next shows at once.
    fireEvent.keyDown(menu, { key: "End" });
    expect(hint()!.dataset.mode).toBe("yolo");
  });
});

describe("a touch screen", () => {
  it("a finger held on a row shows its meaning, and lifting it picks nothing", () => {
    vi.useFakeTimers();
    const { row, onChange } = setup();
    const r = row("yolo");
    fireEvent.pointerDown(r, { pointerType: "touch" });
    wait(PERMISSION_HINT_DELAY_MS);
    expect(hint()!.dataset.mode).toBe("yolo");
    fireEvent.pointerUp(r, { pointerType: "touch" });
    fireEvent.click(r);
    expect(onChange).not.toHaveBeenCalled();
    expect(screen.getByTestId("permission-menu")).toBeTruthy();
    // The next tap is an ordinary pick.
    const ask = row("always_ask");
    fireEvent.pointerDown(ask, { pointerType: "touch" });
    fireEvent.pointerUp(ask, { pointerType: "touch" });
    fireEvent.click(ask);
    expect(onChange).toHaveBeenCalledWith("always_ask");
  });

  it("a quick tap picks and never shows the meaning", () => {
    vi.useFakeTimers();
    const { row, onChange } = setup();
    const r = row("accept_edits");
    fireEvent.pointerDown(r, { pointerType: "touch" });
    wait(100);
    fireEvent.pointerUp(r, { pointerType: "touch" });
    expect(hint()).toBeNull();
    // The hold time passing after the lift changes nothing.
    wait(PERMISSION_HINT_DELAY_MS * 2);
    expect(hint()).toBeNull();
    fireEvent.click(r);
    expect(onChange).toHaveBeenCalledWith("accept_edits");
  });

  it("a mouse press is never a hold", () => {
    vi.useFakeTimers();
    const { row, onChange } = setup();
    const r = row("auto");
    fireEvent.pointerDown(r, { pointerType: "mouse" });
    wait(PERMISSION_HINT_DELAY_MS * 2);
    fireEvent.click(r);
    expect(onChange).toHaveBeenCalledWith("auto");
  });
});

describe("permissionHintPlacement", () => {
  const box = (left: number, top: number, w: number, h: number) => ({ left, top, right: left + w, bottom: top + h });
  const hintSize = { width: PERMISSION_HINT_W, height: 90 };

  it("sits right of the menu, level with its row, when there is room", () => {
    const menu = box(300, 400, 224, 180);
    const row = box(304, 470, 216, 32);
    expect(permissionHintPlacement(menu, row, hintSize, { width: 1440, height: 900 })).toEqual({
      side: "right",
      left: 300 + 224 + 6,
      top: 470,
    });
  });

  it("goes left of the menu when the right edge is close", () => {
    const menu = box(1100, 400, 224, 180);
    const row = box(1104, 470, 216, 32);
    const at = permissionHintPlacement(menu, row, hintSize, { width: 1440, height: 900 });
    expect(at.side).toBe("left");
    expect(at.left).toBe(1100 - 6 - PERMISSION_HINT_W);
  });

  it("never runs off the bottom beside the menu", () => {
    const menu = box(300, 700, 224, 190);
    const row = box(304, 852, 216, 32);
    const at = permissionHintPlacement(menu, row, hintSize, { width: 1440, height: 900 });
    expect(at.top + hintSize.height).toBeLessThanOrEqual(900 - 8);
  });

  it("on a phone it hangs under the row, or over it when there is no room under", () => {
    const vp = { width: 390, height: 844 };
    const menu = box(83, 500, 224, 180);
    const row = box(87, 540, 216, 32);
    const under = permissionHintPlacement(menu, row, hintSize, vp);
    expect(under.side).toBe("under");
    expect(under.top).toBe(540 + 32 + 6);
    expect(under.left).toBeGreaterThanOrEqual(8);
    expect(under.left + PERMISSION_HINT_W).toBeLessThanOrEqual(390 - 8);
    const low = box(87, 790, 216, 32);
    const over = permissionHintPlacement(menu, low, hintSize, vp);
    expect(over.top).toBe(790 - 6 - hintSize.height);
  });

  it("stays inside every phone width, wherever the menu sits", () => {
    for (const width of [300, 320, 360, 390]) {
      const vp = { width, height: 700 };
      for (let left = 8; left <= width - 120; left += 7) {
        const w = Math.min(224, width - 32);
        const menu = box(left, 300, w, 180);
        const row = box(left + 4, 340, w - 8, 32);
        const at = permissionHintPlacement(menu, row, hintSize, vp);
        const shown = Math.min(PERMISSION_HINT_W, width - 32);
        expect(at.left, `${width}px, menu at ${left}`).toBeGreaterThanOrEqual(8);
        expect(at.left + shown, `${width}px, menu at ${left}`).toBeLessThanOrEqual(width - 8);
        expect(at.top).toBeGreaterThanOrEqual(8);
        expect(at.top + hintSize.height).toBeLessThanOrEqual(700 - 8);
      }
    }
  });
});
