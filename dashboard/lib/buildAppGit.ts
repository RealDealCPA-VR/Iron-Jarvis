/**
 * 22-b (R3.4): Build › Code's Commit and Open a pull request, one press each —
 * the screen's reading of the daemon's routes/build_app_git.py.
 *
 * Two kinds of session (hosted/session_git):
 *  - on its OWN branch (the start box's yes to "Work on a new branch in this
 *    folder?", offered once per folder, never the default): the Branch view
 *    of Chat › Code (HarnessBranch) on the hosted routes, and Commit commits
 *    everything in its own copy of the folder;
 *  - in the folder directly (the default): Commit lists every changed file
 *    with a tick and commits ONLY the ticked ones; a pull request is offered
 *    only when the folder is not on the repository's main branch.
 *
 * The commit message is drafted by Iron Jarvis from the session's own record
 * (the first message and the files), never by a model.
 *
 * Every decoder WHITELISTS. Kept OUTSIDE lib/api.ts (many tests mock it).
 */

import { get, post } from "@/lib/api";
import { decodeBranchStatus, type BranchStatus, type GhState } from "@/lib/buildGit";

function rec(v: unknown): Record<string, unknown> | null {
  return v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : null;
}

function str(v: unknown, max = 2000): string {
  return typeof v === "string" ? v.slice(0, max) : "";
}

function num(v: unknown): number {
  return typeof v === "number" && Number.isFinite(v) && v >= 0 ? Math.floor(v) : 0;
}

// ------------------------------------------------------------------ words

/** The start box's one question, asked once per folder (D-22.1). */
export const BRANCH_OFFER = "Work on a new branch in this folder?";
export const BRANCH_OFFER_YES = "Yes, a new branch";
export const BRANCH_OFFER_NO = "No, in the folder";
/** What a yes does, and what a no keeps. */
export function branchOfferWords(from: string): string {
  return `A new session here works in its own copy of the folder, on a new branch from ${from || "the current commit"}. Your folder stays as it is until you merge. Pull requests need it.`;
}
/** The switch after the folder's answer (Chat › Code's own words). */
export const BRANCH_SWITCH = "Work on a new branch";

export const COMMIT_LABEL = "Commit";
export const OPEN_PR_LABEL = "Open a pull request";
/** The line over a folder session's ticks. */
export const TICKS_LINE = "Ticked: what changed since this session started. Only ticked files are committed.";
/** The line over a branch session's files. */
export function branchFilesLine(n: number, branch: string): string {
  return `${n} file${n === 1 ? "" : "s"} not committed on ${branch}, in this session's own copy of the folder.`;
}
export const MESSAGE_FROM_RECORD = "Written by Iron Jarvis from your first message and the files. Edit it before you commit.";
/** Chat › Code's word for the same press (HarnessBranch). */
export const PUSH_LABEL = "Push";
/** 22-b review: an open pull request lacks the commits not pushed yet. */
export function unpushedLine(n: number, branch: string): string {
  return n === 1
    ? `1 commit on ${branch} is not in the pull request yet.`
    : `${n} commits on ${branch} are not in the pull request yet.`;
}
export function pushedWords(branch: string, remote: string, n: number): string {
  return `Pushed ${branch} to ${remote}: the pull request has ${n === 1 ? "that commit" : "those commits"} now.`;
}

// ------------------------------------------------------------------ the reading

export interface AppGitFile {
  rel: string;
  status: "created" | "modified" | "deleted" | "renamed";
  ticked: boolean;
}

export interface AppGit {
  kind: "branch" | "folder" | "none";
  /** Why there is nothing here ("none"). */
  note: string;
  branch: string;
  /** The repository's main branch (folder) or the session's base (branch). */
  main: string;
  onMain: boolean;
  files: AppGitFile[];
  /** Changed files not listed. */
  more: number;
  uncommitted: number;
  /** The commit message, from the record. */
  draft: string;
  ahead: number;
  pushed: boolean;
  /** Commits the remote's copy of the branch lacks (an open pull request
   *  does not have them yet). */
  unpushed: number;
  remote: string;
  canPr: boolean;
  prNote: string;
  prUrl: string;
  prDraft: { title: string; body: string };
  compareUrl: string;
  gh: GhState | null;
  /** A branch session's Branch view status. */
  status: BranchStatus | null;
}

const STATUSES = new Set(["created", "modified", "deleted", "renamed"]);

