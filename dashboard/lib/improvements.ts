/**
 * The Improvements page's data (v1.495.0, vision plan leap 5, Phase 1).
 *
 * `GET /improvements` lists every suggestion Iron Jarvis is waiting on the
 * user to answer, from every store that keeps one. Each item names ITS OWN
 * Accept and Decline (the route the suggestion's own screen presses), so this
 * page decides nothing by itself: it presses the address it was handed.
 *
 * Everything from the wire is decoded through a whitelist here: an item
 * whose shape is wrong is dropped, and a decision whose address is not one of
 * the suggestion routes this app has is dropped too (the item is then
 * link-only), so a page can never be made to press some other address.
 */

import { offerDismissed } from "./goalOffers";

export interface ImprovementAction {
  label: string;
  path: string;
  body: Record<string, unknown>;
  /** What pressing it does, in one sentence ("" = nothing more to say). */
  note: string;
  /** Two presses: accepting does something at once (starts a run, makes a
   *  tool, rewrites notes). */
  confirm: boolean;
  /** Why it cannot be accepted here ("" = it can). */
  blocked: string;
}

export interface ImprovementDetail {
  mode: "diff" | "text";
  before: string;
  after: string;
  label: string;
  /** The text was cut for the list; the whole of it is where it lives. */
  cut: boolean;
}

export interface ImprovementItem {
  id: string;
  kind: string;
  ref: string;
  title: string;
  about: string;
  why: string;
  evidence: string[];
  createdAt: string;
  detail: ImprovementDetail | null;
  accept: ImprovementAction | null;
  decline: ImprovementAction | null;
  home: { href: string; label: string } | null;
  extra: Record<string, unknown>;
}

export interface ImprovementGroup {
  kind: string;
  label: string;
  /** How many are waiting. */
  count: number;
  /** How many of them are in `items`. */
  shown: number;
}

export interface ImprovementsInbox {
  items: ImprovementItem[];
  groups: ImprovementGroup[];
  total: number;
  /** Kinds whose store could not be read: never shown as "nothing waiting". */
  unreadable: { kind: string; label: string }[];
}

export const IMPROVEMENTS_PATH = "/improvements";

/** The page's own words. */
export const IMPROVEMENTS_TITLE = "Improvements";
export const IMPROVEMENTS_SUBTITLE =
  "Every suggestion Iron Jarvis is waiting on you to accept or decline, in one place. Nothing here changes anything until you say so.";
export const NOTHING_WAITING_TITLE = "Nothing is waiting for you";
export const NOTHING_WAITING =
  "When Iron Jarvis notices a preference, drafts a skill, or wants to change how an agent works, it is listed here for you to accept or decline.";
export const OFFER_ONLY_NOTE = "This one is answered where it lives.";
export const CUT_NOTE = "Shortened here. Open where it lives to read all of it.";
export const TAKEN_OFF = "Taken off the list.";

/** What to say after an Accept, by kind. */
const ACCEPTED_WORDS: Record<string, string> = {
  preference: "Kept. Iron Jarvis follows it from now on.",
  skill: "Saved as a skill.",
  coach: "The agent's instructions were changed.",
  habit: "Added to that team member's habits.",
  lesson: "Kept. Later coding sessions in that folder read it.",
  session_edit: "The file was changed.",
  memory: "Done. The notes were changed.",
  tool: "The tool was created.",
  wants: "Started. It is running now.",
  // v1.499.0 (leap 3, Phase 3): a name Iron Jarvis noticed in your folders.
  client: "It is a client now. Its page is on Clients, and chats are told about it only in its own folders.",
};

export function acceptedWords(kind: string): string {
  return ACCEPTED_WORDS[kind] ?? "Accepted.";
}

/**
 * What the suggestion's own route said when it accepted, when it said
 * something ("Team notes are turned off, so no model is shown the habit
 * until you turn them on."). Its words are truer than this page's.
 */
