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
// THREAD_POLL_MS (lib/threadListPoll), and a `chat.thread_updated` event
// re-reads it at once.
//
// Kept out of lib/api.ts on purpose (tests mock that module wholesale).

import { useCallback, useEffect, useMemo, useSyncExternalStore } from "react";

import { get } from "@/lib/api";
import { cacheSet, cachedGet } from "@/lib/apiCache";
import { THREAD_POLL_MS, useThreadListPoll } from "@/lib/threadListPoll";
import { readLastViewed, threadStatuses, type ThreadStatus, type ThreadSummary } from "@/lib/threadStatus";
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
    state = { ...state, threads: t.threads ?? [] };
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
 * failure keeps what is shown (an empty list on the very first failure, so
 * the sidebar can say so).
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
      set({ threads: d?.threads ?? [] });
    } catch {
      if (state.threads === null) set({ threads: [] });
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
  adoptedThreads = null;
  adoptedProjects = null;
  threadsFlight = null;
  projectsFlight = null;
  threadsReadAt = 0;
  projectsReadAt = 0;
  threadsAgain = false;
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
  useEffect(() => {
    if (events.length > 0) void refreshChatList();
  }, [events.length]);

  const refresh = useCallback(() => void refreshChatList(THREAD_POLL_MS - 500), []);
  useThreadListPoll(snap.threads ?? [], refresh);

  const statuses = useMemo(
    () => (snap.threads ? threadStatuses(snap.threads, readLastViewed(), null) : {}),
    [snap.threads],
  );
  return { ...snap, statuses, refresh: () => void refreshChatList() };
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
