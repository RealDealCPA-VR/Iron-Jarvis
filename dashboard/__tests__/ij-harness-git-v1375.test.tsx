/**
 * v1.375.0 (IJ harness part 2, Phase 4) — a Build session on its OWN git
 * branch, on the screen:
 *
 *  - New session asks the daemon whether the folder is in a repository and
 *    then shows the switch "Work on a new branch" (on by default) — only for
 *    such a folder; a folder outside one, or a PC without git, gets no switch
 *    (and the latter a line saying why);
 *  - the header names the branch and its base;
 *  - the Changes pane gains a Branch view: one line for the branch, the whole
 *    diff against the base, and the person's buttons — Commit (the session's
 *    model drafts, the user edits), Push, Create PR (or, signed out / no
 *    GitHub CLI, the compare link), Merge into <base> (two presses, then
 *    "Remove the worktree") and Discard (two presses) — each refusal in the
 *    daemon's words with git's output folded under Details;
 *  - an agent's commit / push card says why it asks every time;
 *  - nothing pans sideways at 390 px (the rows wrap; long words break).
 */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const api = vi.hoisted(() => {
  class ApiError extends Error {
    status: number;
    cancelled = false;
    body?: unknown;
    constructor(message: string, status = 0, _cancelled = false, body?: unknown) {
      super(message);
      this.status = status;
      if (body !== undefined) this.body = body;
    }
  }
  return {
    ApiError,
    gets: {} as Record<string, unknown>,
    posts: {} as Record<string, unknown>,
    calls: [] as { method: string; path: string; body?: unknown }[],
  };
});

vi.mock("@/lib/api", () => {
  const answer = (method: string, path: string, body?: unknown) => {
    api.calls.push({ method, path, body });
    const hit = method === "GET" ? api.gets[path] : api.posts[`${method} ${path}`];
    const value = typeof hit === "function" ? (hit as (b: unknown) => unknown)(body) : hit;
    if (value instanceof Error) return Promise.reject(value);
    if (value === undefined && method === "GET") return Promise.reject(new api.ApiError(`unmocked ${path}`, 404));
    return Promise.resolve(value ?? {});
  };
  return {
    ApiError: api.ApiError,
    API_BASE: "http://d",
    ijToken: () => "",
    flattenDetail: (d: unknown) => String(d),
    get: (p: string) => answer("GET", p),
    post: (p: string, b?: unknown) => answer("POST", p, b),
    put: (p: string, b?: unknown) => answer("PUT", p, b),
    patch: (p: string, b?: unknown) => answer("PATCH", p, b),
    del: (p: string) => answer("DELETE", p),
  };
});
vi.mock("@/lib/daemon", () => ({
  useDaemon: () => ({
    online: true,
    checking: false,
    health: { default_provider: "mock", default_model: "mock-1", providers: [] },
    refresh: () => {},
  }),
}));
vi.mock("@/lib/useModels", () => ({ useModels: () => ({ models: [], usable: [], loading: false }) }));
vi.mock("@/components/terminal/FilesPanel", () => ({
  FilesPanel: ({ folder }: { folder: string | null }) => <div data-testid="files-panel">{folder}</div>,
}));
vi.mock("@/components/terminal/DirectoryTree", () => ({
  DirectoryTree: () => <div data-testid="tree" />,
}));

import { HarnessStartFolder } from "@/components/harness/HarnessStartFolder";
import { HarnessPanes, type SessionTerminalApi } from "@/components/harness/HarnessPanes";
import { HarnessSession } from "@/components/harness/HarnessSession";
import { ApprovalCard } from "@/components/chat/ApprovalCard";
import { decodeSessionView } from "@/lib/buildSessions";
import { branchLine, decodeBranchStatus, statusLine } from "@/lib/buildGit";

const REPO = "C:\\work\\shop";
const WT = "C:\\work\\shop.ij-worktrees\\session-3f2a";
const BRANCH = "ij/session-3f2a";
const GIT = {
  branch: BRANCH, base: "main", base_commit: "abc123def456", worktree: WT, checkout: REPO, repo: REPO,
  merged: false, merged_into: "", removed: false, pr_url: "",
};

