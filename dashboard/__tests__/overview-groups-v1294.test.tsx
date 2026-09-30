/**
 * v1.294.0 — the Overview desktop is three doors (Office, Operations,
 * System); opening one hides the other two and reveals its ten modules in
 * place. No strip, no swipe (v1.293.0's screens, superseded at the user's
 * request).
 *
 * Pinned here, because each is a promise the user relies on:
 *
 * * every tile has exactly one home, no group holds more than ten, and a
 *   page nobody placed still shows (in a trailing "More" group) rather than
 *   vanishing — the v1.151.1 lesson;
 * * the order INSIDE a group is the order it always was (arrangement, then
 *   most-used, then the catalogue), and an arrangement saved before any of
 *   this renders the same relative order;
 * * a rearrangement is still ONE saved list, so the storage shape is unchanged;
 * * the doors open by press, close by the back control or Esc, are remembered
 *   by key, and a drag inside a group never closes it.
 *
 * The component tests run the REAL grid (dnd-kit's PointerSensor on jsdom,
 * tile rectangles stubbed), inside the app's own MotionProvider.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import { AppGrid } from "@/components/overview/AppGrid";
import { MotionProvider } from "@/components/MotionProvider";
import {
  GROUP_SIZE,
  TILE_GROUPS,
  UNGROUPED,
  allTiles,
  groupOf,
  groupedTiles,
  orderedTiles,
  readOpenGroup,
  reorderWithinGroup,
  writeOpenGroup,
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
    // Every tile placed: the "More" group never appears for the real catalogue.
    for (const href of tiles) expect(groupOf(href)).not.toBe(UNGROUPED);
    expect(groupedTiles(orderedTiles()).map((s) => s.group.key)).toEqual(
      TILE_GROUPS.map((g) => g.key),
    );
  });

  it("hold at most ten tiles each — the ask, and one row of the widest grid", () => {
    expect(GROUP_SIZE).toBe(10);
    for (const s of groupedTiles(orderedTiles())) {
      expect(s.tiles.length).toBeLessThanOrEqual(GROUP_SIZE);
      expect(s.tiles.length).toBeGreaterThan(0);
    }
  });

  it("show a page nobody placed in a trailing More group instead of hiding it", () => {
    const stray: AppTile = {
      ...allTiles()[0],
      href: "/added-in-a-later-version",
      label: "New page",
      opens: 0,
      section: "Work",
    };
    const out = groupedTiles([...orderedTiles(), stray]);
    expect(out[out.length - 1].group).toBe(UNGROUPED);
    expect(out[out.length - 1].tiles.map((t) => t.href)).toEqual(["/added-in-a-later-version"]);
  });
});

describe("the order inside a group", () => {
  it("is the flat order, filtered — nothing is re-sorted", () => {
    const flat = orderedTiles({ "/memory": 9, "/agents": 4, "/settings": 2 }, []);
    const joined = groupedTiles(flat).flatMap((s) => s.tiles.map((t) => t.href));
    expect(joined.length).toBe(flat.length);
    for (const s of groupedTiles(flat)) {
      const hrefs = s.tiles.map((t) => t.href);
      const inFlat = flat.map((t) => t.href).filter((h) => hrefs.includes(h));
      expect(hrefs).toEqual(inFlat);
    }
  });

  it("puts the most-opened tile first IN ITS OWN group", () => {
    const out = groupedTiles(orderedTiles({ "/memory": 9 }, []));
    expect(out.find((s) => s.group.key === "office")!.tiles[0].href).toBe("/memory");
    expect(out.find((s) => s.group.key === "operations")!.tiles[0].href).toBe("/workflows");
  });

  it("renders an arrangement saved before the groups existed in the same relative order", () => {
    const saved = ["/documents", "/agents", "/chat"];
    const out = groupedTiles(orderedTiles({ "/chat": 50 }, saved));
    expect(out.find((s) => s.group.key === "office")!.tiles.slice(0, 2).map((t) => t.href)).toEqual([
      "/documents",
      "/chat",
    ]);
    expect(out.find((s) => s.group.key === "operations")!.tiles[0].href).toBe("/agents");
  });
});

describe("a rearrangement is still one saved list", () => {
  it("re-sequences the group's tiles in the slots they already hold", () => {
    const flat = ["/chat", "/agents", "/terminals", "/settings", "/projects"];
    const office = ["/chat", "/terminals", "/projects"];
    expect(reorderWithinGroup(flat, office, 2, 0)).toEqual([
      "/projects",
      "/agents",
      "/chat",
      "/settings",
      "/terminals",
    ]);
  });

  it("leaves the list alone on an out-of-range index", () => {
    const flat = ["/chat", "/terminals"];
    expect(reorderWithinGroup(flat, ["/chat", "/terminals"], 0, 5)).toEqual(flat);
    expect(reorderWithinGroup(flat, ["/chat", "/terminals"], -1, 0)).toEqual(flat);
  });

  it("remembers the open group by key, forgets it on close, and shrugs off junk", () => {
    window.localStorage.clear();
    expect(readOpenGroup()).toBeNull();
    writeOpenGroup("system");
    expect(readOpenGroup()).toBe("system");
    writeOpenGroup(null);
    expect(readOpenGroup()).toBeNull();
    window.localStorage.setItem("ironjarvis.overview.group", "42");
    expect(readOpenGroup()).toBeNull();
  });
});

// --- the doors, through the real grid ----------------------------------------

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

afterEach(async () => {
  // dnd-kit keeps a capture-phase document click listener for 50 ms after a
  // drag ends (forever, if the grid unmounts mid-drag); the next case's door
  // press would be swallowed by it. End any drag and outlast the removal.
  fireEvent.pointerUp(document, { clientX: 140, clientY: 140, pointerId: 1 });
  await new Promise((r) => setTimeout(r, 60));
  cleanup();
  vi.restoreAllMocks();
});

describe("the Overview doors", () => {
  it("open on the three doors, with no module tile in the DOM", async () => {
    mount();
    const desk = await screen.findByTestId("app-desk");
    await waitFor(() => expect(desk.getAttribute("data-open")).toBe("none"));
    expect(screen.getByTestId("group-office")).toBeInTheDocument();
    expect(screen.getByTestId("group-operations")).toBeInTheDocument();
    expect(screen.getByTestId("group-system")).toBeInTheDocument();
    expect(desk.querySelectorAll("[data-testid^='tile-']").length).toBe(0);
    expect(screen.queryByTestId("group-back")).toBeNull();
    // Each door says how many modules are behind it.
    expect(screen.getByTestId("group-office").textContent).toContain("10");
  });

  it("a press opens the door: the other doors go, the ten modules come", async () => {
    mount();
    fireEvent.click(await screen.findByTestId("group-office"));
    await screen.findByTestId("tile-chat");
    const desk = screen.getByTestId("app-desk");
    expect(desk.getAttribute("data-open")).toBe("office");
    expect(screen.queryByTestId("group-operations")).toBeNull();
    expect(screen.queryByTestId("group-system")).toBeNull();
    expect(desk.querySelectorAll("[data-testid^='tile-']").length).toBe(10);
    expect(screen.getByTestId("tile-memory")).toBeInTheDocument();
    expect(screen.queryByTestId("tile-agents")).toBeNull();
    expect(screen.getByTestId("group-back").textContent).toContain("Office");
    expect(screen.getByText("Most used first")).toBeInTheDocument();
  });

  it("the back control closes it and the three doors return", async () => {
    mount();
    fireEvent.click(await screen.findByTestId("group-system"));
    await screen.findByTestId("tile-settings");
    fireEvent.click(screen.getByTestId("group-back"));
    await screen.findByTestId("group-office");
    expect(screen.getByTestId("app-desk").getAttribute("data-open")).toBe("none");
    expect(screen.queryByTestId("tile-settings")).toBeNull();
    expect(window.localStorage.getItem("ironjarvis.overview.group")).toBeNull();
  });

  it("Esc on the grid closes it too", async () => {
    mount();
    fireEvent.click(await screen.findByTestId("group-operations"));
    const grid = await screen.findByTestId("module-grid");
    fireEvent.keyDown(grid, { key: "Escape" });
    await screen.findByTestId("group-office");
    expect(screen.queryByTestId("tile-agents")).toBeNull();
  });

  it("the open group is remembered for next time; a stale key means the doors", async () => {
    mount();
    fireEvent.click(await screen.findByTestId("group-operations"));
    await screen.findByTestId("tile-agents");
    expect(window.localStorage.getItem("ironjarvis.overview.group")).toBe(JSON.stringify("operations"));
    cleanup();
    mount();
    await screen.findByTestId("tile-agents");
    expect(screen.getByTestId("app-desk").getAttribute("data-open")).toBe("operations");

    cleanup();
    window.localStorage.setItem("ironjarvis.overview.group", JSON.stringify("removed-group"));
    mount();
    await screen.findByTestId("group-office");
    expect(screen.getByTestId("app-desk").getAttribute("data-open")).toBe("none");
  });

  it("a drag inside a group never closes it, and Esc mid-drag is the drag's", async () => {
    mount();
    fireEvent.click(await screen.findByTestId("group-office"));
    const tile = await screen.findByTestId("tile-chat");
    const grid = screen.getByTestId("module-grid");
    await pickUp(tile, 140);
    fireEvent.keyDown(grid, { key: "Escape" });
    fireEvent.pointerMove(document, { clientX: 400, clientY: 140, pointerId: 1 });
    fireEvent.pointerUp(document, { clientX: 400, clientY: 140, pointerId: 1 });
    await new Promise((r) => setTimeout(r, 30));
    expect(screen.getByTestId("app-desk").getAttribute("data-open")).toBe("office");
    expect(screen.getByTestId("tile-chat")).toBeInTheDocument();
  });

  it("dragging a tile within a group saves ONE flat order with the other groups untouched", async () => {
    mount();
    fireEvent.click(await screen.findByTestId("group-office"));
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
    expect(saved.length).toBe(before.length);
    expect([...saved].sort()).toEqual([...before].sort());
    expect(saved.indexOf("/terminals")).toBeLessThan(saved.indexOf("/chat"));
    for (const g of TILE_GROUPS.filter((x) => x.key !== "office")) {
      for (const href of g.hrefs) expect(saved.indexOf(href)).toBe(before.indexOf(href));
    }
    await waitFor(() => {
      const grid = screen.getByTestId("module-grid");
      const shown = Array.from(grid.querySelectorAll("[data-testid^='tile-']")).map((el) =>
        el.getAttribute("data-testid"),
      );
      expect(shown.indexOf("tile-terminals")).toBeLessThan(shown.indexOf("tile-chat"));
    });
    expect(screen.getByText("Your arrangement")).toBeInTheDocument();
    // Still open, still Office.
    expect(screen.getByTestId("app-desk").getAttribute("data-open")).toBe("office");
  });
});
