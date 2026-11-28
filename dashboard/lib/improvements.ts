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
  /** Next leaps 03-a: the third answer of a suggestion Iron Jarvis noticed in
   *  your work, "Stop suggesting this kind" (its reject route, `stop_kind`). */
  stop?: ImprovementAction | null;
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

/**
 * 18-a: the result of ONE change the owner accepted (improve/results). Its
 * words are the daemon's, built from the numbers ("Since you accepted this
 * on 12 March, research finished 9 of 10 tasks without help. Before, it was
 * 6 of 10." / "Not enough work yet: 3 of 5 tasks since …"). Shown, never
 * acted on: no button here changes anything.
 */
export interface ChangeResult {
  id: string;
  kind: string;
  title: string;
  words: string;
  /** waiting | not_enough | ready | not_measured */
  state: string;
  /** better | same | worse | "" (18-b words a worse one). */
  verdict: string;
  acceptedAt: string;
  home: { href: string; label: string } | null;
}

export interface ImprovementsInbox {
  items: ImprovementItem[];
  groups: ImprovementGroup[];
  total: number;
  /** Kinds whose store could not be read: never shown as "nothing waiting". */
  unreadable: { kind: string; label: string }[];
  /** 18-a: the results of changes already accepted (they wait for nothing). */
  results: ChangeResult[];
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
  // Next leaps 16-a (the route's own note, the status line, wins when it has one).
  meaning_search: "Search by meaning is on.",
  // Next leaps 20-a: something you asked for that Iron Jarvis can't do yet.
  missing: "Kept on this PC as a note for the next version. Nothing was sent anywhere.",
  // Next leaps 19-b (the route's own note, which names the schedule, wins).
  schedule_offer: "The schedule was made.",
  // Next leaps 03-a: a watcher's files (a run); a Yes that opens a chat
  // says so in the route's own note, which wins.
  noticed: "Started. It is running now.",
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
  // 17-a: last night's review made suggestions.
  "improvement.night_review",
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
  // Next leaps 16-a: search by meaning's own routes (no id: there is one).
  /^\/memory\/meaning\/(choose|decline)$/,
  // Next leaps 20-a: a thing you asked for that Iron Jarvis can't do yet.
  new RegExp(`^/capability/misses/${ID}/(note|decline)$`),
  // Next leaps 19-b: an offer to run a repeated task by itself.
  new RegExp(`^/schedules/offers/${ID}/(accept|decline)$`),
];

/** 19-b: the one address Rehearse may post to (not a decision: it changes
 *  nothing and makes no schedule). */
const REHEARSE_PATH = new RegExp(`^/schedules/offers/${ID}/rehearse$`);

export interface OfferRehearse {
  label: string;
  path: string;
  note: string;
}

/** 19-b: an offer's Rehearse, when its address is the offer's own. */
export function offerRehearse(item: ImprovementItem): OfferRehearse | null {
  if (item.kind !== "schedule_offer") return null;
  const r = record(item.extra.rehearse);
  if (!r) return null;
  const path = text(r.path, 300);
  const label = text(r.label, 60).trim();
  if (!label || !REHEARSE_PATH.test(path)) return null;
  return { label, path, note: text(r.note, 400) };
}

/** 19-b: the offer's newest rehearsal, "" when it was never rehearsed. */
export function offerRehearsalId(item: ImprovementItem): string {
  const id = text(item.extra.rehearsal_id, 80);
  return /^[A-Za-z0-9_-]+$/.test(id) ? id : "";
}

/**
 * Next leaps 03-a: a Yes to "pick it up" starts nothing; its route answers
 * the door to open, a NEW chat in that folder (`/chat?new=1&folder=…`, the
 * folder URL-quoted). Only that one shape is ever opened ("" otherwise).
 */
const NOTICE_DOOR = /^\/chat\?new=1&folder=[A-Za-z0-9%._~-]{1,1200}$/;

export function noticeDoor(answer: unknown): string {
  const r = record(answer);
  const door = r && typeof r.door === "string" ? r.door : "";
  return NOTICE_DOOR.test(door) ? door : "";
}

/**
 * Next leaps 03-a review: where a waiting suggestion on the Autonomy page
 * came from, in plain words. The engine's own name for it ("notice",
 * "sentinel", "deliberation") is never shown; an unknown one shows nothing.
 */
const SOURCE_WORDS: Record<string, string> = {
  notice: "Noticed in your work",
  sentinel: "Watcher",
  deliberation: "From your goals",
  event: "From something that happened",
};

export function proposalSourceWords(source: unknown): string {
  return typeof source === "string" ? SOURCE_WORDS[source] ?? "" : "";
}

/** How the page goes to a door (a seam a test replaces). */
export const doorNav = {
  go(href: string): void {
    window.location.assign(href);
  },
};

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
function decodeHome(raw: unknown): { href: string; label: string } | null {
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
    stop: decodeAction(r.stop),
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
  return { items, groups, total: count(r.total), unreadable, results: decodeResults(r.results) };
}