function rawView(over: Record<string, unknown> = {}) {
  return {
    id: "s1", title: "Cart", updated_at: "2026-10-23T10:00:00", running: false, waiting: false, archived: false,
    folder: WT, folder_name: "shop", is_project: false, project_id: null, mode: "act", plan: null, setup: {},
    messages: [], session_id: "build:s1", queued: [], turn_id: "", git: GIT, ...over,
  };
}

function status(over: Record<string, unknown> = {}) {
  return {
    exists: true, ahead: 1, behind: 0, uncommitted: 2, remote: "origin", pushed: false, unpushed: 1,
    in_base: false, base_exists: true, base_checked_out_at: REPO,
    commits: [{ sha: "1a2b3c4", subject: "Add the cart" }],
    pr_draft: { title: "Add the cart", body: "" },
    compare_url: `https://github.com/acme/shop/compare/main...${BRANCH}?expand=1`,
    gh: { installed: true, signed_in: true, note: "" },
    ...over,
  };
}

const DIFF = {
  changes: [
    { path: `${WT}\\cart.py`, rel: "cart.py", name: "cart.py", status: "created", added: 2, removed: 0,
      diff: "--- /dev/null\n+++ b/cart.py\n@@ -0,0 +1,2 @@\n+a\n+b\n" },
    { path: `${WT}\\app.py`, rel: "app.py", name: "app.py", status: "modified", added: 1, removed: 1,
      diff: "@@ -1 +1 @@\n-x = 1\n+x = 2\n" },
  ],
  files: 2, added: 3, removed: 1, truncated_files: false, base: "main", merge_base: "abc123def456",
};

const terminal: SessionTerminalApi = {
  find: () => null,
  open: async () => null,
  render: () => <div data-testid="term-body" />,
  close: () => {},
};