export function answerNote(answer: unknown): string {
  const r = answer && typeof answer === "object" && !Array.isArray(answer) ? (answer as Record<string, unknown>) : null;
  const note = r && typeof r.note === "string" ? r.note.trim() : "";
  return note.slice(0, 400);
}

/** Events after which the list is read again. */
export const IMPROVEMENT_EVENT_TYPES = [
  "coach.proposal",
  "library.proposal",
  "preference.suggested",
  "preference.kept",
  "preference.declined",
  "autonomy.proposed",
  "skill.proposal_created",
  // v1.499.0: a name noticed in your folders, and its answer from anywhere.
  "client.suggested",
  "client.confirmed",
  "client.declined",
];

/**
 * The only addresses a decision may be posted to: the suggestion routes this
 * app has, each with ONE id and its own verb. Anything else on the wire is
 * not a decision.
 *
 * The id is letters, digits, `_` and `-` only (every id Iron Jarvis makes
 * is). Not "anything but a slash": a browser reads a backslash as a slash
 * and resolves dot segments, so an id holding backslashes and dots would be
 * posted to another route altogether.
 */
const ID = "[A-Za-z0-9_-]+";
/** v1.499.0: a client's id is narrower still (`client_` and 12 hex, the
 *  daemon's `CLIENT_ID_RE`): a name never fits, so no client's name can
 *  become part of an address this page posts to. */
const CLIENT_ID = "client_[0-9a-f]{12}";
const DECISION_PATHS: RegExp[] = [
  new RegExp(`^/clients/${CLIENT_ID}/(confirm|decline)$`),
  new RegExp(`^/coach/proposals/${ID}/(accept|decline)$`),
  new RegExp(`^/build/library/${ID}/(accept|decline)$`),
  new RegExp(`^/skills/proposals/${ID}/(approve|reject)$`),
  new RegExp(`^/capability/proposals/${ID}/(approve|reject)$`),
  new RegExp(`^/memory/review/${ID}/(approve|dismiss)$`),
  new RegExp(`^/memory/preferences/${ID}/(keep|decline)$`),
  new RegExp(`^/proposals/${ID}/(approve|reject)$`),
];

export function isDecisionPath(path: string): boolean {
  return DECISION_PATHS.some((rx) => rx.test(path));
}

function text(v: unknown, max = 8000): string {
  return typeof v === "string" ? v.slice(0, max) : "";
}

function record(v: unknown): Record<string, unknown> | null {
  return v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : null;
}

function decodeAction(raw: unknown): ImprovementAction | null {
  const r = record(raw);
  if (!r) return null;
  const label = text(r.label, 60).trim();
  const path = text(r.path, 300);
  if (!label || r.method !== "POST" || !isDecisionPath(path)) return null;
  return {
    label,
    path,
    body: record(r.body) ?? {},
    note: text(r.note, 400),
    confirm: r.confirm === true,
    blocked: text(r.blocked, 400),
  };
}

function decodeDetail(raw: unknown): ImprovementDetail | null {
  const r = record(raw);
  if (!r) return null;
  const mode = r.mode === "diff" ? "diff" : r.mode === "text" ? "text" : null;
  const after = text(r.after);
  const before = text(r.before);
  if (!mode || (!after && !before)) return null;
  return { mode, before, after, label: text(r.label, 200), cut: r.cut === true };
}

/** True for a tab, a newline or any other control character. */
function hasControl(s: string): boolean {
  for (let i = 0; i < s.length; i++) {
    const c = s.charCodeAt(i);
    if (c < 32 || c === 127) return true;
  }
  return false;
}

/** An in-app address only: one leading slash, never a scheme or `//host`,
 *  and no backslash or control character anywhere (a browser reads a
 *  backslash as a slash and drops a tab or a newline, so such an address
 *  could leave the app). */
function decodeHome(raw: unknown): ImprovementItem["home"] {
  const r = record(raw);
  if (!r) return null;
  const href = text(r.href, 400);
  if (!href.startsWith("/") || href.startsWith("//") || href.includes("\\") || hasControl(href)) return null;
  return { href, label: text(r.label, 60) || "Open where it lives" };
}

