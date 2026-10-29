/**
 * THE NEXT-STEP BOX (v1.406.0 part 4). The user: like Claude Code desktop,
 * every finished turn ends with a short "here's where we are" and one or two
 * buttons for the obvious next move, so they press instead of typing.
 *
 * Built DETERMINISTICALLY from what the reply's record already says — the
 * files it named, its finish check (tests, the team's review, points left),
 * a step limit or the session's cap, the session's branch, and whether its
 * last sentence offers more work (lib/closingOffer, the daemon's own
 * reading). No model call. Nothing here is saved: the box is derived on
 * render from the NEWEST settled reply, so reopening a conversation shows it
 * again on that reply only.
 *
 * Rules the tests pin (dashboard/__tests__/next-step-v1406.test.tsx):
 *  - a plain answer (nothing recorded worth saying, no press) is NO box —
 *    never filler;
 *  - at most two buttons, the first is the recommendation;
 *  - every button is a REAL existing action: it sends exactly what the user
 *    would have typed ("continue", "Yes, go ahead.") or opens the existing
 *    press (the branch's Commit / Create PR forms, which still ask exactly
 *    as before). It never bypasses a card, a grant or a once-only rule;
 *  - an offer whose action is the USER'S to decide (send, post, push, merge,
 *    delete, pay …) is NAMED, and its press only fills the message box or
 *    opens the existing press — it never acts by itself;
 *  - a failed reply, a reply that ended on an account's limit card and a
 *    declined one get no box: their own line (Retry, Sign in, the card) is
 *    the next step there, never drawn twice.
 */

import { lastSentence, offerKind, offeredAction, type OfferKind } from "@/lib/closingOffer";
import { finishWarn, type FinishRecord } from "@/lib/stepWords";
import { KEEP_GOING_TEXT, decodeFinish, decodeStepLimit } from "@/lib/buildSessions";
import { decodeCapMet } from "@/lib/buildLongJobs";

/** What "Yes, do it" sends — the same as typing it. */
export const YES_TEXT = "Yes, go ahead.";
/** What "Fix the failing tests" sends. */
export const FIX_TESTS_TEXT = "Fix the failing tests.";
/** What "Fix what the reviewer found" sends. */
export const FIX_REVIEW_TEXT = "Fix what the reviewer found.";
/** "Keep going": the step-limit line's own message (lib/buildSessions). */
export const NEXT_KEEP_GOING_TEXT = KEEP_GOING_TEXT;
/** At most this many buttons. */
export const MAX_NEXT_ACTIONS = 2;
/** At most this many characters of an offer's action on a button. */
const LABEL_MAX = 44;

export type NextActionKind =
  | "keep_going"
  | "fix_tests"
  | "fix_review"
  | "do_offer"
  | "fill_offer"
  | "commit"
  | "open_pr"
  | "connect_continue";

export interface NextAction {
  kind: NextActionKind;
  label: string;
  /** The button's hover: what the press does, in words. */
  title: string;
  /** "send": the words go out as a message (the same as typing them and
   *  pressing Send). "fill": they go in the message box and nothing is sent.
   *  "press": the host opens its existing press (Commit, Create PR,
   *  Continue). */
  how: "send" | "fill" | "press";
  /** The words a "send" or "fill" carries. */
  text?: string;
  /** The step-limit / cap line's old test id, kept on the folded button. */
  testid?: string;
}

export interface NextStep {
  /** One or two short sentences; "" when the box carries only a press whose
   *  own words say it (Continue after an app was connected). */
  recap: string;
  actions: NextAction[];
}

/** What the box reads off ONE reply (the host decodes it). */
export interface ReplyFacts {
  content: string;
  /** How many files the reply's record names (its `documents`). */
  documents: number;
  finish: FinishRecord | null;
  /** The reply met its step limit, or the session's time/cost cap. */
  stopped: "step_limit" | "cap" | null;
  /** The reply failed, ended on an account's limit card, or was declined:
   *  its own line is the next step. */
  failed: boolean;
  /** The reply was cut off mid-answer (its own Continue is the press). */
  cutOff: boolean;
}

