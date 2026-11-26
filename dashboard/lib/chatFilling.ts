/**
 * WHAT IS FILLING THIS CHAT (the intelligence plan, release 6): the list the
 * chat's fullness meter opens.
 *
 * The numbers are the daemon's: `GET /chat/threads/{id}/filling` reads the
 * newest reply's own record (the size of each part of what the reply was
 * sent; sizes and names only, never the text) and answers in the Context
 * drop-down's shape (lib/contextBreakdown), in a plain chat's words. This
 * file decodes that answer and holds the page's own words; it computes no
 * size. The one component is components/chat/ChatFilling, which draws the
 * coding session's own drop-down (components/harness/ContextBreakdown).
 *
 * Plain language (VOCABULARY.md): every size is an estimate, said as a share
 * of the chat's room ("about 12%"), never as a count.
 */

import { decodeBreakdown, type ContextList, type ContextReading } from "@/lib/contextBreakdown";
import { fillWords } from "@/lib/contextWords";
import { toolWords } from "@/lib/toolWords";

/** The list's title: the exact words (VOCABULARY.md). */
export const FILLING_TITLE = "What is filling this chat";
const PRESS_TO_SEE = "Press to see what is filling it.";
/** The meter's name for a screen reader (it opens the list). `percent` is
 *  the daemon's own figure, not capped: a chat that holds more than its
 *  room is said in the list's own first words, never as "about 100% full". */
export function fillingMeterLabel(percent: number, windowKnown = true): string {
  return percent > 100
    ? `${overWords(windowKnown)} ${PRESS_TO_SEE}`
    : `This chat is about ${percent}% full. ${PRESS_TO_SEE}`;
}

// ── A chat that HOLDS MORE THAN ITS ROOM ───────────────────────────────────
// ONE SET OF WORDS with the chat's own answer (`chat_fullness`): the twins
// are context/filling.py's over_words, over_left_words, OVER_SHARES,
// ROOM_LEFT_NONE and OF_THE_ROOM, and the contract fixture holds both
// readers' words for the same record. The daemon decides (`reading.over`);
// no row then says a share of the room for a part (it would be above 100):
// each part is its share of everything the chat holds, which adds up to 100.

/** The first line, in place of a percent. */
export function overWords(windowKnown: boolean): string {
  return windowKnown
    ? "This chat holds more than this model can read at once."
    : "This chat holds more than Iron Jarvis sends this model at once. This model's own limit is not known, so a safe size is used.";
}
const OVER_LEFT = "Iron Jarvis leaves the oldest messages out of each reply so it fits";
/** Said once: the oldest messages are left out, with the daemon's count when
 *  it saved one ("at least": the fit made for each model call may leave out
 *  more). */
export function overLeftWords(n: number): string {
  return n > 0 ? `${OVER_LEFT} (at least ${n} in the newest reply).` : `${OVER_LEFT}.`;
}
export const OVER_SHARES = "Each share here is of everything this chat holds.";
/** "Room left" for a chat that holds more than its room. */
export const ROOM_LEFT_NONE = "none";
/** After a tools row's share there ("about 7% of the room"): the tools are
 *  sent beside the chat, so their share stays a share of the room. */
export const OF_THE_ROOM = " of the room";

/** The list's first line: the gauge's own sentence for the same reply, or
 *  the over-full words. */
export function fillingHeadline(f: ChatFilling): string {
  if (!f.reading) return "";
  return f.over ? overWords(f.windowKnown) : fillWords("chat", f.reading.percent, f.windowKnown);
}

/** The line under the list: what was left out, and that sizes are estimates. */
export function fillingNote(f: ChatFilling): string {
  return (f.over ? [overLeftWords(f.leftOut), OVER_SHARES, FILLING_ESTIMATE] : [leftOutWords(f.leftOut), FILLING_ESTIMATE])
    .filter(Boolean)
    .join(" ");
}
export const FILLING_CLOSE = "Close";
export const FILLING_LOADING = "Reading what is filling this chat…";
/** A chat with no record yet: an older chat, or one not saved yet. */
export const FILLING_NONE =
  "Iron Jarvis has no list for this chat yet. It keeps one with each reply, so it will be here after the next reply.";
export const FILLING_FAILED = "The list could not be read just now. Press the meter again in a moment.";
export const FILLING_ESTIMATE = "Every size here is an estimate.";
/** The fold that names Iron Jarvis's own tools, in the cards' words. */
export const OWN_TOOLS_LABEL = "Iron Jarvis's own tools";

export interface ChatFilling {
  found: boolean;
  reading: ContextReading | null;
  /** False: the room is the safe size Iron Jarvis uses, not the model's own limit. */
  windowKnown: boolean;
  /** Earlier messages the last reply left out so it fits, at least (0 = none). */
  leftOut: number;
  /** The chat holds more than its room (the daemon's decision,
   *  context/breakdown.over_room): shares are of what the chat holds. */
  over: boolean;
}

const rec = (v: unknown): Record<string, unknown> | null =>
  v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : null;

/** "read a file" -> "Read a file". */
function capital(words: string): string {
  return words ? words[0].toUpperCase() + words.slice(1) : words;
}

/** Iron Jarvis's own tools the reply was given, each in the approval card's
 *  words (lib/toolWords, the one table); a tool the table cannot word is
 *  left out of the names, never shown by its id (the count stays whole). */
function ownToolsList(names: unknown, count: number): ContextList | null {
  if (!Array.isArray(names) || count <= 0) return null;
  const seen = new Set<string>();
  const items: ContextList["items"] = [];
  for (const n of names) {
    if (typeof n !== "string" || !n || n.startsWith("mcp__")) continue;
    const words = toolWords(n);
    if (!words) continue;
    const said = capital(words);
    if (seen.has(said)) continue;
    seen.add(said);
    items.push({ name: said, detail: "", path: "", tokens: null });
  }
  return items.length ? { key: "own_tools", label: OWN_TOOLS_LABEL, count: Math.max(count, items.length), items } : null;
}

/** `GET /chat/threads/{id}/filling`. Null for an answer that is no object. */
export function decodeChatFilling(raw: unknown): ChatFilling | null {
  const r = rec(raw);
  if (!r) return null;
  const rd = rec(r.reading);
  const reading = r.found === true ? decodeBreakdown(rd) : null;
  if (reading && rd) {
    const tools = reading.lists.find((l) => l.key === "tools");
    const own = tools?.items.find((i) => i.name === OWN_TOOLS_LABEL);
    const ownCount = own ? Number.parseInt(own.detail, 10) || 0 : 0;
    const list = ownToolsList(rd.tool_names, ownCount);
    if (list) reading.lists.push(list);
  }
  const left = typeof r.left_out === "number" && Number.isFinite(r.left_out) && r.left_out > 0 ? Math.round(r.left_out) : 0;
  return {
    found: !!reading,
    reading,
    windowKnown: r.window_known !== false,
    leftOut: left,
    over: !!reading && rd?.over === true,
  };
}

/** "At least 3 earlier messages were left out of the last reply so it
 *  fits." (the daemon's count is the planner's: the fit made for each model
 *  call may leave out more, so it is never said as an exact count). */
export function leftOutWords(n: number): string {
  return n > 0
    ? `At least ${n} earlier message${n === 1 ? " was" : "s were"} left out of the last reply so it fits.`
    : "";
}

/** The route for one saved chat. */
export function fillingPath(threadId: string): string {
  return `/chat/threads/${encodeURIComponent(threadId)}/filling`;
}
