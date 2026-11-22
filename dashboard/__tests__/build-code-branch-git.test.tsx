/**
 * 22-b (R3.4): Build › Code's Commit and Open a pull request, one press each
 * (components/buildCode/ReplyGit, lib/buildAppGit, routes/build_app_git.py).
 *
 * Under the newest settled reply the message is ALREADY there (written by
 * Iron Jarvis from the session's record), so Commit is one press and Open a
 * pull request the next. A session in the folder directly commits only the
 * ticked files; one on the main branch is not offered a pull request and is
 * told why. A branch session is grouped under the folder the user picked.
 * The start box offers a new branch ONCE per folder ("Work on a new branch
 * in this folder?") and keeps the answer; a yes starts the session on it.
 */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const api = vi.hoisted(() => {
  class ApiError extends Error {
    status: number;
    body: unknown;
    constructor(message: string, status = 0, body?: unknown) {
      super(message);
      this.status = status;
      this.body = body;
    }
  }
  return {
    ApiError,
    gets: {} as Record<string, unknown>,
    getCalls: [] as string[],
    posts: [] as { path: string; body: unknown }[],
    puts: [] as { path: string; body: unknown }[],
    postReply: {} as Record<string, unknown>,
    afterPost: {} as Record<string, () => void>,
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: api.ApiError,
  API_BASE: "http://d",
  ijToken: () => "",
  get: (p: string) => {
    api.getCalls.push(p);
    const hit = api.gets[p];
    return hit === undefined ? Promise.reject(new api.ApiError(`unmocked ${p}`, 404)) : Promise.resolve(hit);
  },
  post: (p: string, b?: unknown) => {
    api.posts.push({ path: p, body: b });
    api.afterPost[p]?.();
    return Promise.resolve(api.postReply[p] ?? {});
  },
  put: (p: string, b?: unknown) => {
    api.puts.push({ path: p, body: b });
    const body = b as { view?: string; defaults?: unknown };
    return Promise.resolve({ folder: "", defaults: body.view === "branch" ? { branch: body.defaults } : {} });
  },
  patch: () => Promise.resolve({}),
  del: () => Promise.resolve({}),
}));
vi.mock("@/lib/useModels", () => ({ useModels: () => ({ models: [], usable: [], loading: false }) }));
vi.mock("@/components/terminal/DirectoryTree", () => ({ DirectoryTree: () => null }));

import { ReplyGit } from "@/components/buildCode/ReplyGit";
import { StartComposer } from "@/components/buildCode/StartComposer";
import { decodeCodeSession, decodeCodingTools, groupByFolder, sessionFolderWords } from "@/lib/buildApp";
import { BRANCH_OFFER, OPEN_PR_LABEL, decodeAppGit, ghNoteWords, hasGitWork } from "@/lib/buildAppGit";
import { resetFolderDefaultsCache } from "@/lib/folderDefaults";

const SID = "app_1";
const GIT = `/build/app/sessions/${SID}/git`;
const DRAFT = "Add a rate limiter to the api\n\nFiles:\n- app.py\n- api/limits.py";

function folderGit(over: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    kind: "folder",
    repo: "C:\\work\\shop",
    branch: "feature",
    main: "main",
    on_main: false,
    files: [
      { rel: "README.md", status: "modified", ticked: false },
      { rel: "app.py", status: "modified", ticked: true },
      { rel: "api/limits.py", status: "created", ticked: true },
    ],
    more: 0,
    uncommitted: 3,
    remote: "origin",
    remote_host: "github.com",
    pushed: false,
    ahead: 0,
    draft: DRAFT,
    pr_url: "",
    can_pr: true,
    pr_note: "",
    pr_draft: { title: "", body: "" },
    compare_url: "https://github.com/acme/shop/compare/main...feature?expand=1",
    gh: { installed: true, signed_in: true, note: "" },
    ...over,
  };
}

beforeEach(() => {
  resetFolderDefaultsCache();
  window.localStorage.clear();
  api.gets = {};
  api.getCalls = [];
  api.posts = [];
  api.puts = [];
  api.postReply = {};
  api.afterPost = {};
});
afterEach(() => cleanup());

