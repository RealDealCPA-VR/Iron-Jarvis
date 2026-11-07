/**
 * v1.443.0 — the Directory's card lists never grow wider than a phone.
 *
 * Found by the fresh-install crawl at 390 px: below `sm` the one-column grid
 * was sized by its widest card's content (the Microsoft 365 pill), so the
 * card ran past the screen edge and cut off Connect. `minmax(0,1fr)` lets
 * the column shrink to the screen (the PageGrid pattern). jsdom has no
 * layout, so the class is pinned; the crawl measured the real width.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const read = (p: string) => readFileSync(join(__dirname, "..", p), "utf8").replace(/\r\n/g, "\n");

describe("Directory card lists on a phone", () => {
  it("every card grid can shrink to the screen below sm", () => {
    const src = read("components/settings/pages/DirectoryPage.tsx");
    const grids = src.match(/grid[^"]*sm:grid-cols-2 xl:grid-cols-3/g) ?? [];
    expect(grids.length).toBeGreaterThanOrEqual(3);
    for (const g of grids) expect(g).toContain("grid-cols-[minmax(0,1fr)]");
  });
});
