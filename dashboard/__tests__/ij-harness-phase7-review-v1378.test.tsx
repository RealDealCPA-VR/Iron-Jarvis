/**
 * v1.378.0 review (IJ harness part 2, Phase 7): the two screen fixes, at the
 * edges the doer's pins did not reach, and the stale Branch line the
 * walkthrough found.
 *
 * 1. Changes opens on the Branch view only while the branch is a LIVE piece
 *    of work. A session whose worktree was removed (the Branch view then
 *    shows one sentence and no files) or whose branch was merged (its diff
 *    against the base is empty) keeps today's default, "This session": what
 *    the session changed on disk.
 * 2. New session in a session on its own branch opens on the folder the user
 *    PICKED (its group's folder), never the worktree path.
 * 3. After Commit (or Push, or Create PR) the note saying what happened is
 *    shown together with the branch as it is NOW: the walkthrough caught
 *    "Committed 9401…" beside "0 commits … 2 files not committed" and a grey
 *    Push, because the note landed before the branch was read again.
 */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const api = vi.hoisted(() => ({
  gets: {} as Record<string, unknown>,
  /** A GET answered by hand: path -> pending resolvers, in order. */
  held: {} as Record<string, Array<(v: unknown) => void>>,
  holdNext: new Set<string>(),
  posts: [] as Array<[string, unknown]>,
  postAnswers: {} as Record<string, unknown>,
}));
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
    get: (p: string) => {
      if (api.holdNext.has(p)) {
        api.holdNext.delete(p);
        return new Promise((resolve) => {
          (api.held[p] ||= []).push(resolve);
        });
      }
      return p in api.gets ? Promise.resolve(api.gets[p]) : Promise.reject(new ApiError(`unmocked ${p}`, 404));
    },
    post: (p: string, body: unknown) => {
      api.posts.push([p, body]);
      return Promise.resolve(api.postAnswers[p] ?? {});
    },
    put: () => Promise.resolve({}),
    patch: () => Promise.resolve({}),
    del: () => Promise.resolve({}),
  };
});
vi.mock("@/components/terminal/FilesPanel", () => ({ FilesPanel: () => <div data-testid="files-panel" /> }));
vi.mock("@/components/terminal/DirectoryTree", () => ({ DirectoryTree: () => <div data-testid="tree" /> }));
vi.mock("@/components/harness/HarnessSession", () => ({
  HarnessSession: ({ threadId, folderPickFrom }: { threadId: string; folderPickFrom?: string | null }) => (
    <div data-testid="session-stub" data-id={threadId} data-pick-from={folderPickFrom ?? ""} />
  ),
}));

import { HarnessPanes, type SessionTerminalApi } from "@/components/harness/HarnessPanes";
import { IjHarnessView } from "@/components/harness/IjHarnessView";
import { decodeSessionView } from "@/lib/buildSessions";

