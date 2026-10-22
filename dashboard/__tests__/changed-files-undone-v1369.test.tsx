/**
 * v1.369.0 review — AN UNDO THAT WORKED READS AS UNDONE ONLY.
 *
 * The daemon (`POST /chat/changes`) now answers a file whose writes were all
 * undone as `undone: true, changed_since: false` with no note
 * (tests/test_chat_changes_undone_v1369.py). The one changes line (chat
 * replies AND a Build session's Changes pane) draws that as undone and
 * nothing else: no "changed since", no counts, and the closed line says
 * "· undone" so it never reads as live changes.
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

vi.mock("@/lib/api", () => ({ post: vi.fn() }));

import { ChangedFiles, type FileChange } from "@/components/chat/ChangedFiles";

afterEach(cleanup);

const UNDONE: FileChange = {
  path: "C:\\work\\RESULT.md",
  rel: "RESULT.md",
  name: "RESULT.md",
  status: "created",
  added: null,
  removed: null,
  diff: "",
  changed_since: false,
  undone: true,
  note: "",
};

describe("an undone change", () => {
  it("reads as undone only: on the closed line, on its row and in its preview", () => {
    render(<ChangedFiles changes={[UNDONE]} />);
    const line = screen.getByTestId("changed-files-line");
    expect(line.textContent).toBe("1 file changed· undone");
    expect(line.textContent).not.toMatch(/changed since/);
    fireEvent.click(line);
    const row = screen.getByTestId("changed-file-0");
    expect(row.textContent).toContain("undone");
    expect(row.textContent).not.toMatch(/changed since/);
    expect(screen.queryByTestId("changed-since-0")).toBeNull();
    fireEvent.mouseEnter(row);
    expect(screen.getByTestId("changed-preview")).toHaveTextContent("This change was undone.");
    // Open again: the "· undone" note belongs to the closed line only.
    expect(screen.queryByTestId("changed-files-undone")).toBeNull();
  });

  it("a file changed again after the undo still says so, and is not called undone on the line", () => {
    render(<ChangedFiles changes={[{ ...UNDONE, changed_since: true }]} />);
    const line = screen.getByTestId("changed-files-line");
    expect(line.textContent).toMatch(/changed since/);
    expect(screen.queryByTestId("changed-files-undone")).toBeNull();
  });

  it("a list with one live change is not called undone", () => {
    const live: FileChange = { ...UNDONE, path: "C:\\work\\a.txt", rel: "a.txt", name: "a.txt", undone: false, added: 1, removed: 0 };
    render(<ChangedFiles changes={[UNDONE, live]} />);
    expect(screen.queryByTestId("changed-files-undone")).toBeNull();
  });
});