// --------------------------------------------------------------------------- //
// 18-a: THE RESULTS OF CHANGES YOU ACCEPTED (improve/results). A second part
// of the page, under the waiting suggestions: each change the owner accepted
// (an agent's instructions, a new or improved skill, a working habit) with
// its before and after in the daemon's own words. Read with the list (no
// read of its own, no clock); no button, nothing here changes anything.
// --------------------------------------------------------------------------- //

export const RESULTS_TITLE = "Results of changes you accepted";
export const RESULTS_NOTE = "Counted from Iron Jarvis's own records: the work before and after each change.";
/** Results drawn at most (the daemon sends 30 at most). */
export const RESULTS_MAX = 30;

function decodeResult(raw: unknown): ChangeResult | null {
  const r = record(raw);
  if (!r) return null;
  const id = text(r.id, 200);
  const title = text(r.title, 400).trim();
  const words = text(r.words, 600).trim();
  if (!id || !title || !words) return null;
  return {
    id,
    kind: text(r.kind, 40),
    title,
    words,
    state: text(r.state, 20),
    verdict: text(r.verdict, 20),
    acceptedAt: text(r.accepted_at, 40),
    home: decodeHome(r.home),
  };
}

/** The results on the wire, defensively: a row without its words is dropped
 *  (never a number made up here), at most {@link RESULTS_MAX}. */
export function decodeResults(raw: unknown): ChangeResult[] {
  return (Array.isArray(raw) ? raw : [])
    .map(decodeResult)
    .filter((x): x is ChangeResult => x !== null)
    .slice(0, RESULTS_MAX);
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

// --------------------------------------------------------------------------- //
// 17-a: THE NIGHT REVIEW (improve/night_review). `GET /improvements/night`
// says two things, each only when it is true: the ONE-TIME OFFER (D-17.1:
// nothing runs until the owner says yes; the answer is the setting itself,
// saved through PUT /settings, so it has Undo), and LAST NIGHT'S LINE
// ("From last night's review: 3" while 3 of its suggestions still wait, or
// "Skipped: ..." when only the owner can change why it made nothing). A
// suggestion the review made carries its reason on `extra.night`.
// --------------------------------------------------------------------------- //

export const NIGHT_REVIEW_PATH = "/improvements/night";
/** The setting the offer answers (Settings › Memory & you › Night review). */
export const NIGHT_REVIEW_SETTING = "night_review";
export const NIGHT_OFFER_TITLE = "Let Iron Jarvis review each day at night?";
export const NIGHT_OFFER_YES = "Turn on the night review";
export const NIGHT_OFFER_NO = "No thanks";
export const NIGHT_OFFER_ON = "The night review is on. Its suggestions will wait here in the morning.";
export const NIGHT_OFFER_OFF = "The night review stays off. You can turn it on in Settings › Memory & you.";
/** The reason line on a suggestion the review made. */
export const NIGHT_WHY_PREFIX = "From the night review: ";

export interface NightOffer {
  requests: number;
  /** "up to 6 requests a night on your plan" (the daemon's words). */
  words: string;
}

export interface NightLine {
  night: string;
  state: string;
  made: number;
  waiting: number;
  skippedWhy: string;
  needsYou: boolean;
  /** The one line the page shows ("" = show none). */
  line: string;
}

export interface NightReview {
  offer: NightOffer | null;
  night: NightLine | null;
}

/** The offer's words, with the daemon's own cost words in them. */
export function nightOfferWords(offer: NightOffer): string {
  return (
    "While your PC has nothing else to do at night, Iron Jarvis looks at what went wrong during the day " +
    `and leaves suggestions here for you to accept or decline. It uses ${offer.words}, only on a plan, ` +
    "this PC or your network, never a model that bills per use, and only while each account is under " +
    "70% of its limits. Nothing changes until you accept a suggestion."
  );
}

export function decodeNightReview(raw: unknown): NightReview {
  const r = record(raw) ?? {};
  const o = record(r.offer);
  const offerWords = o ? text(o.words, 120).trim() : "";
  const offer = o && offerWords ? { requests: count(o.requests), words: offerWords } : null;
  const n = record(r.night);
  const line = n ? text(n.line, 400).trim() : "";
  const night =
    n && line
      ? {
          night: text(n.night, 20),
          state: text(n.state, 20),
          made: count(n.made),
          waiting: count(n.waiting),
          skippedWhy: text(n.skipped_why, 400),
          needsYou: n.needs_you === true,
          line,
        }
      : null;
  return { offer, night };
}

/** Why the night review made this suggestion ("" = it did not). */
export function nightWhy(item: ImprovementItem): string {
  const n = record(item.extra.night);
  const why = n ? text(n.why, 600).trim() : "";
  return why ? `${NIGHT_WHY_PREFIX}${why}` : "";
}
