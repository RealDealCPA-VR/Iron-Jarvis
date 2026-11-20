/**
 * Scroll back in a chat (48-a, idea 48 "Scroll back forever").
 *
 * A long chat keeps its newest 200 messages LIVE (what the page loads, shows
 * and sends back on every save) and moves the rest to its older messages,
 * never deleting them (v1.499.3, `daemon/chat_append.py`). This is the page's
 * side of reading them back: when the reader reaches the top of a long chat,
 * the 50 before load in place, a page at a time, back to the first message.
 *
 * The rows held here are NEVER part of the page's `messages`. The save sends
 * `messages` whole and the daemon works out what leaves the live window from
 * it; an older row in there would be saved again as a live message. So the
 * held rows are a separate state, drawn above the live rows, read-only.
 *
 * Positions: 0 is the chat's very first message. `total` is where the page's
 * live messages start (the daemon's `older` when the page last took its live
 * list from the daemon). The rows held cover `[loadedFrom, heldTo)`, oldest
 * first; `heldTo` is `total` except for the moment after the page adopted a
 * newer copy whose count grew, until the rows in between are fetched (so no
 * gap opens between the held rows and the live ones).
 *
 * Pure, so the rules are tested on their own.
 */

/** How many earlier messages one press (or one reach of the top) loads. */
export const OLDER_PAGE_SIZE = 50;

/** The most rows one read answers (the daemon's own cap). */
export const OLDER_READ_MAX = 1000;

/** The words on screen (VOCABULARY.md, "Earlier messages"). */
export const OLDER_WORDS = {
  more: "Earlier messages",
  failed: "Could not load earlier messages.",
  retry: "Try again",
  start: "Start of this chat",
} as const;

export interface OlderRow<M> {
  /** The message's position in the chat (0 = its first message). */
  seq: number;
  message: M;
}

export interface OlderState<M> {
  /** The chat these rows belong to ("" = none). */
  threadId: string;
  /** Where the page's live messages start: how many older messages sit
   *  before them. */
  total: number;
  /** The position of the first row held (`total` when none is held). */
  loadedFrom: number;
  /** The position just after the last row held. */
  heldTo: number;
  /** The rows held, oldest first. */
  rows: OlderRow<M>[];
  /** An earlier page is on its way. */
  loading: boolean;
  /** The last earlier page could not be read (the line offers Try again). */
  failed: boolean;
}

/** A count from the daemon, or null when it is not one (an older daemon
 *  answers no `older`). */
export function olderCount(raw: unknown): number | null {
  return typeof raw === "number" && Number.isFinite(raw) && raw >= 0 ? Math.floor(raw) : null;
}

/** The state for a chat whose live messages start at `total`, nothing held. */
export function olderStart<M>(threadId: string, total: unknown): OlderState<M> {
  const n = olderCount(total) ?? 0;
  return {
    threadId: threadId || "",
    total: n,
    loadedFrom: n,
    heldTo: n,
    rows: [],
    loading: false,
    failed: false,
  };
}

/** There are earlier messages still to load above the rows held. */
export function hasEarlier<M>(s: OlderState<M>): boolean {
  return Boolean(s.threadId) && s.total > 0 && s.loadedFrom > 0;
}

/** Every earlier message is held: the top of the chat is its first message. */
export function atStartOfChat<M>(s: OlderState<M>): boolean {
  return Boolean(s.threadId) && s.total > 0 && s.loadedFrom <= 0;
}

/** The read for the `limit` messages just before position `before`. */
export function olderPagePath(threadId: string, before: number, limit: number = OLDER_PAGE_SIZE): string {
  const n = Math.max(1, Math.min(Math.floor(limit), OLDER_READ_MAX));
  return `/chat/threads/${encodeURIComponent(threadId)}/older?before=${Math.max(0, Math.floor(before))}&limit=${n}`;
}

export interface OlderPage<M> {
  /** Where the answer starts. */
  firstSeq: number;
  /** How many older messages the chat holds now. */
  total: number;
  messages: M[];
}

/** The daemon's answer to `GET /chat/threads/{id}/older?before=`, or null
 *  when it is not one (no `first_seq`: a daemon that does not know `before`
 *  answered from the first message, which is not the page asked for). Only
 *  objects with a role are kept as messages. */