describe("Commit and Open a pull request under the reply", () => {
  it("commits the ticked files with the drafted message, then opens the pull request: two presses", async () => {
    api.gets[GIT] = folderGit();
    api.postReply[`${GIT}/commit`] = { committed: true, sha: "abc1234", subject: "Add a rate limiter to the api", files: 2 };
    api.afterPost[`${GIT}/commit`] = () => {
      api.gets[GIT] = folderGit({
        files: [{ rel: "README.md", status: "modified", ticked: false }],
        uncommitted: 1,
        ahead: 1,
        pr_draft: { title: "Add a rate limiter to the api", body: "Files:\n- app.py" },
      });
    };
    api.postReply[`${GIT}/pr`] = { url: "https://github.com/acme/shop/pull/7", number: 7 };
    render(<ReplyGit sessionId={SID} readKey="r1" />);
    const message = (await screen.findByTestId("code-git-message")) as HTMLTextAreaElement;
    expect(message.value).toBe(DRAFT);
    const ticks = screen.getAllByTestId("code-git-tick") as HTMLInputElement[];
    expect(ticks.map((t) => [t.dataset.rel, t.checked])).toEqual([
      ["README.md", false],
      ["app.py", true],
      ["api/limits.py", true],
    ]);
    // No pull request before there is a commit for it.
    expect(screen.queryByTestId("code-git-open-pr")).toBeNull();

    fireEvent.click(screen.getByTestId("code-git-commit")); // press 1
    await waitFor(() => expect(api.posts.some((p) => p.path === `${GIT}/commit`)).toBe(true));
    expect(api.posts.find((p) => p.path === `${GIT}/commit`)!.body).toEqual({
      message: DRAFT,
      paths: ["app.py", "api/limits.py"],
    });
    const title = (await screen.findByTestId("code-git-pr-title")) as HTMLInputElement;
    expect(title.value).toBe("Add a rate limiter to the api");
    fireEvent.click(screen.getByTestId("code-git-open-pr")); // press 2
    await waitFor(() => expect(api.posts.some((p) => p.path === `${GIT}/pr`)).toBe(true));
    expect(api.posts.find((p) => p.path === `${GIT}/pr`)!.body).toEqual({
      title: "Add a rate limiter to the api",
      body: "Files:\n- app.py",
    });
    expect((await screen.findByTestId("code-git-pr-link")).getAttribute("href")).toBe("https://github.com/acme/shop/pull/7");
    expect(api.posts.filter((p) => p.path.startsWith(GIT)).map((p) => p.path)).toEqual([`${GIT}/commit`, `${GIT}/pr`]);
  });

  it("an unticked file is left out, and the message is written again for the ticks (no model)", async () => {
    api.gets[GIT] = folderGit();
    api.postReply[`${GIT}/draft`] = { drafted: true, message: "Add a rate limiter to the api\n\nFiles:\n- app.py" };
    render(<ReplyGit sessionId={SID} readKey="r1" />);
    await screen.findByTestId("code-git-message");
    const ticks = screen.getAllByTestId("code-git-tick") as HTMLInputElement[];
    fireEvent.click(ticks[2]); // untick api/limits.py
    await waitFor(() => expect(api.posts.some((p) => p.path === `${GIT}/draft`)).toBe(true));
    expect(api.posts.find((p) => p.path === `${GIT}/draft`)!.body).toEqual({ paths: ["app.py"] });
    await waitFor(() =>
      expect((screen.getByTestId("code-git-message") as HTMLTextAreaElement).value).toBe(
        "Add a rate limiter to the api\n\nFiles:\n- app.py",
      ),
    );
    fireEvent.click(screen.getByTestId("code-git-commit"));
    await waitFor(() => expect(api.posts.some((p) => p.path === `${GIT}/commit`)).toBe(true));
    expect((api.posts.find((p) => p.path === `${GIT}/commit`)!.body as { paths: string[] }).paths).toEqual(["app.py"]);
  });

  it("a message the user edited is never written over by a tick", async () => {
    api.gets[GIT] = folderGit();
    render(<ReplyGit sessionId={SID} readKey="r1" />);
    const message = (await screen.findByTestId("code-git-message")) as HTMLTextAreaElement;
    fireEvent.change(message, { target: { value: "My own words" } });
    fireEvent.click((screen.getAllByTestId("code-git-tick") as HTMLInputElement[])[0]);
    await new Promise((r) => setTimeout(r, 20));
    expect(api.posts.some((p) => p.path === `${GIT}/draft`)).toBe(false);
    expect(message.value).toBe("My own words");
  });

  it("every tick taken off: the list stays and Commit waits", async () => {
    api.gets[GIT] = folderGit({
      files: [
        { rel: "README.md", status: "modified", ticked: false },
        { rel: "app.py", status: "modified", ticked: true },
      ],
      uncommitted: 2,
    });
    api.postReply[`${GIT}/draft`] = { drafted: true, message: "Changes from a coding session" };
    render(<ReplyGit sessionId={SID} readKey="r1" />);
    const commit = (await screen.findByTestId("code-git-commit")) as HTMLButtonElement;
    expect(commit.disabled).toBe(false);
    fireEvent.click((screen.getAllByTestId("code-git-tick") as HTMLInputElement[])[1]); // untick app.py
    await waitFor(() => expect((screen.getByTestId("code-git-commit") as HTMLButtonElement).disabled).toBe(true));
    expect(screen.getAllByTestId("code-git-tick")).toHaveLength(2);
  });

  it("a reply that changed nothing draws nothing, whatever else the folder has (G3)", async () => {
    // The user's own notes.txt is the folder's only change: not this session's.
    api.gets[GIT] = folderGit({ files: [{ rel: "notes.txt", status: "created", ticked: false }], uncommitted: 1 });
    const { container } = render(<ReplyGit sessionId={SID} readKey="r1" />);
    await waitFor(() => expect(api.getCalls).toContain(GIT));
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0)); // the answer lands
    });
    expect(container.querySelector("[data-testid='code-git']")).toBeNull();
    // ...and with a pull request to open, the row offers that alone.
    cleanup();
    api.getCalls = [];
    api.gets[GIT] = folderGit({ files: [{ rel: "notes.txt", status: "created", ticked: false }], uncommitted: 1, ahead: 1 });
    render(<ReplyGit sessionId={SID} readKey="r2" />);
    expect(await screen.findByTestId("code-git-open-pr")).toBeTruthy();
    expect(screen.queryByTestId("code-git-commit")).toBeNull();
  });

  it("the GitHub CLI's note says Build › Code's word for the press", () => {
    const note = "The GitHub CLI (gh) is not installed, so Create PR opens GitHub's own page instead.";
    expect(ghNoteWords(note)).toBe(`The GitHub CLI (gh) is not installed, so ${OPEN_PR_LABEL} opens GitHub's own page instead.`);
    expect(ghNoteWords("")).toBe("");
  });

  it("on the main branch there is no pull request, and it says why", async () => {
    const note =
      "This folder is on main, the repository's main branch. Start the next session on its own branch to open a pull request.";
    api.gets[GIT] = folderGit({ branch: "main", on_main: true, can_pr: false, pr_note: note });
    render(<ReplyGit sessionId={SID} readKey="r1" />);
    expect((await screen.findByTestId("code-git-pr-note")).textContent).toBe(note);
    expect(screen.queryByTestId("code-git-open-pr")).toBeNull();
  });

  it("a branch session commits everything in its own copy (no ticks)", async () => {
    api.gets[GIT] = {
      kind: "branch",
      branch: "ij/rate-3f2a",
      base: "main",
      exists: true,
      ahead: 0,
      uncommitted: 1,
      remote: "origin",
      pushed: false,
      files: [{ rel: "api/limits.py", status: "created", ticked: true }],
      draft: "Add a rate limiter to the api\n\nFiles:\n- api/limits.py",
      pr_draft: { title: "ij/rate-3f2a", body: "" },
      gh: { installed: true, signed_in: true, note: "" },
    };
    api.postReply[`${GIT}/commit`] = { committed: true, sha: "abc", subject: "Add a rate limiter to the api" };
    render(<ReplyGit sessionId={SID} readKey="r1" />);
    expect((await screen.findByTestId("code-git-files-line")).textContent).toBe(
      "1 file not committed on ij/rate-3f2a, in this session's own copy of the folder.",
    );
    expect(screen.queryByTestId("code-git-tick")).toBeNull();
    fireEvent.click(screen.getByTestId("code-git-commit"));
    await waitFor(() => expect(api.posts.some((p) => p.path === `${GIT}/commit`)).toBe(true));
    expect(api.posts.find((p) => p.path === `${GIT}/commit`)!.body).toEqual({
      message: "Add a rate limiter to the api\n\nFiles:\n- api/limits.py",
    });
  });

  it("draws nothing outside a repository or with nothing to offer", async () => {
    api.gets[GIT] = { kind: "none", note: "This folder is not in a git repository, so there is nothing to commit here." };
    const { container } = render(<ReplyGit sessionId={SID} readKey="r1" />);
    // Asserted only once the row has READ the folder (it reads after the
    // reply settles), never before.
    await waitFor(() => expect(api.getCalls).toContain(GIT));
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0)); // the answer lands
    });
    expect(container.querySelector("[data-testid='code-git']")).toBeNull();
    expect(hasGitWork(decodeAppGit(folderGit({ uncommitted: 0, files: [], ahead: 0 })), "")).toBe(false);
    expect(hasGitWork(decodeAppGit(folderGit({ uncommitted: 0, files: [], ahead: 2 })), "")).toBe(true);
  });
});

