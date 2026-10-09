"use client";

// The sidebar's chat list away from the chat surface (v1.329.0, calm chat W4
// F2). The SAME list the chat page draws (ThreadGroups): chats grouped under
// their projects, Pinned first, "No project" last, each row a status dot
// (working on it, waiting on you, new since you last looked), the title and a
// short age; five per group, then "Show more". Pressing a row opens that chat
// on the chat page (/chat?thread=<id>), where the full list (search, the row
// options, the project scope and the archive) takes this space over.
//
// Data: lib/chatList (one store per window; the rail and the phone drawer
// share it). Theme tokens only, ghosts that fill on hover (ThreadGroups').

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import ThreadGroups from "@/components/chat/ThreadGroups";
import { readPinnedChats, useChatList, type ChatListThread } from "@/lib/chatList";

export default function SidebarChats({ onNavigate }: { onNavigate?: () => void }) {
  const router = useRouter();
  const { threads, projects, statuses } = useChatList();
  const [pinned, setPinned] = useState<string[]>([]);
  useEffect(() => {
    setPinned(readPinnedChats());
  }, [threads]);

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
      <ThreadGroups<ChatListThread>
        threads={threads}
        projects={projects ?? []}
        statuses={statuses}
        pinnedIds={pinned}
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
      />
    </div>
  );
}
