"use client";

// ONE chat list for the sidebar, on every page (v1.329.0, calm chat W4 F2).
//
// Away from the chat surface the sidebar (and the phone nav drawer) used to
// show its own flat "Today / Previous 7 days" list: no projects, no status
// dots, no ages, so a chat that was running or waiting on the user was
// invisible anywhere but /chat. It now draws the same ThreadGroups list the
// chat page does, fed from here.
//
// One store per window, so the persistent rail (mounted even on a phone,
// hidden by CSS) and the drawer never fetch twice: a read is single-flight
// and a poll tick that lands right after another read is skipped. The answer
// is written to lib/apiCache under the SAME path the chat page reads for its
// unscoped list ("/chat/threads"), and the store starts from that entry, so
// going from Chat to another page (or back) paints the list at once and
// revalidates behind it. On the chat surface the sidebar holds the page's own
// rail instead (lib/sidebarSlot), so this store is not read there at all.
//
// Dots: running and waiting come from the daemon's list rows, unread from
// this browser's last-viewed stamps (lib/threadStatus) — the same rule the
// chat page uses. While a row is running or waiting the list is re-read every
// THREAD_POLL_MS (lib/threadListPoll), and every `chat.thread_updated` event
// re-reads it at once (keyed on the newest frame's id since v1.333.0).
//
// Kept out of lib/api.ts on purpose (tests mock that module wholesale).

import { useCallback, useEffect, useMemo, useState, useSyncExternalStore } from "react";

import { get } from "@/lib/api";
import { cacheSet, cachedGet } from "@/lib/apiCache";
import { failureOf, type FailureLike } from "@/lib/loadFailed";
import { THREAD_POLL_MS, useThreadListPoll } from "@/lib/threadListPoll";
import {
  readLastViewed,
  threadStatuses,
  threadTime,
  type ThreadStatus,
  type ThreadSummary,
} from "@/lib/threadStatus";
import { useEvents } from "@/lib/useEvents";

/** The unscoped list: the chat page's own path for it (its apiCache key). */
export const CHAT_LIST_PATH = "/chat/threads";
export const PROJECTS_PATH = "/projects";
/** Where the chat page keeps the "Pin to top" ids (per device). */
export const CHAT_PINNED_KEY = "ij_chat_pinned";

/** A list row as the sidebar draws it: a messaging chat names its channel. */
export interface ChatListThread extends ThreadSummary {
  owner?: string;
  comm_channel?: string;
}

export interface ChatListProject {
  id: string;
  name: string;
  status?: string;
}

export interface ChatListState {
  /** null until the first answer (or cached entry). */
  threads: ChatListThread[] | null;
  /** Every project the daemon lists (archived included: their chats still
   *  group under their name, as on the chat page). */
  projects: ChatListProject[] | null;
}

let state: ChatListState = { threads: null, projects: null };
// v1.446.0: the older pages "Show older chats" read (see below).
let pages: OlderPages<ChatListThread> = { older: [], next: undefined, firstLen: 0 };
let olderFlight: Promise<void> | null = null;
let olderFailed = false;
// v1.449.0 (Wave B, item 16): the failure behind the LAST list read, kept
// until a read succeeds. The sidebar shows it only while it has nothing to
// show (a failed FIRST read used to leave `threads: []`, which read as "Your
// chats appear here." — a user with chats took that for "my chats are gone").
let loadFailed: FailureLike | null = null;
const subs = new Set<() => void>();
let threadsFlight: Promise<void> | null = null;
let projectsFlight: Promise<void> | null = null;
let threadsReadAt = 0;
let projectsReadAt = 0;
/** A forced read was asked for while one was on its way. */
let threadsAgain = false;
// The apiCache payloads this store last took its lists from. The chat page
// writes the same "/chat/threads" entry after every save; a NEWER payload
// there (another object) is taken over on the next read of the store.
let adoptedThreads: unknown = null;
let adoptedProjects: unknown = null;

function adoptCache(): void {
  const t = cachedGet<{ threads?: ChatListThread[] }>(CHAT_LIST_PATH);
  const p = cachedGet<{ projects?: ChatListProject[] }>(PROJECTS_PATH);
  if (t && t !== adoptedThreads) {
    adoptedThreads = t;
    // v1.446.0: the cache holds page 1 only; older pages read are kept.
    const merged = withFreshFirstPage(t.threads ?? [], state.threads ?? [], pages);
    pages = merged.pages;
    state = { ...state, threads: merged.threads };
  }
  if (p && p !== adoptedProjects) {
    adoptedProjects = p;
    state = { ...state, projects: p.projects ?? [] };
  }
}