/** A coding session's branch, as the Branch view reads it (lib/buildGit). */
export interface GitFacts {
  branch: string;
  base: string;
  uncommitted: number;
  ahead: number;
  prUrl: string;
}

const plural = (n: number, w: string) => `${n} ${w}${n === 1 ? "" : "s"}`;
const cap1 = (s: string) => (s ? s[0].toUpperCase() + s.slice(1) : s);

function joinAnd(parts: string[]): string {
  if (parts.length <= 1) return parts.join("");
  return `${parts.slice(0, -1).join(", ")} and ${parts[parts.length - 1]}`;
}

/** The tests' state in the box's words, from the finish record only. */
function testsWords(f: FinishRecord): string {
  if (finishWarn(f)) return "tests are failing";
  if (f.tests === "failed" && f.after === "passed") return "tests pass after a fix";
  if (f.tests === "passed" || f.after === "passed") return "tests pass";
  if (f.tests === "not_run") return "tests were not run";
  return "no tests found";
}

function reviewWords(f: FinishRecord): string {
  const r = f.review;
  if (!r) return "";
  if (r.state === "failed") return "the review failed";
  if (r.state !== "done") return "";
  if (r.verdict === "approved") return "the reviewer approved it";
  if (r.verdict === "changes_needed") return "the reviewer asked for changes";
  return "";
}

function shortLabel(action: string): string {
  const a = cap1(action);
  if (a.length <= LABEL_MAX) return `${a}…`;
  const cut = a.slice(0, LABEL_MAX);
  const sp = cut.lastIndexOf(" ");
  return `${(sp > 10 ? cut.slice(0, sp) : cut).trim()}…`;
}

/**
 * Review (v1.406.0 part 4): words that make an offer the USER'S call on the
 * box even when the daemon's reading calls it plain "work". The daemon's
 * `user_owns` list was written for the closing NUDGE, where a miss only
 * means the model may carry on by itself; here a "work" offer is ONE press
 * that sends "Yes, go ahead." — consent in the user's own words — so
 * "Want me to git push --force?", "…transfer the funds?", "…rm -rf the build
 * folder?" and "…wipe the logs?" must only be named, like a send. Read over
 * the offer's whole last sentence; a false hit only costs one more press
 * (the words go in the box, the user presses Send). The daemon's reading,
 * and so its nudge, is unchanged.
 */
const ASKS_FIRST_ANYWHERE =
  /(?:\b(?:push(?:es|ed|ing)?|force[- ]?push\w*|deploy\w*|publish\w*|releas(?:e|es|ed|ing)\b(?!\s+notes?)|merg(?:e|es|ed|ing)|commit(?:s|ted|ting)?|delet\w*|wip(?:e|es|ed|ing)|eras(?:e|es|ed|ing)|purg\w*|destroy\w*|truncat\w*|drop(?:s|ped|ping)?\s+(?:the\s+|that\s+|this\s+)?(?:\w+\s+)?(?:tables?|databases?|db|schemas?|columns?|collections?)|pay(?:s|ing|ments?)?|charg(?:e|es|ed|ing)|refund\w*|purchas\w*|transfer\w*|wire\s+(?:the\s+)?(?:money|funds|payments?|cash)|revok\w*|uninstall\w*|unsubscrib\w*|overwrit\w*|production|prod|migrations?|rm|del)\b|--force\b|--hard\b)/i;
/** …and these only as the VERB of a clause ("text her", not "the text"). */
const ASKS_FIRST_VERB =
  /(?:\b(?:me\s+to|shall\s+(?:i|we)|should\s+i|(?:can|could|may)\s+i|happy\s+to|glad\s+to|and|then|also)\s+|[,;]\s*)(?:go\s+ahead\s+and\s+)?(?:send|e-?mail|text|message|post|share|upload|notify|invite|tweet|reply|respond|forward|submit|file|sign|approve|accept|book|order|place\s+the\s+order|buy|cancel|reset|clear|empty|archive|revert|rotate|ship)\b/i;

