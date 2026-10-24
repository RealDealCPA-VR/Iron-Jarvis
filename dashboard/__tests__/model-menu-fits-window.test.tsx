/**
 * The composer's model menu fits the window (the user's report, 2026-10-24:
 * "in chat the module, the model selection is cut off" — a new chat about
 * 1406 × 857, the menu opened upward and its top, with the "Type to find a
 * model" box and Recent, was cut off by the window's top and by the chat's
 * scrolling section).
 *
 *  1. lib/modelMenuPlacement: the placement maths — room above (ending at
 *     the title bar) and below the chip, the side chosen (above when the
 *     whole menu fits, else the roomier side), the height capped to that
 *     room, the menu kept inside the window's width.
 *  2. components/chat/ModelMenu: the menu is on the page's top layer (a
 *     portal, fixed at the chip — no parent's overflow can clip it), carries
 *     that cap, its rows scroll in a list while the filter box (and "Make
 *     this my default") stay outside it; it is placed again when the models
 *     load late and when the window changes size, and keeps its side while
 *     the user types.
 *  3. components/chat/FitPanel `float`: a provider's model panel, now inside
 *     a scrolling list, is fixed beside its row (shifted into the window when
 *     the left has no room) — and stays in the flow under the IJ harness's
 *     phone sheet.
 *
 * jsdom draws nothing, so each element's box is supplied: the chip's, the
 * title bar's, and the menu's natural height (a 40 px header plus 28 px per
 * row in the list) — what a browser would measure. The real browser checks
 * the same at four window sizes in e2e/model-menu-fit.spec.ts.
 */

import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { ModelMenu, modelTriggerOf } from "@/components/chat/ModelMenu";
import { EDGE_PX, FLOAT_GAP_PX, FitPanel, fitPlacement, floatEdges } from "@/components/chat/FitPanel";
import { companyMenu } from "@/lib/harnessModelMenu";
import {
  MENU_EDGE_PX,
  MENU_GAP_PX,
  MENU_WIDTH_PX,
  modelMenuPlacement,
} from "@/lib/modelMenuPlacement";
import type { ModelOption } from "@/lib/types";

/* ------------------------------------------------------------ the maths */

describe("modelMenuPlacement — where the menu goes", () => {
  const vp = { width: 1406, height: 857 };
  const chipAt = (top: number, right = 1060) => ({ top, bottom: top + 30, left: right - 120, right });

  it("opens above the chip when the whole menu fits there, capped to the room above", () => {
    const p = modelMenuPlacement(chipAt(700), vp, { width: 240, height: 300 });
    const roomAbove = 700 - MENU_GAP_PX - MENU_EDGE_PX;
    expect(p.side).toBe("above");
    expect(p.maxHeight).toBe(roomAbove);
    // Its BOTTOM hangs just over the chip, so its own height never moves it.
    expect(p.bottom).toBe(857 - 700 + MENU_GAP_PX);
    expect(p.top).toBeUndefined();
    // Right-aligned to the chip, as the menu always hung.
    expect(p.left).toBe(1060 - 240);
  });

  it("the report: a menu taller than the room above a centred chip is capped there — never past the top", () => {
    // 1406 × 857, the new chat's composer in the middle, a 40 px title bar.
    const p = modelMenuPlacement(chipAt(457), vp, { width: 240, height: 572 }, { topLimit: 40 });
    const roomAbove = 457 - MENU_GAP_PX - 40 - MENU_EDGE_PX; // 403
    const roomBelow = 857 - 487 - MENU_GAP_PX - MENU_EDGE_PX; // 356
    expect(roomAbove).toBeGreaterThan(roomBelow);
    expect(p).toEqual({ side: "above", maxHeight: roomAbove, bottom: 857 - 457 + MENU_GAP_PX, left: 820 });
    // The menu's top: the window's bottom - bottom - height, at most.
    expect(857 - (p.bottom ?? 0) - p.maxHeight).toBeGreaterThanOrEqual(40 + MENU_EDGE_PX);
  });

  it("opens below when it does not fit above and there is more room below, capped to that room", () => {
    const p = modelMenuPlacement(chipAt(300), vp, { width: 240, height: 572 }, { topLimit: 40 });
    const roomBelow = 857 - 330 - MENU_GAP_PX - MENU_EDGE_PX;
    expect(p).toEqual({ side: "below", maxHeight: roomBelow, top: 330 + MENU_GAP_PX, left: 820 });
  });

  it("the room above ends at the title bar, not at the window's top", () => {
    const bare = modelMenuPlacement(chipAt(600), vp, { width: 240, height: 900 });
    const barred = modelMenuPlacement(chipAt(600), vp, { width: 240, height: 900 }, { topLimit: 40 });
    expect(bare.maxHeight - barred.maxHeight).toBe(40);
    expect(barred.maxHeight).toBe(600 - MENU_GAP_PX - 40 - MENU_EDGE_PX);
  });

  it("keepSide keeps the side it opened on (a scroll moving the chip never flips it)", () => {
    const p = modelMenuPlacement(chipAt(700), vp, { width: 240, height: 100 }, { keepSide: "below" });
    expect(p.side).toBe("below");
    expect(p.maxHeight).toBe(857 - 730 - MENU_GAP_PX - MENU_EDGE_PX);
  });

  it("stays inside the window's width: shifted right at a phone's left edge, left at the right edge", () => {
    const phone = { width: 390, height: 844 };
    expect(modelMenuPlacement({ top: 700, bottom: 730, left: 100, right: 200 }, phone, { width: 240, height: 100 }).left).toBe(
      MENU_EDGE_PX,
    );
    expect(modelMenuPlacement({ top: 700, bottom: 730, left: 1300, right: 1500 }, vp, { width: 240, height: 100 }).left).toBe(
      1406 - MENU_EDGE_PX - 240,
    );
    // A window narrower than the menu: its edges 8 px in on both sides.
    expect(modelMenuPlacement({ top: 500, bottom: 530, left: 10, right: 150 }, { width: 200, height: 600 }, { width: 240, height: 100 }).left).toBe(
      MENU_EDGE_PX,
    );
  });

  it("never reports a negative height", () => {
    const p = modelMenuPlacement({ top: -50, bottom: -20, left: 0, right: 0 }, { width: 100, height: 10 }, { width: 240, height: 500 });
    expect(p.maxHeight).toBeGreaterThanOrEqual(0);
  });
});