function decodeItem(raw: unknown): ImprovementItem | null {
  const r = record(raw);
  if (!r) return null;
  const id = text(r.id, 200);
  const kind = text(r.kind, 40);
  const title = text(r.title, 400).trim();
  if (!id || !kind || !title) return null;
  return {
    id,
    kind,
    ref: text(r.ref, 200),
    title,
    about: text(r.about, 200),
    why: text(r.why, 1200),
    evidence: Array.isArray(r.evidence)
      ? r.evidence.filter((e): e is string => typeof e === "string" && !!e.trim()).slice(0, 8).map((e) => e.slice(0, 400))
      : [],
    createdAt: text(r.created_at, 40),
    detail: decodeDetail(r.detail),
    accept: decodeAction(r.accept),
    decline: decodeAction(r.decline),
    home: decodeHome(r.home),
    extra: record(r.extra) ?? {},
  };
}

function count(v: unknown): number {
  return typeof v === "number" && Number.isFinite(v) && v >= 0 ? Math.floor(v) : 0;
}

export function decodeInbox(raw: unknown): ImprovementsInbox {
  const r = record(raw) ?? {};
  const items = (Array.isArray(r.items) ? r.items : [])
    .map(decodeItem)
    .filter((i): i is ImprovementItem => i !== null);
  const groups = (Array.isArray(r.groups) ? r.groups : [])
    .map((g): ImprovementGroup | null => {
      const o = record(g);
      const kind = o ? text(o.kind, 40) : "";
      if (!o || !kind) return null;
      return { kind, label: text(o.label, 80) || kind, count: count(o.count), shown: count(o.shown) };
    })
    .filter((g): g is ImprovementGroup => g !== null);
  const unreadable = (Array.isArray(r.unreadable) ? r.unreadable : [])
    .map((u) => {
      const o = record(u);
      const kind = o ? text(o.kind, 40) : "";
      return o && kind ? { kind, label: text(o.label, 80) || kind } : null;
    })
    .filter((u): u is { kind: string; label: string } => u !== null);
  return { items, groups, total: count(r.total), unreadable };
}

/**
 * An offer to stop asking that this browser answered "Not now" on the
 * Autonomy page is not waiting any more (that answer lives in this browser,
 * so the daemon cannot know it).
 */
export function stillWaiting(item: ImprovementItem): boolean {
  if (item.kind !== "unasked") return true;
  const goal = typeof item.extra.goal_id === "string" ? item.extra.goal_id : "";
  const tool = typeof item.extra.tool === "string" ? item.extra.tool : "";
  const hash = typeof item.extra.args_hash === "string" ? item.extra.args_hash : "";
  if (!goal || !tool) return true;
  return !offerDismissed(goal, tool, hash);
}

/**
 * The page's groups: the daemon's order, each with its items; a kind this
 * browser has nothing left to show for is left out, and its count is the
 * daemon's count minus what this browser set aside.
 */
export function groupsToShow(
  inbox: ImprovementsInbox,
  gone: ReadonlySet<string>,
): { kind: string; label: string; items: ImprovementItem[]; more: number }[] {
  return inbox.groups
    .map((g) => {
      const mine = inbox.items.filter((i) => i.kind === g.kind);
      const items = mine.filter((i) => !gone.has(i.id) && stillWaiting(i));
      // Everything that waits and is not drawn here: past the list's bound,
      // or an item this page could not read.
      return { kind: g.kind, label: g.label, items, more: Math.max(0, g.count - mine.length) };
    })
    .filter((g) => g.items.length > 0 || g.more > 0);
}

/** "3 suggestions are waiting" (never "1 suggestions"). */
export function waitingWords(n: number): string {
  if (n <= 0) return "";
  return n === 1 ? "1 suggestion is waiting for you." : `${n} suggestions are waiting for you.`;
}