function asksFirst(content: string): boolean {
  const s = lastSentence(content);
  return !!s && (ASKS_FIRST_ANYWHERE.test(s) || ASKS_FIRST_VERB.test(s));
}

/** The offer the reply ended on, as a press (or null). */
function offerAction(content: string, git: GitFacts | null): { action: NextAction; recap: string } | null {
  let kind: OfferKind = offerKind(content);
  if (!kind) return null;
  if (kind === "work" && asksFirst(content)) kind = "user";
  const words = offeredAction(content);
  if (kind === "work") {
    return {
      recap: words ? `Offered next: ${words}.` : "It offered to do more.",
      action: {
        kind: "do_offer",
        label: "Yes, do it",
        title: `Sends "${YES_TEXT}" (the same as typing it)`,
        how: "send",
        text: YES_TEXT,
      },
    };
  }
  // The USER'S decision: named, never acted on. A commit or a pull request
  // on a session's own branch opens the existing press (its form waits for
  // the user); anything else only fills the message box.
  const recap = words ? `Offered next: ${words}. That one is your call.` : "It asked before doing something that is your call.";
  if (git && /^commit/i.test(words) && git.uncommitted > 0) return { recap, action: commitAction() };
  if (git && /^(?:open|opening|create|creating|file|raise|submit)\b.*\b(?:pr|pull\s+request)s?$/i.test(words) && git.uncommitted === 0 && git.ahead > 0 && git.base)
    return { recap, action: prAction() };
  const fill = words ? `Yes, ${words}.` : YES_TEXT;
  return {
    recap,
    action: {
      kind: "fill_offer",
      label: words ? shortLabel(words) : "Answer it…",
      title: `Puts "${fill}" in the message box. Nothing is sent until you press Send.`,
      how: "fill",
      text: fill,
    },
  };
}

function commitAction(): NextAction {
  return {
    kind: "commit",
    label: "Commit",
    title: "Opens the branch's Commit: the message is drafted for you to read and edit; nothing is committed until you press Commit there",
    how: "press",
  };
}

function prAction(): NextAction {
  return {
    kind: "open_pr",
    label: "Open a pull request",
    title: "Opens the branch's Create PR: the title and description wait for you; nothing is opened until you press Create pull request there",
    how: "press",
  };
}

function capped(actions: NextAction[]): NextAction[] {
  const out: NextAction[] = [];
  for (const a of actions) {
    if (out.some((b) => b.kind === a.kind)) continue;
    out.push(a);
    if (out.length >= MAX_NEXT_ACTIONS) break;
  }
  return out;
}

/** A CODING SESSION's box under its newest settled reply, or null. `git` is
 *  the session's branch when it works on one (null otherwise, or unread). */
export function codeNextStep(r: ReplyFacts, git: GitFacts | null): NextStep | null {
  if (r.failed) return null;
  const f = r.finish;
  const changed = r.documents > 0 || !!f;
  const done: string[] = [];
  if (r.documents > 0) done.push(`changed ${plural(r.documents, "file")}`);
  if (f) {
    done.push(testsWords(f));
    const rw = reviewWords(f);
    if (rw) done.push(rw);
  }
  const actions: NextAction[] = [];
  let left = "";
  if (r.stopped) {
    left = r.stopped === "cap" ? "It stopped at this session's limit before it finished." : "It stopped before it finished.";
    actions.push({
      kind: "keep_going",
      label: "Keep going",
      title: `Carry on from where it stopped (sends "${NEXT_KEEP_GOING_TEXT}")`,
      how: "send",
      text: NEXT_KEEP_GOING_TEXT,
      testid: r.stopped === "cap" ? "harness-cap-keep-going" : "harness-keep-going",
    });
  }
  if (f && finishWarn(f)) {
    actions.push({ kind: "fix_tests", label: "Fix the failing tests", title: `Sends "${FIX_TESTS_TEXT}"`, how: "send", text: FIX_TESTS_TEXT });
  }
  if (f?.review?.state === "done" && f.review.verdict === "changes_needed") {
    actions.push({ kind: "fix_review", label: "Fix what the reviewer found", title: `Sends "${FIX_REVIEW_TEXT}"`, how: "send", text: FIX_REVIEW_TEXT });
  }
  if (!left && f && f.unmet > 0) left = `${plural(f.unmet, "point")} of the request still need${f.unmet === 1 ? "s" : ""} work.`;
  // The offer is read only off a reply that ended whole.
  const offer = r.cutOff || r.stopped ? null : offerAction(r.content, changed ? git : null);
  if (offer) {
    actions.push(offer.action);
    if (!left) left = offer.recap;
  }
  // The branch: only after a reply that changed something (a plain question
  // in a session with work waiting does not nag).
  if (git && changed) {
    if (git.uncommitted > 0) {
      actions.push(commitAction());
      if (!left) left = `The changes on ${git.branch} are not committed yet.`;
    } else if (git.ahead > 0 && !git.prUrl && git.base) {
      actions.push(prAction());
      if (!left) left = `${git.branch} is committed; there is no pull request yet.`;
    }
  }
  const first = done.length ? `${cap1(joinAnd(done))}.` : "";
  const recap = [first, left].filter(Boolean).join(" ");
  if (!recap && !actions.length) return null;
  return { recap, actions: capped(actions) };
}

