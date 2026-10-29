/**
 * v1.405.0 review — the review pane is MEMOISED: marking one file reviewed
 * (or folding it) redraws that file only, and a file that redraws does not
 * read its diff again for "Open in <editor>" (`firstNewLine` is memoised).
 *
 * Counted, not timed: the press component is replaced by one that counts its
 * draws per test id, and `parseUnified` by one that counts its calls.
 */
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

const count = vi.hoisted(() => ({ draws: {} as Record<string, number>, parses: 0 }));

vi.mock("@/components/harness/OpenInEditor", () => ({
  OpenInEditorButton: ({ testId }: { testId?: string }) => {
    const id = testId ?? "open";
    count.draws[id] = (count.draws[id] ?? 0) + 1;
    return null;
  },
}));
vi.mock("@/components/chat/DiffView", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/components/chat/DiffView")>();
  return {
    ...real,
    parseUnified: (diff: string) => {
      count.parses += 1;
      return real.parseUnified(diff);
    },
  };
});

import { ChangesReview } from "@/components/harness/ChangesReview";
import type { FileChange } from "@/components/chat/ChangedFiles";

const file = (i: number): FileChange => ({
  path: `C:\\work\\shop\\f${i}.py`,
  rel: `f${i}.py`,
  name: `f${i}.py`,
  status: "modified",
  added: 1,
  removed: 1,
  diff: `@@ -1,2 +1,2 @@\n a\n-b${i}\n+c${i}\n`,
});

afterEach(() => {
  cleanup();
  count.draws = {};
  count.parses = 0;
  localStorage.clear();
});

describe("the review pane redraws only what changed", () => {
  it("marking one file reviewed redraws that file, never the others", () => {
    const changes = [file(0), file(1), file(2)];
    const onUndo = vi.fn(async () => {});
    const { rerender } = render(
      <ChangesReview
        changes={changes}
        sessionId="s1"
        // A NEW function and a NEW object every render, as a caller writes it.
        undoFor={(p) => ({ actionId: `u-${p}`, undoable: true })}
        onUndo={onUndo}
      />,
    );
    const before = { ...count.draws };
    expect(before["review-open-1"]).toBeGreaterThan(0);
    fireEvent.click(screen.getByTestId("review-mark-0"));
    expect(count.draws["review-open-1"]).toBe(before["review-open-1"]);
    expect(count.draws["review-open-2"]).toBe(before["review-open-2"]);
    expect(count.draws["review-open-0"]).toBeGreaterThan(before["review-open-0"]);
    // The caller rendering again with fresh handlers redraws no file.
    const settled = { ...count.draws };
    rerender(
      <ChangesReview
        changes={changes}
        sessionId="s1"
        undoFor={(p) => ({ actionId: `u-${p}`, undoable: true })}
        onUndo={async () => {}}
      />,
    );
    expect(count.draws).toEqual(settled);
  });

  it("a file that redraws (folded) does not read its diff again for its first line", () => {
    render(<ChangesReview changes={[file(0)]} sessionId="s1" />);
    const parses = count.parses;
    fireEvent.click(screen.getByTestId("review-fold-0")); // folds: the file redraws, its rows go
    expect(screen.queryByTestId("review-rows-0")).toBeNull();
    expect(count.parses).toBe(parses);
  });
});
