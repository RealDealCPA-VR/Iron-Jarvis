"use client";

// The sidebar's chat list away from the chat surface (v1.329.0, calm chat W4
// F2). The SAME list the chat page draws (ThreadGroups): chats grouped under
// their projects, Pinned first, "No project" last, each row a status dot
// (working on it, waiting on you, new since you last looked), the title and a
// short age; five per group, then "Show more". Pressing a row opens that chat
// on the chat page (/chat?thread=<id>), where the full list (search, the row
// options, the project scope and the archive) takes this space over.
//
// v1.329.0 (calm chat W5 G3): the same quiet "Search chats…" box as the chat
// page's list, filtering in place (titles, as there; a search shows every
// match). In place rather than a link to /chat: the list keeps one shape on
// every page, a search does not leave the page the user is on, and it works
// the same in the phone drawer. The row ⋯ stays on the chat page: rename,
// archive (which asks first when work is running), delete, memory, workflow
// and move are the chat page's own actions on its own state, and a second
// copy here would drift from it; a row is one press from them.
//
// Data: lib/chatList (one store per window; the rail and the phone drawer
// share it). Theme tokens only, ghosts that fill on hover (ThreadGroups').
//
// v1.446.0 (review item 10): the box first narrows the chats already listed
// (at once, as before), then — from two characters, a quarter second after
// the last key — asks the daemon to search EVERY chat's name and the words
// said in it, and shows that answer (with the matched words) in the list's
// place. When that search cannot be reached the title filter stays and one
// quiet line says so. A list of 100 chats ends in "Show older chats".

import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { Search } from "lucide-react";

import ThreadGroups, { GROUP_LIMIT, ShowOlderChats, ThreadSnippet } from "@/components/chat/ThreadGroups";
import {
  CHAT_SEARCH_FALLBACK_WORDS,
  filterTitles,
  readPinnedChats,
  useChatList,
  useChatSearch,
  type ChatSearchRow,
} from "@/lib/chatList";
import { readLastViewed, threadStatuses } from "@/lib/threadStatus";

export default function SidebarChats({ onNavigate }: { onNavigate?: () => void }) {
  const router = useRouter();
  const { threads, projects, statuses, mayShowOlder, olderFailed, loadOlder } = useChatList();
  const [olderBusy, setOlderBusy] = useState(false);
  const [pinned, setPinned] = useState<string[]>([]);
  const [query, setQuery] = useState("");
  useEffect(() => {
    setPinned(readPinnedChats());
  }, [threads]);
  const q = query.trim().toLowerCase();
  const search = useChatSearch(query);
  const shown = useMemo<ChatSearchRow[] | null>(
    () => (search.results ? search.results : threads && q ? filterTitles(threads, q) : threads),
    [threads, q, search.results],
  );
  // A result the list does not hold (an older chat) still gets its dot.
  const shownStatuses = useMemo(
    () => (search.results ? { ...threadStatuses(search.results, readLastViewed(), null), ...statuses } : statuses),
    [search.results, statuses],
  );

  if (threads === null) return null;
  if (threads.length === 0) {
    return (
      <p data-testid="sidebar-recent-chats" className="px-2.5 py-1 text-[12px] text-zinc-500">
        Your chats appear here.
      </p>
    );
  }
  return (
    <div data-testid="sidebar-recent-chats" className="min-w-0">
      {/* `isolate` + `z-[1]` (the v1.313.0 rule): the icon paints OVER the
          field, not under its fill. */}
      <div className="relative isolate mx-1 mb-1.5 mt-0.5">
        <Search
          size={12}
          aria-hidden="true"
          className="pointer-events-none absolute left-2.5 top-1/2 z-[1] -translate-y-1/2 text-zinc-500"
        />
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => {
            // Escape clears the box first; the drawer around it closes only
            // when there is nothing left to clear.
            if (e.key === "Escape" && query) {
              e.stopPropagation();
              setQuery("");
            }
          }}
          placeholder="Search chats…"
          aria-label="Search chats"
          data-testid="sidebar-chat-search"
          className="field w-full py-1.5 pl-8 text-[12px]"
        />
      </div>
      {search.failed ? (
        <p role="status" data-testid="sidebar-chat-search-fallback" className="px-2.5 pb-1 text-[12px] leading-relaxed text-zinc-500">
          {CHAT_SEARCH_FALLBACK_WORDS}
        </p>
      ) : null}
      {shown && shown.length === 0 ? (
        <p className="px-2.5 py-2 text-[12px] leading-relaxed text-zinc-500">
          No chats match “{query.trim()}”.
        </p>
      ) : (
        <ThreadGroups<ChatSearchRow>
          threads={shown ?? []}
          projects={projects ?? []}
          statuses={shownStatuses}
          pinnedIds={pinned}
          limit={q ? Infinity : GROUP_LIMIT}
          onOpen={(id) => {
            onNavigate?.();
            router.push(`/chat?thread=${encodeURIComponent(id)}`);
          }}
          rowBadge={(t) =>
            // A messaging chat names where it comes from, as on the chat page.
            t.owner === "daemon" ? (
              <span className="shrink-0 text-[11px] text-accent-soft/80" title="Messaging thread">
                {(t.comm_channel || "linked").replace(/^./, (c) => c.toUpperCase())}
              </span>
            ) : null
          }
          rowDetail={(t) => (search.results && t.snippet ? <ThreadSnippet snippet={t.snippet} /> : null)}
        />
      )}
      {!q && mayShowOlder ? (
        <ShowOlderChats
          testId="sidebar-show-older"
          busy={olderBusy}
          failed={olderFailed}
          onPress={() => {
            setOlderBusy(true);
            void loadOlder().finally(() => setOlderBusy(false));
          }}
        />
      ) : null}
    </div>
  );
}