/** A CHAT's box under its newest settled reply, or null: the closing-offer
 *  press, or Continue after the app the reply offered to connect was
 *  connected somewhere else. Nothing else. */
export function chatNextStep(r: ReplyFacts, opts: { connected?: boolean } = {}): NextStep | null {
  if (r.failed) return null;
  if (opts.connected) {
    return {
      recap: "",
      actions: [{ kind: "connect_continue", label: "Continue", title: "Carry on with what you asked, now that it is connected", how: "press" }],
    };
  }
  if (r.cutOff) return null;
  const offer = offerAction(r.content, null);
  if (!offer) return null;
  return { recap: offer.recap, actions: [offer.action] };
}

/** A saved CODING-SESSION reply line, as the box reads it: its finish check,
 *  step limit and cap through the screen's own decoders. */
export function codeReplyFacts(m: {
  content?: unknown;
  documents?: unknown;
  finish?: unknown;
  stepLimit?: unknown;
  capMet?: unknown;
  error?: unknown;
  accountLimit?: unknown;
  errorKind?: unknown;
  truncated?: unknown;
  interrupted?: unknown;
}): ReplyFacts {
  return {
    content: typeof m.content === "string" ? m.content : "",
    documents: Array.isArray(m.documents) ? m.documents.length : 0,
    finish: decodeFinish(m.finish),
    stopped: decodeStepLimit(m.stepLimit) ? "step_limit" : decodeCapMet(m.capMet) ? "cap" : null,
    failed: !!m.error || !!m.accountLimit || m.errorKind === "refusal",
    cutOff: !!m.truncated || !!m.interrupted,
  };
}

/** A session's branch for the box: its live branch (never a removed worktree
 *  or a merged branch) and the Branch view's own reading. */
export function gitFacts(
  git: { branch: string; base: string; prUrl: string; removed: boolean; merged: boolean } | null | undefined,
  status: { uncommitted: number; ahead: number } | null | undefined,
): GitFacts | null {
  if (!git || git.removed || git.merged || !status) return null;
  return { branch: git.branch, base: git.base, prUrl: git.prUrl, uncommitted: status.uncommitted, ahead: status.ahead };
}

/** A saved CHAT reply, as the box reads it (only these keys). */
export interface ChatReplyShape {
  content?: unknown;
  documents?: unknown;
  truncated?: unknown;
  interrupted?: unknown;
  refused?: unknown;
  panelError?: unknown;
}

/** The facts of a chat reply — its words, the files its record names, and
 *  whether it was cut off or declined. A chat reply has no finish check. */
export function chatReplyFacts(m: ChatReplyShape): ReplyFacts {
  return {
    content: typeof m.content === "string" ? m.content : "",
    documents: Array.isArray(m.documents) ? m.documents.length : 0,
    finish: null,
    stopped: null,
    failed: !!m.refused || !!m.panelError,
    cutOff: !!m.truncated || !!m.interrupted,
  };
}
