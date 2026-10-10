"use client";

/**
 * "@ another chat" (calm chat W3-1, v1.328.0).
 *
 * Typing "@" in the composer lists saved chats next to the agents. Picking a
 * chat puts a small chip in the composer card (never text in the message),
 * and the next turn sends the chips' ids as `thread_refs` on BOTH lanes; the
 * daemon reads a snapshot of each chat as reference material
 * (`daemon/chat_refs.py`) and the reply's receipt says "Read N earlier chats".
 *
 * The search is GET /chat/threads/search-refs?q=&exclude= : saved chats whose
 * title contains `q`, newest first, at most 8, archived ones and `exclude`
 * (the open chat) left out. Rows carry only id, title and updated_at (and,
 * from a daemon that sends it, project_id: v1.329.0).
 *
 * Its own module (not in useChatStream or the page) because tests mock
 * `@/lib/useChatStream` with a fixed export list.
 */

import { useEffect, useRef, useState } from "react";
import { get } from "@/lib/api";

/** At most this many chats go with one message (the daemon's THREAD_REFS_MAX). */
export const CHAT_REFS_MAX = 3;
/** What a pick past the cap says, quietly, next to the chips. */
export const CHAT_REFS_FULL_NOTE = `Up to ${CHAT_REFS_MAX} chats`;
/** Typing after "@" waits this long before asking again. */
export const CHAT_REF_SEARCH_DEBOUNCE_MS = 200;
/** The daemon answers at most 8; never draw more than that. */
const SEARCH_ROWS_MAX = 8;
/** The daemon's id bound (THREAD_REF_ID_CHARS); a longer one is not a chat id. */
const ID_CHARS = 200;

/** A saved chat picked (or offered) in the "@" menu. `updatedAt` is only for
 *  telling two chats with the same title apart (a quiet age on the row and
 *  the chip); a turn sends the ids alone ({@link chatRefIds}). */
export interface ChatRefPick {
  id: string;
  title: string;
  updatedAt?: string | null;
  /** v1.329.0: the project the chat belongs to, when the daemon says (a
   *  string id, or null for none). Only for the row's quiet second part
   *  (`lib/atMenuRows.chatRefSecondary`); never sent, never saved. */
  projectId?: string | null;
}

/** The search path for `q`, leaving out the open chat when there is one. */
export function chatRefSearchPath(q: string, exclude?: string | null): string {
  const parts = [`q=${encodeURIComponent(q)}`];
  if (exclude) parts.push(`exclude=${encodeURIComponent(exclude)}`);
  return `/chat/threads/search-refs?${parts.join("&")}`;
}

/** The response's rows, whitelisted: a string id (no blanks, no duplicates),
 *  a title ("Untitled chat" when it has none) and, when the daemon sent one
 *  as a string, when it last changed (`updatedAt`), at most 8. */
export function decodeChatRefRows(raw: unknown): ChatRefPick[] {
  const list = (raw as { threads?: unknown } | null)?.threads;
  if (!Array.isArray(list)) return [];
  const out: ChatRefPick[] = [];
  for (const r of list) {
    if (!r || typeof r !== "object") continue;
    const { id, title, updated_at, project_id } = r as {
      id?: unknown;
      title?: unknown;
      updated_at?: unknown;
      project_id?: unknown;
    };
    if (typeof id !== "string") continue;
    const tid = id.trim();
    if (!tid || tid.length > ID_CHARS || out.some((x) => x.id === tid)) continue;
    const t = typeof title === "string" ? title.trim() : "";
    const row: ChatRefPick = { id: tid, title: t && t !== "(untitled)" ? t : "Untitled chat" };
    if (typeof updated_at === "string" && updated_at && updated_at.length <= 64) {
      row.updatedAt = updated_at;
    }
    // v1.329.0: carried only when the daemon sends it (a short string, or
    // null for a chat in no project); an older daemon sends neither.
    if (project_id === null) row.projectId = null;
    else if (typeof project_id === "string" && project_id.trim() && project_id.length <= ID_CHARS) {
      row.projectId = project_id.trim();
    }
    out.push(row);
    if (out.length >= SEARCH_ROWS_MAX) break;
  }
  return out;
}

/**
 * Add `pick` to the chips. A chat already there is not added twice; a pick
 * past {@link CHAT_REFS_MAX} is refused (`full`) and the list is unchanged.
 */
export function addChatRef(
  cur: ChatRefPick[],
  pick: ChatRefPick,
): { next: ChatRefPick[]; full: boolean } {
  if (cur.some((x) => x.id === pick.id)) return { next: cur, full: false };
  if (cur.length >= CHAT_REFS_MAX) return { next: cur, full: true };
  return { next: [...cur, pick], full: false };
}

/** The ids a turn sends as `thread_refs` (capped, no duplicates, no blanks). */
export function chatRefIds(refs: ChatRefPick[] | undefined): string[] {
  const out: string[] = [];
  for (const r of refs ?? []) {
    if (r?.id && !out.includes(r.id)) out.push(r.id);
    if (out.length >= CHAT_REFS_MAX) break;
  }
  return out;
}

/**
 * The rows the menu draws: the search's answer minus the chats already picked
 * and the open chat (the daemon leaves it out too; this is the second lock),
 * narrowed by what is typed now so a row never lingers for a query it no
 * longer matches while the next answer is on its way.
 */
export function visibleChatRefRows(
  rows: ChatRefPick[] | null,
  picked: ChatRefPick[],
  openId: string | null | undefined,
  query: string,
): ChatRefPick[] {
  const q = query.trim().toLowerCase();
  return (rows ?? []).filter(
    (r) =>
      r.id !== openId &&
      !picked.some((p) => p.id === r.id) &&
      (!q || r.title.toLowerCase().includes(q)),
  );
}

/**
 * The saved chats for the "@" menu while it is open: `null` until the first
 * answer arrives. Each time the menu opens it asks at once; typing after "@"
 * asks again after {@link CHAT_REF_SEARCH_DEBOUNCE_MS}. An answer that arrives
 * after a newer question (or after the menu closed) is dropped. A failed
 * search shows no chats; the agents are still there.
 */
export function useChatRefSearch(
  open: boolean,
  query: string,
  exclude: string | null,
): ChatRefPick[] | null {
  const [rows, setRows] = useState<ChatRefPick[] | null>(null);
  const seqRef = useRef(0);
  const wasOpenRef = useRef(false);
  useEffect(() => {
    if (!open) {
      wasOpenRef.current = false;
      seqRef.current += 1; // an answer still on its way is for a closed menu
      setRows(null);
      return;
    }
    const opening = !wasOpenRef.current;
    wasOpenRef.current = true;
    const seq = ++seqRef.current;
    const timer = setTimeout(
      () => {
        get<unknown>(chatRefSearchPath(query, exclude))
          .then((d) => {
            if (seq === seqRef.current) setRows(decodeChatRefRows(d));
          })
          .catch(() => {
            if (seq === seqRef.current) setRows([]);
          });
      },
      opening ? 0 : CHAT_REF_SEARCH_DEBOUNCE_MS,
    );
    return () => clearTimeout(timer);
  }, [open, query, exclude]);
  return rows;
}
