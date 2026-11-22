/**
 * Pick up where you left off (next leaps 04-a; build-out plan R2.1, idea 04):
 * the card a NEW chat, a new Chat › Code session or a new Build › Code session
 * shows when its folder is picked. One read of `GET /work-record/pick-up` per
 * folder pick (the daemon's `workmap/pickup.py` is the contract), never
 * polled.
 *
 * - The full card ("Last time (12 Mar): …", Done, Decided, Still open,
 *   Continue) only when something is still open or the last work was in the
 *   past 30 days (D-04.4, the daemon's `full`); otherwise one quiet line,
 *   "Last here: 12 Mar", that opens it.
 * - Continue sends ONE fixed sentence written here (`CONTINUE_SENTENCE`):
 *   the card's lines reach the model the way they already do (the work
 *   block), as a record, never as the owner's words.
 * - A cross hides the card for that folder until new work is on record
 *   (kept in this browser only: `ij_pick_up_hidden`, keyed by a one-way
 *   digest of the folder, so no folder's path can be read back from it; the
 *   Memory page's "Forget this folder" clears that folder's note).
 * - Build › Code: the user's own coding tool is told ONLY the sentence, not
 *   what the card lists, and the card says so in those words under Continue
 *   (`PICK_UP_TOOL_NOTE`).
 *
 * Decoders keep only what the card shows, typed. No imports from lib/api
 * (many tests mock that module whole).
 */

import { normalizeIso } from "./format";
import { insideLink } from "./workRecord";

export const PICK_UP_PATH = "/work-record/pick-up";

/** `local`: the new chat or session is Local only. `chatCode`: it is a new
 *  Chat › Code session, whose turns are grounded in the project that holds
 *  its folder (the daemon then answers no card for a project's folder). */
export function pickUpPath(folder: string, local = false, chatCode = false): string {
  return `${PICK_UP_PATH}?folder=${encodeURIComponent(folder)}${local ? "&local=true" : ""}${chatCode ? "&chat_code=true" : ""}`;
}

/** What Continue sends, word for word (written in code, never by a model).
 *  The daemon takes it out of the turn's words before it matches earlier
 *  work (`workmap/block.without_continue`, a test reads it here): it points
 *  at this folder and nothing else. */
export const CONTINUE_SENTENCE = "Continue where we left off in this folder.";

/** What the name of a chat or session Continue starts ends with. */
export const CONTINUED_MARK = " (continued)";
/** The longest name of the work continued that is kept (a chat's own name
 *  from its first message is cut at the same length). */
const CONTINUED_BASE_CHARS = 48;

/** The name of the chat or session Continue starts: the work it continues,
 *  then "(continued)" — never the fixed sentence, which every such chat
 *  would share (the next card would be headed by it, and the record would
 *  list one name for many pieces of work). A chain of Continues keeps one
 *  mark. A last piece named by the sentence itself (a chat the owner typed
 *  it into) gives the folder's name instead. */
export function continuedTitle(p: PickUp): string {
  let base = p.last.title.replace(/\s+/g, " ").trim();
  while (base.toLowerCase().endsWith(CONTINUED_MARK.toLowerCase()))
    base = base.slice(0, base.length - CONTINUED_MARK.length).trim();
  if (!base || base.toLowerCase() === CONTINUE_SENTENCE.toLowerCase()) base = p.name.trim() || "Earlier work";
  if (base.length > CONTINUED_BASE_CHARS) base = `${base.slice(0, CONTINUED_BASE_CHARS - 1).trimEnd()}…`;
  return `${base}${CONTINUED_MARK}`;
}

/** One Decided or Still open line, with the piece of work it came from. */
export interface PickUpLine {
  text: string;
  entryId: string;
  title: string;
  /** An address inside the app, or "". */
  link: string;
  at: string | null;
}

export interface PickUpLast {
  id: string;
  kind: string;
  title: string;
  tool: string;
  at: string | null;
  link: string;
  outcome: string;
}