describe("FitPanel's maths — the title bar and a floating panel's edges", () => {
  it("fitPlacement's room above ends at the title bar", () => {
    expect(fitPlacement(250, 280, 900, 384, 40)).toEqual({ side: "up", maxHeight: 280 - 40 - EDGE_PX });
    // Unchanged without it (v1.350.1).
    expect(fitPlacement(250, 280, 900, 384)).toEqual({ side: "up", maxHeight: 280 - EDGE_PX });
  });

  it("a floating panel sits beside its row on the left, or shifted into the window when the left has no room", () => {
    const up = { side: "up" as const, maxHeight: 300 };
    expect(floatEdges({ top: 400, bottom: 428, left: 800 }, up, 1406, 857, 224)).toEqual({
      bottom: 857 - 428,
      right: 1406 - 800,
      align: "beside",
    });
    const down = { side: "down" as const, maxHeight: 300 };
    // A phone: the menu sits by the right edge and the panel cannot fit left
    // of the row — it is shifted to stay inside the window.
    expect(floatEdges({ top: 100, bottom: 128, left: 150 }, down, 390, 844, 224)).toEqual({
      top: 100,
      left: EDGE_PX,
      align: "shifted",
    });
    expect(150 - FLOAT_GAP_PX - 224).toBeLessThan(EDGE_PX);
  });
});

/* ------------------------------------------------- the component, laid out */

type Box = { top: number; bottom: number; left: number; right: number };
const ZERO: Box = { top: 0, bottom: 0, left: 0, right: 0 };
const L = {
  vw: 1406,
  vh: 857,
  chip: ZERO as Box,
  bar: null as number | null,
  rows: new Map<string, Box>(),
  /** A row whose box follows the MENU's own edges as they are drawn — what a
   *  browser does: the row moves when the menu's style has changed, not
   *  before (review: the side panel read its row before the menu moved). */
  rowsInMenu: new Map<string, (menu: HTMLElement) => Box>(),
};
const asRect = (b: Box) =>
  ({ ...b, width: b.right - b.left, height: b.bottom - b.top, x: b.left, y: b.top, toJSON: () => ({}) }) as DOMRect;

