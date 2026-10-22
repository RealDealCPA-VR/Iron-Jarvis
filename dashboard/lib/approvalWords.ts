/**
 * ONE SET OF APPROVAL WORDS (v1.352.0, product-readiness G24/G25).
 *
 * Every place that answers an ask (the chat card, the session page, a
 * project's paused task, the mission screen, the bell) offers the same three
 * answers in the same words: "Allow once", "Allow for this chat" or "Allow
 * for this run" (the answer the daemon calls `conversation`: the rest of THIS
 * chat, or the rest of THIS agent run), and "Deny". Before this file the bell
 * said "Approve once", the mission screen "Allow for this task" and
 * "Decline", and the session page offered "Allow for this conversation" on an
 * agent run that has no conversation.
 *
 * DEPENDENCY-FREE on purpose (like lib/toolWords): the bell is on every page
 * and must not pull the chat card (ApprovalPreview, the diff renderer) into
 * every page's bundle. `components/chat/ApprovalCard` re-exports these, so
 * "the card's labels" and "these labels" are one thing.
 *
 * The answer VALUES posted to `POST /chat/approvals/{id}` are unchanged
 * (once | conversation | deny | tab | always): rename a word freely, never a
 * value.
 */

import { toolWords } from "@/lib/toolWords";
import { REMOTE_KIND, remoteTaskSubject } from "@/lib/remoteTask";

/** The narrow answer: this one call. */
export const ALLOW_ONCE = "Allow once";

/** The refusal. The assistant (or the run) is told the user declined. */
export const DENY = "Deny";

/** A browser page action, for its tab (the chat card only). */
export const ALLOW_TAB = "Allow for this tab";

/** A standing grant on the EXACT arguments (offered only by the daemon). */
export const ALWAYS_EXACT = "Always allow exactly this";

/** Where the wider answer applies: the open chat, or the agent run that
 *  asked (the session page, a project's task, the mission screen, the bell). */
export type ApprovalScope = "chat" | "run" | "session";

/** "Allow for this chat" / "Allow for this run": the daemon's `conversation`
 *  answer, named for where it applies. */
export function scopeLabel(scope: ApprovalScope): string {
  // v1.369.0: a Build session's card (the daemon's `conversation` answer
  // keeps the grant on the session).
  if (scope === "session") return "Allow for this session";
  return scope === "run" ? "Allow for this run" : "Allow for this chat";
}

/** The scope's own noun, for the card's explanation ("this chat" / "this run"). */
export function scopeNoun(scope: ApprovalScope): string {
  if (scope === "session") return "this session";
  return scope === "run" ? "this run" : "this chat";
}

/** The card's labels, in one object, for any surface that lists them. */
export const APPROVAL_LABELS = {
  once: ALLOW_ONCE,
  chat: scopeLabel("chat"),
  run: scopeLabel("run"),
  tab: ALLOW_TAB,
  always: ALWAYS_EXACT,
  deny: DENY,
} as const;

/**
 * What the action does, completing "… wants to …": the tool's plain words
 * ("save a file") when lib/toolWords knows the tool, else "run <id>" (the
 * chat card's own fallback). Never invented words for a tool nobody
 * described. The raw id stays in a `title` wherever this is shown.
 */
export function askPhrase(tool: string): string {
  const id = (tool || "").trim();
  return toolWords(id) ?? `run ${id || "a tool"}`;
}

/**
 * CAN IT BE UNDONE (G25). The daemon puts `reversible` on the ask: `true`
 * for a tool that keeps an inverse (a file save in the undo journal, a
 * settings change), `false` for one that does not (a sent message, a
 * command, a browser click, a paid generation, any app tool, any tool that
 * never said). It is ABSENT for a read-only tool and for a name the registry
 * does not hold (the plan approval): there is nothing to undo, so the card
 * says nothing about undo. "May" because an undeclared tool defaults to not
 * undoable, which is the safe side, not a proven fact.
 */
export function undoLine(reversible: unknown): string | null {
  if (reversible === true) return "You can undo this.";
  if (reversible === false) return "This may not be undoable.";
  return null;
}

/** The kinds of run whose origin suffix is a NAME the user gave it
 *  ("schedule:nightly-brief"). The rest carry an internal id, never shown. */
const NAMED_RUN_KINDS: ReadonlySet<string> = new Set(["schedule", "reflex", "sentinel"]);

/**
 * WHO IS ASKING, as the subject of the bell's sentence: "Your nightly-brief
 * run" for a schedule, "Your objective" for a mission's coordinator, "A
 * teammate on your objective", "A task from desk" for a task another Jarvis
 * sent (v1.357.0, the canon's words: VOCABULARY.md), else "An agent run".
 * Read from `Session.origin` (the same values OriginChip reads); an origin
 * with no name worth saying is the plain "An agent run", never a guessed one.
 */
export function runSubject(origin: string | null | undefined): string {
  const value = (origin || "").trim();
  if (value === "job:mission") return "Your objective";
  if (value === "job:mission-member") return "A teammate on your objective";
  // v1.372.0: a helper agent a Build session started (harness/helpers).
  if (value === "job:build-helper") return "A helper in your Build session";
  const i = value.indexOf(":");
  if (i > 0) {
    const kind = value.slice(0, i).trim();
    const name = value.slice(i + 1).trim();
    if (kind === REMOTE_KIND) return remoteTaskSubject(name);
    if (NAMED_RUN_KINDS.has(kind) && name) return `Your ${name} run`;
  }
  return "An agent run";
}

/** The bell's whole ask sentence: "Your nightly-brief run wants to save a
 *  file". A plan ask has its own words. */
export function askSentence(origin: string | null | undefined, tool: string): string {
  return `${runSubject(origin)} wants to ${askPhrase(tool)}`;
}

/** What the bell says after an answer landed, for a few seconds, before the
 *  row leaves (the run goes on either way: a denied call is refused, and the
 *  agent is told). Plain sentences, no dash asides (the calm copy rule). */
export function answeredLine(decision: "approved" | "denied"): string {
  return decision === "denied" ? "Denied. That action will not run." : "Approved. The run continues";
}

/** How long the bell keeps the "Approved" / "Denied" line before the row
 *  leaves. */
export const ANSWERED_LINGER_MS = 4000;