export function decodeOlderPage<M>(raw: unknown): OlderPage<M> | null {
  if (!raw || typeof raw !== "object") return null;
  const r = raw as { first_seq?: unknown; total?: unknown; messages?: unknown };
  const firstSeq = olderCount(r.first_seq);
  if (firstSeq === null || !Array.isArray(r.messages)) return null;
  const messages = r.messages.filter(
    (m): m is M =>
      !!m && typeof m === "object" && typeof (m as { role?: unknown }).role === "string",
  );
  return { firstSeq, total: olderCount(r.total) ?? 0, messages };
}

function rowsOf<M>(page: OlderPage<M>): OlderRow<M>[] {
  return page.messages.map((message, i) => ({ seq: page.firstSeq + i, message }));
}

/** Mark an earlier page as on its way (or asked again after a failure). */
export function olderLoading<M>(s: OlderState<M>): OlderState<M> {
  return { ...s, loading: true, failed: false };
}

/** An earlier page could not be read: the line says so and offers Try again. */
export function olderFailed<M>(s: OlderState<M>): OlderState<M> {
  return { ...s, loading: false, failed: true };
}

/**
 * Put an earlier page, asked for `before` a position, above the rows held.
 * A page for a position the state no longer starts at (the chat changed
 * meanwhile) is dropped; so is one that does not start before it (nothing
 * would be gained, and asking again would ask the same).
 */
export function withEarlierPage<M>(s: OlderState<M>, before: number, page: OlderPage<M>): OlderState<M> {
  if (s.loadedFrom !== before) return { ...s, loading: false };
  if (page.firstSeq >= before) return olderFailed(s);
  return {
    ...s,
    rows: [...rowsOf(page), ...s.rows],
    loadedFrom: page.firstSeq,
    loading: false,
    failed: false,
  };
}

/**
 * The page took a newer live list from the daemon whose count is `total`.
 * Same count: nothing changes. A smaller count (the chat was replaced, or
 * the state was another chat's): start over at it. A larger one: the live
 * list now starts later, and the messages in between (which the page was
 * showing as live ones a moment ago) still have to be fetched; `heldTo`
 * stays where the held rows end until they are (see `withRowsBetween`).
 */
export function withTotal<M>(s: OlderState<M>, threadId: string, total: unknown): OlderState<M> {
  const n = olderCount(total);
  if (n === null || !threadId) return s;
  if (s.threadId !== threadId || n < s.total) return olderStart(threadId, n);
  if (n === s.total) return s;
  return { ...s, total: n };
}

/** The rows still to fetch between the held rows and the live ones, or
 *  null when there is no gap. */
export function gapToFill<M>(s: OlderState<M>): { from: number; to: number } | null {
  if (!s.threadId || s.heldTo >= s.total) return null;
  return { from: s.heldTo, to: s.total };
}

/**
 * The rows in between arrived, asked for `[from, to)`. When they join the
 * held rows exactly, they go on the end; otherwise (the read was capped, or
 * the held rows changed meanwhile) what was read becomes the whole held
 * block, so the held rows are always one run that ends at the live ones.
 * A failed read (null) drops the held rows: nothing is held, and the
 * "Earlier messages" line covers everything above the live messages again.
 */
export function withRowsBetween<M>(
  s: OlderState<M>,
  from: number,
  to: number,
  page: OlderPage<M> | null,
): OlderState<M> {
  if (to > s.total) return s; // asked of a state that is gone (the chat started over)
  if (!page) return { ...s, rows: [], loadedFrom: s.total, heldTo: s.total };
  const fresh = rowsOf(page);
  if (page.firstSeq === from && s.heldTo === from) {
    return { ...s, rows: [...s.rows, ...fresh], heldTo: to };
  }
  return { ...s, rows: fresh, loadedFrom: page.firstSeq, heldTo: to };
}

/** Which local day a message was written on, to tell where the day changes
 *  (the date line says it with `lib/atMenuRows.chatDay`, the chat list's
 *  words); "" without a usable time. */
export function dayKey(at: unknown): string {
  if (typeof at !== "string" || !at) return "";
  const t = Date.parse(at);
  if (!Number.isFinite(t)) return "";
  const d = new Date(t);
  return `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
}
