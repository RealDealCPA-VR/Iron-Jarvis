/**
 * v1.232.0 (audit Wave 6, U15) — the Memory page's second search box says
 * what it searches, and "k" is "Results".
 *
 * /memory stacks the Recall box (every store at once) over the Working tab's
 * own search; the second box had no title and a field labelled "k 5". Folding
 * the Working search into Recall's store filter is a redesign, so the box
 * gains a one-line title instead, and the count field is named for what it is.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";

vi.mock("@/lib/api", () => ({
  ApiError: class extends Error {
    status = 0;
  },
  get: () => Promise.resolve({ results: [] }),
}));
vi.mock("@/components/VoiceInput", () => ({
  VoiceInput: () => null,
  appendDictation: (p: string, c: string) => p + c,
}));

import { WorkingMemory } from "@/components/memory/WorkingMemory";

afterEach(cleanup);

describe("WorkingMemory copy (v1.232.0)", () => {
  it("titles the box and says it searches only the working store", () => {
    render(<WorkingMemory />);
    expect(screen.getByText("Search working memory")).toBeInTheDocument();
    expect(screen.getByText(/only the working store .* the Recall box searches everything/)).toBeInTheDocument();
  });

  it("labels the count field 'Results', not 'k'", () => {
    render(<WorkingMemory />);
    expect(screen.getByText("Results", { selector: "label" })).toBeInTheDocument();
    expect(screen.getByLabelText("How many results to show")).toHaveValue(5);
    expect(screen.queryByText(/^k$/)).toBeNull();
  });
});

describe("LongTerm copy (v1.232.0)", () => {
  it("the long-term tab's count field is 'Results' too", () => {
    const src = readFileSync(
      join(process.cwd(), "components", "memory", "LongTerm.tsx"),
      "utf8",
    );
    expect(src).toContain('aria-label="How many results to show"');
    expect(src).not.toContain('aria-label="Results to retrieve (k)"');
    expect(src).not.toMatch(/>\s*k\s*<\/label>/);
  });
});
