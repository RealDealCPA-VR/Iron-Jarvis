/**
 * A SECOND MODEL double-checks finished work (the intelligence plan, 5b;
 * the daemon's `checks/second` has the rules). This module decodes what the
 * daemon says, as untrusted input, and names the presses:
 *
 * - the reply's `double_check` (done frame and POST answer, saved on the
 *   reply as `doubleCheck`): the ONCE-ONLY offer (`{offer: {choice, model,
 *   account, words}}`), or a double-check that was started after the reply
 *   (`{token, who}`);
 * - `GET /checks/double-check`: how it stands, and what a yes stores now;
 * - `GET /checks/double-check/run/{token}`: `{running, checked}`. "<model>
 *   is double-checking this" is drawn ONLY while this says running; once it
 *   has landed, `checked` is the reply's whole line again.
 *
 * The card's words come from the daemon whole (the plan's own sentence with
 * the exact model and account); this module composes only the presses and
 * the waiting line.
 */

import { get, post } from "./api";
import { decodeChecked, seenText, type CheckedMark } from "./checked";

export const DOUBLE_CHECK_PATH = "/checks/double-check";
export const DOUBLE_CHECK_SETTING = "double_check";
export const DOUBLE_CHECK_SETTINGS_HREF = "/settings?focus=double_check";

export const YES = "Yes, double-check";
export const NO = "No";
export const TURN_OFF = "Turn off";
export const DID_NOT_WORK = "That did not work. Try again.";
/** Said under the reply while the daemon says the double-check is running. */
export function waitingWords(who: string): string {
  return `${who} is double-checking this…`;
}
/** The Settings row when there is nothing to offer (one account or none). */
export const NOTHING_TO_OFFER =
  "Needs a second model account on the Several accounts card (Settings › Connections). With one account, Iron Jarvis's own checks are the only ones.";
export const OFF_WORDS = "Off: Iron Jarvis's own checks only.";

/** How often the page asks while a double-check runs, and how many times at
 *  most (about two minutes: past the daemon's own 90-second cap). */
export const RUN_ASK_MS = 3000;
export const RUN_ASKS_MAX = 45;

const TEXT_MAX = 400;

function text(v: unknown, cap = TEXT_MAX): string {
  return typeof v === "string" ? seenText(v).trim().slice(0, cap) : "";
}

export interface DoubleCheckOffer {
  kind: "offer";
  choice: string;
  model: string;
  account: string;
  words: string;
}

export interface DoubleCheckRunning {
  kind: "running";
  token: string;
  who: string;
}

export type DoubleCheckMark = DoubleCheckOffer | DoubleCheckRunning;

const TOKEN_RX = /^[0-9a-f]{8,64}$/;

/** The reply's `double_check`, or null for anything that is not one. */
export function decodeDoubleCheck(raw: unknown): DoubleCheckMark | null {
  if (!raw || typeof raw !== "object") return null;
  const r = raw as Record<string, unknown>;
  if (r.kind === "offer" || (r.offer && typeof r.offer === "object")) {
    const o = (r.kind === "offer" ? r : r.offer) as Record<string, unknown>;
    const choice = text(o.choice);
    const words = text(o.words);
    if (!choice || !words) return null;
    return { kind: "offer", choice, model: text(o.model, 120), account: text(o.account, 120), words };
  }
  const token = typeof r.token === "string" && TOKEN_RX.test(r.token) ? r.token : "";
  const who = text(r.who, 120);
  if (!token || !who) return null;
  return { kind: "running", token, who };
}

export type DoubleCheckState = "unset" | "off" | "on";

export interface DoubleCheckView {
  state: DoubleCheckState;
  words: string;
  choice: { id: string; model: string; account: string } | null;
  offer: { choice: string; model: string; account: string; words: string } | null;
}

export function decodeDoubleCheckView(raw: unknown): DoubleCheckView | null {
  if (!raw || typeof raw !== "object") return null;
  const r = raw as Record<string, unknown>;
  const state = r.state === "unset" || r.state === "off" || r.state === "on" ? r.state : null;
  if (!state) return null;
  const c = r.choice && typeof r.choice === "object" ? (r.choice as Record<string, unknown>) : null;
  const o = r.offer && typeof r.offer === "object" ? (r.offer as Record<string, unknown>) : null;
  const offer =
    o && text(o.choice) && text(o.words)
      ? { choice: text(o.choice), model: text(o.model, 120), account: text(o.account, 120), words: text(o.words) }
      : null;
  return {
    state,
    words: text(r.words),
    choice: c && text(c.id) ? { id: text(c.id), model: text(c.model, 120), account: text(c.account, 120) } : null,
    offer,
  };
}

export async function readDoubleCheck(): Promise<DoubleCheckView | null> {
  return decodeDoubleCheckView(await get<unknown>(DOUBLE_CHECK_PATH));
}

/** A yes for the choice the card named (the daemon refuses one that is no
 *  longer what a yes stores), or "no". Returns the daemon's own note. */
export async function answerDoubleCheck(answer: string): Promise<{ note: string; view: DoubleCheckView | null }> {
  const got = await post<{ note?: unknown; view?: unknown }>(DOUBLE_CHECK_PATH, { answer });
  return { note: text(got?.note), view: decodeDoubleCheckView(got?.view) };
}

export interface DoubleCheckRun {
  running: boolean;
  checked: CheckedMark | null;
}

export async function readRun(token: string): Promise<DoubleCheckRun> {
  const got = await get<{ running?: unknown; checked?: unknown }>(
    `${DOUBLE_CHECK_PATH}/run/${encodeURIComponent(token)}`,
  );
  return { running: got?.running === true, checked: decodeChecked(got?.checked) };
}

/** What a reply saves (`doubleCheck`): the whole mark as decoded. */
export function doubleCheckForSave(mark: DoubleCheckMark): Record<string, unknown> {
  return { ...mark };
}
