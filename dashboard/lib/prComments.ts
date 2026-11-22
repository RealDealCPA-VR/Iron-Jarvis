/**
 * Review comments on a coding session's pull request (26-a, R2.16; idea 26,
 * "GitHub comes to Jarvis"). Chat › Code sessions only: a Build › Code session
 * has no pull request yet.
 *
 * Iron Jarvis reads them from GitHub with the user's OWN `gh` sign-in (the
 * daemon's pull-request watcher, daemon/routes/ci_watch.py) and keeps them
 * (harness/pr_comments.py). This module is the screen's half:
 *
 *  - `GET /build/sessions/{id}/pr-comments` — the kept comments (no GitHub
 *    call), decoded by a WHITELIST: a field the daemon did not promise dies;
 *  - `POST …/pr-comments/answer {ids}` — ANSWER THESE: one coding turn in the
 *    session, which the page then follows like any turn (its turn id);
 *  - `POST …/pr-comments/{id}/set-aside {aside}` and `POST …/pr-comments/quiet
 *    {quiet}` — Set aside, and "Stop telling me about comments here" with its
 *    Undo.
 *
 * Nothing here sends anything to GitHub. A comment's words are what a person
 * wrote on GitHub: the screen draws them as plain text, never as Markdown or
 * a link it made up.
 */

import { get, post } from "@/lib/api";

export type PrAuthorKind = "owner" | "member" | "collaborator" | "outside" | "bot";
export type PrCommentState = "new" | "answering" | "answered" | "unchanged" | "set_aside";

export interface PrComment {
  id: string;
  kind: "line" | "review";
  author: string;
  authorKind: PrAuthorKind;
  /** Ticked in the Answer list (an owner, a member or a collaborator). */
  ticked: boolean;
  /** The file, relative to the repository ("" for a review's own words). */
  path: string;
  /** The file's full path in the session's copy of the folder ("" = none):
   *  what the Changes tab pins it by. */
  abs: string;
  line: number;
  startLine: number;
  side: "new" | "old";
  outdated: boolean;
  body: string;
  quote: string;
  /** GitHub's own page for it ("" = none). */
  url: string;
  state: PrCommentState;
  /** The files the reply that answered it changed (repository relative). */
  answerFiles: string[];
  /** Where it sits, in the daemon's words. */
  place: string;
}

export interface PrCommentsView {
  prUrl: string;
  comments: PrComment[];
  /** "Stop telling me about comments here" is on. */
  quiet: boolean;
  /** Why reading ended ("" = it goes on): merged | closed | quiet | failed. */
  done: string;
  maxAnswer: number;
}

// ------------------------------------------------------------------ words

export const PR_COMMENTS_ANSWER = "Answer these";
export const PR_COMMENTS_SET_ASIDE = "Set aside";
export const PR_COMMENTS_STOP = "Stop telling me about comments here";
export const PR_COMMENTS_TELL_AGAIN = "Tell me again";
export const PR_COMMENTS_FROM_GITHUB = "from GitHub";
export const PR_COMMENTS_OPEN = "Open on GitHub";
export const PR_COMMENTS_NOTHING_SENT = "Nothing is sent to GitHub.";
export const PR_COMMENTS_QUIET_NOTE =
  "The bell will not tell you about new review comments here. They still arrive in this session.";
export const PR_COMMENTS_NOT_PINNED = "From GitHub, not on a line shown here";

const AUTHOR_LABELS: Record<PrAuthorKind, string> = {
  owner: "owner",
  member: "member",
  collaborator: "collaborator",
  outside: "from outside the repository",
  bot: "bot",
};

export function authorLabel(kind: PrAuthorKind): string {
  return AUTHOR_LABELS[kind] ?? AUTHOR_LABELS.outside;
}

/** "3 review comments from sam arrived." — the session's line, from its
 *  structured field (the transcript's own words carry no names). */
export function arrivedWords(n: number, authors: readonly string[]): string {
  const names = authors.filter((a) => a.trim() !== "");
  const count = `${n} review comment${n === 1 ? "" : "s"}`;
  if (names.length === 0) return `${count} arrived.`;
  let who: string;
  if (names.length === 1) who = names[0];
  else if (names.length === 2) who = `${names[0]} and ${names[1]}`;
  else {
    const more = names.length - 2;
    who = `${names[0]}, ${names[1]} and ${more} other${more === 1 ? "" : "s"}`;
  }
  return `${count} from ${who} arrived.`;
}

/** What became of a comment, in a few words ("" for a new one). */
export function stateWords(c: Pick<PrComment, "state" | "answerFiles" | "kind">): string {
  switch (c.state) {
    case "answering":
      return "Being answered";
    case "answered":
      return c.answerFiles.length
        ? `Answered: changed ${c.answerFiles.slice(0, 3).join(", ")}${c.answerFiles.length > 3 ? " and more" : ""}`
        : "Answered";
    case "unchanged":
      return c.kind === "review" ? "Answered with no file changed" : "Answered with no change to this file";
    case "set_aside":
      return "Set aside";
    default:
      return "";
  }
}

export function answerButtonWords(n: number): string {
  return n > 0 ? `${PR_COMMENTS_ANSWER} (${n})` : PR_COMMENTS_ANSWER;
}

// ------------------------------------------------------------------ decoding

function rec(v: unknown): Record<string, unknown> | null {
  return v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : null;
}

function str(v: unknown, max = 600): string {
  return typeof v === "string" ? v.slice(0, max) : "";
}

function num(v: unknown): number {
  return typeof v === "number" && Number.isFinite(v) && v > 0 ? Math.floor(v) : 0;
}

