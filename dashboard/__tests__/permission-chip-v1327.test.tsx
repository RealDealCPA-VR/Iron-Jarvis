import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";

/**
 * v1.327.0 (Calm chat, B3): ONE permission chip with plain level names.
 *
 * Pinned here:
 *  - the levels table covers EXACTLY the modes the chat page's
 *    `#chat-approval-mode` select offers (and the daemon's vocabulary), in
 *    the same order: no invented level, none missing;
 *  - the riskiest level (yolo) is the one amber level, in tone tokens only;
 *  - the chip: shield + plain name + chevron, a ghost (transparent at rest,
 *    a fill only on hover), 30px tall; an unknown value reads as the default;
 *  - the menu: every level with its one-line description, the current one
 *    checked; arrows wrap, Enter picks, Esc closes WITHOUT reaching the page
 *    and gives focus back; an outside press closes; picking the current level
 *    calls nothing; disabled = nothing opens;
 *  - the menu fits the window: above by default, below when above has no room,
 *    right-aligned when it would run off the right edge, shifted inline when
 *    neither alignment fits (a phone), and placed again from its REAL measured
 *    box once it has rendered.
 */

import {
  PermissionChip,
  permissionMenuPlacement,
  PERMISSION_MENU_H,
  PERMISSION_MENU_W,
} from "@/components/chat/PermissionChip";
import {
  PERMISSION_LEVELS,
  PERMISSION_MODES,
  DEFAULT_PERMISSION_MODE,
  asPermissionMode,
} from "@/lib/permissionLevels";

const read = (...parts: string[]) =>
  readFileSync(join(process.cwd(), ...parts), "utf8").replace(/\r\n/g, "\n");

/** The `value:` strings inside the page's `const APPROVAL_MODES = [ ... ] as const;`. */
function pageModes(): string[] {
  const src = read("app", "chat", "page.tsx");
  const start = src.indexOf("const APPROVAL_MODES = [");
  expect(start, "page.tsx no longer declares APPROVAL_MODES").toBeGreaterThan(-1);
  const end = src.indexOf("] as const;", start);
  expect(end).toBeGreaterThan(start);
  const block = src.slice(start, end);
  return Array.from(block.matchAll(/value:\s*"([^"]+)"/g), (m) => m[1]);
}

/** The daemon's `APPROVAL_MODES = (...)` tuple in chat_turn.py. */
function daemonModes(): string[] {
  const src = read("..", "src", "iron_jarvis", "daemon", "chat_turn.py");
  const m = src.match(/^APPROVAL_MODES = \(([^)]*)\)/m);
  expect(m, "chat_turn.py no longer declares APPROVAL_MODES").not.toBeNull();
  return Array.from(m![1].matchAll(/"([^"]+)"/g), (x) => x[1]);
}

afterEach(() => {
  cleanup();
});

function setup(value = "approve_for_me", disabled = false) {
  const onChange = vi.fn();
  const utils = render(
    <div>
      <button type="button" data-testid="elsewhere">
        elsewhere
      </button>
      <PermissionChip value={value} onChange={onChange} disabled={disabled} />
    </div>,
  );
  const chip = screen.getByTestId("permission-chip");
  return { ...utils, onChange, chip };
}

function openWithClick(chip: HTMLElement) {
  fireEvent.click(chip);
  return screen.getByTestId("permission-menu");
}

describe("the levels table", () => {
  it("covers exactly the modes the chat page's select offers, in its order", () => {
    const fromPage = pageModes();
    expect(fromPage.length).toBeGreaterThan(0);
    expect([...PERMISSION_MODES]).toEqual(fromPage);
    expect(Object.keys(PERMISSION_LEVELS).sort()).toEqual([...fromPage].sort());
  });

  it("covers exactly the daemon's approval vocabulary", () => {
    expect([...PERMISSION_MODES].sort()).toEqual(daemonModes().sort());
  });

  it("gives every level a plain name and a one-line description", () => {
    for (const mode of PERMISSION_MODES) {
      const row = PERMISSION_LEVELS[mode];
      expect(row.label.trim().length).toBeGreaterThan(0);
      expect(row.label.length).toBeLessThanOrEqual(16);
      expect(row.description.trim().length).toBeGreaterThan(0);
      expect(row.description).not.toMatch(/\n/);
      // Plain words: no em-dash asides, no slang, no wire ids.
      expect(row.label + row.description).not.toMatch(/—|yolo|approve_for_me|always_ask/i);
    }
    expect(PERMISSION_LEVELS.always_ask.label).toBe("Ask first");
  });

  it("marks the riskiest level, and only it, amber", () => {
    expect(PERMISSION_LEVELS.yolo.tone).toBe("warn");
    expect(PERMISSION_MODES.filter((m) => PERMISSION_LEVELS[m].tone === "warn")).toEqual(["yolo"]);
  });

  it("reads an unknown value as the default, never the no-ask level", () => {
    expect(DEFAULT_PERMISSION_MODE).toBe("approve_for_me");
    expect(asPermissionMode("something-newer")).toBe("approve_for_me");
    expect(asPermissionMode(undefined)).toBe("approve_for_me");
    expect(asPermissionMode("yolo")).toBe("yolo");
  });
});