export interface PickUp {
  folder: string;
  name: string;
  last: PickUpLast;
  done: string[];
  decided: PickUpLine[];
  open: PickUpLine[];
  pieces: number;
  /** A recap here was written before the latest messages (not shown). */
  recapPending: boolean;
  /** D-04.4: the full card (else the quiet line). */
  full: boolean;
  words: string;
}

function text(v: unknown): string {
  return typeof v === "string" ? v : "";
}

function line(v: unknown): PickUpLine | null {
  if (!v || typeof v !== "object" || Array.isArray(v)) return null;
  const o = v as Record<string, unknown>;
  const t = text(o.text).trim();
  if (!t) return null;
  return {
    text: t.slice(0, 400),
    entryId: text(o.entry_id),
    title: text(o.title).slice(0, 200),
    link: insideLink(o.link),
    at: text(o.at) || null,
  };
}

function lines(v: unknown): PickUpLine[] {
  return (Array.isArray(v) ? v : []).map(line).filter((l): l is PickUpLine => l !== null);
}

/** The card, or null when the folder has no earlier work on record (or the
 *  answer is not one). */
export function decodePickUp(raw: unknown): PickUp | null {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  const o = raw as Record<string, unknown>;
  const l = o.last;
  if (!l || typeof l !== "object" || Array.isArray(l)) return null;
  const last = l as Record<string, unknown>;
  const id = text(last.id);
  const title = text(last.title).trim();
  if (!id || !title) return null;
  const done = (Array.isArray(o.done) ? o.done : [])
    .filter((d): d is string => typeof d === "string" && d.trim().length > 0)
    .map((d) => d.trim().slice(0, 400));
  const pieces = typeof o.pieces === "number" && Number.isFinite(o.pieces) && o.pieces > 0 ? Math.floor(o.pieces) : 1;
  return {
    folder: text(o.folder),
    name: text(o.name),
    last: {
      id,
      kind: text(last.kind),
      title: title.slice(0, 200),
      tool: text(last.tool),
      at: text(last.at) || null,
      link: insideLink(last.link),
      outcome: text(last.outcome),
    },
    done,
    decided: lines(o.decided),
    open: lines(o.open),
    pieces,
    recapPending: o.recap_pending === true,
    full: o.full === true,
    words: text(o.words),
  };
}

// ---------------------------------------------------------------- the words

export const PICK_UP_CONTINUE = "Continue";
export const PICK_UP_DONE = "Done";
export const PICK_UP_DECIDED = "Decided";
export const PICK_UP_STILL_OPEN = "Still open";
export const PICK_UP_HIDE = "Hide this until there is new work here";
export const PICK_UP_PENDING =
  "A recap here was written before the latest messages, so it is left out until it is written again.";
/** Lines of one kind the card shows before "and N more". */
export const PICK_UP_LINES_SHOWN = 3;

/** Build › Code: said ON the card, under Continue (a hover is never seen on
 *  a phone, and most never hover). The user's own coding tool is told only
 *  the sentence: the card's Done, Decided and Still open lines are never
 *  sent to it (the record reaching that tool, with its own rules for what
 *  may leave this PC, is next leaps 14-c), and nothing is promised about
 *  what it does with the sentence. A test pins that the session starts with
 *  the sentence alone, beside these words. */
export const PICK_UP_TOOL_NOTE = `Your coding tool is not told what this card lists. Continue sends it only "${CONTINUE_SENTENCE}" and nothing else.`;

/** Continue's hover words: what is sent, and who is told the card. In
 *  Build › Code the coding tool is not shown Iron Jarvis's record. */
export function continueTitle(tool: boolean): string {
  return tool ? PICK_UP_TOOL_NOTE : `Sends "${CONTINUE_SENTENCE}" The reply is told what this card shows.`;
}

/** What failed to load, in the app's one sentence shape. */
export const PICK_UP_FAILED_THING = "Earlier work in this folder";

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "12 Mar" (this year) / "12 Mar 2025", on this PC's own day; "" for none. */
export function dayWords(iso: string | null | undefined, now: number = Date.now()): string {
  if (!iso) return "";
  const d = new Date(normalizeIso(iso));
  if (Number.isNaN(d.getTime())) return "";
  const head = `${d.getDate()} ${MONTHS[d.getMonth()]}`;
  return d.getFullYear() === new Date(now).getFullYear() ? head : `${head} ${d.getFullYear()}`;
}