function set(next: Partial<ChatListState>): void {
  state = { ...state, ...next };
  for (const f of subs) f();
}

/**
 * Re-read the chat list. Single-flight: a read already on its way is joined,
 * and a FORCED read asked for while one is on its way (a thread update that
 * may have landed after that read was answered) runs once more after it, so
 * any number of updates during one read cost one more read, never one each.
 * `maxAgeMs` > 0 skips the read when the last one STARTED that recently (a
 * second copy of the list mounting, a second subscriber's poll tick). A
 * failure keeps what is shown (an empty list on the very first failure, and
 * `loadFailed` holds why, so the sidebar can say so — v1.449.0: in words,
 * "Your chats couldn't load. Try again.", never the empty-list words).
 */
export function refreshChatList(maxAgeMs = 0): Promise<void> {
  if (threadsFlight) {
    if (maxAgeMs === 0) threadsAgain = true;
    return threadsFlight;
  }
  if (maxAgeMs > 0 && Date.now() - threadsReadAt < maxAgeMs) return Promise.resolve();
  threadsReadAt = Date.now();
  threadsFlight = (async () => {
    try {
      const d = await get<{ threads?: ChatListThread[] }>(CHAT_LIST_PATH);
      cacheSet(CHAT_LIST_PATH, d);
      adoptedThreads = d;
      // v1.446.0: page 1 only; older pages already read are kept.
      const merged = withFreshFirstPage(d?.threads ?? [], state.threads ?? [], pages);
      pages = merged.pages;
      loadFailed = null;
      set({ threads: merged.threads });
    } catch (e) {
      loadFailed = failureOf(e);
      // `set` always: the flag is outside `state`, so a read that failed
      // again must still redraw the line it keeps up.
      set(state.threads === null ? { threads: [] } : {});
    } finally {
      threadsFlight = null;
    }
    if (threadsAgain) {
      threadsAgain = false;
      await refreshChatList();
    }
  })();
  return threadsFlight;
}

/** Re-read the project list (single-flight, like the chats; `maxAgeMs` as
 *  there). */
export function refreshChatProjects(maxAgeMs = 0): Promise<void> {
  if (projectsFlight) return projectsFlight;
  if (maxAgeMs > 0 && Date.now() - projectsReadAt < maxAgeMs) return Promise.resolve();
  projectsReadAt = Date.now();
  projectsFlight = (async () => {
    try {
      const d = await get<{ projects?: ChatListProject[] }>(PROJECTS_PATH);
      cacheSet(PROJECTS_PATH, d);
      adoptedProjects = d;
      set({ projects: d?.projects ?? [] });
    } catch {
      if (state.projects === null) set({ projects: [] });
    } finally {
      projectsFlight = null;
    }
  })();
  return projectsFlight;
}

function subscribe(f: () => void): () => void {
  subs.add(f);
  return () => {
    subs.delete(f);
  };
}

function snapshot(): ChatListState {
  adoptCache();
  return state;
}

const SERVER_STATE: ChatListState = { threads: null, projects: null };

/** The "Pin to top" ids this device keeps (the chat page writes them). */
export function readPinnedChats(): string[] {
  try {
    const raw = window.localStorage.getItem(CHAT_PINNED_KEY);
    const arr = raw ? (JSON.parse(raw) as unknown) : [];
    return Array.isArray(arr) ? arr.filter((x): x is string => typeof x === "string") : [];
  } catch {
    return [];
  }
}

/** Test seam: forget everything (each suite starts clean). */
export function __resetChatList(): void {
  state = { threads: null, projects: null };
  pages = { older: [], next: undefined, firstLen: 0 };
  olderFlight = null;
  olderFailed = false;
  loadFailed = null;
  adoptedThreads = null;
  adoptedProjects = null;
  threadsFlight = null;
  projectsFlight = null;
  threadsReadAt = 0;
  projectsReadAt = 0;
  threadsAgain = false;
}

/** v1.446.0: read the next older page of the sidebar's list (single-flight).
 *  A failure is said once under the list; pressing again retries. */