const saved: Record<string, PropertyDescriptor | undefined> = {};
function defineLayout() {
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (this: HTMLElement) {
    if (this.getAttribute("title") === "Switch model") return asRect(L.chip);
    if (this.hasAttribute("data-ij-titlebar")) {
      return asRect(L.bar === null ? ZERO : { top: 0, bottom: L.bar, left: 0, right: L.vw });
    }
    const row = this.getAttribute("data-row");
    if (row && L.rows.has(row)) return asRect(L.rows.get(row) as Box);
    const inMenu = row ? L.rowsInMenu.get(row) : undefined;
    const menuEl = document.querySelector<HTMLElement>('[data-testid="model-menu"]');
    if (inMenu && menuEl) return asRect(inMenu(menuEl));
    return asRect(ZERO);
  });
  const list = (el: HTMLElement) => el.getAttribute("data-testid") === "model-menu-list";
  const define = (name: string, get: (el: HTMLElement) => number | undefined) => {
    saved[name] = Object.getOwnPropertyDescriptor(HTMLElement.prototype, name);
    const fallback = Object.getOwnPropertyDescriptor(HTMLElement.prototype, name) ?? Object.getOwnPropertyDescriptor(Element.prototype, name);
    Object.defineProperty(HTMLElement.prototype, name, {
      configurable: true,
      get(this: HTMLElement) {
        const v = get(this);
        return v === undefined ? (fallback?.get?.call(this) ?? 0) : v;
      },
    });
  };
  // The menu's natural height: a 40 px header (filter box, footer) plus
  // 28 px for every row in its list — what a browser measures.
  define("offsetHeight", (el) => (el.getAttribute("data-testid") === "model-menu" ? 40 : undefined));
  define("offsetWidth", (el) =>
    el.getAttribute("data-testid") === "model-menu" ? MENU_WIDTH_PX : el.getAttribute("data-testid") === "model-sub" ? 224 : undefined,
  );
  define("scrollHeight", (el) => (list(el) ? 28 * el.querySelectorAll("button").length : undefined));
  define("clientHeight", (el) => (list(el) ? 0 : undefined));
  setWindow(L.vw, L.vh);
}
function setWindow(w: number, h: number) {
  L.vw = w;
  L.vh = h;
  Object.defineProperty(window, "innerWidth", { configurable: true, writable: true, value: w });
  Object.defineProperty(window, "innerHeight", { configurable: true, writable: true, value: h });
}
function undefineLayout() {
  for (const name of Object.keys(saved)) {
    const d = saved[name];
    if (d) Object.defineProperty(HTMLElement.prototype, name, d);
    else delete (HTMLElement.prototype as unknown as Record<string, unknown>)[name];
    delete saved[name];
  }
}

const row = (provider: string, model: string, kind: string, extra: Partial<ModelOption> = {}): ModelOption =>
  ({ provider, model, kind, available: true, inherited_from: null, ...extra }) as ModelOption;
/** 15 providers, three of them offline: as tall as the user's. */
const TALL: ModelOption[] = [
  row("fleet-rtx", "glm-5.3-flash", "local", { name: "RTX6000ada", on_this_network: true }),
  row("ollama", "qwen3:8b", "local", { on_this_network: true, size_b: 8 }),
  row("fleet", "fleet", "local", { name: "Your model server", on_this_network: true }),
  row("opencode-cli", "opencode/big-pickle", "cli"),
  row("claude-cli", "claude-opus-4-8", "cli", { label: "Opus 4.8" }),
  row("codex-cli", "gpt-5.5", "cli"),
  row("grok-cli", "grok-4.7", "cli"),
  row("anthropic", "claude-haiku-4-5", "api"),
  row("openai", "gpt-5.5", "api"),
  row("openrouter", "vendor/model-1", "api"),
  row("deepseek", "deepseek-chat", "api"),
  row("mistral", "mistral-large", "api"),
  row("custom", "my-model", "local", { available: false, name: "Custom endpoint" }),
  row("google", "gemini-3.5-pro", "api", { available: false }),
  row("xai", "grok-4", "api", { available: false }),
];

function Menu({ models, harness = false }: { models: ModelOption[]; harness?: boolean }) {
  const [open, setOpen] = useState(false);
  const [choice, setChoice] = useState("fleet-rtx::glm-5.3-flash");
  return (
    <div>
      <p data-testid="outside">outside</p>
      <ModelMenu
        open={open}
        onOpenChange={setOpen}
        choice={choice}
        models={models}
        trigger={modelTriggerOf(choice, models, "", "")}
        onPick={(v) => setChoice(v)}
        onMakeDefault={() => {}}
        companies={harness ? companyMenu(models, null, null, choice) : undefined}
      />
    </div>
  );
}

