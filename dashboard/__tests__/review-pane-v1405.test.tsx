/**
 * v1.405.0 — ONE SCROLLING REVIEW in a coding session's Changes pane, and
 * "Open in VS Code / Cursor at this line" on every file, hunk, step and
 * reviewer finding.
 *
 *  - every changed file's diff shows WITHOUT a press (no dialog);
 *  - each file's header: what happened, the path, its counts, the honesty
 *    words, Open in <editor>, Undo ("This session" only, through the
 *    handler the session passes — never a second undo path) and Reviewed;
 *  - each hunk offers "Open at line N" (N = the line of its first CHANGE in
 *    the NEW file — v1.405.0 review, moved on purpose: it was the hunk
 *    header's start, up to three context lines ABOVE the change); a deleted
 *    file opens nothing;
 *  - Reviewed marks persist per session and clear by themselves when the
 *    file's diff changes; Collapse reviewed / Expand all;
 *  - a file shows at most REVIEW_ROWS_PER_FILE rows until "Show all N lines";
 *  - Wide view: the same review in a dialog, side by side;
 *  - a step that worked on a file gets "Open in VS Code" ONLY inside a coding
 *    session, with the right path and line; the chat page shows nothing new;
 *  - a reviewer's `path:line` mentions open that place — never a URL, never
 *    `..` out of the folder;
 *  - no editor detected = no button anywhere; the options are read AGAIN on
 *    every coding-session mount (v1.405.0 review, moved on purpose: a read
 *    kept for the window's life named the wrong editor after the setting
 *    changed), and every press names the editor it opens;
 *  - a session folder on a network share shows no press at all;
 *  - the Reviewed hash covers the counts, the cut flag and a binary size;
 *  - a press's accessible name starts with its words; a Reviewed box names
 *    its file;
 *  - a settled file step still opens, read from what the REAL daemon saves
 *    (fixtures/editor-step-inputs-v1405.json, from chat_turn.step_detail).
 *
 * The editor is NEVER launched here: the context's `open` is a spy, and the
 * provider's POST goes to the mocked api.
 */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const api = vi.hoisted(() => {
  class ApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
    }
  }
  return {
    ApiError,
    gets: {} as Record<string, unknown>,
    posts: [] as [string, unknown][],
    calls: [] as string[],
    refuse: null as string | null,
  };
});
vi.mock("@/lib/api", () => ({
  ApiError: api.ApiError,
  API_BASE: "http://d",
  ijToken: () => "",
  get: (p: string) => {
    api.calls.push(p);
    return p in api.gets ? Promise.resolve(api.gets[p]) : Promise.reject(new api.ApiError(`unmocked ${p}`, 404));
  },
  post: (p: string, b?: unknown) => {
    api.posts.push([p, b]);
    if (p === "/chat/changes") return Promise.resolve(api.gets["POST /chat/changes"] ?? { changes: [] });
    if (p === "/editor/open" && api.refuse) return Promise.reject(new api.ApiError(api.refuse, 404));
    return Promise.resolve({ ok: true });
  },
  put: () => Promise.resolve({}),
  patch: () => Promise.resolve({}),
  del: () => Promise.resolve({}),
}));
vi.mock("@/components/terminal/FilesPanel", () => ({
  FilesPanel: () => <div data-testid="files-panel" />,
}));

import { ChangesReview, firstNewLine, hunkChangeLines } from "@/components/harness/ChangesReview";
import { parseUnified } from "@/components/chat/DiffView";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { HarnessPanes, type SessionTerminalApi } from "@/components/harness/HarnessPanes";
import { LinkedRefs, SessionEditorProvider } from "@/components/harness/OpenInEditor";
import { SettledHelperRow } from "@/components/harness/HarnessHelpers";
import { WorkLine } from "@/components/chat/WorkLine";
import type { FileChange } from "@/components/chat/ChangedFiles";
import {
  EditorContext,
  decodeEditorOptions,
  findEditorRefs,
  forgetEditorOptions,
  readEditorOptions,
  resolveInFolder,
  stepEditorTarget,
  type EditorOpener,
} from "@/lib/codeEditor";
import { REVIEW_KEY_PREFIX, REVIEW_ROWS_PER_FILE, diffHash, markOf, reviewSummary } from "@/lib/changesReview";
import { decodeSessionView, type BuildSessionView } from "@/lib/buildSessions";

const FOLDER = "C:\\work\\shop";

const CART: FileChange = {
  path: `${FOLDER}\\src\\cart.py`,
  rel: "src/cart.py",
  name: "cart.py",
  status: "modified",
  added: 2,
  removed: 1,
  diff: "--- a/src/cart.py\n+++ b/src/cart.py\n@@ -3,3 +3,4 @@\n a = 1\n-b = 2\n+b = 3\n+c = 4\n d = 5\n@@ -40,2 +41,2 @@\n x = 1\n-y = 2\n+y = 9\n",
};
const NEW: FileChange = {
  path: `${FOLDER}\\src\\new.py`,
  rel: "src/new.py",
  name: "new.py",
  status: "created",
  added: 2,
  removed: 0,
  diff: "--- /dev/null\n+++ b/src/new.py\n@@ -0,0 +1,2 @@\n+one\n+two\n",
};
const GONE: FileChange = {
  path: `${FOLDER}\\old.py`,
  rel: "old.py",
  name: "old.py",
  status: "deleted",
  added: 0,
  removed: 1,
  diff: "--- a/old.py\n+++ /dev/null\n@@ -1,1 +0,0 @@\n-bye\n",
};
const LOGO: FileChange = {
  path: `${FOLDER}\\logo.png`,
  rel: "logo.png",
  name: "logo.png",
  status: "modified",
  added: null,
  removed: null,
  diff: "binary file",
  binary: true,
};