export function decodeAppGit(raw: unknown): AppGit | null {
  const r = rec(raw);
  if (!r) return null;
  const kind = r.kind === "branch" || r.kind === "folder" ? r.kind : "none";
  const files = (Array.isArray(r.files) ? r.files : [])
    .map((f) => rec(f))
    .filter((f): f is Record<string, unknown> => !!f && typeof f.rel === "string" && !!f.rel)
    .slice(0, 500)
    .map((f) => ({
      rel: str(f.rel, 1000),
      status: (STATUSES.has(str(f.status, 20)) ? str(f.status, 20) : "modified") as AppGitFile["status"],
      ticked: f.ticked === true,
    }));
  const status = kind === "branch" ? decodeBranchStatus(raw) : null;
  const draft = rec(r.pr_draft) ?? {};
  const gh = rec(r.gh);
  const compare = str(r.compare_url, 2000);
  const prUrl = str(r.pr_url, 1000);
  const base = str(r.base, 300);
  return {
    kind,
    note: str(r.note, 400),
    branch: str(r.branch, 300),
    main: kind === "branch" ? base : str(r.main, 300),
    onMain: r.on_main === true,
    files,
    more: num(r.more),
    uncommitted: num(r.uncommitted),
    draft: str(r.draft, 5000),
    ahead: num(r.ahead),
    pushed: r.pushed === true,
    unpushed: num(r.unpushed),
    remote: str(r.remote, 200),
    // A branch session can open one once it has commits its base lacks.
    canPr: kind === "branch" ? num(r.ahead) > 0 && !!base : r.can_pr === true,
    prNote: str(r.pr_note, 400),
    prUrl: /^https:\/\//.test(prUrl) ? prUrl : "",
    prDraft: { title: str(draft.title, 250), body: str(draft.body, 20_000) },
    compareUrl: /^https:\/\/[^/\s]+\//.test(compare) ? compare : "",
    gh: gh
      ? {
          installed: gh.installed === true,
          signedIn: gh.signed_in === true ? true : gh.signed_in === false ? false : null,
          note: str(gh.note, 400),
        }
      : null,
    status,
  };
}

/** Written out whole (not from a variable): the route check
 *  (tests/test_wave6_dead_code_v14390.py) reads each caller statically. */
function base(id: string): string {
  return `/build/app/sessions/${encodeURIComponent(id)}/git`;
}

export async function getAppGit(id: string): Promise<AppGit | null> {
  return decodeAppGit(await get<unknown>(`${base(id)}`));
}

/** The message again for the files ticked now (from the record; no model). */
export async function draftFor(id: string, paths: string[]): Promise<string> {
  const r = rec(await post<unknown>(`${base(id)}/draft`, { paths })) ?? {};
  return str(r.message, 5000);
}

export async function commitAppGit(
  id: string,
  message: string,
  paths: string[] | null,
): Promise<{ sha: string; subject: string; note: string; files: number }> {
  const r = rec(await post<unknown>(`${base(id)}/commit`, paths ? { message, paths } : { message })) ?? {};
  return { sha: str(r.sha, 40), subject: str(r.subject, 300), note: str(r.note, 400), files: num(r.files) };
}

export async function openAppPr(id: string, title: string, body: string): Promise<{ url: string; number: number | null }> {
  const r = rec(await post<unknown>(`${base(id)}/pr`, { title, body })) ?? {};
  const url = str(r.url, 1000);
  return { url: /^https:\/\//.test(url) ? url : "", number: typeof r.number === "number" ? r.number : null };
}

/** Can Commit go: something ticked (a branch session commits everything). */
export function canCommit(g: AppGit, ticked: ReadonlySet<string>, message: string): boolean {
  if (!message.trim() || g.uncommitted === 0) return false;
  return g.kind === "branch" || ticked.size > 0;
}

/** Has the session itself something to commit? A branch session: anything
 *  not committed in its own copy of the folder. A folder session: a file
 *  Iron Jarvis ticked (changed since the session started); the user's own
 *  changes alone are not this session's to offer. */
export function ownChanges(g: AppGit): boolean {
  return g.kind === "branch" ? g.uncommitted > 0 : g.files.some((f) => f.ticked);
}

/** Is there anything for the row under a reply to offer? (G3: nothing is
 *  drawn under a reply that changed nothing and has no pull request to open.) */
export function hasGitWork(g: AppGit | null, prUrl: string): boolean {
  if (!g || g.kind === "none") return false;
  return ownChanges(g) || (g.canPr && g.ahead > 0) || !!prUrl || !!g.prUrl;
}

/** 22-b review: the pull request the row names. A folder session's comes
 *  from the folder's read ALONE: the daemon sends it only while the folder is
 *  on that pull request's branch (`session_git.folder_status`), so a folder
 *  now on another branch is offered a pull request of its own. The session
 *  view's link (`viewUrl`) is a branch session's only: its branch never
 *  changes. */
export function prLinkFor(g: AppGit | null, viewUrl: string): string {
  if (!g) return "";
  return g.prUrl || (g.kind === "branch" ? viewUrl : "");
}

/** 22-b review: an open pull request lacks commits: one quiet Push. */
export function needsPush(g: AppGit, link: string): boolean {
  return !!link && g.kind !== "none" && g.unpushed > 0 && !!g.branch;
}

/** The GitHub CLI's note in Build › Code's words ("Open a pull request",
 *  VOCABULARY.md; the note is Chat › Code's, which says "Create PR"). */
export function ghNoteWords(note: string): string {
  return note.replace(/\bCreate PR\b/g, OPEN_PR_LABEL);
}

/** The pull request's link words. */
export function prLinkWords(n: number | null): string {
  return n ? `Pull request #${n}` : "The pull request";
}
