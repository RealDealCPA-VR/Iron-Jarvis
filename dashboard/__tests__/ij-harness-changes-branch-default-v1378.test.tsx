/**
 * v1.378.0 (IJ harness part 2, Phase 7, the walkthrough) — in a session on
 * its own branch, Changes opens on the Branch view.
 *
 * The walkthrough's third task (a feature on a branch, ending in a pull
 * request) pressed Changes, then Branch, every time it wanted the branch's
 * diff and its Commit / Push / Create PR buttons: the pane opened on "This
 * session" for every session. In a session on its own branch the branch IS
 * that session's work, and the Branch view is where its buttons are (Claude
 * Code's Changes pane shows the diff with Create PR), so it is the first view
 * now; "This session" (the undo journal) is one press away. A session in its
 * folder directly has no scope bar, as before.
 */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const api = vi.hoisted(() => ({ gets: {} as Record<string, unknown> }));
vi.mock("@/lib/api", () => {
  class ApiError extends Error {
    status: number;
    cancelled = false;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
    }
  }
  return {
    ApiError,
    API_BASE: "http://d",
    ijToken: () => "",
    flattenDetail: (d: unknown) => String(d),
    get: (p: string) => (p in api.gets ? Promise.resolve(api.gets[p]) : Promise.reject(new ApiError(`unmocked ${p}`, 404))),
    post: () => Promise.resolve({}),
    put: () => Promise.resolve({}),
    patch: () => Promise.resolve({}),
    del: () => Promise.resolve({}),
  };
});
vi.mock("@/components/terminal/FilesPanel", () => ({ FilesPanel: () => <div data-testid="files-panel" /> }));
vi.mock("@/components/terminal/DirectoryTree", () => ({ DirectoryTree: () => <div data-testid="tree" /> }));

import { HarnessPanes, type SessionTerminalApi } from "@/components/harness/HarnessPanes";
import { decodeSessionView } from "@/lib/buildSessions";

const REPO = "C:\\work\\shop";
const WT = "C:\\work\\shop.ij-worktrees\\session-3f2a";
const BRANCH = "ij/session-3f2a";
const GIT = {
  branch: BRANCH, base: "main", base_commit: "abc123def456", worktree: WT, checkout: REPO, repo: REPO,
  merged: false, merged_into: "", removed: false, pr_url: "",
};

function view(over: Record<string, unknown> = {}) {
  const v = decodeSessionView({
    id: "s1", title: "Cart", updated_at: "2026-10-23T10:00:00", running: false, waiting: false, archived: false,
    folder: WT, folder_name: "shop", is_project: false, project_id: null, mode: "act", plan: null, setup: {},
    messages: [], session_id: "build:s1", queued: [], turn_id: "", git: GIT, ...over,
  });
  if (!v) throw new Error("bad fixture");
  return v;
}

const terminal: SessionTerminalApi = { find: () => null, open: async () => null, render: () => null, close: () => {} };

function panes(over: Record<string, unknown> = {}) {
  render(
    <HarnessPanes view={view(over)} livePlan={null} terminal={terminal} refreshKey={0} undoFor={() => null} onUndo={async () => {}} />,
  );
  fireEvent.click(screen.getByTestId("harness-pane-tab-changes"));
}

beforeEach(() => {
  api.gets = {
    "/build/sessions/s1/git": {
      exists: true, ahead: 0, behind: 0, uncommitted: 2, remote: "origin", pushed: false, unpushed: 0,
      in_base: false, base_exists: true, base_checked_out_at: REPO, commits: [],
      pr_draft: { title: "", body: "" }, compare_url: "", gh: { installed: true, signed_in: true, note: "" },
    },
    "/build/sessions/s1/git/diff": {
      changes: [
        { path: `${WT}\\cart.py`, rel: "cart.py", name: "cart.py", status: "modified", added: 2, removed: 0,
          diff: "@@ -1 +1,3 @@\n x\n+a\n+b\n" },
      ],
      files: 1, added: 2, removed: 0, truncated_files: false, base: "main", merge_base: "abc123def456",
    },
    "/browser/status": { paired: false },
  };
  localStorage.clear();
});
afterEach(() => cleanup());

describe("Changes in a session on its own branch", () => {
  it("opens on the Branch view: the branch's line, its diff and its buttons, with no second press", async () => {
    panes();
    expect(screen.getByTestId("changes-scope-branch")).toHaveAttribute("aria-pressed", "true");
    await waitFor(() =>
      expect(screen.getByTestId("branch-status")).toHaveTextContent(`0 commits on ${BRANCH} · 2 files not committed`),
    );
    expect(screen.getByTestId("branch-commit")).toBeInTheDocument();
    expect(await screen.findByText("cart.py")).toBeInTheDocument();
  });

  it("This session (the undo journal) is one press away", async () => {
    panes();
    fireEvent.click(screen.getByTestId("changes-scope-session"));
    expect(screen.getByTestId("changes-scope-session")).toHaveAttribute("aria-pressed", "true");
    expect(screen.queryByTestId("branch-status")).toBeNull();
    expect(await screen.findByText("What this session changed on disk.")).toBeInTheDocument();
  });

  it("a session in its folder directly has no scope bar and shows what it changed, as before", async () => {
    panes({ git: undefined, folder: REPO });
    expect(screen.queryByTestId("changes-scope")).toBeNull();
    expect(screen.queryByTestId("branch-status")).toBeNull();
    expect(await screen.findByText("What this session changed on disk.")).toBeInTheDocument();
  });
});
