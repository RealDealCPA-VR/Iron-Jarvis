"use client";

/**
 * "Completed" (v1.304.0): this project's finished work, newest first — the
 * outcome in a word, who did it, the file NAMES it produced, and a link to
 * the session (or the assignment) where the whole result lives.
 */

import Link from "next/link";
import { ArrowRight, FileText, Inbox } from "lucide-react";
import { Badge } from "@/components/ui";
import { timeAgo } from "@/lib/format";
import { bareMemberName, completedHref, type CompletedItem } from "@/lib/agentWorlds";

export function CompletedList({
  projectId,
  items,
}: {
  projectId: string;
  items: CompletedItem[];
}) {
  if (items.length === 0)
    return (
      <p
        data-testid="world-completed-empty"
        className="flex items-center gap-2 py-6 text-sm text-zinc-500"
      >
        <Inbox size={16} aria-hidden /> Nothing finished here yet.
      </p>
    );
  return (
    <ul data-testid="world-completed-list" className="space-y-2">
      {items.map((it, i) => {
        const files = Array.isArray(it.files) ? it.files.filter((f) => typeof f === "string") : [];
        return (
          <li
            key={it.id || `${it.title}-${i}`}
            data-testid={`world-completed-item-${it.id || i}`}
            className="rounded-xl border hairline px-3 py-2"
          >
            <div className="flex items-start gap-2">
              <span className="min-w-0 flex-1">
                <span className="block text-sm text-zinc-100">{it.title || "Untitled"}</span>
                <span className="block text-[11px] text-zinc-500">
                  {it.agent ? `${bareMemberName(it.agent)} · ` : ""}
                  {timeAgo(it.finished_at ?? null)}
                </span>
              </span>
              {it.outcome && <Badge value={it.outcome} />}
            </div>
            {files.length > 0 && (
              <ul className="mt-1.5 flex flex-wrap gap-1.5" aria-label="Files">
                {files.slice(0, 8).map((f) => (
                  <li
                    key={f}
                    className="flex items-center gap-1 rounded-md border hairline px-1.5 py-0.5 font-mono text-[11px] text-zinc-300"
                  >
                    <FileText size={11} aria-hidden /> {f}
                  </li>
                ))}
                {files.length > 8 && (
                  <li className="text-[11px] text-zinc-500">+{files.length - 8} more</li>
                )}
              </ul>
            )}
            <Link
              href={completedHref(it, projectId)}
              className="mt-1.5 inline-flex items-center gap-1 text-xs text-accent-soft hover:underline"
            >
              Open <ArrowRight size={12} aria-hidden />
            </Link>
          </li>
        );
      })}
    </ul>
  );
}

export default CompletedList;
