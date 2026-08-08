/**
 * The Overview app grid's ordering (v1.151.0).
 *
 * Two rules carry it, and they are in tension, which is why they are pinned:
 * most-used-first is a helpful default, and it must STOP being applied the
 * moment the user arranges the grid themselves. A desktop that re-sorts itself
 * because yesterday's usage shifted is not a desktop.
 */

import { describe, it, expect, beforeEach, vi } from "vitest";
import { allTiles, orderedTiles, recordOpen, readUsage, readOrder, writeOrder, clearOrder } from "@/lib/appTiles";
import { NAV } from "@/lib/nav";

function fakeStorage() {
  const map = new Map<string, string>();
  return {
    getItem: (k: string) => map.get(k) ?? null,
    setItem: (k: string, v: string) => void map.set(k, v),
    removeItem: (k: string) => void map.delete(k),
    clear: () => map.clear(),
  };
}

beforeEach(() => {
  vi.stubGlobal("window", { localStorage: fakeStorage() });
});

describe("the tile catalogue", () => {
  it("is the nav catalogue, not a second list", () => {
    const navHrefs = NAV.flatMap((s) => s.items.map((i) => i.href));
    const tileHrefs = allTiles().map((t) => t.href);
    // Every tile comes from the nav; nothing is invented here. A page added to
    // nav.ts therefore appears on the desktop with its icon + blurb already
    // right — the whole reason the catalogue isn't duplicated.
    for (const href of tileHrefs) expect(navHrefs).toContain(href);
  });

  it("leaves out the pages that are chrome, not apps", () => {
    const hrefs = allTiles().map((t) => t.href);
    expect(hrefs).not.toContain("/");       // this IS the overview
    expect(hrefs).not.toContain("/help");
    expect(hrefs).not.toContain("/updates");
  });

  it("carries what the hover card needs", () => {
    for (const t of allTiles()) {
      expect(t.label).toBeTruthy();
      expect(t.blurb).toBeTruthy();
      expect(t.section).toBeTruthy();
      expect(t.icon).toBeTruthy();
    }
  });
});

describe("ordering", () => {
  it("falls back to the curated catalogue order when nothing is known", () => {
    const plain = orderedTiles({}, []).map((t) => t.href);
    expect(plain).toEqual(allTiles().map((t) => t.href));
  });

  it("puts the most-opened first", () => {
    const out = orderedTiles({ "/memory": 9, "/creative": 3 }, []).map((t) => t.href);
    expect(out[0]).toBe("/memory");
    expect(out[1]).toBe("/creative");
  });

  it("breaks ties on the catalogue order, never arbitrarily", () => {
    const usage = { "/memory": 4, "/creative": 4 };
    const catalogue = allTiles().map((t) => t.href);
    const out = orderedTiles(usage, []).map((t) => t.href);
    const expectFirst =
      catalogue.indexOf("/memory") < catalogue.indexOf("/creative") ? "/memory" : "/creative";
    expect(out[0]).toBe(expectFirst);
  });

  it("lets a saved arrangement WIN over usage", () => {
    // The point: /documents is dragged to the front and stays there even though
    // /memory is opened far more often.
    const out = orderedTiles({ "/memory": 50 }, ["/documents"]).map((t) => t.href);
    expect(out[0]).toBe("/documents");
    expect(out[1]).toBe("/memory"); // the rest still sorts by usage beneath it
  });

  it("keeps modules the saved arrangement has never heard of", () => {
    // An upgrade adds a page; a layout saved before it existed must not hide it.
    const out = orderedTiles({}, ["/documents", "/memory"]).map((t) => t.href);
    expect(out.length).toBe(allTiles().length);
    expect(out.slice(0, 2)).toEqual(["/documents", "/memory"]);
  });

  it("ignores an entry for a page that no longer exists", () => {
    const out = orderedTiles({}, ["/removed-in-a-later-version", "/memory"]);
    expect(out.map((t) => t.href)).not.toContain("/removed-in-a-later-version");
    expect(out[0].href).toBe("/memory");
  });

  it("never duplicates a tile when the saved order repeats one", () => {
    const out = orderedTiles({}, ["/memory", "/memory"]).map((t) => t.href);
    expect(out.filter((h) => h === "/memory")).toHaveLength(1);
  });
});

describe("usage counting", () => {
  it("counts opens", () => {
    recordOpen("/memory");
    recordOpen("/memory");
    recordOpen("/creative");
    expect(readUsage()).toEqual({ "/memory": 2, "/creative": 1 });
  });

  it("does not count the overview or help", () => {
    recordOpen("/");
    recordOpen("/help");
    expect(readUsage()).toEqual({});
  });

  it("survives unreadable storage instead of throwing", () => {
    vi.stubGlobal("window", {
      localStorage: {
        getItem: () => "{not json",
        setItem: () => {
          throw new Error("quota");
        },
        removeItem: () => {},
      },
    });
    // A corrupt or full store must degrade to "no preference", never break the
    // page that renders the grid.
    expect(() => recordOpen("/memory")).not.toThrow();
    expect(readUsage()).toEqual({});
    expect(readOrder()).toEqual([]);
    expect(orderedTiles(readUsage(), readOrder()).length).toBe(allTiles().length);
  });
});

describe("the saved arrangement round-trips", () => {
  it("writes, reads and clears", () => {
    writeOrder(["/memory", "/documents"]);
    expect(readOrder()).toEqual(["/memory", "/documents"]);
    clearOrder();
    expect(readOrder()).toEqual([]);
  });

  it("discards a non-array payload", () => {
    window.localStorage.setItem("ironjarvis.overview.order", '{"nope":1}');
    expect(readOrder()).toEqual([]);
  });
});