beforeEach(() => {
  api.gets = {
    "/build/sessions/s1/git": status(),
    "/build/sessions/s1/git/diff": DIFF,
    "/browser/status": { paired: false },
  };
  api.posts = {};
  api.calls = [];
  localStorage.clear();
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function view(over: Record<string, unknown> = {}) {
  const v = decodeSessionView(rawView(over));
  if (!v) throw new Error("bad fixture");
  return v;
}

function renderBranch(over: Record<string, unknown> = {}, extra: Partial<React.ComponentProps<typeof HarnessPanes>> = {}) {
  const onGone = vi.fn();
  const onChanged = vi.fn();
  render(
    <HarnessPanes
      view={view(over)}
      livePlan={null}
      terminal={terminal}
      refreshKey={0}
      undoFor={() => null}
      onUndo={async () => {}}
      onSessionChanged={onChanged}
      onSessionGone={onGone}
      {...extra}
    />,
  );
  fireEvent.click(screen.getByTestId("harness-pane-tab-changes"));
  fireEvent.click(screen.getByTestId("changes-scope-branch"));
  return { onGone, onChanged };
}

const posted = (path: string) => api.calls.filter((c) => c.method === "POST" && c.path === path);

/* ------------------------------------------------------------ New session */

// v1.412.0 (moved on purpose): New session starts at once with no folder;
// the folder is chosen under the composer (components/harness/
// HarnessStartFolder), and the branch switch is asked THERE, for a repository.
describe("choosing a folder asks about the branch only for a repository", () => {
  function renderChip(onMove = vi.fn(async (_f: string, _b?: boolean) => null as string | null)) {
    (window as unknown as { ironjarvis?: unknown }).ironjarvis = {
      isDesktop: true,
      dialog: { pickFolder: async () => ({ path: REPO }) },
    };
    render(<HarnessStartFolder folder={null} workRoot="" onMove={onMove} />);
    fireEvent.click(screen.getByTestId("harness-start-folder-choose"));
    return onMove;
  }
  afterEach(() => {
    delete (window as unknown as { ironjarvis?: unknown }).ironjarvis;
  });
  const folderPath = (p: string) => `/build/git/folder?path=${encodeURIComponent(p)}`;

  it("a repository: the switch is on by default, and turning it off works in the folder directly", async () => {
    api.gets[folderPath(REPO)] = { git: true, repo: true, branch: "main", can_branch: true, note: "", worktrees_dir: `${REPO}.ij-worktrees` };
    const onMove = renderChip();
    const sw = (await screen.findByTestId("harness-start-branch-switch")) as HTMLInputElement;
    expect(sw.checked).toBe(true);
    expect(screen.getByTestId("harness-start-branch")).toHaveTextContent("on its own ij/ branch from main");
    expect(screen.getByTestId("harness-start-branch")).toHaveTextContent("Your checkout is not touched.");
    expect(onMove).not.toHaveBeenCalled();
    fireEvent.click(sw);
    expect(screen.getByTestId("harness-start-branch")).toHaveTextContent("works in the folder directly");
    fireEvent.click(screen.getByTestId("harness-start-branch-use"));
    await waitFor(() => expect(onMove).toHaveBeenCalledWith(REPO, false));
  });

  it("the switch left on sends branch: true", async () => {
    api.gets[folderPath(REPO)] = { git: true, repo: true, branch: "main", can_branch: true, note: "", worktrees_dir: "" };
    const onMove = renderChip();
    await screen.findByTestId("harness-start-branch-switch");
    fireEvent.click(screen.getByTestId("harness-start-branch-use"));
    await waitFor(() => expect(onMove).toHaveBeenCalledWith(REPO, true));
  });

  it("a folder outside a repository: no switch, no line, the folder alone is sent (exactly as before)", async () => {
    api.gets[folderPath(REPO)] = { git: true, repo: false, branch: "", can_branch: false, note: "not in a repository", worktrees_dir: "" };
    const onMove = renderChip();
    await waitFor(() => expect(onMove).toHaveBeenCalled());
    expect(onMove.mock.calls[0]).toEqual([REPO, undefined]);
    expect(screen.queryByTestId("harness-start-branch")).toBeNull();
    expect(screen.queryByTestId("harness-start-folder-note")).toBeNull();
  });

  it("no git on this PC: no switch, and a line says why", async () => {
    api.gets[folderPath(REPO)] = { git: false, repo: false, branch: "", can_branch: false, note: "", worktrees_dir: "" };
    const onMove = renderChip();
    expect(await screen.findByTestId("harness-start-folder-note")).toHaveTextContent(
      "Git is not installed on this PC, so the session works in the folder directly.",
    );
    expect(screen.queryByTestId("harness-start-branch")).toBeNull();
    await waitFor(() => expect(onMove).toHaveBeenCalledWith(REPO, undefined));
  });

  it("a repository that cannot branch says why in the daemon's words", async () => {
    api.gets[folderPath(REPO)] = { git: true, repo: true, branch: "main", can_branch: false, note: "This repository has no commits yet, so a branch cannot start from it.", worktrees_dir: "" };
    renderChip();
    expect(await screen.findByTestId("harness-start-folder-note")).toHaveTextContent("no commits yet");
    expect(screen.queryByTestId("harness-start-branch")).toBeNull();
  });
});

/* -------------------------------------------------------------- the header */

describe("the header names the branch and its base", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("", { status: 200 })));
  });
  afterEach(() => vi.unstubAllGlobals());

  it("shows the branch from its base, with the worktree in the title", async () => {
    api.gets["/build/sessions/s1"] = rawView();
    api.gets["/build/accounts"] = new api.ApiError("no such route", 404);
    render(<HarnessSession threadId="s1" terminal={terminal} listRunning={false} onChanged={() => {}} onGone={() => {}} />);
    const line = await screen.findByTestId("harness-branch");
    expect(line).toHaveTextContent(`${BRANCH} from main`);
    expect(line.getAttribute("title")).toContain(WT);
    expect(line.className).toContain("truncate");
  });

  it("a session that works in its folder directly has no branch line", async () => {
    api.gets["/build/sessions/s1"] = { ...rawView(), git: undefined, folder: REPO };
    api.gets["/build/accounts"] = new api.ApiError("no such route", 404);
    render(<HarnessSession threadId="s1" terminal={terminal} listRunning={false} onChanged={() => {}} onGone={() => {}} />);
    await screen.findByTestId("harness-folder");
    expect(screen.queryByTestId("harness-branch")).toBeNull();
  });

  it("says when the branch was merged, and when its worktree was removed", () => {
    const g = view().git!;
    expect(branchLine({ ...g, merged: true, mergedInto: "main" })).toBe(`${BRANCH} from main · merged into main`);
    expect(branchLine({ ...g, removed: true })).toBe(`${BRANCH} from main · worktree removed`);
  });
});