const KINDS: readonly PrAuthorKind[] = ["owner", "member", "collaborator", "outside", "bot"];
const STATES: readonly PrCommentState[] = ["new", "answering", "answered", "unchanged", "set_aside"];

export function decodeComment(raw: unknown): PrComment | null {
  const r = rec(raw);
  const id = str(r?.id, 200).trim();
  if (!r || !id) return null;
  const kind = r.kind === "review" ? "review" : "line";
  const authorKind = (KINDS as readonly string[]).includes(String(r.author_kind))
    ? (r.author_kind as PrAuthorKind)
    : "outside";
  const state = (STATES as readonly string[]).includes(String(r.state)) ? (r.state as PrCommentState) : "new";
  const url = str(r.url, 500);
  return {
    id,
    kind,
    author: str(r.author, 60) || "someone",
    authorKind,
    ticked: r.ticked === true,
    path: str(r.path, 1000),
    abs: str(r.abs, 2000),
    line: num(r.line),
    startLine: num(r.start_line),
    side: r.side === "old" ? "old" : "new",
    outdated: r.outdated === true,
    body: str(r.body, 4200),
    quote: str(r.quote, 4200),
    // Only GitHub's own https address is ever a link.
    url: /^https:\/\/[^\s/]+\//.test(url) ? url : "",
    state,
    answerFiles: Array.isArray(r.answer_files)
      ? r.answer_files.filter((f): f is string => typeof f === "string").slice(0, 20)
      : [],
    place: str(r.place, 1200),
  };
}

export function decodePrCommentsView(raw: unknown): PrCommentsView {
  const r = rec(raw) ?? {};
  const comments: PrComment[] = [];
  for (const c of Array.isArray(r.comments) ? r.comments : []) {
    const d = decodeComment(c);
    if (d) comments.push(d);
  }
  return {
    prUrl: str(r.pr_url, 500),
    comments: comments.slice(0, 200),
    quiet: r.quiet === true,
    done: str(r.done, 20),
    maxAnswer: num(r.max_answer) || 20,
  };
}

/** The `prComments` field of a session line ({ids, n, authors, stopped}). */
export interface PrCommentsLine {
  ids: string[];
  n: number;
  authors: string[];
  stopped: boolean;
}

export function decodePrCommentsLine(raw: unknown): PrCommentsLine | null {
  const r = rec(raw);
  if (!r) return null;
  const ids = Array.isArray(r.ids) ? r.ids.filter((x): x is string => typeof x === "string").slice(0, 200) : [];
  const authors = Array.isArray(r.authors)
    ? r.authors.filter((x): x is string => typeof x === "string").map((a) => a.slice(0, 60)).slice(0, 10)
    : [];
  return { ids, n: num(r.n) || ids.length, authors, stopped: r.stopped === true };
}

// ------------------------------------------------------------------ the card

/** The comments the session's ONE card offers (on its newest line): every
 *  comment of the pull request still to answer (new, or being answered),
 *  oldest first — whichever line said it arrived, so one press can answer
 *  them all and the view keeps one main button. */
export function openComments(view: PrCommentsView | null): PrComment[] {
  if (!view) return [];
  return view.comments.filter((c) => c.state === "new" || c.state === "answering");
}

/** Which line of a session draws the card: the newest line that said review
 *  comments arrived (not one that says reading stopped). -1 = none. */
export function cardLineIndex(messages: readonly { role?: string; prComments?: unknown }[]): number {
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    const m = messages[i];
    if (m.role !== "assistant" || !m.prComments) continue;
    const line = decodePrCommentsLine(m.prComments);
    if (line && !line.stopped && line.ids.length > 0) return i;
  }
  return -1;
}

/** Which are ticked when the card opens: an owner's, a member's or a
 *  collaborator's new comment. */
export function firstTicks(comments: readonly PrComment[]): Set<string> {
  return new Set(comments.filter((c) => c.ticked && c.state === "new").map((c) => c.id));
}

// ------------------------------------------------------------------ the Changes tab

function norm(path: string): string {
  return path.replace(/\\/g, "/").replace(/\/+$/, "").toLowerCase();
}

/** The comments to pin on one changed file (matched by the file's full
 *  path), set-aside ones left out. */
export function pinsForFile(comments: readonly PrComment[], path: string): PrComment[] {
  const key = norm(path);
  if (!key) return [];
  return comments.filter((c) => c.kind === "line" && c.abs && c.state !== "set_aside" && norm(c.abs) === key);
}

/** The line a pin sits on in the diff, or 0 when it has none there (an
 *  outdated comment never sits on a line: its line has changed). */
export function pinLine(c: PrComment): number {
  return c.outdated ? 0 : c.line;
}

// ------------------------------------------------------------------ calls

const base = (sessionId: string) => `/build/sessions/${encodeURIComponent(sessionId)}/pr-comments`;

export async function getPrComments(sessionId: string): Promise<PrCommentsView> {
  return decodePrCommentsView(await get<unknown>(base(sessionId)));
}

/** Answer these: the turn id the page follows. */
export async function answerPrComments(sessionId: string, ids: readonly string[]): Promise<string> {
  const got = rec(await post<unknown>(`${base(sessionId)}/answer`, { ids: [...ids] }));
  return str(got?.turn_id, 120);
}

export async function setAsidePrComment(sessionId: string, id: string, aside: boolean): Promise<void> {
  await post<unknown>(`${base(sessionId)}/${encodeURIComponent(id)}/set-aside`, { aside });
}

export async function setPrCommentsQuiet(sessionId: string, quiet: boolean): Promise<boolean> {
  const got = rec(await post<unknown>(`${base(sessionId)}/quiet`, { quiet }));
  return got?.quiet === true;
}