function openMenu() {
  fireEvent.click(screen.getByTitle("Switch model"));
  return screen.getByTestId("model-menu");
}

describe("ModelMenu — the menu is on the top layer, capped to the room, its rows scroll under the filter", () => {
  beforeEach(() => {
    window.localStorage.clear();
    L.vw = 1406;
    L.vh = 857;
    L.chip = ZERO;
    L.bar = null;
    L.rows.clear();
    L.rowsInMenu.clear();
    defineLayout();
  });
  afterEach(() => {
    cleanup();
    undefineLayout();
    vi.restoreAllMocks();
    document.querySelectorAll("[data-ij-titlebar]").forEach((n) => n.remove());
    delete (window as { matchMedia?: unknown }).matchMedia;
  });

  function titleBar(bottom: number) {
    const bar = document.createElement("header");
    bar.setAttribute("data-ij-titlebar", "");
    document.body.prepend(bar);
    L.bar = bottom;
  }

  it("the report (1406 × 857, a centred chip): portaled, fixed above the chip, capped below the title bar", () => {
    titleBar(40);
    L.chip = { top: 457, bottom: 487, left: 940, right: 1060 };
    render(<Menu models={TALL} />);
    const menu = openMenu();
    // On the page's top layer: no parent's overflow can clip it.
    expect(menu.parentElement).toBe(document.body);
    expect(menu.className.split(/\s+/)).toEqual(expect.arrayContaining(["fixed", "flex", "flex-col"]));
    expect(menu.className).not.toMatch(/\bbottom-full\b/);
    expect(menu.dataset.side).toBe("above");
    expect(menu.style.maxHeight).toBe(`${457 - MENU_GAP_PX - 40 - MENU_EDGE_PX}px`);
    expect(menu.style.bottom).toBe(`${857 - 457 + MENU_GAP_PX}px`);
    expect(menu.style.left).toBe(`${1060 - MENU_WIDTH_PX}px`);
  });

  it("the rows scroll in a list; the filter box and 'Make this my default' stay outside it", () => {
    L.chip = { top: 457, bottom: 487, left: 940, right: 1060 };
    render(<Menu models={TALL} />);
    const menu = openMenu();
    const list = within(menu).getByTestId("model-menu-list");
    expect(list.className.split(/\s+/)).toEqual(expect.arrayContaining(["min-h-0", "overflow-y-auto"]));
    const filter = within(menu).getByTestId("model-filter");
    expect(list.contains(filter)).toBe(false);
    expect(filter.parentElement).toBe(menu);
    expect(list.contains(within(menu).getByTestId("model-make-default"))).toBe(false);
    // Every provider row is in the list.
    for (const name of ["Claude Code", "OpenRouter", "Custom endpoint"]) {
      expect(list.contains(within(menu).getByText(name))).toBe(true);
    }
    // The filter box takes the focus as the menu opens.
    expect(document.activeElement).toBe(filter);
  });

  it("a chip high in the window opens the menu below it, capped to the room below", () => {
    titleBar(40);
    L.chip = { top: 100, bottom: 130, left: 940, right: 1060 };
    render(<Menu models={TALL} />);
    const menu = openMenu();
    expect(menu.dataset.side).toBe("below");
    expect(menu.style.top).toBe(`${130 + MENU_GAP_PX}px`);
    expect(menu.style.maxHeight).toBe(`${857 - 130 - MENU_GAP_PX - MENU_EDGE_PX}px`);
    expect(menu.style.bottom).toBe("");
  });

  it("models that load after the menu opened choose the side again", () => {
    titleBar(40);
    L.chip = { top: 300, bottom: 330, left: 940, right: 1060 };
    const { rerender } = render(<Menu models={[]} />);
    let menu = openMenu();
    // Only "default model": it fits above.
    expect(menu.dataset.side).toBe("above");
    rerender(<Menu models={TALL} />);
    menu = screen.getByTestId("model-menu");
    // 15 providers: too tall for the 246 px above, more room below.
    expect(menu.dataset.side).toBe("below");
    expect(menu.style.maxHeight).toBe(`${857 - 330 - MENU_GAP_PX - MENU_EDGE_PX}px`);
  });

  it("typing in the filter keeps the side it opened on, even when the page scrolls", () => {
    titleBar(40);
    L.chip = { top: 300, bottom: 330, left: 940, right: 1060 };
    render(<Menu models={TALL} />);
    const menu = openMenu();
    expect(menu.dataset.side).toBe("below");
    fireEvent.change(within(menu).getByTestId("model-filter"), { target: { value: "opus" } });
    // Two rows now fit above — but the menu does not jump over the chip.
    act(() => {
      window.dispatchEvent(new Event("scroll"));
    });
    expect(screen.getByTestId("model-menu").dataset.side).toBe("below");
  });

  it("reopened after a typed word, it is placed for the WHOLE menu (the box is emptied before it is measured)", () => {
    titleBar(40);
    L.chip = { top: 300, bottom: 330, left: 940, right: 1060 };
    render(<Menu models={TALL} />);
    let menu = openMenu();
    expect(menu.dataset.side).toBe("below");
    fireEvent.change(within(menu).getByTestId("model-filter"), { target: { value: "opus" } });
    fireEvent.keyDown(within(menu).getByTestId("model-filter"), { key: "Escape" });
    expect(screen.queryByTestId("model-menu")).toBeNull();
    menu = openMenu();
    // The box is empty and every provider is listed — and the side was
    // chosen for that, not for the one row "opus" had left.
    expect((within(menu).getByTestId("model-filter") as HTMLInputElement).value).toBe("");
    expect(within(menu).getByText("OpenRouter")).toBeTruthy();
    expect(menu.dataset.side).toBe("below");
    expect(menu.style.maxHeight).toBe(`${857 - 330 - MENU_GAP_PX - MENU_EDGE_PX}px`);
  });

  it("a window resize places it again", () => {
    titleBar(40);
    L.chip = { top: 600, bottom: 630, left: 940, right: 1060 };
    render(<Menu models={TALL} />);
    const menu = openMenu();
    expect(menu.style.maxHeight).toBe(`${600 - MENU_GAP_PX - 40 - MENU_EDGE_PX}px`);
    // The window gets shorter; the composer (and its chip) moves up with it.
    setWindow(1406, 700);
    L.chip = { top: 443, bottom: 473, left: 940, right: 1060 };
    act(() => {
      window.dispatchEvent(new Event("resize"));
    });
    expect(menu.style.maxHeight).toBe(`${443 - MENU_GAP_PX - 40 - MENU_EDGE_PX}px`);
    expect(menu.style.bottom).toBe(`${700 - 443 + MENU_GAP_PX}px`);
  });

  it("a press inside the portaled menu keeps it open; a press elsewhere closes it", () => {
    L.chip = { top: 457, bottom: 487, left: 940, right: 1060 };
    render(<Menu models={TALL} />);
    const menu = openMenu();
    fireEvent.mouseDown(within(menu).getByTestId("model-menu-list"));
    expect(screen.getByTestId("model-menu")).toBeTruthy();
    fireEvent.mouseDown(screen.getByTestId("outside"));
    expect(screen.queryByTestId("model-menu")).toBeNull();
  });

  it("Tab keeps the order it had beside the chip: Shift+Tab from the box goes back to it, Tab from the last row moves on from it", () => {
    L.chip = { top: 600, bottom: 630, left: 940, right: 1060 };
    render(<Menu models={TALL} />);
    const menu = openMenu();
    const chip = screen.getByTitle("Switch model");
    const filter = within(menu).getByTestId("model-filter");
    expect(document.activeElement).toBe(filter);
    fireEvent.keyDown(filter, { key: "Tab", shiftKey: true });
    expect(document.activeElement).toBe(chip);
    expect(screen.getByTestId("model-menu")).toBeTruthy();
    const last = within(menu).getByTestId("model-make-default");
    last.focus();
    // jsdom does not move focus on Tab: the handler puts it on the chip,
    // and a browser's own Tab then moves on to the next control.
    fireEvent.keyDown(last, { key: "Tab" });
    expect(document.activeElement).toBe(chip);
    // A Tab in the middle of the menu is the browser's.
    const middle = within(menu).getByText("default model");
    middle.focus();
    fireEvent.keyDown(middle, { key: "Tab" });
    expect(document.activeElement).toBe(middle);
  });

  it("a provider's models float beside its row (fixed), outside the scrolling list's clip", () => {
    L.chip = { top: 600, bottom: 630, left: 940, right: 1060 };
    render(<Menu models={TALL} />);
    const menu = openMenu();
    const claude = within(menu)
      .getAllByRole("button")
      .find((b) => b.getAttribute("aria-expanded") !== null && b.textContent?.startsWith("Claude Code")) as HTMLElement;
    // The row's box (its wrapper is the panel's anchor).
    claude.parentElement?.setAttribute("data-row", "claude");
    L.rows.set("claude", { top: 400, bottom: 428, left: 825, right: 1055 });
    fireEvent.click(claude);
    const sub = screen.getByTestId("model-sub");
    expect(sub.style.position).toBe("fixed");
    expect(sub.dataset.align).toBe("beside");
    expect(sub.style.right).toBe(`${1406 - 825}px`);
    expect(sub.dataset.side).toBe("up");
    expect(sub.style.bottom).toBe(`${857 - 428}px`);
  });

  it("the IJ harness: Ctrl+K in the menu's box stays in the menu (the app's palette never sees it)", () => {
    L.chip = { top: 600, bottom: 630, left: 940, right: 1060 };
    const seen: string[] = [];
    const onKey = (e: KeyboardEvent) => seen.push(e.key);
    window.addEventListener("keydown", onKey);
    try {
      render(<Menu models={TALL} harness />);
      const menu = openMenu();
      fireEvent.keyDown(within(menu).getByTestId("model-filter"), { key: "k", ctrlKey: true });
      expect(seen).toEqual([]);
      expect(screen.getByTestId("model-menu")).toBeTruthy();
    } finally {
      window.removeEventListener("keydown", onKey);
    }
  });

  it("the IJ harness: a pick hands the focus back to the chip (its keys are read inside the harness)", () => {
    L.chip = { top: 600, bottom: 630, left: 940, right: 1060 };
    render(<Menu models={TALL} harness />);
    const menu = openMenu();
    fireEvent.click(within(menu).getByText("default model"));
    expect(screen.queryByTestId("model-menu")).toBeNull();
    expect(document.activeElement).toBe(screen.getByTitle("Switch model"));
  });

  it("the IJ harness's phone sheet keeps its own place (no fixed edges), the filter still outside the list", () => {
    window.matchMedia = ((q: string) => ({
      matches: q === "(max-width: 639.98px)",
      media: q,
      addEventListener: () => {},
      removeEventListener: () => {},
    })) as unknown as typeof window.matchMedia;
    setWindow(390, 844);
    L.chip = { top: 780, bottom: 810, left: 200, right: 320 };
    render(<Menu models={TALL} harness />);
    const menu = openMenu();
    expect(menu.parentElement).toBe(document.body);
    expect(menu.className.split(/\s+/)).toEqual(
      expect.arrayContaining(["max-sm:fixed", "max-sm:inset-x-2", "max-sm:bottom-2", "max-sm:max-h-[70dvh]"]),
    );
    expect(menu.style.top).toBe("");
    expect(menu.style.bottom).toBe("");
    expect(menu.style.left).toBe("");
    expect(menu.style.maxHeight).toBe("");
    const list = within(menu).getByTestId("model-menu-list");
    expect(list.contains(within(menu).getByTestId("model-filter"))).toBe(false);
  });
});

