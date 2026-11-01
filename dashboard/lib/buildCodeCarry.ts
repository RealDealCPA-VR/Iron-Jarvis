/**
 * v1.422.0 (Wave 2 "Never lose work", B3 + B5): Build › Code carrying on by
 * itself after an interruption — the words and the shapes the page reads
 * (the daemon's rules live in src/iron_jarvis/hosted/carry_on.py).
 *
 * - `cut` (on GET /build/app/sessions/{id}): the newest reply was cut off by
 *   a restart and was NOT carried on by itself; the page offers **Continue**
 *   (POST …/continue) under it, and says why it did not carry on itself.
 * - `account_wait`: the reply waits for its account's limit to reset (or
 *   looks for another account of the user's group) and carries on BY
 *   ITSELF; the limit card says until when, with **Cancel** (POST
 *   …/wait/cancel).
 * - a `user` event's `carry`: a message Iron Jarvis sent by itself to carry
 *   the reply on — drawn as one quiet line, never as the user's bubble.
 *
 * Dependency-free (the conversation and the page import it).
 */

export interface CutFacts {
  turn: string;
  /** The coding tool saved the conversation (Continue resumes it). */
  resumable: boolean;
  /** Why it was not carried on by itself ("" = it will be, or it was pressed). */
  whyNot: string;
  /** A card or a question was open when it was cut (that question was lost). */
  waiting: boolean;
}

export interface WaitFacts {
  account: string;
  accountName: string;
  /** ISO time the limit resets, or null (a grouped wait looking for a move). */
  resetsAt: string | null;
  /** The user's account group it may move within ("" = none). */
  group: string;
}

export interface CarryFacts {
  cut: CutFacts | null;
  wait: WaitFacts | null;
  /** The limit card's own note (it carried on by itself N times in a row). */
  limitNote: string;
}

/** What a hidden carry-on message says it was (its `carry`). */
export interface CarryMark {
  why: "restart" | "reset" | "move";
  fromAccount: string;
  toAccount: string;
  accountName: string;
  group: string;
  pressed: boolean;
}

function obj(v: unknown): Record<string, unknown> | null {
  return v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : null;
}

function str(v: unknown, cap = 300): string {
  return typeof v === "string" ? v.slice(0, cap) : "";
}

/** The carry facts a session read answers (each null when absent — an older
 *  daemon answers neither). */
export function decodeCarry(raw: unknown): CarryFacts {
  const r = obj(raw) ?? {};
  const c = obj(r.cut);
  const w = obj(r.account_wait);
  const cutTurn = c ? str(c.turn, 200) : "";
  return {
    cut: c && cutTurn
      ? {
          turn: cutTurn,
          resumable: c.resumable !== false,
          whyNot: str(c.why_not, 400),
          waiting: c.waiting === true,
        }
      : null,
    wait: w
      ? {
          account: str(w.account, 200),
          accountName: str(w.account_name, 200),
          resetsAt: str(w.resets_at, 64) || null,
          group: str(w.group, 120),
        }
      : null,
    limitNote: str(r.account_limit_note, 400),
  };
}

/** A `user` event's `carry`, or null (the user's own message). */
export function decodeCarryMark(raw: unknown): CarryMark | null {
  const r = obj(raw);
  if (!r) return null;
  const why = r.why === "restart" || r.why === "reset" || r.why === "move" ? r.why : null;
  if (!why) return null;
  return {
    why,
    fromAccount: str(r.from_account, 200),
    toAccount: str(r.to_account, 200),
    accountName: str(r.account_name, 200),
    group: str(r.group, 120),
    pressed: r.pressed === true,
  };
}

/** The quiet line a carry-on message is drawn as. */
export function carryLineWords(m: CarryMark): string {
  if (m.why === "restart") {
    return m.pressed
      ? "You pressed Continue: carrying on the reply Iron Jarvis's restart cut off."
      : "Carried on by itself after Iron Jarvis restarted — it checks what was already done first.";
  }
  if (m.why === "move") {
    const to = m.toAccount ? ` with ${m.toAccount}` : " on another account";
    const from = m.fromAccount ? ` — ${m.fromAccount} reached its limit` : "";
    return `Carried on by itself${to}${from}.`;
  }
  const who = m.accountName ? `${m.accountName}'s` : "The account's";
  return `${who} limit reset, so it carried on by itself.`;
}

/** "3:45 PM" today, else "Tue 3:45 PM" (the reader's own clock). */
export function resetTimeWords(iso: string | null, now: Date = new Date()): string {
  if (!iso) return "";
  const t = new Date(iso);
  if (Number.isNaN(t.getTime())) return "";
  const time = t.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
  const sameDay = t.toDateString() === now.toDateString();
  return sameDay ? time : `${t.toLocaleDateString(undefined, { weekday: "short" })} ${time}`;
}

/** The limit card's line while it waits by itself. */
export function waitWords(w: WaitFacts, now: Date = new Date()): string {
  const who = w.accountName || "this account";
  const when = resetTimeWords(w.resetsAt, now);
  if (w.group && !when) {
    return `Looking for another account in your group "${w.group}" to carry on with — it carries on by itself when one is free.`;
  }
  const base = when
    ? `Waiting for ${who} to reset at ${when} — it carries on by itself then.`
    : `Waiting for ${who} to reset — it carries on by itself then.`;
  return w.group ? `${base} If another account in "${w.group}" is free first, it moves there instead.` : base;
}

/** The press on a reply a restart cut off. */
export const CONTINUE_CUT = "Continue";
export const CONTINUE_CUT_TITLE = "Carry this reply on from where it stopped (the conversation is kept)";
/** Cancel on a limit card that waits by itself. */
export const CANCEL_WAIT = "Cancel";
export const CANCEL_WAIT_TITLE = "Stop waiting — nothing carries on by itself";

/** The line under a cut reply that was not carried on by itself. */
export function cutWords(c: CutFacts): string {
  if (!c.resumable) {
    return "Iron Jarvis restarted before the coding tool saved this conversation, so it cannot be carried on — send your message again.";
  }
  if (!c.whyNot) {
    return "Iron Jarvis restarted while this was working. Press Continue to carry it on from where it stopped.";
  }
  // v1.422.0 review: a reason the coding tool's own refusal gave is a whole
  // SENTENCE ("This conversation is open in a Build terminal — …."), never a
  // phrase to follow "because" (it read "because This … here..").
  return /[.!?]$/.test(c.whyNot.trim())
    ? `It did not carry on by itself: ${c.whyNot.trim()}`
    : `It did not carry on by itself because ${c.whyNot}.`;
}

/** The tools whose conversation can move to another account. */
export const MOVABLE_TOOLS: readonly string[] = ["claude-code", "codex"];

export function canMoveAccounts(tool: string): boolean {
  return MOVABLE_TOOLS.includes(tool);
}
