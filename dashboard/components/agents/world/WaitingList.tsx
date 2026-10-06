"use client";

/**
 * "Waiting on you" (v1.304.0): what this project needs from the USER — an
 * agent's question paused on an ask card, an assignment that is blocked or
 * held, a review. Each row LINKS TO WHERE IT IS RESOLVED (the daemon's `link`:
 * the session's ask card, the assignment's unblock/retry), so the list is a
 * door, never a second place to answer from.
 */

import Link from "next/link";
import { ArrowRight, CheckCircle2 } from "lucide-react";
import { timeAgo } from "@/lib/format";
import {
  bareMemberName,
  waitingHref,
  waitingKindLabel,
  type WaitingItem,
} from "@/lib/agentWorlds";

export function WaitingList({
  projectId,
  items,
}: {
  projectId: string;
  items: WaitingItem[];
}) {
  if (items.length === 0)
    return (
      <p
        data-testid="world-waiting-empty"
        className="flex items-center gap-2 py-6 text-sm text-zinc-500"
      >
        <CheckCircle2 size={16} className="text-emerald-400" aria-hidden />
        Nothing is waiting on you here.
      </p>
    );
  return (
    <ul data-testid="world-waiting-list" className="space-y-2">
      {items.map((it, i) => (
        <li key={it.id || `${it.kind}-${i}`}>
          <Link
            href={waitingHref(it, projectId)}
            data-testid={`world-waiting-item-${it.id || i}`}
            className="group flex items-start gap-3 rounded-xl border border-amber-400/30 bg-amber-400/5 px-3 py-2 transition-colors hover:border-amber-400/50"
          >
            <span className="min-w-0 flex-1">
              <span className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-wider text-amber-300">
                {waitingKindLabel(it.kind)}
                {it.agent && (
                  <span className="normal-case tracking-normal text-zinc-400">
                    · {bareMemberName(it.agent)}
                  </span>
                )}
              </span>
              <span className="block text-sm text-zinc-100">{it.title || "Untitled"}</span>
              {it.reason && (
                <span className="block truncate text-xs text-zinc-500">{it.reason}</span>
              )}
              {it.since && (
                <span className="block text-[11px] text-zinc-500">{timeAgo(it.since)}</span>
              )}
            </span>
            <ArrowRight
              size={14}
              className="mt-1 shrink-0 text-zinc-500 group-hover:text-zinc-200"
              aria-hidden
            />
          </Link>
        </li>
      ))}
    </ul>
  );
}

export default WaitingList;