describe("FitPanel float", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    delete (window as { matchMedia?: unknown }).matchMedia;
  });

  function rowAt(top: number, bottom: number, left: number) {
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(
      () => asRect({ top, bottom, left, right: left + 200 }),
    );
  }

  it("is fixed at its row, beside it, when the row has room on its left", () => {
    rowAt(400, 428, 700);
    render(
      <div className="relative">
        <FitPanel testId="model-sub" className="absolute right-full mr-1 w-56" float>
          <span>m</span>
        </FitPanel>
      </div>,
    );
    const panel = screen.getByTestId("model-sub");
    expect(panel.style.position).toBe("fixed");
    expect(panel.dataset.align).toBe("beside");
    expect(panel.style.right).toBe(`${window.innerWidth - 700}px`);
  });

  it("floats on a wide window, and stays in the flow under `inlineQuery` (the harness's phone sheet), as before", () => {
    let phone = false;
    window.matchMedia = ((q: string) => ({ matches: phone && q === "(max-width: 639.98px)", media: q })) as unknown as typeof window.matchMedia;
    rowAt(400, 428, 700);
    const panelIn = () => (
      <div className="relative">
        <FitPanel testId="model-sub" className="absolute right-full max-sm:static" float inlineQuery="(max-width: 639.98px)">
          <span>m</span>
        </FitPanel>
      </div>
    );
    render(panelIn());
    expect(screen.getByTestId("model-sub").style.position).toBe("fixed");
    cleanup();
    phone = true;
    render(panelIn());
    const panel = screen.getByTestId("model-sub");
    expect(panel.style.position).toBe("");
    expect(panel.dataset.align).toBeUndefined();
    expect(panel.style.maxHeight).not.toBe("");
  });
});

