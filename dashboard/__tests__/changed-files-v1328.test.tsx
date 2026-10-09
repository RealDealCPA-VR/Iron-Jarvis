/**
 * "3 files changed +42 −7" (v1.328.0, calm chat B5).
 *
 * ChangedFiles is the one quiet line under a reply whose turn wrote files;
 * DiffView is the diff inside the shared <Modal>. Pinned here:
 *  - the JSON boundary (`decodeChanges`) and the exact request
 *    (`fetchTurnChanges` → POST /chat/changes);
 *  - the line's words and counts by value, colour ONLY on the counts;
 *  - honesty without a press: "changed since" shows on the collapsed line and
 *    on the file's row;
 *  - hover/focus previews the first changed lines; a press opens the diff
 *    (the caller's onOpen, or DiffView itself);
 *  - the unified parser keeps the hunk's line numbers; side by side pairs a
 *    removed run with the added run after it; wide = split, narrow = unified;
 *  - before/after texts go through lib/diff's diffLines;
 *  - binary / note / row cap / cut-short each say so.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";

const postMock = vi.fn();
vi.mock("@/lib/api", () => ({
  post: (...a: unknown[]) => postMock(...a),
}));

import {
  ChangedFiles,
  decodeChanges,
  fetchTurnChanges,
  filesWord,
  previewLines,
  totals,
  type FileChange,
} from "@/components/chat/ChangedFiles";
import {
  DiffBody,
  DiffView,
  MAX_DIFF_ROWS,
  pairRows,
  parseUnified,
  rowsFromTexts,
} from "@/components/chat/DiffView";

afterEach(() => {
  cleanup();
  postMock.mockReset();
});

const MEMO_DIFF = [
  "--- a/notes/memo.txt",
  "+++ b/notes/memo.txt",
  "@@ -3,4 +3,5 @@",
  " three",
  "-four",
  "+FOUR",
  "+four and a half",
  " five",
  " six",
].join("\n");

function change(over: Partial<FileChange> = {}): FileChange {
  return {
    path: "C:/work/notes/memo.txt",
    rel: "notes/memo.txt",
    name: "memo.txt",
    status: "modified",
    added: 2,
    removed: 1,
    diff: MEMO_DIFF,
    truncated: false,
    binary: false,
    changed_since: false,
    undone: false,
    note: "",
    ...over,
  };
}

const THREE: FileChange[] = [
  change(),
  change({
    path: "C:/work/out/report.md",
    rel: "out/report.md",
    name: "report.md",
    status: "created",
    added: 40,
    removed: 0,
    diff: "--- a/out/report.md\n+++ b/out/report.md\n@@ -0,0 +1,40 @@\n+# Report",
  }),
  change({
    path: "C:/work/old.txt",
    rel: "old.txt",
    name: "old.txt",
    status: "deleted",
    added: 0,
    removed: 6,
    changed_since: true,
    diff: "--- a/old.txt\n+++ b/old.txt\n@@ -1,6 +0,0 @@\n-a\n-b\n-c\n-d\n-e\n-f",
  }),
];

// --------------------------------------------------------------- boundary --

describe("decodeChanges + fetchTurnChanges", () => {
  it("checks every field and drops a row without a path", () => {
    const rows = decodeChanges({
      changes: [
        { path: "C:/a.txt", status: "created", added: 3, removed: 0, diff: "x", changed_since: null },
        { status: "modified" },
        { path: "C:/b.bin", status: "weird", added: "7", removed: -1, binary: true, changed_since: true, undone: 1 },
      ],
    });
    expect(rows).toHaveLength(2);
    expect(rows[0]).toMatchObject({ path: "C:/a.txt", status: "created", added: 3, removed: 0, diff: "x", changed_since: null });
    expect(rows[1]).toMatchObject({
      path: "C:/b.bin",
      status: "modified",
      added: null,
      removed: null,
      binary: true,
      changed_since: true,
      undone: false,
      diff: "",
    });
    expect(decodeChanges(null)).toEqual([]);
    expect(decodeChanges([{ path: "C:/c" }])).toHaveLength(1);
  });

  it("posts the turn window and the files, exactly", async () => {
    postMock.mockResolvedValue({ changes: [{ path: "C:/a.txt", status: "created", added: 1, removed: 0, diff: "" }] });
    const out = await fetchTurnChanges({
      since: Date.UTC(2026, 9, 9, 12, 0, 0),
      until: "2026-10-09T12:00:30.000Z",
      paths: ["C:/a.txt"],
    });
    expect(postMock).toHaveBeenCalledWith("/chat/changes", {
      since: "2026-10-09T12:00:00.000Z",
      until: "2026-10-09T12:00:30.000Z",
      paths: ["C:/a.txt"],
    });
    expect(out.map((c) => c.path)).toEqual(["C:/a.txt"]);

    postMock.mockResolvedValue({ changes: [] });
    await fetchTurnChanges({ since: "2026-10-09T12:00:00Z", paths: [] });
    expect(postMock).toHaveBeenLastCalledWith("/chat/changes", { since: "2026-10-09T12:00:00Z" });
  });
});

// --------------------------------------------------------------- the line --

describe("ChangedFiles", () => {
  it("renders nothing for a turn that changed no file", () => {
    const { container } = render(<ChangedFiles changes={[]} />);
    expect(container.innerHTML).toBe("");
  });

  it("says the count and the totals, colour only on the numbers", () => {
    render(<ChangedFiles changes={THREE} />);
    const line = screen.getByTestId("changed-files-line");
    expect(line.textContent).toContain("3 files changed");
    const plus = within(line).getByText("+42");
    const minus = within(line).getByText("−7");
    expect(plus.className).toContain("text-tone-success");
    expect(minus.className).toContain("text-tone-danger");
    expect(within(line).getByText("3 files changed").className).not.toMatch(/tone-(success|danger)/);
    expect(filesWord(1)).toBe("1 file changed");
  });

  it("shows changed-since on the collapsed line without a press", () => {
    render(<ChangedFiles changes={THREE} />);
    const line = screen.getByTestId("changed-files-line");
    expect(line.getAttribute("aria-expanded")).toBe("false");
    expect(within(line).getByText("· changed since").className).toContain("text-tone-warn");
  });

  it("draws no numbers when no file was compared", () => {
    const bin = [change({ binary: true, added: null, removed: null, diff: "binary file" })];
    expect(totals(bin)).toBeNull();
    render(<ChangedFiles changes={bin} />);
    expect(screen.getByTestId("changed-files-line").textContent).not.toMatch(/[+−]\d/);
  });

  it("lists each file with what happened, its path and counts", () => {
    render(<ChangedFiles changes={THREE} />);
    fireEvent.click(screen.getByTestId("changed-files-line"));
    expect(screen.getByTestId("changed-files-line").getAttribute("aria-expanded")).toBe("true");
    const r0 = screen.getByTestId("changed-file-0");
    expect(r0.textContent).toContain("edited");
    expect(r0.textContent).toContain("notes/memo.txt");
    expect(within(r0).getByText("+2")).toBeTruthy();
    expect(screen.getByTestId("changed-file-1").textContent).toContain("new file");
    const r2 = screen.getByTestId("changed-file-2");
    expect(r2.textContent).toContain("deleted");
    expect(screen.getByTestId("changed-since-2").className).toContain("text-tone-warn");
    expect(screen.queryByTestId("changed-since-0")).toBeNull();
  });

  it("previews the first changed lines on hover and on focus", () => {
    render(<ChangedFiles changes={THREE} />);
    fireEvent.click(screen.getByTestId("changed-files-line"));
    const row = screen.getByTestId("changed-file-0");
    expect(within(row).queryByTestId("changed-preview")).toBeNull();
    fireEvent.mouseEnter(row);
    const tip = within(row).getByTestId("changed-preview");
    expect(Array.from(tip.querySelectorAll("[data-kind]")).map((n) => n.textContent)).toEqual([
      "− four",
      "+ FOUR",
      "+ four and a half",
    ]);
    fireEvent.mouseLeave(row);
    expect(within(row).queryByTestId("changed-preview")).toBeNull();
    fireEvent.focus(within(row).getByRole("button"));
    expect(within(row).getByTestId("changed-preview")).toBeTruthy();
  });

  it("caps the preview at eight lines", () => {
    const many = "@@ -1,0 +1,20 @@\n" + Array.from({ length: 20 }, (_, i) => `+l${i}`).join("\n");
    expect(previewLines(change({ diff: many }))).toHaveLength(8);
  });

  it("hands the caller the file's path on a press", () => {
    const onOpen = vi.fn();
    render(<ChangedFiles changes={THREE} onOpen={onOpen} />);
    fireEvent.click(screen.getByTestId("changed-files-line"));
    fireEvent.click(within(screen.getByTestId("changed-file-1")).getByRole("button"));
    expect(onOpen).toHaveBeenCalledWith("C:/work/out/report.md");
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("opens the diff itself when no caller owns the dialog, and closes it", async () => {
    render(<ChangedFiles changes={THREE} />);
    fireEvent.click(screen.getByTestId("changed-files-line"));
    fireEvent.click(within(screen.getByTestId("changed-file-0")).getByRole("button"));
    const dialog = await screen.findByRole("dialog", { name: "Changes to memo.txt" });
    expect(within(dialog).getByTestId("diff-split")).toBeTruthy();
    fireEvent.click(within(dialog).getByRole("button", { name: "Close" }));
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});

// --------------------------------------------------------------- the diff --

describe("DiffView", () => {
  it("keeps the hunk's line numbers", () => {
    expect(parseUnified(MEMO_DIFF)).toEqual([
      { kind: "hunk", text: "@@ -3,4 +3,5 @@" },
      { kind: "same", text: "three", oldNo: 3, newNo: 3 },
      { kind: "removed", text: "four", oldNo: 4 },
      { kind: "added", text: "FOUR", newNo: 4 },
      { kind: "added", text: "four and a half", newNo: 5 },
      { kind: "same", text: "five", oldNo: 5, newNo: 6 },
      { kind: "same", text: "six", oldNo: 6, newNo: 7 },
    ]);
  });

  it("pairs a removed run with the added run after it, side by side", () => {
    const pairs = pairRows(parseUnified(MEMO_DIFF));
    expect(pairs[0]).toEqual({ hunk: "@@ -3,4 +3,5 @@" });
    expect(pairs[2].left?.text).toBe("four");
    expect(pairs[2].right?.text).toBe("FOUR");
    expect(pairs[3].left).toBeUndefined();
    expect(pairs[3].right?.text).toBe("four and a half");
    expect(pairs[1].left).toBe(pairs[1].right);
  });

  it("is side by side on a wide window and one column on a narrow one", async () => {
    render(<DiffView change={change()} onClose={() => {}} />);
    const dialog = await screen.findByRole("dialog");
    const split = within(dialog).getByTestId("diff-split");
    const unified = within(dialog).getByTestId("diff-unified");
    expect(split.className.split(" ")).toEqual(expect.arrayContaining(["hidden", "md:block"]));
    expect(unified.className.split(" ")).toContain("md:hidden");
    const row = within(split).getAllByTestId("diff-split-row")[1];
    expect(row.querySelector('[data-side="left"]')?.textContent).toContain("four");
    expect(row.querySelector('[data-side="right"]')?.textContent).toContain("FOUR");
  });

  it("tints only the changed lines, mono 12px, readable text", async () => {
    render(<DiffView change={change()} onClose={() => {}} />);
    const dialog = await screen.findByRole("dialog");
    const unified = within(dialog).getByTestId("diff-unified");
    const added = unified.querySelector('[data-kind="added"]') as HTMLElement;
    const removed = unified.querySelector('[data-kind="removed"]') as HTMLElement;
    const same = unified.querySelector('[data-kind="same"]') as HTMLElement;
    expect(added.className).toContain("bg-tone-success/10");
    expect(removed.className).toContain("bg-tone-danger/10");
    expect(same.className).not.toMatch(/tone-/);
    expect(added.lastElementChild?.className).toContain("text-zinc-300");
    expect(unified.parentElement?.className).toContain("font-mono");
    expect(unified.parentElement?.className).toContain("text-[12px]");
    expect(within(dialog).getByText("+2").className).toContain("text-tone-success");
  });

  it("says changed-again in the header", async () => {
    render(<DiffView change={change({ changed_since: true })} onClose={() => {}} />);
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByTestId("diff-changed-since").textContent).toBe(
      "changed again since this reply",
    );
  });

  it("reads before/after through the shared diffLines", () => {
    expect(rowsFromTexts("a\nb\nc", "a\nB\nc")).toEqual([
      { kind: "same", text: "a", oldNo: 1, newNo: 1 },
      { kind: "removed", text: "b", oldNo: 2 },
      { kind: "added", text: "B", newNo: 2 },
      { kind: "same", text: "c", oldNo: 3, newNo: 3 },
    ]);
    render(<DiffBody change={change({ diff: "" })} before={"x"} after={"y"} />);
    expect(screen.getByTestId("diff-unified").querySelectorAll('[data-kind="added"]')).toHaveLength(1);
  });

  it("says binary, says the note, never an empty grid", () => {
    render(<DiffBody change={change({ binary: true, diff: "binary file" })} />);
    expect(screen.getByTestId("diff-binary").textContent).toBe(
      "This is a binary file, so there are no lines to compare.",
    );
    cleanup();
    render(<DiffBody change={change({ diff: "", note: "Too large to compare here." })} />);
    expect(screen.getByTestId("diff-empty").textContent).toBe("Too large to compare here.");
  });

  it("caps the rows and says how many more, or that the daemon cut it", () => {
    const n = MAX_DIFF_ROWS + 5;
    const big = `@@ -0,0 +1,${n} @@\n` + Array.from({ length: n }, (_, i) => `+r${i}`).join("\n");
    render(<DiffBody change={change({ diff: big })} />);
    expect(screen.getByTestId("diff-unified").querySelectorAll('[data-kind="added"]')).toHaveLength(
      MAX_DIFF_ROWS - 1, // the hunk row is one of the capped rows
    );
    expect(screen.getByTestId("diff-more").textContent).toBe("6 more lines are not shown here.");
    cleanup();
    render(<DiffBody change={change({ truncated: true })} />);
    expect(screen.getByTestId("diff-more").textContent).toBe(
      "This diff was cut short. The counts above cover all of it.",
    );
  });
});

// ------------------------------------------------------------ house rules --

describe("house rules", () => {
  const read = (f: string) =>
    readFileSync(join(__dirname, "..", "components", "chat", f), "utf8").replace(/\r\n/g, "\n");

  it("uses whole-pixel text and theme tokens, never literal colours", () => {
    for (const f of ["ChangedFiles.tsx", "DiffView.tsx"]) {
      const src = read(f);
      expect(src, f).not.toMatch(/text-\[\d+\.\d+px\]/);
      expect(src, f).not.toMatch(/\b(emerald|rose|green|red|amber)-\d{2,3}\b/);
      expect(src, f).not.toMatch(/#[0-9a-fA-F]{3,6}\b/);
    }
  });

  it("draws the diff inside the shared Modal", () => {
    expect(read("DiffView.tsx")).toContain('import { Modal } from "@/components/Modal"');
  });
});