function opener(over: Partial<EditorOpener> = {}): EditorOpener & { opened: [string, number | null | undefined, number | null | undefined][] } {
  const opened: [string, number | null | undefined, number | null | undefined][] = [];
  return {
    folder: FOLDER,
    label: "VS Code",
    open: async (path, line, column) => {
      opened.push([path, line, column]);
    },
    ...over,
    opened,
  };
}

function withEditor(node: React.ReactNode, ed: EditorOpener | null = opener()) {
  return render(<EditorContext.Provider value={ed}>{node}</EditorContext.Provider>);
}

beforeEach(() => {
  api.gets = {};
  api.posts = [];
  api.calls = [];
  api.refuse = null;
  localStorage.clear();
  forgetEditorOptions();
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

/* ------------------------------------------------------------ the review */

describe("one scrolling review", () => {
  it("shows every file's diff with no press and no dialog", () => {
    withEditor(<ChangesReview changes={[CART, NEW, GONE]} sessionId="s1" />);
    for (const i of [0, 1, 2]) {
      expect(screen.getByTestId(`review-rows-${i}`)).toBeInTheDocument();
    }
    expect(within(screen.getByTestId("review-rows-0")).getByText("b = 3")).toBeInTheDocument();
    expect(within(screen.getByTestId("review-rows-1")).getByText("two")).toBeInTheDocument();
    expect(screen.queryByTestId("diff-view")).toBeNull();
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("each header says what happened, the path and its counts, and stays in sight while its file scrolls", () => {
    withEditor(<ChangesReview changes={[CART, GONE]} sessionId="s1" />);
    const head = screen.getByTestId("review-head-0");
    expect(head.className).toContain("sticky");
    expect(head.className).toContain("top-0");
    expect(head).toHaveTextContent("edited");
    expect(screen.getByTestId("review-path-0")).toHaveTextContent("src/cart.py");
    expect(head).toHaveTextContent("+2 −1");
    expect(screen.getByTestId("review-head-1")).toHaveTextContent("deleted");
  });

  it("the top line counts files, lines and what was reviewed", () => {
    withEditor(<ChangesReview changes={[CART, NEW]} sessionId="s1" />);
    expect(screen.getByTestId("review-summary")).toHaveTextContent("2 files changed +4 −1 · 0 of 2 reviewed");
    expect(reviewSummary([CART, NEW], 1)).toBe("2 files changed +4 −1 · 1 of 2 reviewed");
  });

  it("each hunk offers Open at line N — the line of its first CHANGE in the NEW file", () => {
    // Moved on purpose (v1.405.0 review): the hunk headers say +3 and +41,
    // but those are CONTEXT lines; the changes start at new lines 4 and 42.
    const ed = opener();
    withEditor(<ChangesReview changes={[CART]} sessionId="s1" />, ed);
    const hunks = screen.getAllByTestId("review-hunk-open-0");
    expect(hunks.map((b) => b.textContent)).toEqual(["Open at line 4", "Open at line 42"]);
    fireEvent.click(hunks[1]);
    expect(ed.opened).toEqual([[CART.path, 42, null]]);
  });

  it("reads a hunk's first change from the rows: an addition's own line, a removal's place", () => {
    const lines = (diff: string) => [...hunkChangeLines(parseUnified(diff)).values()];
    // An addition after three context lines: its own new number.
    expect(lines("@@ -10,3 +10,4 @@\n a\n b\n c\n+d\n")).toEqual([13]);
    // A removal after two context lines: the new line it sits before.
    expect(lines("@@ -10,4 +10,3 @@\n a\n b\n-c\n d\n")).toEqual([12]);
    // A change on the hunk's first row: the header's own start.
    expect(lines("@@ -1,2 +1,2 @@\n-x\n+y\n z\n")).toEqual([1]);
    // A new file.
    expect(lines(NEW.diff)).toEqual([1]);
    expect(firstNewLine(CART)).toBe(4);
  });

  it("the header's Open in VS Code opens at the first changed line; a deleted file opens nothing", () => {
    const ed = opener();
    withEditor(<ChangesReview changes={[CART, GONE]} sessionId="s1" />, ed);
    const open = screen.getByTestId("review-open-0");
    expect(open).toHaveTextContent("Open in VS Code");
    fireEvent.click(open);
    expect(ed.opened).toEqual([[CART.path, 4, null]]);
    expect(screen.queryByTestId("review-open-1")).toBeNull();
    expect(screen.queryByTestId("review-hunk-open-1")).toBeNull();
    expect(firstNewLine(GONE)).toBeNull();
    expect(firstNewLine(NEW)).toBe(1);
  });

  it("a failed open says why in one line beside the press", async () => {
    const ed = opener({
      open: async () => {
        throw new Error("That file is not on this PC any more.");
      },
    });
    withEditor(<ChangesReview changes={[CART]} sessionId="s1" />, ed);
    fireEvent.click(screen.getByTestId("review-open-0"));
    expect(await screen.findByTestId("review-open-0-error")).toHaveTextContent("That file is not on this PC any more.");
  });

  it("the honesty cases stay visible without a press: changed since, undone, binary, a note", () => {
    withEditor(
      <ChangesReview
        changes={[
          { ...CART, changed_since: true },
          { ...NEW, undone: true, diff: "", note: "" },
          LOGO,
          { ...GONE, path: `${FOLDER}\\big.txt`, rel: "big.txt", diff: "", status: "modified", note: "Too large to compare." },
        ]}
        sessionId="s1"
      />,
    );
    expect(screen.getByTestId("review-changed-since-0")).toHaveTextContent("changed since");
    expect(screen.getByTestId("review-undone-1")).toHaveTextContent("undone");
    expect(screen.getByTestId("review-empty-1")).toHaveTextContent("This change was undone.");
    expect(screen.getByTestId("review-binary-2")).toHaveTextContent("binary file");
    expect(screen.getByTestId("review-empty-3")).toHaveTextContent("Too large to compare.");
  });

  it("there is no Keep: the session view says changes are already saved", () => {
    withEditor(<ChangesReview changes={[CART]} sessionId="s1" hint />);
    expect(screen.getByTestId("review-hint")).toHaveTextContent("Changes are already saved; Undo takes one back.");
    expect(screen.queryByRole("button", { name: /keep/i })).toBeNull();
  });
});

describe("Undo", () => {
  it("is offered only with the caller's handler, and goes through it", async () => {
    const onUndo = vi.fn(async () => {});
    const onUndone = vi.fn();
    withEditor(
      <ChangesReview
        changes={[CART, NEW]}
        sessionId="s1"
        undoFor={(p) => (p === CART.path ? { actionId: "act1", undoable: true } : null)}
        onUndo={onUndo}
        onUndone={onUndone}
      />,
    );
    expect(screen.queryByTestId("review-undo-1")).toBeNull(); // no journal match: no guess
    fireEvent.click(screen.getByTestId("review-undo-0"));
    await waitFor(() => expect(onUndone).toHaveBeenCalled());
    expect(onUndo).toHaveBeenCalledWith("act1", CART.path);
  });

  it("without a handler (the Branch view) no Undo shows, even when the journal matches", () => {
    withEditor(<ChangesReview changes={[CART]} sessionId="s1" undoFor={() => ({ actionId: "a", undoable: true })} />);
    expect(screen.queryByTestId("review-undo-0")).toBeNull();
  });

  it("a refused undo is said, never looks done", async () => {
    withEditor(
      <ChangesReview
        changes={[CART]}
        sessionId="s1"
        undoFor={() => ({ actionId: "a", undoable: true })}
        onUndo={async () => {
          throw new Error("The file changed since.");
        }}
      />,
    );
    fireEvent.click(screen.getByTestId("review-undo-0"));
    expect(await screen.findByTestId("review-undo-error")).toHaveTextContent("The file changed since.");
  });
});

describe("Reviewed", () => {
  it("is remembered per session by path AND diff, folds the file, and clears when the diff changes", () => {
    const { unmount } = withEditor(<ChangesReview changes={[CART, NEW]} sessionId="s1" />);
    fireEvent.click(screen.getByTestId("review-mark-0"));
    expect(screen.getByTestId("review-file-0")).toHaveAttribute("data-reviewed", "true");
    expect(screen.queryByTestId("review-rows-0")).toBeNull(); // folded; its header stays
    expect(screen.getByTestId("review-head-0")).toBeInTheDocument();
    expect(screen.getByTestId("review-summary")).toHaveTextContent("1 of 2 reviewed");
    const saved = JSON.parse(localStorage.getItem(`${REVIEW_KEY_PREFIX}s1`) ?? "[]");
    expect(saved).toEqual([markOf(CART)]);
    unmount();

    // Back again: still reviewed (and folded).
    withEditor(<ChangesReview changes={[CART, NEW]} sessionId="s1" />);
    expect(screen.getByTestId("review-file-0")).toHaveAttribute("data-reviewed", "true");
    expect((screen.getByTestId("review-mark-0") as HTMLInputElement).checked).toBe(true);
    cleanup();

    // The file changed again: unreviewed by itself, and open.
    const changed = { ...CART, diff: `${CART.diff}@@ -60,1 +61,1 @@\n-z\n+zz\n` };
    expect(diffHash(changed)).not.toBe(diffHash(CART));
    withEditor(<ChangesReview changes={[changed, NEW]} sessionId="s1" />);
    expect(screen.getByTestId("review-file-0")).toHaveAttribute("data-reviewed", "false");
    expect(screen.getByTestId("review-rows-0")).toBeInTheDocument();
    cleanup();

    // Another session keeps its own marks.
    withEditor(<ChangesReview changes={[CART]} sessionId="s2" />);
    expect(screen.getByTestId("review-file-0")).toHaveAttribute("data-reviewed", "false");
  });

  it("Collapse reviewed folds every reviewed file; Expand all opens every file", () => {
    withEditor(<ChangesReview changes={[CART, NEW]} sessionId="s1" />);
    expect(screen.getByTestId("review-collapse-reviewed")).toBeDisabled();
    fireEvent.click(screen.getByTestId("review-mark-0"));
    fireEvent.click(screen.getByTestId("review-expand-all"));
    expect(screen.getByTestId("review-rows-0")).toBeInTheDocument();
    expect(screen.getByTestId("review-rows-1")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("review-collapse-reviewed"));
    expect(screen.queryByTestId("review-rows-0")).toBeNull();
    expect(screen.getByTestId("review-rows-1")).toBeInTheDocument();
  });

  it("the file's own header folds and opens it", () => {
    withEditor(<ChangesReview changes={[CART]} sessionId="s1" />);
    const fold = screen.getByTestId("review-fold-0");
    expect(fold).toHaveAttribute("aria-expanded", "true");
    fireEvent.click(fold);
    expect(screen.queryByTestId("review-rows-0")).toBeNull();
    expect(fold).toHaveAttribute("aria-expanded", "false");
  });
});

describe("bounded", () => {
  it(`shows at most ${REVIEW_ROWS_PER_FILE} rows a file until Show all`, () => {
    const lines = Array.from({ length: 600 }, (_, i) => `+line ${i}`).join("\n");
    const big: FileChange = { ...NEW, added: 600, diff: `@@ -0,0 +1,600 @@\n${lines}\n` };
    withEditor(<ChangesReview changes={[big]} sessionId="s1" />);
    const rows = () => screen.getByTestId("review-rows-0").querySelectorAll("[data-kind=added]").length;
    expect(rows()).toBe(REVIEW_ROWS_PER_FILE - 1); // the hunk row is one of them
    const all = screen.getByTestId("review-show-all-0");
    expect(all).toHaveTextContent("Show all 601 lines");
    fireEvent.click(all);
    expect(rows()).toBe(600);
  });

  it("from 8 files on, a file's rows wait until it is near the screen", () => {
    const seen: ((e: { isIntersecting: boolean }[]) => void)[] = [];
    class FakeIO {
      cb: (e: { isIntersecting: boolean }[]) => void;
      constructor(cb: (e: { isIntersecting: boolean }[]) => void) {
        this.cb = cb;
        seen.push(cb);
      }
      observe() {}
      disconnect() {}
    }
    vi.stubGlobal("IntersectionObserver", FakeIO);
    const many = Array.from({ length: 9 }, (_, i) => ({ ...CART, path: `${FOLDER}\\f${i}.py`, rel: `f${i}.py` }));
    withEditor(<ChangesReview changes={many} sessionId="s1" />);
    expect(screen.queryByTestId("review-rows-0")).toBeNull();
    expect(screen.getByTestId("review-pending-0")).toBeInTheDocument();
    act(() => seen[0]([{ isIntersecting: true }]));
    expect(screen.getByTestId("review-rows-0")).toBeInTheDocument();
    expect(screen.queryByTestId("review-rows-1")).toBeNull();
  });

  it("with no IntersectionObserver every file is drawn", () => {
    vi.stubGlobal("IntersectionObserver", undefined);
    const many = Array.from({ length: 9 }, (_, i) => ({ ...CART, path: `${FOLDER}\\f${i}.py`, rel: `f${i}.py` }));
    withEditor(<ChangesReview changes={many} sessionId="s1" />);
    expect(screen.getAllByTestId(/^review-rows-/)).toHaveLength(9);
  });
});

describe("Wide view", () => {
  it("opens the same review in a large dialog, side by side, sharing the Reviewed marks", () => {
    withEditor(<ChangesReview changes={[CART, NEW]} sessionId="s1" />);
    fireEvent.click(screen.getByTestId("review-wide-open"));
    const dlg = screen.getByTestId("review-wide");
    expect(within(dlg).getByRole("dialog")).toHaveAccessibleName("Review the changes");
    expect(within(dlg).getAllByTestId("diff-split").length).toBe(2);
    expect(within(dlg).getAllByTestId("diff-split-row").length).toBeGreaterThan(0);
    fireEvent.click(within(dlg).getByTestId("review-mark-1"));
    expect(within(dlg).getByTestId("review-wide-summary")).toHaveTextContent("1 of 2 reviewed");
    fireEvent.click(within(dlg).getByRole("button", { name: "Close" }));
    expect(screen.queryByTestId("review-wide")).toBeNull();
    expect(screen.getByTestId("review-summary")).toHaveTextContent("1 of 2 reviewed");
  });

  it("the pane itself draws ONE column at any width (it is narrow)", () => {
    withEditor(<ChangesReview changes={[CART]} sessionId="s1" />);
    const unified = within(screen.getByTestId("review-rows-0")).getByTestId("diff-unified");
    expect(unified.className).not.toContain("md:hidden");
    expect(within(screen.getByTestId("review-rows-0")).queryByTestId("diff-split")).toBeNull();
  });
});

/* -------------------------------------------------------- the pane wiring */

function view(over: Record<string, unknown> = {}): BuildSessionView {
  const v = decodeSessionView({
    id: "s1",
    title: "Cart",
    folder: FOLDER,
    folder_name: "shop",
    session_id: "build:s1",
    messages: [{ role: "user", content: "Add a cart", at: "2026-10-22T10:00:00" }],
    ...over,
  });
  if (!v) throw new Error("bad fixture");
  return v;
}

const terminal: SessionTerminalApi = {
  find: () => null,
  open: async () => null,
  render: () => null,
  close: () => {},
};

describe("the Changes pane", () => {
  it("This session: the review, with Undo through the session's own handler", async () => {
    api.gets["POST /chat/changes"] = { changes: [CART, NEW] };
    const onUndo = vi.fn(async () => {});
    withEditor(
      <HarnessPanes
        view={view()}
        livePlan={null}
        terminal={terminal}
        refreshKey={0}
        undoFor={(p) => (p === NEW.path ? { actionId: "u1", undoable: true } : null)}
        onUndo={onUndo}
      />,
    );
    fireEvent.click(screen.getByTestId("harness-pane-tab-changes"));
    expect(await screen.findByTestId("changes-review")).toBeInTheDocument();
    expect(screen.getByTestId("review-rows-0")).toBeInTheDocument();
    expect(screen.getByTestId("review-hint")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("review-undo-1"));
    await waitFor(() => expect(onUndo).toHaveBeenCalledWith("u1", NEW.path));
  });

  it("Branch: the review of the branch's diff, with NO Undo", async () => {
    const git = {
      branch: "ij/cart-1a2b",
      base: "main",
      base_commit: "abc123def456",
      worktree: FOLDER,
      checkout: "C:\\work\\shop-main",
      repo: "C:\\work\\shop-main",
      merged: false,
      merged_into: "",
      removed: false,
      pr_url: "",
    };
    api.gets["/build/sessions/s1/git"] = {
      exists: true, ahead: 1, behind: 0, uncommitted: 0, remote: "origin", pushed: false, unpushed: 1,
      in_base: false, base_exists: true, base_checked_out_at: "", commits: [],
      pr_draft: { title: "", body: "" }, compare_url: "",
      gh: { installed: false, signed_in: false, note: "" },
    };
    api.gets["/build/sessions/s1/git/diff"] = { changes: [CART] };
    withEditor(
      <HarnessPanes
        view={view({ git })}
        livePlan={null}
        terminal={terminal}
        refreshKey={0}
        undoFor={() => ({ actionId: "u1", undoable: true })}
        onUndo={async () => {}}
      />,
    );
    fireEvent.click(screen.getByTestId("harness-pane-tab-changes"));
    const branchScope = await screen.findByTestId("changes-scope-branch");
    if (branchScope.getAttribute("aria-pressed") !== "true") fireEvent.click(branchScope);
    expect(await screen.findByTestId("review-rows-0")).toBeInTheDocument();
    expect(screen.queryByTestId("review-undo-0")).toBeNull();
    expect(screen.queryByTestId("review-hint")).toBeNull();
    expect(screen.getByTestId("review-open-0")).toHaveTextContent("Open in VS Code");
  });
});

/* ------------------------------------------------------------- the steps */

const READ_STEP = {
  name: "read_file",
  ok: true,
  ms: 5,
  detail: { input: '{"path": "src/cart.py", "offset": 40, "limit": 20}', output: "x = 1" },
};

function openWork() {
  fireEvent.click(within(screen.getByTestId("work-line")).getAllByRole("button")[0]);
}

describe("a coding step", () => {
  it("that worked on a file offers Open in VS Code, at a read's offset line, inside a coding session", () => {
    const ed = opener();
    withEditor(<WorkLine steps={[READ_STEP]} />, ed);
    openWork();
    const press = screen.getByTestId("step-open-in-editor");
    expect(press).toHaveTextContent("Open in VS Code");
    expect(press).toHaveAttribute("title", "Open cart.py at line 40 in VS Code");
    fireEvent.click(press);
    expect(ed.opened).toEqual([[`${FOLDER}\\src\\cart.py`, 40, null]]);
  });

  it("shows nothing new on the chat page (no coding session)", () => {
    render(<WorkLine steps={[READ_STEP]} />);
    openWork();
    expect(screen.queryByTestId("step-open-in-editor")).toBeNull();
    expect(screen.queryByTestId("work-row-with-open")).toBeNull();
  });

  it("shows nothing for a step that did not work on a file, failed, or whose path was cut", () => {
    withEditor(
      <WorkLine
        steps={[
          { name: "shell", ok: true, ms: 1, detail: { input: '{"command": "pytest"}', output: "ok" } },
          { ...READ_STEP, ok: false },
          { name: "edit_file", ok: true, ms: 1, detail: { input: '{"path": "src/very/long/fold', output: "" } },
        ]}
      />,
    );
    openWork();
    expect(screen.queryByTestId("step-open-in-editor")).toBeNull();
  });

  it("reads a patch's file from a cut detail, and resolves an absolute path as it is", () => {
    expect(
      stepEditorTarget(
        "apply_patch",
        null,
        '{"patch": "*** Begin Patch\\n*** Update File: src/app.py\\n@@ def main():\\n-    old\\n+    new\\n*** End Pa',
      ),
    ).toEqual({ path: "src/app.py", line: null });
    expect(stepEditorTarget("apply_patch", null, '{"patch": "*** Begin Patch\\n*** Delete File: x.py\\n*** End Patch"}')).toBeNull();
    expect(stepEditorTarget("apply_patch", null, '{"patch": "*** Begin Patch\\n*** Update File: src/ap')).toBeNull();
    expect(stepEditorTarget("write_file", { path: "C:\\elsewhere\\notes.md", content: "…" })).toEqual({
      path: "C:\\elsewhere\\notes.md",
      line: null,
    });
    expect(stepEditorTarget("read_file", null, '{"path": "a\\\\b.py", "offset": 7, "conte')).toEqual({ path: "a\\b.py", line: 7 });
    expect(stepEditorTarget("grep", { path: "src" })).toBeNull();
  });
});

/* -------------------------------------------------------- the findings */

describe("a reviewer's finding", () => {
  it("makes every path:line a press that opens that place", () => {
    const ed = opener();
    withEditor(
      <p>
        <LinkedRefs text={"Two problems: src/cart.py:12 drops the tax, and see `src/api/views.ts:40:7`. Also app.py#L9."} />
      </p>,
      ed,
    );
    const refs = screen.getAllByTestId("editor-ref");
    expect(refs.map((r) => r.textContent)).toEqual(["src/cart.py:12", "src/api/views.ts:40:7", "app.py#L9"]);
    fireEvent.click(refs[1]);
    fireEvent.click(refs[2]);
    expect(ed.opened).toEqual([
      [`${FOLDER}\\src\\api\\views.ts`, 40, 7],
      [`${FOLDER}\\app.py`, 9, null],
    ]);
  });

  it("never follows a URL, never leaves the folder, never a bare word", () => {
    withEditor(
      <p>
        <LinkedRefs
          text={
            "See https://github.com/x/y/blob/main/src/a.py#L12 and ../secrets/key.py:3 and C:\\Windows\\win.ini:1, " +
            "localhost:5173, at 10:30, version 1.2:3, \\\\host\\share\\a.py:4."
          }
        />
      </p>,
    );
    expect(screen.queryByTestId("editor-ref")).toBeNull();
  });

  it("an absolute path inside the folder is a press", () => {
    withEditor(<LinkedRefs text={`Look at ${FOLDER}\\src\\cart.py:5 again.`} />);
    expect(screen.getByTestId("editor-ref")).toHaveTextContent(`${FOLDER}\\src\\cart.py:5`);
  });

  it("is linked in a helper's saved report inside a coding session", () => {
    withEditor(
      <SettledHelperRow
        helper={{
          id: "ij-finish-review",
          session: "s9",
          agent: "reviewer",
          task: "Review the change",
          state: "done",
          steps: 2,
          startedAt: 1,
          endedAt: 2,
          result: "Changes needed: src/cart.py:12 rounds wrong.\nVERDICT: changes needed",
          work: [],
          role: "review",
        }}
      />,
    );
    fireEvent.click(screen.getAllByRole("button")[0]); // open the settled row
    expect(screen.getByTestId("editor-ref")).toHaveTextContent("src/cart.py:12");
  });

  it("finds refs with a tight pattern", () => {
    expect(findEditorRefs("x src/a.py:12 y").map((r) => [r.path, r.line])).toEqual([["src/a.py", 12]]);
    expect(findEditorRefs("README:3 and notes:4")).toEqual([]); // no folder, no source extension
    expect(findEditorRefs("http://example.com/a.py:12")).toEqual([]);
  });

  // v1.405.0 review: [the text, the refs it holds as written]. Never a guess,
  // never PART of a path.
  const REF_TABLE: [string, string[]][] = [
    ["Needs Node.js:18 or later.", []], // a product, not a file
    ["Upgrade to Next.js:15 first.", []],
    ["Released 2024/10/30:12 and again.", []], // a date
    ["See github.com/acme/shop/blob/main/a.py#L4 for it.", []], // a web address with no scheme
    ["Also www.example.org/docs/app.py:3 maybe.", []],
    ["Broken in `my project/app.py:12` today.", []], // a path with a space, in backticks
    ['Broken in "my project/app.py:12" today.', []], // the same in quotes
    ["Look at C:\\work\\my project\\app.py:12 now.", []], // the tail of a path with a space
    ["See src/app.py:12, then lib/x.ts:3.", ["src/app.py:12", "lib/x.ts:3"]],
    ["Fix `src/app.py:12` and `app.js:4`.", ["src/app.py:12", "app.js:4"]],
    ["App.js:7 renders twice.", ["App.js:7"]], // a capitalised FILE is still a file
    ["my project/app.py:12", ["project/app.py:12"]], // plain words before a path are not part of it
  ];
  it.each(REF_TABLE)("%s", (text, want) => {
    expect(findEditorRefs(text).map((r) => r.text)).toEqual(want);
  });
});

/* ------------------------------------------------------------- the paths */

describe("resolveInFolder", () => {
  it("joins a relative path to the folder and keeps it inside", () => {
    expect(resolveInFolder(FOLDER, "src/a.py")).toBe(`${FOLDER}\\src\\a.py`);
    expect(resolveInFolder(FOLDER, "./src/../b.py")).toBe(`${FOLDER}\\b.py`);
    expect(resolveInFolder(FOLDER, "../x.py")).toBeNull();
    expect(resolveInFolder("/home/me/shop", "src/a.py")).toBe("/home/me/shop/src/a.py");
  });

  it("refuses URLs, shares, device paths, drive-relative paths and masked values", () => {
    for (const bad of [
      "https://evil.example/a.py",
      "file:///C:/a.py",
      "vscode://file/C:/a.py",
      "\\\\host\\share\\a.py",
      "//host/share/a.py",
      "C:a.py",
      "src/***/a.py",
      "",
    ]) {
      expect(resolveInFolder(FOLDER, bad), bad).toBeNull();
    }
  });

  it("an absolute path: kept for a step, confined to the folder for a finding", () => {
    expect(resolveInFolder(FOLDER, "C:\\other\\a.py")).toBe("C:\\other\\a.py");
    expect(resolveInFolder(FOLDER, "C:\\other\\a.py", { confine: true })).toBeNull();
    expect(resolveInFolder(FOLDER, "c:\\WORK\\shop\\a.py", { confine: true })).toBe("c:\\WORK\\shop\\a.py");
    expect(resolveInFolder(FOLDER, "C:\\work\\shop\\..\\x.py", { confine: true })).toBeNull();
  });
});

/* --------------------------------------------------------- the options */

describe("the editor options", () => {
  it("are read AGAIN on every coding-session mount, and a session with no editor shows no press", async () => {
    api.gets["/editor/options"] = {
      editors: [
        { id: "vscode", label: "VS Code", installed: false },
        { id: "cursor", label: "Cursor", installed: false },
      ],
      preferred: null,
      preferred_label: "",
      note: "",
    };
    const { unmount } = render(
      <SessionEditorProvider folder={FOLDER}>
        <WorkLine steps={[READ_STEP]} />
      </SessionEditorProvider>,
    );
    await waitFor(() => expect(api.calls).toContain("/editor/options"));
    openWork();
    expect(screen.queryByTestId("step-open-in-editor")).toBeNull();
    unmount();
    render(
      <SessionEditorProvider folder={FOLDER}>
        <ChangesReview changes={[CART]} sessionId="s1" />
      </SessionEditorProvider>,
    );
    await act(async () => {});
    // Moved on purpose (v1.405.0 review): read again on the second mount.
    expect(api.calls.filter((c) => c === "/editor/options")).toHaveLength(2);
    expect(screen.queryByTestId("review-open-0")).toBeNull();
    expect(screen.queryByTestId("review-hunk-open-0")).toBeNull();
  });

  it("a changed setting and an editor installed later show on the next mount, and the press names that editor", async () => {
    api.gets["/editor/options"] = {
      editors: [{ id: "vscode", label: "VS Code", installed: true }],
      preferred: "vscode",
      preferred_label: "VS Code",
    };
    const first = render(
      <SessionEditorProvider folder={FOLDER}>
        <ChangesReview changes={[CART]} sessionId="s1" />
      </SessionEditorProvider>,
    );
    expect(await screen.findByTestId("review-open-0")).toHaveTextContent("Open in VS Code");
    first.unmount();
    // The user moved the setting to Cursor (or installed it) meanwhile.
    api.gets["/editor/options"] = {
      editors: [
        { id: "vscode", label: "VS Code", installed: true },
        { id: "cursor", label: "Cursor", installed: true },
      ],
      preferred: "cursor",
      preferred_label: "Cursor",
    };
    render(
      <SessionEditorProvider folder={FOLDER}>
        <ChangesReview changes={[CART]} sessionId="s1" />
      </SessionEditorProvider>,
    );
    await waitFor(() => expect(screen.getByTestId("review-open-0")).toHaveTextContent("Open in Cursor"));
    fireEvent.click(screen.getByTestId("review-open-0"));
    await waitFor(() => expect(api.posts.some(([p]) => p === "/editor/open")).toBe(true));
    const [, body] = api.posts.find(([p]) => p === "/editor/open") ?? [];
    expect(body).toEqual({ path: CART.path, line: 4, editor: "cursor" });
  });

  it("a session folder on a network share shows no press anywhere (and asks nothing)", async () => {
    api.gets["/editor/options"] = { editors: [], preferred: "vscode", preferred_label: "VS Code" };
    const share = "\\\\fileserver\\team\\shop";
    render(
      <SessionEditorProvider folder={share}>
        <ChangesReview changes={[{ ...CART, path: `${share}\\src\\cart.py` }]} sessionId="s1" />
        <WorkLine steps={[READ_STEP]} />
      </SessionEditorProvider>,
    );
    await act(async () => {});
    openWork();
    expect(screen.queryByTestId("review-open-0")).toBeNull();
    expect(screen.queryByTestId("review-hunk-open-0")).toBeNull();
    expect(screen.queryByTestId("step-open-in-editor")).toBeNull();
    expect(api.calls).not.toContain("/editor/options");
  });

  it("name the editor a press opens, and the press asks the daemon with a path — never a URL", async () => {
    api.gets["/editor/options"] = {
      editors: [{ id: "cursor", label: "Cursor", installed: true }],
      preferred: "cursor",
      preferred_label: "Cursor",
    };
    render(
      <SessionEditorProvider folder={FOLDER}>
        <ChangesReview changes={[CART]} sessionId="s1" />
      </SessionEditorProvider>,
    );
    const press = await screen.findByTestId("review-open-0");
    expect(press).toHaveTextContent("Open in Cursor");
    fireEvent.click(screen.getAllByTestId("review-hunk-open-0")[0]);
    await waitFor(() => expect(api.posts.some(([p]) => p === "/editor/open")).toBe(true));
    const [, body] = api.posts.find(([p]) => p === "/editor/open") ?? [];
    expect(body).toEqual({ path: CART.path, line: 4, editor: "cursor" });
    expect(JSON.stringify(body)).not.toMatch(/:\/\//);
  });

  it("a refusal from the daemon is said beside the press", async () => {
    api.gets["/editor/options"] = { editors: [], preferred: "vscode", preferred_label: "VS Code" };
    api.refuse = "That file is not on this PC any more.";
    render(
      <SessionEditorProvider folder={FOLDER}>
        <ChangesReview changes={[CART]} sessionId="s1" />
      </SessionEditorProvider>,
    );
    fireEvent.click(await screen.findByTestId("review-open-0"));
    expect(await screen.findByTestId("review-open-0-error")).toHaveTextContent("That file is not on this PC any more.");
  });

  it("decode only what the daemon may say", async () => {
    expect(decodeEditorOptions({ preferred: "notepad", editors: [{ id: "notepad" }] })).toEqual({
      editors: [],
      preferred: null,
      preferredLabel: "",
      note: "",
    });
    expect(decodeEditorOptions(null)).toBeNull();
    // A failed read is forgotten: the next caller asks again.
    expect(await readEditorOptions()).toBeNull();
    api.gets["/editor/options"] = { editors: [], preferred: "vscode" };
    expect((await readEditorOptions())?.preferredLabel).toBe("VS Code");
    // An answer is not kept either: the next read is a new request.
    api.gets["/editor/options"] = { editors: [], preferred: "cursor" };
    expect((await readEditorOptions())?.preferredLabel).toBe("Cursor");
  });
});

/* ---------------------------------------------- v1.405.0 review fixes */

interface SavedStep {
  name: string;
  input: string;
  path: string;
  line: number | null;
}
/** What the REAL daemon saves for each file step (chat_turn.step_detail,
 *  recorded by tests/test_code_editor_v1405.py). */
const SAVED_STEPS: SavedStep[] = JSON.parse(
  readFileSync(resolve(__dirname, "fixtures", "editor-step-inputs-v1405.json"), "utf8"),
);

describe("a settled step, from what the daemon really saves", () => {
  it("covers every file tool that saves a long input", () => {
    expect(SAVED_STEPS.map((s) => s.name)).toEqual(["write_file", "edit_file", "multi_edit", "notebook_edit", "read_file"]);
    for (const s of SAVED_STEPS) expect(s.input.length).toBeLessThanOrEqual(300);
  });

  it.each(SAVED_STEPS.map((s) => [s.name, s] as const))("%s keeps its file (and a read its line)", (_n, s) => {
    expect(stepEditorTarget(s.name, null, s.input)).toEqual({ path: s.path, line: s.line });
  });

  it("offers Open in VS Code on a settled write after a reload", () => {
    const ed = opener();
    const write = SAVED_STEPS.find((s) => s.name === "write_file")!;
    withEditor(<WorkLine steps={[{ name: write.name, ok: true, ms: 3, detail: { input: write.input, output: "ok" } }]} />, ed);
    openWork();
    fireEvent.click(screen.getByTestId("step-open-in-editor"));
    expect(ed.opened).toEqual([[`${FOLDER}\\src\\shop\\cart.py`, null, null]]);
  });
});

describe("the Reviewed hash", () => {
  it("changes when the counts, the cut flag or a binary file's size change", () => {
    const cut: FileChange = { ...CART, truncated: true, added: 5000, removed: 3 };
    // A change past the 200 KB cut: the diff text is the same, the counts are not.
    expect(diffHash({ ...cut, added: 5001 })).not.toBe(diffHash(cut));
    expect(diffHash({ ...CART, truncated: true })).not.toBe(diffHash(CART));
    expect(diffHash({ ...LOGO, size: 2048 })).not.toBe(diffHash({ ...LOGO, size: 1024 }));
    expect(diffHash({ ...LOGO, size: 1024 })).toBe(diffHash({ ...LOGO, size: 1024 }));
  });

  it("a reviewed binary file opens again when its size changes", () => {
    const { unmount } = withEditor(<ChangesReview changes={[{ ...LOGO, size: 1024 }]} sessionId="s1" />);
    fireEvent.click(screen.getByTestId("review-mark-0"));
    expect(screen.getByTestId("review-file-0")).toHaveAttribute("data-reviewed", "true");
    unmount();
    withEditor(<ChangesReview changes={[{ ...LOGO, size: 4096 }]} sessionId="s1" />);
    expect(screen.getByTestId("review-file-0")).toHaveAttribute("data-reviewed", "false");
  });

  it("the changes decoder keeps a binary file's size", async () => {
    const { decodeChanges } = await import("@/components/chat/ChangedFiles");
    const [row] = decodeChanges({ changes: [{ path: "C:\\x\\a.png", status: "modified", diff: "binary file", binary: true, size: 77 }] });
    expect(row.size).toBe(77);
    const [plain] = decodeChanges({ changes: [{ path: "C:\\x\\a.py", status: "modified", diff: "" }] });
    expect("size" in plain).toBe(false);
  });
});

describe("names a screen reader hears", () => {
  it("a press's name starts with its own words", () => {
    withEditor(<ChangesReview changes={[CART]} sessionId="s1" />);
    expect(screen.getByRole("button", { name: /^Open at line 4, / })).toBe(screen.getAllByTestId("review-hunk-open-0")[0]);
    expect(screen.getByTestId("review-open-0")).toHaveAccessibleName("Open in VS Code, open cart.py at line 4 in VS Code");
    expect(screen.getAllByTestId("review-hunk-open-0")[1]).toHaveAccessibleName(
      "Open at line 42, open cart.py at line 42 in VS Code",
    );
  });

  it("a finding's press says the place it names first", () => {
    withEditor(<LinkedRefs text="See src/cart.py:12 now." />);
    expect(screen.getByTestId("editor-ref")).toHaveAccessibleName("src/cart.py:12, open src/cart.py at line 12 in VS Code");
  });

  it("every Reviewed box names its file", () => {
    withEditor(<ChangesReview changes={[CART, NEW]} sessionId="s1" />);
    expect(screen.getByRole("checkbox", { name: "Reviewed: src/cart.py" })).toBe(screen.getByTestId("review-mark-0"));
    expect(screen.getByRole("checkbox", { name: "Reviewed: src/new.py" })).toBe(screen.getByTestId("review-mark-1"));
  });
});