describe("the session's own branch, grouped under the folder the user picked", () => {
  it("decodes the branch and groups it under its checkout", () => {
    const branchy = decodeCodeSession({
      id: "app_b",
      harness: "codex",
      folder: "C:\\work\\shop.ij-worktrees\\rate-3f2a",
      checkout: "C:\\work\\shop",
      git: { branch: "ij/rate-3f2a", base: "main", worktree: "C:\\work\\shop.ij-worktrees\\rate-3f2a", pr_url: "" },
      updated_at: "2026-11-22T10:00:00Z",
    })!;
    const plain = decodeCodeSession({
      id: "app_p",
      harness: "claude-code",
      folder: "C:\\work\\shop",
      checkout: "C:\\work\\shop",
      pull_request: { url: "https://github.com/acme/shop/pull/9", number: 9, branch: "feature" },
      updated_at: "2026-11-22T09:00:00Z",
    })!;
    expect(branchy.git?.branch).toBe("ij/rate-3f2a");
    expect(plain.git).toBeUndefined();
    expect(plain.pullRequestUrl).toBe("https://github.com/acme/shop/pull/9");
    const groups = groupByFolder([branchy, plain]);
    expect(groups.map((g) => [g.folder, g.sessions.map((s) => s.id)])).toEqual([["C:\\work\\shop", ["app_b", "app_p"]]]);
    // The header names the folder picked and the branch, never the copy's path alone.
    expect(sessionFolderWords(branchy)).toBe("C:\\work\\shop, on ij/rate-3f2a from main in its own copy of the folder");
    expect(sessionFolderWords(plain)).toBe("C:\\work\\shop");
  });
});

