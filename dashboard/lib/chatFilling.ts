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
import { toolWords } from "@/lib/toolWords";

/** The list's title: the exact words (VOCABULARY.md). */
export const FILLING_TITLE = "What is filling this chat";
/** The meter's name for a screen reader (it opens the list). */
export function fillingMeterLabel(percent: number): string {
  return `This chat is about ${percent}% full. Press to see what is filling it.`;
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
  return { found: !!reading, reading, windowKnown: r.window_known !== false, leftOut: left };
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