/* ------------------------------------------------------------ the review */

describe("ModelMenu review — it stays with its chip, keeps its keys, and its panel follows it", () => {
  beforeEach(() => {
    window.localStorage.clear();
    L.vw = 1406;
    L.vh = 857;
    L.chip = ZERO;
    L.bar = null;
    L.rows.clear();
    L.rowsInMenu.clear();
    defineLayout();
  });
  afterEach(() => {
    cleanup();
    undefineLayout();
    vi.restoreAllMocks();
    delete (window as { matchMedia?: unknown }).matchMedia;
  });

  /** Two of the browser's frames, React's updates flushed. */
  const frames = () =>
    act(async () => {
      for (let i = 0; i < 2; i++) await new Promise<void>((r) => requestAnimationFrame(() => r()));
    });

  it("follows the chip when the page moves it WITHOUT a scroll or a resize (the demo strip goes away)", async () => {
    L.chip = { top: 600, bottom: 630, left: 940, right: 1060 };
    render(<Menu models={TALL} />);
    const menu = openMenu();
    expect(menu.style.bottom).toBe(`${857 - 600 + MENU_GAP_PX}px`);
    // "Make this my default" ends the demo strip: everything under it moves
    // up 40 px. No scroll, no resize, the chip keeps its size.
    L.chip = { top: 560, bottom: 590, left: 940, right: 1060 };
    await frames();
    expect(menu.style.bottom).toBe(`${857 - 560 + MENU_GAP_PX}px`);
    expect(menu.style.maxHeight).toBe(`${560 - MENU_GAP_PX - MENU_EDGE_PX}px`);
    expect(menu.dataset.side).toBe("above");
  });

  it("closes when the chip it was opened from goes away (a question takes the composer's place)", async () => {
    L.chip = { top: 600, bottom: 630, left: 940, right: 1060 };
    render(<Menu models={TALL} />);
    openMenu();
    await frames();
    L.chip = ZERO;
    await frames();
    expect(screen.queryByTestId("model-menu")).toBeNull();
  });

  it("an open provider panel moves WITH the menu when the chip moves (it is placed again after the menu, not before)", async () => {
    L.chip = { top: 600, bottom: 630, left: 940, right: 1060 };
    render(<Menu models={TALL} />);
    const menu = openMenu();
    const claude = within(menu)
      .getAllByRole("button")
      .find((b) => b.getAttribute("aria-expanded") !== null && b.textContent?.startsWith("Claude Code")) as HTMLElement;
    claude.parentElement?.setAttribute("data-row", "claude");
    // The row sits 5 px into the menu, 200 px above its bottom edge — and
    // moves only when the menu's own edges have changed, as in a browser.
    L.rowsInMenu.set("claude", (m) => {
      const left = parseFloat(m.style.left) + 5;
      const bottom = L.vh - parseFloat(m.style.bottom) - 200;
      return { top: bottom - 28, bottom, left, right: left + 230 };
    });
    fireEvent.click(claude);
    await frames();
    expect(screen.getByTestId("model-sub").style.bottom).toBe(`${857 - (600 - MENU_GAP_PX - 200)}px`);
    // The demo strip goes: the chip (and with it the menu) moves up 40 px.
    L.chip = { top: 560, bottom: 590, left: 940, right: 1060 };
    await frames();
    expect(menu.style.bottom).toBe(`${857 - 560 + MENU_GAP_PX}px`);
    // Beside its row where the row is NOW (40 px higher), not where it was.
    expect(screen.getByTestId("model-sub").style.bottom).toBe(`${857 - (560 - MENU_GAP_PX - 200)}px`);
    expect(screen.getByTestId("model-sub").style.right).toBe(`${1406 - (1060 - MENU_WIDTH_PX + 5)}px`);
  });

  it("the IJ harness: Ctrl+K from a ROW of the menu stays in the menu too, not only from its box", () => {
    L.chip = { top: 600, bottom: 630, left: 940, right: 1060 };
    const seen: string[] = [];
    const onKey = (e: KeyboardEvent) => seen.push(e.key);
    window.addEventListener("keydown", onKey);
    try {
      render(<Menu models={TALL} harness />);
      const menu = openMenu();
      const row = within(menu).getByText("default model");
      row.focus();
      fireEvent.keyDown(row, { key: "k", ctrlKey: true });
      expect(seen).toEqual([]);
      expect(screen.getByTestId("model-menu")).toBeTruthy();
      // The CHAT's menu leaves Ctrl+K to the app's palette, as it always did.
      cleanup();
      render(<Menu models={TALL} />);
      const chatRow = within(openMenu()).getByText("default model");
      chatRow.focus();
      fireEvent.keyDown(chatRow, { key: "k", ctrlKey: true });
      expect(seen).toEqual(["k"]);
    } finally {
      window.removeEventListener("keydown", onKey);
    }
  });

  it("the open chip names its menu (aria-controls): on the top layer the menu is not the element after the chip any more", () => {
    L.chip = { top: 600, bottom: 630, left: 940, right: 1060 };
    render(<Menu models={TALL} />);
    const chip = screen.getByTitle("Switch model");
    expect(chip.getAttribute("aria-controls")).toBeNull();
    const menu = openMenu();
    expect(menu.id).not.toBe("");
    expect(chip.getAttribute("aria-controls")).toBe(menu.id);
    expect(document.getElementById(menu.id)).toBe(menu);
  });

  it("an IME finishing a word in the box: its Enter picks nothing and its Escape closes nothing", () => {
    L.chip = { top: 600, bottom: 630, left: 940, right: 1060 };
    render(<Menu models={TALL} />);
    const filter = within(openMenu()).getByTestId("model-filter");
    fireEvent.change(filter, { target: { value: "opus" } });
    fireEvent.keyDown(filter, { key: "Enter", keyCode: 229, isComposing: true });
    fireEvent.keyDown(filter, { key: "Escape", keyCode: 229, isComposing: true });
    expect(screen.getByTestId("model-menu")).toBeTruthy();
    expect(screen.getByTestId("model-chip-words").textContent).toBe("glm-5.3-flash");
    // The word is in: a plain Enter picks the first match.
    fireEvent.keyDown(filter, { key: "Enter" });
    expect(screen.queryByTestId("model-menu")).toBeNull();
    expect(screen.getByTestId("model-chip-words").textContent).toBe("Opus 4.8");
  });

  it("on a phone the chat's provider panel opens UNDER its row inside the menu (as the harness's does), not over the rows", () => {
    window.matchMedia = ((q: string) => ({
      matches: q === "(max-width: 639.98px)",
      media: q,
      addEventListener: () => {},
      removeEventListener: () => {},
    })) as unknown as typeof window.matchMedia;
    setWindow(390, 844);
    L.chip = { top: 600, bottom: 630, left: 160, right: 282 };
    const had = Object.getOwnPropertyDescriptor(Element.prototype, "scrollIntoView");
    const scrolled: Element[] = [];
    Element.prototype.scrollIntoView = function (this: Element) {
      scrolled.push(this);
    };
    try {
      render(<Menu models={TALL} />);
      const menu = openMenu();
      const claude = within(menu)
        .getAllByRole("button")
        .find((b) => b.getAttribute("aria-expanded") !== null && b.textContent?.startsWith("Claude Code")) as HTMLElement;
      fireEvent.click(claude);
      const sub = screen.getByTestId("model-sub");
      expect(sub.style.position).toBe("");
      expect(sub.dataset.align).toBeUndefined();
      expect(sub.className.split(/\s+/)).toEqual(expect.arrayContaining(["max-sm:static", "max-sm:w-full"]));
      expect(within(menu).getByTestId("model-menu-list").contains(sub)).toBe(true);
      // Brought into view inside the list.
      expect(scrolled).toContain(sub);
    } finally {
      if (had) Object.defineProperty(Element.prototype, "scrollIntoView", had);
      else delete (Element.prototype as unknown as Record<string, unknown>).scrollIntoView;
    }
  });
});