const REPO = "C:\\work\\shop";
const WT = "C:\\work\\shop.ij-worktrees\\session-3f2a";
const BRANCH = "ij/session-3f2a";
const GIT = {
  branch: BRANCH, base: "main", base_commit: "abc123def456", worktree: WT, checkout: REPO, repo: REPO,
  merged: false, merged_into: "", removed: false, pr_url: "",
};
const STATUS = (over: Record<string, unknown> = {}) => ({
  exists: true, ahead: 0, behind: 0, uncommitted: 2, remote: "origin", pushed: false, unpushed: 0,
  in_base: false, base_exists: true, base_checked_out_at: REPO, commits: [],
  pr_draft: { title: "", body: "" }, compare_url: "", gh: { installed: true, signed_in: true, note: "" },
  ...over,
});
const DIFF = {
  changes: [
    { path: `${WT}\\cart.py`, rel: "cart.py", name: "cart.py", status: "modified", added: 2, removed: 0,
      diff: "@@ -1 +1,3 @@\n x\n+a\n+b\n" },
  ],
  files: 1, added: 2, removed: 0, truncated_files: false, base: "main", merge_base: "abc123def456",
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

function openChanges(over: Record<string, unknown> = {}) {
  render(
    <HarnessPanes view={view(over)} livePlan={null} terminal={terminal} refreshKey={0} undoFor={() => null} onUndo={async () => {}} />,
  );
  fireEvent.click(screen.getByTestId("harness-pane-tab-changes"));
}

beforeEach(() => {
  api.gets = {
    "/build/sessions/s1/git": STATUS(),
    "/build/sessions/s1/git/diff": DIFF,
    "/browser/status": { paired: false },
  };
  api.held = {};
  api.holdNext = new Set();
  api.posts = [];
  api.postAnswers = {};
  localStorage.clear();
  window.history.replaceState(null, "", "/terminals");
});
afterEach(() => cleanup());

describe("Changes' first view follows what the branch still is", () => {
  it("a live branch opens on the Branch view (the doer's fix, unchanged)", () => {
    openChanges();
    expect(screen.getByTestId("changes-scope-branch")).toHaveAttribute("aria-pressed", "true");
  });

  it("a session whose worktree was removed opens on This session, with what it changed", async () => {
    openChanges({ git: { ...GIT, removed: true } });
    expect(screen.getByTestId("changes-scope-session")).toHaveAttribute("aria-pressed", "true");
    expect(screen.queryByTestId("pane-branch-removed")).toBeNull();
    expect(await screen.findByText("What this session changed on disk.")).toBeInTheDocument();
    // The Branch view is still one press away.
    fireEvent.click(screen.getByTestId("changes-scope-branch"));
    expect(screen.getByTestId("pane-branch-removed")).toBeInTheDocument();
  });

  it("a session whose branch was merged opens on This session", async () => {
    openChanges({ git: { ...GIT, merged: true, merged_into: "main" } });
    expect(screen.getByTestId("changes-scope-session")).toHaveAttribute("aria-pressed", "true");
    expect(screen.queryByTestId("branch-status")).toBeNull();
    expect(await screen.findByText("What this session changed on disk.")).toBeInTheDocument();
  });
});

// v1.412.0 (moved on purpose): New session starts with no folder at once;
// where its "Choose…" opens is the folder the user picked, never the worktree.
describe("New session in a session on its own branch", () => {
  it("its picker opens on the folder the user picked, never the worktree", async () => {
    // The daemon groups a session on its own branch under the PICKED folder
    // (harness/sessions.checkout_of); the session's own folder is the worktree.
    api.gets = {
      "/build/sessions": {
        folders: [
          {
            path: REPO, name: "shop", is_project: false, project_id: null,
            sessions: [{ id: "s1", title: "Cart", updated_at: "2026-10-23T10:00:00", running: false, waiting: false, archived: false }],
          },
        ],
      },
    };
    render(
      <IjHarnessView initialSession="s1" terminals={[]} onOpenTerminal={() => {}} defaultFolder={WT} terminal={terminal} />,
    );
    await waitFor(() => expect(screen.getByTestId("session-stub")).toHaveAttribute("data-id", "s1"));
    await screen.findAllByTestId("harness-folder-group");
    api.postAnswers["/build/sessions"] = { id: "new1", title: "New session", folder: "", no_folder: true };
    fireEvent.click(screen.getByTestId("harness-new-session"));
    await waitFor(() => expect(screen.getByTestId("session-stub")).toHaveAttribute("data-id", "new1"));
    const from = screen.getByTestId("session-stub").getAttribute("data-pick-from") ?? "";
    expect(from).toBe(REPO);
    expect(from).not.toContain(".ij-worktrees");
  });
});

describe("after a branch action, the note and the branch line agree", () => {
  it("never shows Committed beside the branch as it was before the commit", async () => {
    api.postAnswers = {
      "/build/sessions/s1/git/draft": { drafted: true, message: "Add the discount function", note: "" },
      "/build/sessions/s1/git/commit": { sha: "94010e252ab3", subject: "Add the discount function", note: "" },
    };
    openChanges();
    await waitFor(() =>
      expect(screen.getByTestId("branch-status")).toHaveTextContent(`0 commits on ${BRANCH} · 2 files not committed`),
    );
    fireEvent.click(screen.getByTestId("branch-commit"));
    const msg = (await screen.findByTestId("branch-commit-message")) as HTMLTextAreaElement;
    await waitFor(() => expect(msg.value).toBe("Add the discount function"));
    await waitFor(() => expect(screen.getByTestId("branch-commit-go")).toBeEnabled());

    // The commit lands; the branch's next read is held until the test says.
    api.gets["/build/sessions/s1/git"] = STATUS({ ahead: 1, uncommitted: 0, unpushed: 1 });
    api.holdNext.add("/build/sessions/s1/git");
    fireEvent.click(screen.getByTestId("branch-commit-go"));
    await waitFor(() => expect(api.posts.some(([p]) => p === "/build/sessions/s1/git/commit")).toBe(true));
    await waitFor(() => expect(api.held["/build/sessions/s1/git"]?.length).toBe(1));

    // While the branch is being read again, nothing pairs the note with the
    // line from before the commit.
    const stalePair = () =>
      !!screen.queryByTestId("branch-note") &&
      /2 files not committed/.test(screen.getByTestId("branch-status").textContent || "");
    expect(stalePair()).toBe(false);

    await act(async () => {
      api.held["/build/sessions/s1/git"][0](api.gets["/build/sessions/s1/git"]);
    });
    await waitFor(() => expect(screen.getByTestId("branch-note")).toHaveTextContent("Committed 94010e252ab3"));
    expect(screen.getByTestId("branch-status")).toHaveTextContent(`1 commit on ${BRANCH} · nothing waiting to commit`);
    expect(screen.getByTestId("branch-push")).toBeEnabled();
  });

  it("an older read that answers last never puts the old branch back", async () => {
    api.postAnswers = {
      "/build/sessions/s1/git/draft": { drafted: true, message: "Add the discount function", note: "" },
      "/build/sessions/s1/git/commit": { sha: "94010e252ab3", subject: "Add the discount function", note: "" },
    };
    openChanges();
    await waitFor(() => expect(screen.getByTestId("branch-status")).toHaveTextContent("2 files not committed"));
    fireEvent.click(screen.getByTestId("branch-commit"));
    const msg = (await screen.findByTestId("branch-commit-message")) as HTMLTextAreaElement;
    await waitFor(() => expect(msg.value).toBe("Add the discount function"));
    await waitFor(() => expect(screen.getByTestId("branch-commit-go")).toBeEnabled());

    // "Read the branch again" is pressed, and that read is slow (it saw the
    // branch before the commit) ...
    api.holdNext.add("/build/sessions/s1/git");
    fireEvent.click(screen.getByLabelText("Read the branch again"));
    await waitFor(() => expect(api.held["/build/sessions/s1/git"]?.length).toBe(1));
    // ... the commit lands and its own read answers first ...
    api.gets["/build/sessions/s1/git"] = STATUS({ ahead: 1, uncommitted: 0, unpushed: 1 });
    fireEvent.click(screen.getByTestId("branch-commit-go"));
    await waitFor(() => expect(screen.getByTestId("branch-note")).toHaveTextContent("Committed 94010e252ab3"));
    expect(screen.getByTestId("branch-status")).toHaveTextContent(`1 commit on ${BRANCH}`);
    // ... and then the old read answers. The line keeps the newer branch.
    await act(async () => {
      api.held["/build/sessions/s1/git"][0](STATUS());
    });
    expect(screen.getByTestId("branch-status")).toHaveTextContent(`1 commit on ${BRANCH} · nothing waiting to commit`);
  });
});