const TOOLS = decodeCodingTools({
  harnesses: [
    {
      id: "codex",
      label: "Codex",
      installed: true,
      signed_in: true,
      modes: [{ id: "ask", label: "Ask first", dangerous: false }],
      hidden_modes: [],
      models: [{ id: "", label: "Codex's default" }],
      efforts: [],
      accounts: [],
      commands: [],
    },
  ],
});
const FOLDER = "C:\\work\\shop";
const DEFAULTS = `/build/folders/defaults?folder=${encodeURIComponent(FOLDER)}`;
const FOLDER_GIT = `/build/git/folder?path=${encodeURIComponent(FOLDER)}`;

describe("the start box offers a new branch once per folder", () => {
  it("asks once, keeps the answer, and a yes starts the session on its own branch", async () => {
    api.gets[DEFAULTS] = { folder: FOLDER, defaults: {} };
    api.gets[FOLDER_GIT] = { git: true, repo: true, branch: "main", can_branch: true, note: "", worktrees_dir: "x" };
    api.postReply["/build/app/sessions"] = { id: "app_new", harness: "codex", folder: "C:\\x" };
    render(<StartComposer tools={TOOLS} toolsError={null} startFolder={FOLDER} onStarted={() => {}} />);
    const offer = await screen.findByTestId("code-start-branch-offer");
    expect(offer.textContent).toContain(BRANCH_OFFER);
    fireEvent.click(screen.getByTestId("code-start-branch-yes"));
    await waitFor(() => expect(api.puts).toHaveLength(1));
    expect(api.puts[0].body).toEqual({ folder: FOLDER, view: "branch", defaults: { work: "branch" } });
    // Answered: the offer goes; the switch shows the folder's answer.
    const sw = (await screen.findByTestId("code-start-branch-switch")) as HTMLInputElement;
    expect(sw.checked).toBe(true);
    expect(screen.queryByTestId("code-start-branch-offer")).toBeNull();
    fireEvent.change(screen.getByTestId("code-start-box"), { target: { value: "add a limiter" } });
    fireEvent.click(screen.getByTestId("code-start-send"));
    await waitFor(() => expect(api.posts.some((p) => p.path === "/build/app/sessions")).toBe(true));
    expect(api.posts.find((p) => p.path === "/build/app/sessions")!.body).toMatchObject({
      harness: "codex",
      folder: FOLDER,
      branch: true,
    });
  });

  it("a folder that said no is not asked again and starts in the folder", async () => {
    api.gets[DEFAULTS] = { folder: FOLDER, defaults: { branch: { work: "folder" } } };
    api.gets[FOLDER_GIT] = { git: true, repo: true, branch: "main", can_branch: true, note: "", worktrees_dir: "x" };
    api.postReply["/build/app/sessions"] = { id: "app_new", harness: "codex", folder: FOLDER };
    render(<StartComposer tools={TOOLS} toolsError={null} startFolder={FOLDER} onStarted={() => {}} />);
    const sw = (await screen.findByTestId("code-start-branch-switch")) as HTMLInputElement;
    expect(sw.checked).toBe(false);
    expect(screen.queryByTestId("code-start-branch-offer")).toBeNull();
    fireEvent.change(screen.getByTestId("code-start-box"), { target: { value: "fix it" } });
    fireEvent.click(screen.getByTestId("code-start-send"));
    await waitFor(() => expect(api.posts.some((p) => p.path === "/build/app/sessions")).toBe(true));
    expect(api.posts.find((p) => p.path === "/build/app/sessions")!.body).not.toHaveProperty("branch");
    expect(api.puts).toHaveLength(0);
  });

  it("a session started without an answer keeps the folder's no: the offer is made once", async () => {
    api.gets[DEFAULTS] = { folder: FOLDER, defaults: {} };
    api.gets[FOLDER_GIT] = { git: true, repo: true, branch: "main", can_branch: true, note: "", worktrees_dir: "x" };
    api.postReply["/build/app/sessions"] = { id: "app_new", harness: "codex", folder: FOLDER };
    render(<StartComposer tools={TOOLS} toolsError={null} startFolder={FOLDER} onStarted={() => {}} />);
    await screen.findByTestId("code-start-branch-offer");
    fireEvent.change(screen.getByTestId("code-start-box"), { target: { value: "fix it" } });
    fireEvent.click(screen.getByTestId("code-start-send"));
    await waitFor(() => expect(api.puts).toHaveLength(1));
    expect(api.puts[0].body).toEqual({ folder: FOLDER, view: "branch", defaults: { work: "folder" } });
    expect(api.posts.find((p) => p.path === "/build/app/sessions")!.body).not.toHaveProperty("branch");
  });

  it("a folder that cannot branch is never asked", async () => {
    api.gets[DEFAULTS] = { folder: FOLDER, defaults: {} };
    api.gets[FOLDER_GIT] = { git: true, repo: false, branch: "", can_branch: false, note: "not a repo", worktrees_dir: "" };
    render(<StartComposer tools={TOOLS} toolsError={null} startFolder={FOLDER} onStarted={() => {}} />);
    await screen.findByTestId("code-start-box");
    await new Promise((r) => setTimeout(r, 30));
    expect(screen.queryByTestId("code-start-branch-offer")).toBeNull();
    expect(screen.queryByTestId("code-start-branch-switch")).toBeNull();
  });
});
