/**
 * A Build session's own git BRANCH (v1.375.0, IJ harness part 2 Phase 4) —
 * the screen's reading of the daemon's routes/build_git.py.
 *
 * The user's decisions: a new session in a git folder works on its own branch
 * `ij/<name>` in a worktree beside the repository (a switch on New session
 * works in the folder directly); the Branch view shows the whole diff against
 * the base; Commit (the session's model drafts the message, the user edits
 * it), Push (never a force), Create PR (GitHub CLI signed in; otherwise the
 * compare link), Merge into <base> (a normal merge, refused in words when it
 * cannot go cleanly) and Discard (the worktree to the trash, the branch kept)
 * are each a person's press.
 *
 * Every decoder WHITELISTS (a field the daemon did not promise dies here).
 * Kept OUTSIDE lib/api.ts (~71 test files mock it wholesale).
 */

import { ApiError, get, post } from "@/lib/api";

function rec(v: unknown): Record<string, unknown> | null {
  return v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : null;
}

function str(v: unknown, max = 2000): string {
  return typeof v === "string" ? v.slice(0, max) : "";
}

function num(v: unknown): number {
  return typeof v === "number" && Number.isFinite(v) && v >= 0 ? Math.floor(v) : 0;
}

// ------------------------------------------------------------ New session

/** What New session knows about a folder (GET /build/git/folder). */
export interface FolderGit {
  /** git is installed on this PC. */
  git: boolean;
  /** The folder is inside a git repository. */
  repo: boolean;
  /** The branch checked out there ("" when detached). */
  branch: string;
  /** A session can start its own branch here. */
  canBranch: boolean;
  /** Why not, in words ("" when it can). */
  note: string;
  /** Where the session's worktree would go. */
  worktreesDir: string;
}

export function decodeFolderGit(raw: unknown): FolderGit | null {
  const r = rec(raw);
  if (!r) return null;
  return {
    git: r.git === true,
    repo: r.repo === true,
    branch: str(r.branch, 200),
    canBranch: r.can_branch === true,
    note: str(r.note, 400),
    worktreesDir: str(r.worktrees_dir, 1000),
  };
}

/** The folder's git facts, or null (an older Iron Jarvis, a refusal). */
export async function fetchFolderGit(path: string): Promise<FolderGit | null> {
  try {
    return decodeFolderGit(await get<unknown>(`/build/git/folder?path=${encodeURIComponent(path)}`));
  } catch {
    return null;
  }
}

/** The line New session shows for a folder (null = nothing to say: a folder
 *  outside a repository, or no answer). */
export function folderGitLine(info: FolderGit | null): string | null {
  if (!info) return null;
  if (!info.git) return "Git is not installed on this PC, so the session works in the folder directly.";
  if (!info.repo) return null;
  if (!info.canBranch) return info.note || "This repository cannot start a branch here, so the session works in the folder directly.";
  return null;
}

// --------------------------------------------------------------- a session

/** `git` on a session's view (null = it works in its folder directly). */
export interface SessionGit {
  branch: string;
  base: string;
  baseCommit: string;
  worktree: string;
  checkout: string;
  repo: string;
  merged: boolean;
  mergedInto: string;
  removed: boolean;
  prUrl: string;
}

export function decodeSessionGit(raw: unknown): SessionGit | null {
  const r = rec(raw);
  const branch = str(r?.branch, 300);
  if (!r || !branch) return null;
  return {
    branch,
    base: str(r.base, 300),
    baseCommit: str(r.base_commit, 40),
    worktree: str(r.worktree, 1000),
    checkout: str(r.checkout, 1000),
    repo: str(r.repo, 1000),
    merged: r.merged === true,
    mergedInto: str(r.merged_into, 300),
    removed: r.removed === true,
    prUrl: str(r.pr_url, 1000),
  };
}

/** The header's words: "ij/fix-3f2a from main" (and what became of it). */
export function branchLine(g: SessionGit): string {
  const from = g.base ? ` from ${g.base}` : "";
  const end = g.removed ? " · worktree removed" : g.merged ? ` · merged into ${g.mergedInto || g.base}` : "";
  return `${g.branch}${from}${end}`;
}

export interface GhState {
  installed: boolean;
  signedIn: boolean | null;
  note: string;
}

export interface BranchStatus {
  exists: boolean;
  ahead: number;
  behind: number;
  uncommitted: number;
  remote: string;
  pushed: boolean;
  unpushed: number;
  inBase: boolean;
  baseExists: boolean;
  baseCheckedOutAt: string;
  commits: { sha: string; subject: string }[];
  prDraft: { title: string; body: string };
  compareUrl: string;
  gh: GhState;
}

export function decodeBranchStatus(raw: unknown): BranchStatus | null {
  const r = rec(raw);
  if (!r) return null;
  const gh = rec(r.gh) ?? {};
  const draft = rec(r.pr_draft) ?? {};
  const compare = str(r.compare_url, 2000);
  return {
    exists: r.exists === true,
    ahead: num(r.ahead),
    behind: num(r.behind),
    uncommitted: num(r.uncommitted),
    remote: str(r.remote, 200),
    pushed: r.pushed === true,
    unpushed: num(r.unpushed),
    inBase: r.in_base === true,
    baseExists: r.base_exists === true,
    baseCheckedOutAt: str(r.base_checked_out_at, 1000),
    commits: (Array.isArray(r.commits) ? r.commits : [])
      .map((c) => rec(c))
      .filter((c): c is Record<string, unknown> => !!c)
      .map((c) => ({ sha: str(c.sha, 40), subject: str(c.subject, 300) }))
      .filter((c) => c.sha)
      .slice(0, 20),
    prDraft: { title: str(draft.title, 250), body: str(draft.body, 20_000) },
    // Only a web page on GitHub is ever opened from here.
    compareUrl: /^https:\/\/[^/\s]+\//.test(compare) ? compare : "",
    gh: {
      installed: gh.installed === true,
      signedIn: gh.signed_in === true ? true : gh.signed_in === false ? false : null,
      note: str(gh.note, 400),
    },
  };
}