describe("the chip", () => {
  it("shows the current level's plain name as a quiet ghost", () => {
    const { chip } = setup("always_ask");
    expect(chip.textContent).toBe("Ask first");
    expect(chip.getAttribute("aria-label")).toBe("Permissions: Ask first");
    expect(chip.getAttribute("title")).toBe(PERMISSION_LEVELS.always_ask.description);
    expect(chip.getAttribute("aria-haspopup")).toBe("menu");
    expect(chip.getAttribute("aria-expanded")).toBe("false");
    // Shield + chevron icons ride beside the name.
    expect(chip.querySelectorAll("svg").length).toBe(2);
    const cls = chip.className;
    expect(cls).toContain("h-[30px]");
    expect(cls).toContain("bg-transparent");
    expect(cls).toMatch(/hover:bg-/);
    // No resting fill and no border: only hover fills.
    expect(cls.split(/\s+/).filter((c) => /^bg-/.test(c))).toEqual(["bg-transparent"]);
    expect(cls).not.toMatch(/(^|\s)border(\s|-)/);
  });

  it("reads amber in the no-ask level, through a tone token", () => {
    const { chip } = setup("yolo");
    expect(chip.dataset.tone).toBe("warn");
    expect(chip.className).toContain("text-tone-warn");
    expect(chip.className).not.toMatch(/amber-|yellow-/);
  });

  it("is quiet grey in the other levels", () => {
    const { chip } = setup("approve_for_me");
    expect(chip.dataset.tone).toBe("neutral");
    expect(chip.className).toContain("text-zinc-400");
    expect(chip.className).not.toContain("text-tone-warn");
  });

  it("an unknown value reads as the default level", () => {
    const { chip } = setup("from-a-newer-client");
    expect(chip.dataset.mode).toBe("approve_for_me");
    expect(chip.textContent).toBe(PERMISSION_LEVELS.approve_for_me.label);
  });

  it("disabled: nothing opens", () => {
    const { chip, onChange } = setup("approve_for_me", true);
    expect((chip as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(chip);
    fireEvent.keyDown(chip, { key: "ArrowDown" });
    expect(screen.queryByTestId("permission-menu")).toBeNull();
    expect(onChange).not.toHaveBeenCalled();
  });
});

describe("the menu", () => {
  it("lists every level with its description and checks the current one", () => {
    const { chip } = setup("always_ask");
    const menu = openWithClick(chip);
    expect(chip.getAttribute("aria-expanded")).toBe("true");
    expect(menu.getAttribute("role")).toBe("menu");
    const items = within(menu).getAllByTestId("permission-item");
    expect(items.map((el) => el.dataset.mode)).toEqual([...PERMISSION_MODES]);
    for (const el of items) {
      const row = PERMISSION_LEVELS[el.dataset.mode as keyof typeof PERMISSION_LEVELS];
      expect(el.getAttribute("role")).toBe("menuitemradio");
      expect(el.textContent).toContain(row.label);
      expect(el.textContent).toContain(row.description);
    }
    const checked = items.filter((el) => el.getAttribute("aria-checked") === "true");
    expect(checked.map((el) => el.dataset.mode)).toEqual(["always_ask"]);
    expect(within(checked[0]).getByTestId("permission-check")).toBeTruthy();
    expect(within(menu).getAllByTestId("permission-check")).toHaveLength(1);
    // The menu takes focus so its keys are read, starting on the current level.
    expect(document.activeElement).toBe(menu);
    expect(items[0].dataset.active).toBe("true");
    // Moving the keyboard does not move the check: it marks the SAVED level.
    fireEvent.keyDown(menu, { key: "ArrowDown" });
    const after = within(menu).getAllByTestId("permission-item");
    expect(after[1].dataset.active).toBe("true");
    expect(after.filter((el) => el.getAttribute("aria-checked") === "true").map((el) => el.dataset.mode)).toEqual([
      "always_ask",
    ]);
    expect(within(after[0]).getByTestId("permission-check")).toBeTruthy();
  });

  it("the no-ask row is amber in the menu too", () => {
    const { chip } = setup("approve_for_me");
    const menu = openWithClick(chip);
    const yolo = within(menu)
      .getAllByTestId("permission-item")
      .find((el) => el.dataset.mode === "yolo")!;
    expect(yolo.innerHTML).toContain("text-tone-warn");
    const ask = within(menu)
      .getAllByTestId("permission-item")
      .find((el) => el.dataset.mode === "always_ask")!;
    expect(ask.innerHTML).not.toContain("text-tone-warn");
  });

  it("a click picks a different level and closes", () => {
    const { chip, onChange } = setup("approve_for_me");
    const menu = openWithClick(chip);
    const yolo = within(menu)
      .getAllByTestId("permission-item")
      .find((el) => el.dataset.mode === "yolo")!;
    fireEvent.click(yolo);
    expect(onChange).toHaveBeenCalledTimes(1);
    expect(onChange).toHaveBeenCalledWith("yolo");
    expect(screen.queryByTestId("permission-menu")).toBeNull();
    expect(document.activeElement).toBe(chip);
  });

  it("picking the level already set calls nothing", () => {
    const { chip, onChange } = setup("approve_for_me");
    const menu = openWithClick(chip);
    const same = within(menu)
      .getAllByTestId("permission-item")
      .find((el) => el.dataset.mode === "approve_for_me")!;
    fireEvent.click(same);
    expect(onChange).not.toHaveBeenCalled();
    expect(screen.queryByTestId("permission-menu")).toBeNull();
  });

  it("keyboard: ArrowDown on the chip opens, arrows wrap, Enter picks", () => {
    const { chip, onChange } = setup("approve_for_me");
    fireEvent.keyDown(chip, { key: "ArrowDown" });
    const menu = screen.getByTestId("permission-menu");
    const active = () =>
      within(menu)
        .getAllByTestId("permission-item")
        .find((el) => el.dataset.active === "true")?.dataset.mode;
    expect(active()).toBe("approve_for_me");
    expect(menu.getAttribute("aria-activedescendant")).toBe(
      within(menu).getAllByTestId("permission-item")[1].id,
    );
    fireEvent.keyDown(menu, { key: "ArrowDown" });
    expect(active()).toBe("yolo");
    fireEvent.keyDown(menu, { key: "ArrowDown" });
    expect(active()).toBe("always_ask");
    fireEvent.keyDown(menu, { key: "ArrowUp" });
    expect(active()).toBe("yolo");
    fireEvent.keyDown(menu, { key: "Home" });
    expect(active()).toBe("always_ask");
    fireEvent.keyDown(menu, { key: "End" });
    expect(active()).toBe("yolo");
    fireEvent.keyDown(menu, { key: "ArrowUp" });
    fireEvent.keyDown(menu, { key: "ArrowUp" });
    expect(active()).toBe("always_ask");
    fireEvent.keyDown(menu, { key: "Enter" });
    expect(onChange).toHaveBeenCalledWith("always_ask");
    expect(screen.queryByTestId("permission-menu")).toBeNull();
    expect(document.activeElement).toBe(chip);
  });

  it("Esc closes, gives focus back and never reaches the page", () => {
    const pageKeys = vi.fn();
    document.addEventListener("keydown", pageKeys);
    try {
      const { chip, onChange } = setup("approve_for_me");
      const menu = openWithClick(chip);
      fireEvent.keyDown(menu, { key: "ArrowDown" });
      fireEvent.keyDown(menu, { key: "Escape" });
      expect(screen.queryByTestId("permission-menu")).toBeNull();
      expect(document.activeElement).toBe(chip);
      expect(onChange).not.toHaveBeenCalled();
      expect(pageKeys.mock.calls.some(([e]) => (e as KeyboardEvent).key === "Escape")).toBe(false);
    } finally {
      document.removeEventListener("keydown", pageKeys);
    }
  });

  it("a press outside closes it; a press on the chip again closes it", () => {
    const { chip, onChange } = setup("approve_for_me");
    openWithClick(chip);
    fireEvent.mouseDown(screen.getByTestId("elsewhere"));
    expect(screen.queryByTestId("permission-menu")).toBeNull();
    openWithClick(chip);
    fireEvent.mouseDown(chip);
    fireEvent.click(chip);
    expect(screen.queryByTestId("permission-menu")).toBeNull();
    expect(onChange).not.toHaveBeenCalled();
  });

  it("a turn starting under an open menu closes it", () => {
    const onChange = vi.fn();
    const { rerender } = render(<PermissionChip value="approve_for_me" onChange={onChange} />);
    fireEvent.click(screen.getByTestId("permission-chip"));
    expect(screen.getByTestId("permission-menu")).toBeTruthy();
    rerender(<PermissionChip value="approve_for_me" onChange={onChange} disabled />);
    expect(screen.queryByTestId("permission-menu")).toBeNull();
  });

  it("is placed from the chip's real position", () => {
    const { chip } = setup("approve_for_me");
    const W = window.innerWidth;
    const H = window.innerHeight;
    // Near the top-right corner: above has no room, the right edge is close.
    chip.getBoundingClientRect = () =>
      ({ top: 20, bottom: 50, left: W - 100, right: W - 10, width: 90, height: 30, x: W - 100, y: 20 }) as DOMRect;
    const menu = openWithClick(chip);
    expect(menu.dataset.side).toBe("below");
    expect(menu.dataset.align).toBe("end");
    expect(menu.className).toContain("top-full");
    expect(menu.className).toContain("right-0");
    expect(menu.className).toContain("max-w-[calc(100vw-2rem)]");
    fireEvent.keyDown(menu, { key: "Escape" });
    // Low on the left, where the composer sits: above, left-aligned.
    chip.getBoundingClientRect = () =>
      ({ top: H - 60, bottom: H - 30, left: 20, right: 110, width: 90, height: 30, x: 20, y: H - 60 }) as DOMRect;
    const again = openWithClick(chip);
    expect(again.dataset.side).toBe("above");
    expect(again.dataset.align).toBe("start");
    expect(again.className).toContain("bottom-full");
    expect(again.className).toContain("left-0");
  });
});

describe("permissionMenuPlacement", () => {
  const vp = { width: 1280, height: 800 };
  const chipAt = (top: number, left: number) => ({ top, bottom: top + 30, left, right: left + 120 });

  it("opens above when the composer sits low", () => {
    expect(permissionMenuPlacement(chipAt(700, 300), vp)).toEqual({ side: "above", align: "start" });
  });

  it("opens below when above has no room and below has more", () => {
    expect(permissionMenuPlacement(chipAt(40, 300), vp)).toEqual({ side: "below", align: "start" });
  });

  it("stays above when neither side fits but above has more room", () => {
    const short = { width: 1280, height: 300 };
    expect(permissionMenuPlacement(chipAt(170, 300), short).side).toBe("above");
  });

  it("aligns to the chip's right edge near the window's right edge", () => {
    expect(permissionMenuPlacement(chipAt(700, 1280 - 130), vp).align).toBe("end");
  });

  /** The menu's left and right edges in window px for a placement (w = the menu's rendered width). */
  function menuEdges(
    anchor: { left: number; right: number },
    viewportWidth: number,
    place: ReturnType<typeof permissionMenuPlacement>,
  ) {
    const w = Math.min(PERMISSION_MENU_W, viewportWidth - 32); // max-w-[calc(100vw-2rem)]
    const left =
      place.align === "start"
        ? anchor.left
        : place.align === "end"
          ? anchor.right - w
          : anchor.left + (place.left as number);
    return { left, right: left + w };
  }

  it("never runs past either edge of a phone, wherever the chip sits", () => {
    // 320/360/390 phones and a 300 window; every chip position and width the
    // composer can produce, including the 390px band (left 94-170) where
    // neither alignment fits.
    for (const width of [300, 320, 360, 390]) {
      const phone = { width, height: 700 };
      for (let left = 8; left <= width - 60; left += 2) {
        for (const chipW of [60, 96, 120]) {
          const right = Math.min(left + chipW, width - 8);
          const anchor = { top: 600, bottom: 630, left, right };
          const place = permissionMenuPlacement(anchor, phone);
          const edges = menuEdges(anchor, width, place);
          expect(edges.left, `${width}px, chip ${left}-${right}`).toBeGreaterThanOrEqual(8);
          expect(edges.right, `${width}px, chip ${left}-${right}`).toBeLessThanOrEqual(width - 8);
        }
      }
    }
  });

  it("shifts the menu on a 390px phone where neither alignment fits", () => {
    const phone = { width: 390, height: 700 };
    const anchor = { top: 600, bottom: 630, left: 130, right: 250 };
    const place = permissionMenuPlacement(anchor, phone);
    expect(place.align).toBe("shift");
    // 390 - 288 - 8 = 94 is the furthest right the menu's left edge may sit.
    expect(place.left).toBe(94 - 130);
    expect(menuEdges(anchor, 390, place)).toEqual({ left: 94, right: 382 });
  });

  it("uses the menu's real box", () => {
    expect(PERMISSION_MENU_W).toBe(288);
    // The guess covers the box Edge measured (three two-line rows, 226px).
    expect(PERMISSION_MENU_H).toBeGreaterThanOrEqual(226);
    // Exactly enough room above (menu height + the 8px edge) stays above.
    expect(permissionMenuPlacement(chipAt(PERMISSION_MENU_H + 8, 300), vp).side).toBe("above");
    expect(permissionMenuPlacement(chipAt(PERMISSION_MENU_H + 7, 300), { width: 1280, height: 2000 }).side).toBe(
      "below",
    );
    // A chip 220px from the top: the 226px box does not fit above.
    expect(permissionMenuPlacement(chipAt(220, 300), vp).side).toBe("below");
  });
});

describe("the menu, measured once it renders", () => {
  /** Stubs the box every rendered chip and menu reports, restoring in a finally. */
  function withBoxes(
    chip: Partial<DOMRect>,
    menu: { width: number; height: number },
    run: () => void,
    viewportWidth?: number,
  ) {
    const spy = vi
      .spyOn(HTMLElement.prototype, "getBoundingClientRect")
      .mockImplementation(function (this: HTMLElement) {
        const id = this.dataset.testid;
        if (id === "permission-chip") return { x: 0, y: 0, width: 0, height: 0, toJSON() {}, ...chip } as DOMRect;
        if (id === "permission-menu")
          return { x: 0, y: 0, top: 0, left: 0, bottom: menu.height, right: menu.width, toJSON() {}, ...menu } as DOMRect;
        return { x: 0, y: 0, top: 0, left: 0, bottom: 0, right: 0, width: 0, height: 0, toJSON() {} } as DOMRect;
      });
    const prevWidth = window.innerWidth;
    if (viewportWidth !== undefined)
      Object.defineProperty(window, "innerWidth", { configurable: true, writable: true, value: viewportWidth });
    try {
      run();
    } finally {
      spy.mockRestore();
      Object.defineProperty(window, "innerWidth", { configurable: true, writable: true, value: prevWidth });
    }
  }

  it("opens above once the real menu turns out to fit there", () => {
    // 200px from the top: the 232px guess says below; the real 150px box fits above.
    withBoxes({ top: 200, bottom: 230, left: 300, right: 400 }, { width: 288, height: 150 }, () => {
      const { chip } = setup("approve_for_me");
      const menu = openWithClick(chip);
      expect(menu.dataset.side).toBe("above");
      expect(menu.className).toContain("bottom-full");
    });
  });

  it("opens below once the real menu turns out too tall for above", () => {
    // 240px from the top: the 232px guess says above; a real 260px box would
    // lose 28px off the top, so it moves below.
    withBoxes({ top: 240, bottom: 270, left: 300, right: 400 }, { width: 288, height: 260 }, () => {
      const { chip } = setup("approve_for_me");
      const menu = openWithClick(chip);
      expect(menu.dataset.side).toBe("below");
      expect(menu.className).toContain("top-full");
    });
  });

  it("on a 320px phone the menu is shifted inline, inside the window", () => {
    withBoxes(
      { top: 600, bottom: 630, left: 150, right: 250 },
      { width: 288, height: 226 },
      () => {
        const { chip } = setup("approve_for_me");
        const menu = openWithClick(chip);
        expect(menu.dataset.align).toBe("shift");
        expect(menu.className).not.toMatch(/(^|\s)(left-0|right-0)(\s|$)/);
        const left = 150 + parseFloat(menu.style.left);
        // max-w-[calc(100vw-2rem)] holds the menu to 288px at 320 wide.
        expect(left).toBeGreaterThanOrEqual(8);
        expect(left + 288).toBeLessThanOrEqual(320 - 8);
      },
      320,
    );
  });
});
