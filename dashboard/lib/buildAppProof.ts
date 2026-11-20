/**
 * 22-a (R1.9): Iron Jarvis CHECKS every Build › Code reply. The daemon runs
 * the folder's own tests after a reply that changed files (hosted/proof.py)
 * and writes ONE `proof` record into the session's log, keyed by the reply
 * it is about (`for_turn`): first `state: "checking"`, then `"done"` with
 * what happened. This module decodes that record (by whitelist) and says how
 * the conversation draws it. The words come from the daemon (`line`), built
 * from its own record; nothing here reads the coding tool's words.
 *
 *  - checking  → "Iron Jarvis is checking this reply: running `npm test`." + Stop
 *  - passed / failed → "Checked by Iron Jarvis: 14 tests ran, 14 passed. 2 new files: …"
 *  - ask       → the first-time card: "Run `npm test` after each reply in this folder?"
 *  - not_run   → "Not checked: …" (a Stop, a new message, a slow suite, a missing program)
 *  - off       → said only for the folder's own finish.toml, or right after "Not here"
 *  - none      → nothing drawn (no test command found: chat can still say so)
 */

export type ProofTests = "passed" | "failed" | "none" | "off" | "not_run" | "ask";

const TESTS: readonly ProofTests[] = ["passed", "failed", "none", "off", "not_run", "ask"];

export interface ProofMark {
  /** The reply (turn id) the record is about. */
  forTurn: string;
  state: "checking" | "done";
  tests: ProofTests;
  /** The daemon's reason code (hosted/proof WHY_*). */
  why: string;
  /** The exact command (as the card showed it). */
  command: string;
  /** Counts read from the runner's own summary; null = not known (never guessed). */
  ran: number | null;
  passed: number | null;
  failed: number | null;
  seconds: number | null;
  newFiles: string[];
  newSource: "git" | "tool" | "";
  /** The daemon's words for the line under the reply. */
  line: string;
  /** The card's second sentence (only on `ask`). */
  detail: string;
  /** How check-again ended when the tests still failed ("" = it did not end there). */
  ended: string;
  /** The message the work started from (Rewind to before it). */
  originAt: string;
  withheld: boolean;
  /** The record that answered the card ("Not here"). */
  answered: boolean;
}

function s(v: unknown, max = 2_000): string {
  return typeof v === "string" ? v.slice(0, max) : "";
}

function n(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) && v >= 0 ? v : null;
}

/** The record's keys, by whitelist; null when it names no reply or no outcome. */
export function decodeProofMark(raw: Record<string, unknown>): ProofMark | null {
  const forTurn = s(raw.for_turn, 200).trim();
  const tests = TESTS.includes(raw.tests as ProofTests) ? (raw.tests as ProofTests) : null;
  const line = s(raw.line, 3_000).trim();
  if (!forTurn || !tests || !line) return null;
  const files = Array.isArray(raw.new_files)
    ? raw.new_files.filter((f): f is string => typeof f === "string" && f.trim() !== "").slice(0, 50)
    : [];
  return {
    forTurn,
    state: raw.state === "checking" ? "checking" : "done",
    tests,
    why: s(raw.why, 40),
    command: s(raw.command, 600),
    ran: n(raw.ran),
    passed: n(raw.passed),
    failed: n(raw.failed),
    seconds: n(raw.seconds),
    newFiles: files,
    newSource: raw.new_source === "git" || raw.new_source === "tool" ? raw.new_source : "",
    line,
    detail: s(raw.detail, 1_000),
    ended: s(raw.ended, 20),
    originAt: s(raw.origin_at, 64),
    withheld: raw.withheld === true,
    answered: raw.answered === true,
  };
}

/** What the conversation draws for one reply's record. */
export type ProofShape = "checking" | "unfinished" | "card" | "line" | "none";

/** `newestAsk`: this record is the newest card still asked in the session
 *  (an older one is never drawn again: the newest asks for them all).
 *  `checkingNow`: the session says a check runs now. A "checking" record the
 *  session no longer stands behind (Iron Jarvis closed while it ran) is never
 *  drawn as running: it says the check did not finish. */
export function proofShape(p: ProofMark, newestAsk: boolean, checkingNow = true): ProofShape {
  if (p.state === "checking") return checkingNow && newestAsk ? "checking" : "unfinished";
  if (p.tests === "ask") return newestAsk ? "card" : "none";
  if (p.tests === "none" && p.why !== "no_tests") return "none";
  if (p.tests === "off") return p.answered || p.why === "folder_file" ? "line" : "none";
  return "line";
}

/** The line's tone: a check that passed, one that did not, or one that did
 *  not run (said quietly). */
export function proofTone(p: ProofMark): "ok" | "warn" | "quiet" {
  if (p.tests === "passed" || (p.tests === "none" && p.why === "no_tests")) return "ok";
  if (p.tests === "failed") return "warn";
  return "quiet";
}

/** The card's two answers and the Stop beside a running check. */
export const CHECK_YES = "Yes, after each reply";
export const CHECK_NO = "Not in this folder";
export const CHECK_STOP = "Stop the check";
export const CHECK_UNFINISHED = "Not checked: the check did not finish.";
export const CHECK_REWIND = "Rewind to before this message";
export const CHECK_REWIND_TITLE =
  "Puts the folder back as it was before this message started (a preview shows every file first).";

/** Where an answer to the card goes (hosted/proof, routes/build_app_proof). */
export function checkAnswerPath(sessionId: string): string {
  return `/build/app/sessions/${encodeURIComponent(sessionId)}/checks`;
}
