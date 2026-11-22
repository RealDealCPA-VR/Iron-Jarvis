/**
 * 26-a (R2.16) — the Branch view reads the review comments Iron Jarvis kept
 * for a pull request ONLY where the watcher reads them: a Chat › Code
 * session's. A Build › Code session's pull request (22-b, the same view with
 * `root=BUILD_CODE_GIT` and `watchCi={false}`) is not watched, so the view
 * never asks for its comments (the route would only answer "no such coding
 * session", on a clock).
 */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";

const api = vi.hoisted(() => {
  class ApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
    }
  }
  return { ApiError, gets: {} as Record<string, unknown>, getCalls: [] as string[] };
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
  post: () => Promise.resolve({}),
  put: () => Promise.resolve({}),
  patch: () => Promise.resolve({}),
  del: () => Promise.resolve({}),
}));
vi.mock("@/lib/useModels", () => ({ useModels: () => ({ models: [], usable: [], loading: false }) }));
vi.mock("@/components/terminal/DirectoryTree", () => ({ DirectoryTree: () => null }));

import { BranchView } from "@/components/harness/HarnessBranch";
import { BUILD_CODE_GIT, decodeSessionGit } from "@/lib/buildGit";

const SID = "s_pr1";
const PR = "https://github.com/acme/shop/pull/7";
const STATUS = {
  exists: true,
  ahead: 1,
  behind: 0,
  uncommitted: 0,
  remote: "origin",
  pushed: true,
  unpushed: 0,
  in_base: false,
  base_exists: true,
  base_checked_out_at: "",
  commits: [{ sha: "1a2b3c4", subject: "Add a limiter" }],
  pr_draft: { title: "Add a limiter", body: "" },
  compare_url: "",
  gh: { installed: true, signed_in: true, note: "" },
};
const DIFF = { changes: [], files: 0, added: 0, removed: 0, truncated_files: false, base: "main", merge_base: "abc" };
const VIEW = { pr_url: PR, comments: [], quiet: false, done: "", max_answer: 20 };

const sgit = () => decodeSessionGit({ branch: "ij/rate-3f2a", base: "main", worktree: "C:\\w", pr_url: PR })!;
const reads = (root: string) => api.getCalls.filter((p) => p === `${root}/${SID}/pr-comments`);

beforeEach(() => {
  api.gets = {};
  api.getCalls = [];
});
afterEach(cleanup);

describe("the Branch view's review comments", () => {
  it("a Chat › Code session's view reads them", async () => {
    api.gets[`/build/sessions/${SID}/git`] = STATUS;
    api.gets[`/build/sessions/${SID}/git/diff`] = DIFF;
    api.gets[`/build/sessions/${SID}/pr-comments`] = VIEW;
    render(<BranchView sessionId={SID} git={sgit()} refreshKey={0} />);
    await waitFor(() => expect(reads("/build/sessions")).not.toHaveLength(0));
  });

  it("a Build › Code session's view never asks for them", async () => {
    const GIT = `/build/app/sessions/${SID}/git`;
    api.gets[GIT] = STATUS;
    api.gets[`${GIT}/diff`] = DIFF;
    render(<BranchView sessionId={SID} git={sgit()} refreshKey={0} root={BUILD_CODE_GIT} watchCi={false} />);
    // The view has read its branch (the same mount that would read them).
    await screen.findByTestId("branch-pr-link");
    await waitFor(() => expect(api.getCalls).toContain(GIT));
    expect(reads("/build/sessions")).toEqual([]);
    expect(reads("/build/app/sessions")).toEqual([]);
  });
});