export function loadOlderChats(): Promise<void> {
  if (olderFlight) return olderFlight;
  const cursor = olderCursor(state.threads ?? [], pages);
  if (!cursor) return Promise.resolve();
  olderFlight = (async () => {
    try {
      const page = await fetchOlderThreads<ChatListThread>(CHAT_LIST_PATH, cursor);
      const merged = withOlderPage(state.threads ?? [], pages, page);
      pages = merged.pages;
      olderFailed = false;
      set({ threads: merged.threads });
    } catch {
      olderFailed = true;
      set({});
    } finally {
      olderFlight = null;
    }
  })();
  return olderFlight;
}

/** A list read this recently is not read again when another copy of the list
 *  mounts (the phone drawer opening over the rail that just read it). */
export const FRESH_MS = 2000;

// Module-level so the subscription's key is stable.
const THREAD_EVENTS = { types: ["chat.thread_updated"] };

export interface ChatList extends ChatListState {
  /** Status per thread id (running / waiting / unread / idle). */
  statuses: Record<string, ThreadStatus>;
  refresh: () => void;
  /** v1.446.0: the list may hold older chats ("Show older chats"). */
  mayShowOlder: boolean;
  /** The last older page could not be read. */
  olderFailed: boolean;
  loadOlder: () => Promise<void>;
  /** v1.449.0 (Wave B, item 16): why the last list read failed, or null
   *  once one succeeded. The sidebar says it only while the list is empty. */
  loadFailed: FailureLike | null;
}

/** The sidebar's chat list: read on mount, re-read on a thread update and
 *  every few seconds while a chat is running or waiting. */
export function useChatList(): ChatList {
  const snap = useSyncExternalStore(subscribe, snapshot, () => SERVER_STATE);
  const { events } = useEvents(20, THREAD_EVENTS);

  useEffect(() => {
    void refreshChatList(FRESH_MS);
    void refreshChatProjects(FRESH_MS);
  }, []);
  // A thread changed somewhere (a reply landed, a phone message, a rename):
  // read the list again. The first render has no events; skip it.
  // v1.333.0: keyed on the NEWEST frame's id, never on the window's length —
  // useEvents keeps at most 20 frames here, so from the 21st update on the
  // length stayed 20 and the sidebar list was never re-read again.
  const newestId = events[0]?.id ?? null;
  useEffect(() => {
    if (newestId !== null) void refreshChatList();
  }, [newestId]);

  const refresh = useCallback(() => void refreshChatList(THREAD_POLL_MS - 500), []);
  useThreadListPoll(snap.threads ?? [], refresh);

  const statuses = useMemo(
    () => (snap.threads ? threadStatuses(snap.threads, readLastViewed(), null) : {}),
    [snap.threads],
  );
  return {
    ...snap,
    statuses,
    refresh: () => void refreshChatList(),
    mayShowOlder: mayShowOlder(pages),
    olderFailed,
    loadOlder: loadOlderChats,
    loadFailed,
  };
}

/** The project list alone (the sidebar's Projects section). It reads no
 *  chats: on the chat surface the page's own rail holds the chats, and a
 *  second /chat/threads read (and poll) there would be the double fetch this
 *  module exists to avoid. */
export function useChatProjects(): ChatListProject[] | null {
  const snap = useSyncExternalStore(subscribe, snapshot, () => SERVER_STATE);
  useEffect(() => {
    void refreshChatProjects(FRESH_MS);
  }, []);
  return snap.projects;
}

// ---------------------------------------------------------------------------
// v1.446.0 (ease-of-use review item 10): OLDER CHATS and a REAL search.
//
// GET /chat/threads answers the newest CHAT_LIST_PAGE chats. A list holding
// that many may have more: "Show older chats" reads the next page with a
// cursor (the daemon's paged form, which answers `next`) and APPENDS it. The
// FIRST older page after the plain list uses a bare-stamp cursor (every row
// at or before the last row's instant): the plain list's tie order is not a
// contract, so the rows tied at its edge come again and are dropped here by
// id. Later pages follow the daemon's own `next`.
//
// The 4 s poll and every refresh still read page 1 only. When older pages
// are loaded, a fresh page 1 KEEPS them (`withFreshFirstPage`): every row the
// list already showed that is older than (or tied with) the fresh page's
// last row and is not in it stays — so a chat pushed from the 100th place to
// the 101st by a new chat is not lost. A row NEWER than that edge that the
// fresh page no longer holds was deleted, archived or moved, and goes.
//
// "Search chats" asks GET /chat/threads/search (titles AND the words said in
// every chat) from CHAT_SEARCH_MIN_CHARS characters on, CHAT_SEARCH_DEBOUNCE_MS
// after the last keystroke, cancelling the request before it. Until it
// answers (and when it cannot be reached) the caller keeps filtering the
// titles it already holds, so typing still narrows the list at once.

