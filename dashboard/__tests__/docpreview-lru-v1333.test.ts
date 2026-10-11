/**
 * v1.333.0 (lane F, F9) — "Changes since you last previewed" keeps a BOUNDED
 * memory.
 *
 * DocPreview holds the last lines it showed per file (module scope, so
 * closing and reopening the panel still counts as "last previewed"). The map
 * was unbounded: every file previewed in a window stayed in memory, whole, for
 * the life of the window. It now keeps the VIEWED_LINES_MAX most recently
 * previewed, the least recently previewed going first. Counts only.
 */
import { describe, expect, it } from "vitest";

import { VIEWED_LINES_MAX, recallViewedLines, rememberViewedLines } from "@/components/chat/DocPreview";

describe("DocPreview's last-viewed lines", () => {
  it("keeps the newest VIEWED_LINES_MAX previews and forgets the oldest", () => {
    expect(VIEWED_LINES_MAX).toBe(30);
    for (let i = 0; i <= VIEWED_LINES_MAX; i += 1) rememberViewedLines(`lru-a-${i}`, [`v${i}`]);
    expect(recallViewedLines("lru-a-0")).toBeUndefined(); // the 31st pushed the first out
    expect(recallViewedLines("lru-a-1")).toEqual(["v1"]);
    expect(recallViewedLines(`lru-a-${VIEWED_LINES_MAX}`)).toEqual([`v${VIEWED_LINES_MAX}`]);
  });

  it("previewing a file again makes it the most recent (it outlives older ones)", () => {
    for (let i = 0; i < VIEWED_LINES_MAX; i += 1) rememberViewedLines(`lru-b-${i}`, [`v${i}`]);
    rememberViewedLines("lru-b-0", ["again"]); // re-previewed: now the newest
    rememberViewedLines("lru-b-new", ["new"]); // one over the bound
    expect(recallViewedLines("lru-b-0")).toEqual(["again"]);
    expect(recallViewedLines("lru-b-1")).toBeUndefined(); // the oldest now
  });
});