/* ---------------------------------------------------------- the Branch view */

describe("the Branch view", () => {
  it("is offered only for a session on a branch; it shows the line and the whole diff against the base", async () => {
    render(
      <HarnessPanes view={view({ git: undefined })} livePlan={null} terminal={terminal} refreshKey={0}
        undoFor={() => null} onUndo={async () => {}} />,
    );
    fireEvent.click(screen.getByTestId("harness-pane-tab-changes"));
    expect(screen.queryByTestId("changes-scope")).toBeNull();
    cleanup();
    renderBranch();
    // The line is drawn ("Reading the branch…") before the daemon answers:
    // wait for the WORDS, never for the element (the CLAUDE.md waitFor rule).
    await waitFor(() =>
      expect(screen.getByTestId("branch-status")).toHaveTextContent(
        `1 commit on ${BRANCH} · 2 files not committed · 1 not pushed to origin`,
      ),
    );
    expect(await screen.findByText("cart.py")).toBeInTheDocument();
    expect(screen.getByText("app.py")).toBeInTheDocument();
    expect(screen.getByTestId("changes-scope-branch")).toHaveAttribute("aria-pressed", "true");
  });

  it("Commit: the session's model drafts, the user edits, and the edited words are committed", async () => {
    api.posts["POST /build/sessions/s1/git/draft"] = { drafted: true, message: "Add the cart\n\nA new page." };
    api.posts["POST /build/sessions/s1/git/commit"] = { committed: true, sha: "9f8e7d6c5b4a", subject: "Add the cart page" };
    const { onChanged } = renderBranch();
    await screen.findByText("cart.py");
    fireEvent.click(screen.getByTestId("branch-commit"));
    const box = (await screen.findByTestId("branch-commit-message")) as HTMLTextAreaElement;
    await waitFor(() => expect(box.value).toBe("Add the cart\n\nA new page."));
    fireEvent.change(box, { target: { value: "Add the cart page\n\nA new page." } });
    fireEvent.click(screen.getByTestId("branch-commit-go"));
    expect(await screen.findByTestId("branch-note")).toHaveTextContent("Committed 9f8e7d6c5b4a: Add the cart page");
    expect(posted("/build/sessions/s1/git/commit")[0].body).toEqual({ message: "Add the cart page\n\nA new page." });
    expect(onChanged).toHaveBeenCalled();
    expect(screen.queryByTestId("branch-commit-box")).toBeNull();
  });

  it("the demo model drafts nothing and says so; the box waits for the user's own words", async () => {
    api.posts["POST /build/sessions/s1/git/draft"] = {
      drafted: false, message: "", note: "The demo model does not draft commit messages. Write the message yourself.",
    };
    renderBranch();
    await screen.findByText("cart.py");
    fireEvent.click(screen.getByTestId("branch-commit"));
    expect(await screen.findByTestId("branch-draft-note")).toHaveTextContent("The demo model does not draft");
    expect((screen.getByTestId("branch-commit-message") as HTMLTextAreaElement).value).toBe("");
    expect(screen.getByTestId("branch-commit-go")).toBeDisabled();
  });

  it("Commit is off with nothing to commit; Push is off when the remote has every commit", async () => {
    api.gets["/build/sessions/s1/git"] = status({ uncommitted: 0, pushed: true, unpushed: 0 });
    renderBranch();
    await screen.findByText(`1 commit on ${BRANCH} · nothing waiting to commit · pushed to origin`);
    expect(screen.getByTestId("branch-commit")).toBeDisabled();
    expect(screen.getByTestId("branch-push")).toBeDisabled();
  });

  it("Push says where it went; a refusal says the daemon's words and folds git's output under Details", async () => {
    api.posts["POST /build/sessions/s1/git/push"] = { pushed: true, remote: "origin", branch: BRANCH };
    renderBranch();
    await screen.findByText("cart.py");
    fireEvent.click(screen.getByTestId("branch-push"));
    expect(await screen.findByTestId("branch-note")).toHaveTextContent(`Pushed ${BRANCH} to origin.`);
    api.posts["POST /build/sessions/s1/git/push"] = new api.ApiError(
      "flattened", 409, false,
      { detail: "Git could not sign in to the remote \"origin\", so nothing was pushed.", details: "fatal: Authentication failed for 'https://***@github.com/acme/shop.git/'" },
    );
    await waitFor(() => expect(screen.getByTestId("branch-push")).not.toBeDisabled());
    fireEvent.click(screen.getByTestId("branch-push"));
    const err = await screen.findByTestId("branch-error");
    expect(err).toHaveAttribute("role", "alert");
    expect(err).toHaveTextContent("Git could not sign in to the remote \"origin\", so nothing was pushed.");
    expect(within(err).getByText("Details")).toBeInTheDocument();
    expect(screen.getByTestId("branch-error-details")).toHaveTextContent("Authentication failed");
  });

  it("Create PR, signed in: the box is prefilled, the link and the checks follow", async () => {
    api.posts["POST /build/sessions/s1/git/pr"] = { url: "https://github.com/acme/shop/pull/7", number: 7 };
    api.gets["/build/sessions/s1/git/pr"] = {
      url: "https://github.com/acme/shop/pull/7", number: 7, state: "open", words: "1 of 2 checks failed.",
      checks: { passed: 1, failed: 1, pending: 0 },
    };
    const { onChanged } = renderBranch();
    await screen.findByText("cart.py");
    fireEvent.click(screen.getByTestId("branch-pr"));
    const title = (await screen.findByTestId("branch-pr-title")) as HTMLInputElement;
    expect(title.value).toBe("Add the cart");
    fireEvent.change(title, { target: { value: "Add the cart page" } });
    fireEvent.change(screen.getByTestId("branch-pr-body"), { target: { value: "Closes #3" } });
    fireEvent.click(screen.getByTestId("branch-pr-go"));
    expect(await screen.findByTestId("branch-note")).toHaveTextContent("Pull request #7 is open.");
    expect(posted("/build/sessions/s1/git/pr")[0].body).toEqual({ title: "Add the cart page", body: "Closes #3" });
    expect(await screen.findByTestId("branch-pr-checks")).toHaveTextContent("1 of 2 checks failed.");
    expect(screen.getByTestId("branch-pr-checks").className).toContain("text-tone-danger");
    expect(screen.getByTestId("branch-pr-link")).toHaveAttribute("href", "https://github.com/acme/shop/pull/7");
    expect(onChanged).toHaveBeenCalled();
  });

  it("Create PR, signed out or no GitHub CLI: the words say why and the compare link opens GitHub's page", async () => {
    api.gets["/build/sessions/s1/git"] = status({
      gh: { installed: false, signed_in: false, note: "The GitHub CLI (gh) is not installed, so Create PR opens GitHub's own page instead." },
    });
    renderBranch();
    await screen.findByText("cart.py");
    fireEvent.click(screen.getByTestId("branch-pr"));
    const fb = await screen.findByTestId("branch-pr-fallback");
    expect(fb).toHaveTextContent("is not installed, so Create PR opens GitHub's own page instead.");
    expect(screen.getByTestId("branch-compare-link")).toHaveAttribute(
      "href", `https://github.com/acme/shop/compare/main...${BRANCH}?expand=1`,
    );
    expect(screen.queryByTestId("branch-pr-box")).toBeNull();
    expect(posted("/build/sessions/s1/git/pr")).toEqual([]);
  });

  it("Merge takes two presses, then offers to remove the worktree (two presses), which archives the session", async () => {
    api.posts["POST /build/sessions/s1/git/merge"] = { merged: true, into: "main", where: REPO, commit: "aa11", offer_remove: true };
    api.posts["POST /build/sessions/s1/git/discard"] = { moved: true, trash: "C:\\home\\trash\\x", note: `The worktree is in Iron Jarvis's trash and the branch ${BRANCH} is kept.` };
    const { onGone } = renderBranch();
    await screen.findByText("cart.py");
    const merge = screen.getByRole("button", { name: "Merge into main" });
    fireEvent.click(merge);
    expect(posted("/build/sessions/s1/git/merge")).toEqual([]);
    fireEvent.click(screen.getByRole("button", { name: "Confirm merge" }));
    expect(await screen.findByTestId("branch-note")).toHaveTextContent(`Merged into main in ${REPO}.`);
    const offer = await screen.findByTestId("branch-merged");
    fireEvent.click(within(offer).getByRole("button", { name: "Remove the worktree" }));
    expect(posted("/build/sessions/s1/git/discard")).toEqual([]);
    fireEvent.click(within(offer).getByRole("button", { name: "Confirm remove" }));
    await waitFor(() => expect(onGone).toHaveBeenCalled());
    expect(posted("/build/sessions/s1/git/discard")[0].body).toEqual({ confirm: true, after_merge: true });
  });

  it("review: Merge is gone as soon as the merge answers, before the branch is read again", async () => {
    api.posts["POST /build/sessions/s1/git/merge"] = { merged: true, into: "main", where: REPO, commit: "aa11", offer_remove: true };
    renderBranch();
    await screen.findByText("cart.py");
    fireEvent.click(screen.getByRole("button", { name: "Merge into main" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm merge" }));
    await screen.findByTestId("branch-merged");
    // The stale read still says "1 ahead, not in main" (this fixture never
    // changes): the button must not come back from it.
    await waitFor(() => expect(api.calls.filter((c) => c.path === "/build/sessions/s1/git").length).toBeGreaterThan(1));
    expect(screen.queryByRole("button", { name: "Merge into main" })).toBeNull();
  });

  it("review: Discard's second press says nothing more when everything is committed", async () => {
    api.gets["/build/sessions/s1/git"] = status({ uncommitted: 0 });
    api.posts["POST /build/sessions/s1/git/discard"] = { moved: true, trash: "C:\\home\\trash\\x", note: "kept" };
    renderBranch();
    await screen.findByText(`1 commit on ${BRANCH} · nothing waiting to commit · 1 not pushed to origin`);
    fireEvent.click(screen.getByRole("button", { name: "Discard" }));
    expect(screen.getByRole("button", { name: "Confirm discard" })).toBeTruthy();
  });

  it("a refused merge says the daemon's words; nothing else happens", async () => {
    api.posts["POST /build/sessions/s1/git/merge"] = new api.ApiError("x", 409, false, {
      detail: "Merging would conflict in app.py. Nothing was changed: bring main's changes into this branch and settle them first, then merge.",
    });
    renderBranch();
    await screen.findByText("cart.py");
    fireEvent.click(screen.getByRole("button", { name: "Merge into main" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm merge" }));
    expect(await screen.findByTestId("branch-error")).toHaveTextContent("Merging would conflict in app.py. Nothing was changed");
    expect(screen.queryByTestId("branch-merged")).toBeNull();
  });

  it("Merge is not offered when there is nothing to merge", async () => {
    api.gets["/build/sessions/s1/git"] = status({ ahead: 0, in_base: true });
    renderBranch();
    await screen.findByTestId("branch-status");
    await screen.findByText(`0 commits on ${BRANCH} · 2 files not committed · 1 not pushed to origin`);
    expect(screen.queryByRole("button", { name: "Merge into main" })).toBeNull();
  });

  it("Discard takes two presses, keeps the branch, and archives the session", async () => {
    api.posts["POST /build/sessions/s1/git/discard"] = { moved: true, trash: "C:\\home\\trash\\x", note: "kept" };
    const { onGone } = renderBranch();
    await screen.findByText("cart.py");
    const discard = screen.getByRole("button", { name: "Discard" });
    expect(discard.getAttribute("title")).toContain(`the branch ${BRANCH} stays`);
    fireEvent.click(discard);
    expect(posted("/build/sessions/s1/git/discard")).toEqual([]);
    // Review (v1.375.0): the second press names the work not committed (the
    // fixture's status has 2 files) — it goes to the trash with the folder.
    fireEvent.click(screen.getByRole("button", { name: "Confirm: 2 uncommitted files go to the trash" }));
    await waitFor(() => expect(onGone).toHaveBeenCalled());
    expect(posted("/build/sessions/s1/git/discard")[0].body).toEqual({ confirm: true, after_merge: false });
  });

  it("a removed worktree says so and offers nothing", async () => {
    renderBranch({ git: { ...GIT, removed: true } });
    expect(await screen.findByTestId("pane-branch-removed")).toHaveTextContent(`The branch ${BRANCH} is kept`);
    expect(screen.queryByTestId("branch-actions")).toBeNull();
    expect(api.calls.filter((c) => c.path.startsWith("/build/sessions/s1/git"))).toEqual([]);
  });

  it("nothing pans sideways at 390 px: the rows wrap and long words break", async () => {
    window.innerWidth = 390;
    renderBranch();
    await screen.findByText("cart.py");
    expect(screen.getByTestId("branch-actions").className).toContain("flex-wrap");
    expect(screen.getByTestId("branch-status").className).toContain("min-w-0");
    expect(screen.getByTestId("pane-branch").className).toContain("min-w-0");
    fireEvent.click(screen.getByTestId("branch-push"));
    expect(await screen.findByTestId("branch-note")).toHaveClass("break-words");
  });
});

/* ------------------------------------------------- the agent's commit card */

describe("an agent's commit or push card says why it asks every time", () => {
  it("names the branch rule, not the local-only one, and offers Allow once and Deny only", () => {
    render(
      <ApprovalCard
        approval={{ id: "a1", callId: "c1", tool: "shell", args: { command: "git push origin HEAD" },
                    onceOnly: true, onceReason: "git", costNote: "This pushes to origin. Every push from a session's branch asks you first." }}
        docked
      />,
    );
    expect(screen.getByTestId("approval-git-once")).toHaveTextContent("asks you every time");
    // Review (v1.375.0): true for every kind that cards, not commit / push only.
    expect(screen.getByTestId("approval-git-once")).toHaveTextContent(/git setting.*throws work away/);
    expect(screen.queryByTestId("approval-local-only")).toBeNull();
    expect(screen.getByTestId("approval-cost")).toHaveTextContent("This pushes to origin.");
    expect(screen.getByRole("button", { name: "Allow once" })).toBeInTheDocument();
    expect(screen.queryByTestId("approval-scope")).toBeNull();
  });
});

describe("the card arrives through the session's own stream", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("an approval frame with once_reason git draws the branch rule on the docked card", async () => {
    let ctl!: ReadableStreamDefaultController<Uint8Array>;
    const body = new ReadableStream<Uint8Array>({ start(c) { ctl = c; } });
    vi.stubGlobal("fetch", vi.fn(async () => new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } })));
    api.gets["/build/sessions/s1"] = rawView();
    api.gets["/build/accounts"] = new api.ApiError("no such route", 404);
    render(<HarnessSession threadId="s1" terminal={terminal} listRunning={false} onChanged={() => {}} onGone={() => {}} />);
    await screen.findByTestId("harness-empty");
    const box = screen.getByTestId("harness-input") as HTMLTextAreaElement;
    fireEvent.change(box, { target: { value: "Commit it" } });
    fireEvent.keyDown(box, { key: "Enter" });
    await waitFor(() => expect((globalThis.fetch as unknown as ReturnType<typeof vi.fn>).mock.calls.length).toBe(1));
    const frame = {
      id: "ap1", call_id: "c1", tool: "shell", args: { command: "git commit -am wip" },
      once_only: true, once_reason: "git", cost_note: `This makes a commit on ${BRANCH}. Every commit asks you first.`,
    };
    await act(async () => {
      ctl.enqueue(new TextEncoder().encode(`event: approval\ndata: ${JSON.stringify(frame)}\n\n`));
    });
    const card = await screen.findByTestId("chat-approval-card");
    expect(within(card).getByTestId("approval-git-once")).toHaveTextContent("asks you every time");
    expect(within(card).queryByTestId("approval-local-only")).toBeNull();
    expect(within(card).getByTestId("approval-cost")).toHaveTextContent(`This makes a commit on ${BRANCH}.`);
    await act(async () => {
      ctl.close();
    });
  });
});

describe("the decoders whitelist", () => {
  it("drops a compare link that is not a web page", () => {
    expect(decodeBranchStatus({ compare_url: "javascript:alert(1)" })!.compareUrl).toBe("");
    expect(statusLine(decodeBranchStatus({ remote: "" })!, BRANCH)).toBe(
      `0 commits on ${BRANCH} · nothing waiting to commit · no remote`,
    );
  });
});