/** "Last time (12 Mar)" — the card's heading. */
export function lastTimeWords(p: PickUp, now: number = Date.now()): string {
  const day = dayWords(p.last.at, now);
  return day ? `Last time (${day})` : "Last time";
}

/** "Last here: 12 Mar" — the quiet line (D-04.4's words). */
export function lastHereWords(p: PickUp, now: number = Date.now()): string {
  const day = dayWords(p.last.at, now);
  return day ? `Last here: ${day}` : "Last here: earlier";
}

/** How the last piece of work ended, when that is worth saying. */
export function outcomeWords(outcome: string): string {
  if (outcome === "failed") return "failed";
  if (outcome === "cancelled") return "stopped";
  if (outcome === "needs_you") return "needs you";
  if (outcome === "completed_with_failures") return "part failed";
  return "";
}

export function moreWords(n: number): string {
  return n > 0 ? `and ${n} more` : "";
}

// ------------------------------------------------- the cross (this browser)

const HIDDEN_KEY = "ij_pick_up_hidden";
/** Folders remembered, at most (the oldest go first). */
const HIDDEN_MAX = 200;

/** 64 bits of two FNV-1a passes, as base 36: one way, so the note never
 *  holds a readable folder path. */
function digest(s: string): string {
  let a = 0x811c9dc5;
  let b = 0x01000193;
  for (let i = 0; i < s.length; i++) {
    const c = s.charCodeAt(i);
    a = Math.imul(a ^ c, 0x01000193) >>> 0;
    b = Math.imul(b ^ c, 0x0100019d) >>> 0;
  }
  return `${a.toString(36).padStart(7, "0")}${b.toString(36).padStart(7, "0")}`;
}

/** The note's key for a folder: a one-way digest of the folder as the
 *  daemon compares it (forward slashes, no trailing slash, any case). This
 *  browser's storage never holds a folder's path or name, so a folder
 *  forgotten anywhere leaves nothing readable here. */
export function folderNoteKey(folder: string): string {
  return `f${digest(folder.trim().replace(/\\/g, "/").replace(/\/+$/, "").toLowerCase())}`;
}

/** What "new work" means: the last piece of work, and when it last moved. */
export function hiddenStamp(p: PickUp): string {
  return `${p.last.id}|${p.last.at ?? ""}`;
}

function readHidden(): Record<string, string> {
  try {
    const raw = window.localStorage.getItem(HIDDEN_KEY);
    const data = raw ? (JSON.parse(raw) as unknown) : null;
    if (!data || typeof data !== "object" || Array.isArray(data)) return {};
    const out: Record<string, string> = {};
    for (const [k, v] of Object.entries(data as Record<string, unknown>)) if (typeof v === "string") out[k] = v;
    return out;
  } catch {
    return {};
  }
}

/** Was this card hidden for this folder, with no new work since? */
export function isHidden(folder: string, p: PickUp): boolean {
  return readHidden()[folderNoteKey(folder)] === hiddenStamp(p);
}

/** Hide this card for this folder until new work is on record. */
export function hideFor(folder: string, p: PickUp): void {
  try {
    const all = readHidden();
    const key = folderNoteKey(folder);
    delete all[key];
    all[key] = hiddenStamp(p);
    const keys = Object.keys(all);
    for (const k of keys.slice(0, Math.max(0, keys.length - HIDDEN_MAX))) delete all[k];
    window.localStorage.setItem(HIDDEN_KEY, JSON.stringify(all));
  } catch {
    /* a browser that keeps nothing: the card is hidden for this visit only */
  }
}

/** "Forget this folder" (the Memory page): this browser's note for that
 *  folder goes too. */
export function forgetPickUpNote(folder: string): void {
  try {
    const all = readHidden();
    const key = folderNoteKey(folder);
    if (!(key in all)) return;
    delete all[key];
    window.localStorage.setItem(HIDDEN_KEY, JSON.stringify(all));
  } catch {
    /* a browser that keeps nothing has nothing to forget */
  }
}