/** Rows the daemon answers per list page (the plain list's own size). */
export const CHAT_LIST_PAGE = 100;
/** The search box asks the daemon only from this many characters on. */
export const CHAT_SEARCH_MIN_CHARS = 2;
/** Quiet time after the last keystroke before the daemon is asked. */
export const CHAT_SEARCH_DEBOUNCE_MS = 250;
/** Said under the box when the search could not be reached. */
export const CHAT_SEARCH_FALLBACK_WORDS =
  "Searching chat names only: the search inside chats couldn't be reached.";
/** The button at the end of a list that may hold more. */
export const SHOW_OLDER_WORDS = "Show older chats";
/** Said when an older page could not be read. */
export const SHOW_OLDER_FAILED_WORDS = "Older chats couldn't be loaded. Try again.";

/** The older pages a list has read, beside its page 1. `next`: undefined =
 *  none read yet; a string = the cursor for the next one; null = none left. */
export interface OlderPages<T extends ThreadSummary = ThreadSummary> {
  older: T[];
  next: string | null | undefined;
  /** How many rows the last page 1 held. */
  firstLen: number;
}

export function noOlderPages<T extends ThreadSummary>(firstLen = 0): OlderPages<T> {
  return { older: [], next: undefined, firstLen };
}

function rowTime(t: ThreadSummary): number {
  return threadTime(t.updated_at) ?? -Infinity;
}

/** A fresh page 1 landed: what the list shows now, and its older pages. */
export function withFreshFirstPage<T extends ThreadSummary>(
  fresh: readonly T[],
  shown: readonly T[],
  pages: OlderPages<T>,
): { threads: T[]; pages: OlderPages<T> } {
  const first = [...fresh];
  // Nothing older was read, or page 1 now holds the whole list.
  if (pages.next === undefined || first.length < CHAT_LIST_PAGE) {
    return { threads: first, pages: noOlderPages<T>(first.length) };
  }
  const edge = first.length ? rowTime(first[first.length - 1]) : Infinity;
  const have = new Set(first.map((t) => t.id));
  const older: T[] = [];
  // Only what the list SHOWS: a chat deleted or archived from it since its
  // older page was read must not come back with the next poll.
  for (const t of shown) {
    if (!t || have.has(t.id) || rowTime(t) > edge) continue;
    have.add(t.id);
    older.push(t);
  }
  older.sort((a, b) => rowTime(b) - rowTime(a));
  return { threads: [...first, ...older], pages: { ...pages, older, firstLen: first.length } };
}

/** True when the list may hold older chats it has not read. */
export function mayShowOlder(pages: OlderPages): boolean {
  if (pages.next === undefined) return pages.firstLen >= CHAT_LIST_PAGE;
  return pages.next !== null;
}

/** The cursor for the next older page, or null when there is none. */
export function olderCursor<T extends ThreadSummary>(
  shown: readonly T[],
  pages: OlderPages<T>,
): string | null {
  if (!mayShowOlder(pages)) return null;
  if (typeof pages.next === "string") return pages.next;
  const last = shown[shown.length - 1];
  return last?.updated_at ? last.updated_at : null;
}

/** `base` (a list path, maybe with a query) asking for the page after `cursor`. */
export function olderThreadsPath(base: string, cursor: string): string {
  const sep = base.includes("?") ? "&" : "?";
  return `${base}${sep}limit=${CHAT_LIST_PAGE}&before=${encodeURIComponent(cursor)}`;
}

