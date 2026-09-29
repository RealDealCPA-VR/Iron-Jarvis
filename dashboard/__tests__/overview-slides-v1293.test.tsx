/**
 * v1.293.0 — the Overview desktop is three swipeable screens of ten
 * (Office, Operations, System), not one wall of thirty.
 *
 * Pinned here, because each is a promise the user relies on:
 *
 * * every tile has exactly one home, no screen holds more than ten, and a
 *   page nobody placed still shows (on a trailing "More" screen) rather than
 *   vanishing — the v1.151.1 lesson;
 * * the order INSIDE a screen is the order it always was (arrangement, then
 *   most-used, then the catalogue), and an arrangement saved before this
 *   version renders the same relative order;
 * * a rearrangement is still ONE saved list, so the storage shape is unchanged;
 * * the screens change by tab, chevron, arrow key, horizontal wheel and swipe —
 *   and NEVER by releasing a dragged tile, which is a drop.
 *
 * The component tests run the REAL grid (dnd-kit's PointerSensor on jsdom,
 * tile rectangles stubbed), inside the app's own MotionProvider so the slide
 * transition runs the way it does in the app.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import { AppGrid } from "@/components/overview/AppGrid";
import { MotionProvider } from "@/components/MotionProvider";
import {
  SLIDE_SIZE,
  TILE_GROUPS,
  UNGROUPED,
  allTiles,
  groupOf,
  orderedTiles,
  readSlide,
  reorderWithinSlide,
  slides,
  writeSlide,
  type AppTile,
} from "@/lib/appTiles";

// --- the rules, on the pure helpers -----------------------------------------

describe("the groups", () => {
  it("are Office, Operations and System, each named by an icon", () => {
    expect(TILE_GROUPS.map((g) => g.label)).toEqual(["Office", "Operations", "System"]);
    for (const g of TILE_GROUPS) {
      expect(g.icon).toBeTruthy();
      expect(g.hint).toBeTruthy();
      expect(g.key).toMatch(/^[a-z]+$/);
    }
  });

  it("give every desktop tile exactly one home", () => {
    const placed = TILE_GROUPS.flatMap((g) => g.hrefs);
    const tiles = allTiles().map((t) => t.href);
    // No duplicates across groups.
    expect(new Set(placed).size).toBe(placed.length);
    // Nothing placed that is not a tile (a stale href would be a silent no-op).
    for (const href of placed) expect(tiles).toContain(href);
    // Every tile placed: the "More" screen never appears for the real catalogue.
    for (const href of tiles) expect(groupOf(href)).not.toBe(UNGROUPED);
    expect(slides(orderedTiles()).map((s) => s.group.key)).toEqual(
      TILE_GROUPS.map((g) => g.key),
    );
  });

  it("hold at most ten tiles each — the ask, and one row of the widest grid", () => {
    expect(SLIDE_SIZE).toBe(10);
    for (const s of slides(orderedTiles())) {
      expect(s.tiles.length).toBeLessThanOrEqual(SLIDE_SIZE);
      expect(s.tiles.length).toBeGreaterThan(0);
    }
  });

  it("show a page nobody placed on a trailing More screen instead of hiding it", () => {
    const stray: AppTile = {
      ...allTiles()[0],
      href: "/added-in-a-later-version",
      label: "New page",
      opens: 0,
      section: "Work",
    };
    const out = slides([...orderedTiles(), stray]);
    expect(out[out.length - 1].group).toBe(UNGROUPED);
    expect(out[out.length - 1].tiles.map((t) => t.href)).toEqual(["/added-in-a-later-version"]);
  });
});

describe("the order inside a screen", () => {
  it("is the flat order, filtered — nothing is re-sorted", () => {
    const flat = orderedTiles({ "/memory": 9, "/agents": 4, "/settings": 2 }, []);
    const joined = slides(flat).flatMap((s) => s.tiles.map((t) => t.href));
    // Same tiles, same relative order, just partitioned.
    expect(joined.length).toBe(flat.length);
    for (const s of slides(flat)) {
      const hrefs = s.tiles.map((t) => t.href);
      const inFlat = flat.map((t) => t.href).filter((h) => hrefs.includes(h));
      expect(hrefs).toEqual(inFlat);
    }
  });

  it("puts the most-opened tile first ON ITS OWN screen", () => {
    const out = slides(orderedTiles({ "/memory": 9 }, []));
    const office = out.find((s) => s.group.key === "office")!;
    expect(office.tiles[0].href).toBe("/memory");
    // And the other screens are untouched by it.
    const ops = out.find((s) => s.group.key === "operations")!;
    expect(ops.tiles[0].href).toBe("/workflows");
  });

  it("renders an arrangement saved before v1.293.0 in the same relative order", () => {
    // A flat list from the one-screen desktop: Documents dragged to the very
    // front, then Agents, then Chat. Each lands first on its own screen.
    const saved = ["/documents", "/agents", "/chat"];
    const out = slides(orderedTiles({ "/chat": 50 }, saved));
    expect(out.find((s) => s.group.key === "office")!.tiles.slice(0, 2).map((t) => t.href)).toEqual([
      "/documents",
      "/chat",
    ]);
    expect(out.find((s) => s.group.key === "operations")!.tiles[0].href).toBe("/agents");
  });
});

describe("a rearrangement is still one saved list", () => {
  it("re-sequences the screen's tiles in the slots they already hold", () => {
    const flat = ["/chat", "/agents", "/terminals", "/settings", "/projects"];
    const office = ["/chat", "/terminals", "/projects"];
    // Move Projects (2) to the front (0) within Office.
    expect(reorderWithinSlide(flat, office, 2, 0)).toEqual([
      "/projects",
      "/agents",
      "/chat",
      "/settings",
      "/terminals",
    ]);
    // Agents and Settings never moved: their slots are exactly where they were.
  });

  it("leaves the list alone on an out-of-range index", () => {
    const flat = ["/chat", "/terminals"];
    expect(reorderWithinSlide(flat, ["/chat", "/terminals"], 0, 5)).toEqual(flat);
    expect(reorderWithinSlide(flat, ["/chat", "/terminals"], -1, 0)).toEqual(flat);
  });

  it("remembers the screen by key and shrugs off junk", () => {
    window.localStorage.clear();
    expect(readSlide()).toBeNull();
    writeSlide("system");
    expect(readSlide()).toBe("system");
    window.localStorage.setItem("ironjarvis.overview.slide", "42");
    expect(readSlide()).toBeNull();
  });
});

// --- the gesture surface, through the real grid -------------------------------

const TILE = { left: 100, top: 100, width: 80, height: 80 };
const WIN = { width: 1000, height: 800 };

/** Every tile in its own column, so dnd-kit's closest-centre can tell them apart. */
function stubGeometry() {
  Object.defineProperty(window, "innerWidth", { value: WIN.width, configurable: true });
  Object.defineProperty(window, "innerHeight", { value: WIN.height, configurable: true });
  vi.spyOn(Element.prototype, "getBoundingClientRect").mockImplementation(function (this: Element) {
    const tile = (this as Element).closest?.("[data-testid^='tile-']") as HTMLElement | null;
    const grid = tile?.parentElement;
    let col = 0;
    if (tile && grid) col = Math.max(0, Array.from(grid.children).indexOf(tile));
    const left = TILE.left + col * 100;
    return {
      ...TILE,
      left,
      right: left + TILE.width,
      bottom: TILE.top + TILE.height,
      x: left,
      y: TILE.top,
      toJSON: () => ({}),
    } as DOMRect;
  });
}