/** One line for the branch: commits, files not committed, the remote. */
export function statusLine(s: BranchStatus, branch: string): string {
  const commits = `${s.ahead} commit${s.ahead === 1 ? "" : "s"} on ${branch}`;
  const files = s.uncommitted
    ? `${s.uncommitted} file${s.uncommitted === 1 ? "" : "s"} not committed`
    : "nothing waiting to commit";
  const remote = !s.remote
    ? "no remote"
    : s.pushed
      ? `pushed to ${s.remote}`
      : s.unpushed
        ? `${s.unpushed} not pushed to ${s.remote}`
        : `not on ${s.remote} yet`;
  return `${commits} · ${files} · ${remote}`;
}

export interface PrInfo {
  url: string;
  number: number | null;
  state: string;
  words: string;
  checks: { passed: number; failed: number; pending: number };
}

export function decodePr(raw: unknown): PrInfo | null {
  const r = rec(raw);
  if (!r) return null;
  const url = str(r.url, 1000);
  const checks = rec(r.checks) ?? {};
  return {
    url: /^https:\/\//.test(url) ? url : "",
    number: typeof r.number === "number" ? r.number : null,
    state: str(r.state, 40),
    words: str(r.words, 300),
    checks: { passed: num(checks.passed), failed: num(checks.failed), pending: num(checks.pending) },
  };
}

// ------------------------------------------------------------- the actions

/** A refusal in the daemon's words, and git's own output (masked) to fold
 *  under Details. */
export interface GitRefusal {
  detail: string;
  details: string;
}

export function gitRefusal(e: unknown, fallback = "That did not go through."): GitRefusal {
  if (e instanceof ApiError) {
    const body = rec(e.body);
    return {
      detail: str(body?.detail, 2000) || e.message || fallback,
      details: str(body?.details, 8000),
    };
  }
  return { detail: e instanceof Error && e.message ? e.message : fallback, details: "" };
}

function base(id: string): string {
  return `/build/sessions/${encodeURIComponent(id)}/git`;
}

export async function getBranchStatus(id: string): Promise<BranchStatus | null> {
  return decodeBranchStatus(await get<unknown>(base(id)));
}

/** The Branch view's answer, raw — the Changes pane's own `decodeChanges`
 *  (components/chat/ChangedFiles) reads it, so this file stays free of the
 *  component tree (lib/buildSessions imports it). */
export async function getBranchChangesRaw(id: string): Promise<unknown> {
  return get<unknown>(`${base(id)}/diff`);
}

export function moreFiles(raw: unknown): boolean {
  return rec(raw)?.truncated_files === true;
}

export interface CommitDraft {
  drafted: boolean;
  message: string;
  note: string;
}

export async function draftCommit(id: string): Promise<CommitDraft> {
  const r = rec(await post<unknown>(`${base(id)}/draft`, {})) ?? {};
  return { drafted: r.drafted === true, message: str(r.message, 5000), note: str(r.note, 400) };
}

export async function commitBranch(id: string, message: string): Promise<{ sha: string; subject: string; note: string }> {
  const r = rec(await post<unknown>(`${base(id)}/commit`, { message })) ?? {};
  return { sha: str(r.sha, 40), subject: str(r.subject, 300), note: str(r.note, 400) };
}

export async function pushBranch(id: string): Promise<{ remote: string; branch: string }> {
  const r = rec(await post<unknown>(`${base(id)}/push`, {})) ?? {};
  return { remote: str(r.remote, 200), branch: str(r.branch, 300) };
}

export async function createPr(id: string, title: string, body: string): Promise<{ url: string; number: number | null }> {
  const r = rec(await post<unknown>(`${base(id)}/pr`, { title, body })) ?? {};
  const url = str(r.url, 1000);
  return { url: /^https:\/\//.test(url) ? url : "", number: typeof r.number === "number" ? r.number : null };
}

export async function getPr(id: string): Promise<PrInfo | null> {
  return decodePr(await get<unknown>(`${base(id)}/pr`));
}

export async function mergeBranch(id: string): Promise<{ into: string; where: string; commit: string }> {
  const r = rec(await post<unknown>(`${base(id)}/merge`, {})) ?? {};
  return { into: str(r.into, 300), where: str(r.where, 1000), commit: str(r.commit, 40) };
}

export async function discardBranch(id: string, afterMerge = false): Promise<{ trash: string; note: string }> {
  const r = rec(await post<unknown>(`${base(id)}/discard`, { confirm: true, after_merge: afterMerge })) ?? {};
  return { trash: str(r.trash, 1000), note: str(r.note, 400) };
}

/** How often an open pull request's checks are read while the Branch view is
 *  on screen (slow — never a tight loop). */
export const PR_POLL_MS = 60_000;