/** An older page landed: appended, rows already shown dropped by id. */
export function withOlderPage<T extends ThreadSummary>(
  shown: readonly T[],
  pages: OlderPages<T>,
  page: { threads?: T[]; next?: string | null },
): { threads: T[]; pages: OlderPages<T> } {
  const have = new Set(shown.map((t) => t.id));
  const fresh = (Array.isArray(page.threads) ? page.threads : []).filter((t) => {
    if (!t || typeof t.id !== "string" || have.has(t.id)) return false;
    have.add(t.id);
    return true;
  });
  const next = typeof page.next === "string" && page.next ? page.next : null;
  return {
    threads: [...shown, ...fresh],
    pages: { ...pages, older: [...pages.older, ...fresh], next },
  };
}

/** Read the page after `cursor` of the list at `base`. Throws when the answer
 *  is not a list page (an older daemon answers the plain list: no `next`). */
export async function fetchOlderThreads<T extends ThreadSummary>(
  base: string,
  cursor: string,
): Promise<{ threads: T[]; next: string | null }> {
  const d = await get<{ threads?: T[]; next?: string | null }>(olderThreadsPath(base, cursor));
  if (!d || !Array.isArray(d.threads) || !("next" in d)) throw new Error("not a list page");
  return { threads: d.threads, next: typeof d.next === "string" ? d.next : null };
}

/** One search result: a list row, plus where it matched. */
export interface ChatSearchRow extends ChatListThread {
  match?: "title" | "text";
  /** The matched words (masked by the daemon), `[marked]`. */
  snippet?: string;
}

export function chatSearchPath(q: string, archived = false): string {
  return `/chat/threads/search?q=${encodeURIComponent(q)}${archived ? "&archived=only" : ""}`;
}

export interface ChatSearch {
  /** The box holds enough to ask the daemon. */
  active: boolean;
  /** The daemon's answer for the box as it is now, or null (not answered yet,
   *  or it could not be reached: filter the loaded titles instead). */
  results: ChatSearchRow[] | null;
  /** The last search could not be reached (say so, quietly). */
  failed: boolean;
}

/** Ask the daemon for `query`, debounced, the previous request cancelled. */
export function useChatSearch(query: string, archived = false): ChatSearch {
  const q = query.trim();
  const active = q.length >= CHAT_SEARCH_MIN_CHARS;
  const key = `${archived ? "a" : "c"}:${q}`;
  const [answer, setAnswer] = useState<{ key: string; rows: ChatSearchRow[] | null; failed: boolean }>({
    key: "",
    rows: null,
    failed: false,
  });
  useEffect(() => {
    if (!active) return;
    const controller = new AbortController();
    let live = true;
    const timer = setTimeout(() => {
      void (async () => {
        try {
          // `get` hands its options to fetch: the signal cancels the request.
          const opts = { signal: controller.signal } as unknown as { timeoutMs?: number };
          const d = await get<{ threads?: ChatSearchRow[] }>(chatSearchPath(q, archived), opts);
          if (!live) return;
          if (!d || !Array.isArray(d.threads)) throw new Error("not a search answer");
          const rows = d.threads.filter((t) => t && typeof t.id === "string");
          setAnswer({ key, rows, failed: false });
        } catch {
          if (!live || controller.signal.aborted) return;
          setAnswer({ key, rows: null, failed: true });
        }
      })();
    }, CHAT_SEARCH_DEBOUNCE_MS);
    return () => {
      live = false;
      clearTimeout(timer);
      controller.abort();
    };
  }, [active, q, archived, key]);
  if (!active) return { active: false, results: null, failed: false };
  if (answer.key !== key) return { active: true, results: null, failed: false };
  return { active: true, results: answer.rows, failed: answer.failed };
}

/** A snippet's `[marked]` words as parts to draw (marked = the match). */
export function snippetParts(snippet: string): { text: string; marked: boolean }[] {
  const out: { text: string; marked: boolean }[] = [];
  const rx = /\[([^[\]\n]{1,80})\]/g;
  let at = 0;
  for (const m of snippet.matchAll(rx)) {
    const i = m.index ?? 0;
    if (i > at) out.push({ text: snippet.slice(at, i), marked: false });
    out.push({ text: m[1], marked: true });
    at = i + m[0].length;
  }
  if (at < snippet.length) out.push({ text: snippet.slice(at), marked: false });
  return out;
}

/** The titles a box filters while the daemon's search is not answering. */
export function filterTitles<T extends ThreadSummary>(threads: readonly T[], query: string): T[] {
  const q = query.trim().toLowerCase();
  return q ? threads.filter((t) => (t.title || "").toLowerCase().includes(q)) : [...threads];
}