function mount() {
  return render(
    <MotionProvider>
      <AppGrid />
    </MotionProvider>,
  );
}

async function pickUp(tile: HTMLElement, x: number) {
  fireEvent.pointerDown(tile, { clientX: x, clientY: 140, pointerId: 1, button: 0, isPrimary: true });
  fireEvent.pointerMove(document, { clientX: x + 20, clientY: 140, pointerId: 1 });
  await waitFor(() => expect(tile.getAttribute("style") ?? "").toContain("translate3d"));
}

beforeEach(() => {
  window.localStorage.clear();
  stubGeometry();
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("the Overview screens", () => {
  it("open on Office with its ten tiles, the other screens' tiles not in the DOM", async () => {
    mount();
    const strip = await screen.findByTestId("tile-slides");
    await waitFor(() => expect(strip.getAttribute("data-slide")).toBe("office"));
    expect(strip.getAttribute("data-slide-count")).toBe("3");
    expect(screen.getByTestId("tile-chat")).toBeInTheDocument();
    expect(screen.getByTestId("tile-memory")).toBeInTheDocument();
    expect(screen.queryByTestId("tile-agents")).toBeNull();
    expect(screen.queryByTestId("tile-settings")).toBeNull();
    expect(strip.querySelectorAll("[data-testid^='tile-']").length).toBe(10);
    expect(screen.getByTestId("tile-group-office").getAttribute("aria-selected")).toBe("true");
    expect((screen.getByTestId("slides-prev") as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByTestId("slides-next") as HTMLButtonElement).disabled).toBe(false);
    expect(screen.getByTestId("slide-hint").textContent).toContain("Where the work happens");
  });

  it("a tab opens its screen and is remembered for next time", async () => {
    mount();
    await screen.findByTestId("tile-chat");
    fireEvent.click(screen.getByTestId("tile-group-operations"));
    await screen.findByTestId("tile-agents");
    expect(screen.queryByTestId("tile-chat")).toBeNull();
    expect(screen.getByTestId("tile-slides").getAttribute("data-slide")).toBe("operations");
    expect(screen.getByTestId("tile-group-operations").getAttribute("aria-selected")).toBe("true");
    expect(screen.getByTestId("slide-hint").textContent).toContain("Agents and automation");
    expect(window.localStorage.getItem("ironjarvis.overview.slide")).toBe(JSON.stringify("operations"));

    // Reopen the desktop: it comes back where it was left.
    cleanup();
    mount();
    await screen.findByTestId("tile-agents");
    expect(screen.getByTestId("tile-slides").getAttribute("data-slide")).toBe("operations");
  });

  it("a remembered key that names no screen falls back to the first", async () => {
    window.localStorage.setItem("ironjarvis.overview.slide", JSON.stringify("removed-group"));
    mount();
    await screen.findByTestId("tile-chat");
    expect(screen.getByTestId("tile-slides").getAttribute("data-slide")).toBe("office");
  });

  it("the chevrons step through and stop at the ends", async () => {
    mount();
    await screen.findByTestId("tile-chat");
    fireEvent.click(screen.getByTestId("slides-next"));
    await screen.findByTestId("tile-agents");
    fireEvent.click(screen.getByTestId("slides-next"));
    await screen.findByTestId("tile-settings");
    expect((screen.getByTestId("slides-next") as HTMLButtonElement).disabled).toBe(true);
    // Disabled: a further press does nothing.
    fireEvent.click(screen.getByTestId("slides-next"));
    expect(screen.getByTestId("tile-slides").getAttribute("data-slide")).toBe("system");
    fireEvent.click(screen.getByTestId("slides-prev"));
    await screen.findByTestId("tile-agents");
  });

  it("the arrow keys work on the focused strip and on the tabs, not on a tile", async () => {
    mount();
    const strip = await screen.findByTestId("tile-slides");
    fireEvent.keyDown(strip, { key: "ArrowRight" });
    await screen.findByTestId("tile-agents");
    fireEvent.keyDown(strip, { key: "End" });
    await screen.findByTestId("tile-settings");
    fireEvent.keyDown(strip, { key: "Home" });
    await screen.findByTestId("tile-chat");

    // A key pressed ON A TILE is the tile's (dnd-kit's keyboard sort): the
    // screen must not flip under it.
    fireEvent.keyDown(screen.getByTestId("tile-chat"), { key: "ArrowRight" });
    await new Promise((r) => setTimeout(r, 20));
    expect(strip.getAttribute("data-slide")).toBe("office");

    // The tab strip's own arrows move between groups and carry focus.
    const office = screen.getByTestId("tile-group-office");
    office.focus();
    fireEvent.keyDown(office, { key: "ArrowRight" });
    await screen.findByTestId("tile-agents");
    expect(document.activeElement).toBe(screen.getByTestId("tile-group-operations"));
  });

  it("a horizontal wheel is a swipe, one screen per gesture", async () => {
    mount();
    const strip = await screen.findByTestId("tile-slides");
    fireEvent.wheel(strip, { deltaX: 80, deltaY: 0 });
    await screen.findByTestId("tile-agents");
    // The trackpad keeps emitting for the same gesture: still one screen.
    fireEvent.wheel(strip, { deltaX: 80, deltaY: 0 });
    fireEvent.wheel(strip, { deltaX: 80, deltaY: 0 });
    await new Promise((r) => setTimeout(r, 20));
    expect(strip.getAttribute("data-slide")).toBe("operations");
    // A vertical wheel is the page scrolling, not a swipe.
    fireEvent.wheel(strip, { deltaX: 4, deltaY: 120 });
    await new Promise((r) => setTimeout(r, 20));
    expect(strip.getAttribute("data-slide")).toBe("operations");
  });

  it("a pull across the background swipes; a wobble or a vertical pull does not", async () => {
    mount();
    const strip = await screen.findByTestId("tile-slides");
    // Leftward pull: next screen.
    fireEvent.pointerDown(strip, { clientX: 600, clientY: 300, pointerId: 7, button: 0, pointerType: "touch" });
    fireEvent.pointerUp(document, { clientX: 400, clientY: 310, pointerId: 7, pointerType: "touch" });
    await screen.findByTestId("tile-agents");
    // Rightward pull: back.
    fireEvent.pointerDown(strip, { clientX: 300, clientY: 300, pointerId: 8, button: 0, pointerType: "touch" });
    fireEvent.pointerUp(document, { clientX: 520, clientY: 300, pointerId: 8, pointerType: "touch" });
    await screen.findByTestId("tile-chat");
    // Too short.
    fireEvent.pointerDown(strip, { clientX: 300, clientY: 300, pointerId: 9, button: 0 });
    fireEvent.pointerUp(document, { clientX: 270, clientY: 300, pointerId: 9 });
    // Mostly vertical.
    fireEvent.pointerDown(strip, { clientX: 300, clientY: 300, pointerId: 10, button: 0 });
    fireEvent.pointerUp(document, { clientX: 200, clientY: 500, pointerId: 10 });
    await new Promise((r) => setTimeout(r, 20));
    expect(strip.getAttribute("data-slide")).toBe("office");
  });

  it("releasing a DRAGGED tile is a drop, never a swipe", async () => {
    mount();
    const strip = await screen.findByTestId("tile-slides");
    const tile = await screen.findByTestId("tile-chat");
    await pickUp(tile, 540);
    // A long LEFTWARD pull (540 → 200) — as a swipe it would be the next screen.
    fireEvent.pointerMove(document, { clientX: 200, clientY: 140, pointerId: 1 });
    fireEvent.pointerUp(document, { clientX: 200, clientY: 140, pointerId: 1 });
    await new Promise((r) => setTimeout(r, 30));
    expect(strip.getAttribute("data-slide")).toBe("office");
    expect(screen.getByTestId("tile-chat")).toBeInTheDocument();
  });

  it("dragging a tile within a screen saves ONE flat order with the other screens untouched", async () => {
    mount();
    const first = await screen.findByTestId("tile-chat");
    const before = orderedTiles().map((t) => t.href);
    // Column 0 → over column 1 (Build): a rearrange inside Office.
    await pickUp(first, 140);
    fireEvent.pointerMove(document, { clientX: 245, clientY: 140, pointerId: 1 });
    await new Promise((r) => setTimeout(r, 20));
    fireEvent.pointerUp(document, { clientX: 245, clientY: 140, pointerId: 1 });
    await waitFor(() =>
      expect(window.localStorage.getItem("ironjarvis.overview.order")).not.toBeNull(),
    );
    const saved = JSON.parse(window.localStorage.getItem("ironjarvis.overview.order")!) as string[];
    // Same list, same length, same storage key as the one-screen desktop.
    expect(saved.length).toBe(before.length);
    expect([...saved].sort()).toEqual([...before].sort());
    // Office changed: Build now precedes Chat.
    expect(saved.indexOf("/terminals")).toBeLessThan(saved.indexOf("/chat"));
    // Every Operations and System tile is exactly where it was.
    for (const g of TILE_GROUPS.filter((x) => x.key !== "office")) {
      for (const href of g.hrefs) expect(saved.indexOf(href)).toBe(before.indexOf(href));
    }
    // The grid shows it, and the caption says so.
    await waitFor(() => {
      const strip = screen.getByTestId("tile-slides");
      const shown = Array.from(strip.querySelectorAll("[data-testid^='tile-']")).map((el) =>
        el.getAttribute("data-testid"),
      );
      expect(shown.indexOf("tile-terminals")).toBeLessThan(shown.indexOf("tile-chat"));
    });
    expect(screen.getByText("Your arrangement")).toBeInTheDocument();
  });
});
