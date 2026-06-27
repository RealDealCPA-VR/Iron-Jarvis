"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { Bell, GitBranch, MonitorCog, Inbox, ArrowRight } from "lucide-react";
import { useEvents } from "@/lib/useEvents";
import { usePolledApi } from "@/lib/useApi";
import type { ComputerUseStatus, IJEvent } from "@/lib/types";
import { shortId, clockTime } from "@/lib/format";

/** Best-effort session id for a review event (top-level wins, then payload). */
function reviewKey(e: IJEvent): string {
  return String(e.session_id ?? (e.payload?.session_id as string | undefined) ?? e.id);
}

/**
 * Notification center: a bell + unread badge counting work that needs a human —
 * unresolved review requests (from the live event stream) plus any pending
 * computer-use approvals (polled). Clicking opens a dropdown of deep links.
 * Self-contained; renders a calm "all clear" state when nothing is pending.
 */
export function NotificationBell() {
  const { events } = useEvents(100);
  // Computer-use approvals don't ride the event stream, so poll their count.
  const cu = usePolledApi<ComputerUseStatus>("/computeruse", 15000);
  const pendingApprovals = cu.data?.pending_approvals ?? 0;

  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  // Unresolved review.requested events: dedupe by session and drop any whose
  // review later resolved/approved/rejected (defensive — those types may not
  // exist yet, in which case every requested review simply stays pending).
  const reviews = useMemo(() => {
    const resolved = new Set<string>();
    for (const e of events) {
      if (e.type.startsWith("review.") && e.type !== "review.requested") {
        resolved.add(reviewKey(e));
      }
    }
    const seen = new Set<string>();
    const out: IJEvent[] = [];
    for (const e of events) {
      if (e.type !== "review.requested") continue;
      const key = reviewKey(e);
      if (resolved.has(key) || seen.has(key)) continue;
      seen.add(key);
      out.push(e);
    }
    return out;
  }, [events]);

  const count = reviews.length + pendingApprovals;

  // Close the dropdown on an outside click or Escape.
  useEffect(() => {
    if (!open) return;
    function onClick(ev: MouseEvent) {
      if (ref.current && !ref.current.contains(ev.target as Node)) setOpen(false);
    }
    function onKey(ev: KeyboardEvent) {
      if (ev.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onClick);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-label={count ? `${count} notifications` : "Notifications"}
        className={`relative grid h-9 w-9 place-items-center rounded-xl border transition-colors ${
          open
            ? "border-accent/40 bg-accent/[0.1] text-accent-soft"
            : "border-white/10 bg-white/[0.02] text-zinc-400 hover:border-white/20 hover:text-zinc-100"
        }`}
      >
        <Bell size={17} strokeWidth={2} />
        {count > 0 && (
          <span className="absolute -right-1 -top-1 grid h-4 min-w-[1rem] place-items-center rounded-full bg-accent px-1 text-[10px] font-bold text-ink-950 shadow-glow-sm">
            {count > 99 ? "99+" : count}
          </span>
        )}
      </button>

      {open && (
        <div className="absolute right-0 z-50 mt-2 w-80 origin-top-right">
          <div className="card-surface overflow-hidden">
            <header className="flex items-center justify-between border-b hairline px-4 py-2.5">
              <span className="flex items-center gap-2 text-[13px] font-semibold text-zinc-200">
                <Bell size={14} className="text-accent-soft/80" />
                Notifications
              </span>
              {count > 0 && (
                <span className="rounded-full border border-accent/30 bg-accent/[0.1] px-2 py-0.5 text-[10px] font-medium text-accent-soft">
                  {count} pending
                </span>
              )}
            </header>

            <div className="max-h-[22rem] overflow-y-auto">
              {count === 0 ? (
                <div className="flex flex-col items-center gap-2 px-4 py-8 text-center">
                  <Inbox size={22} className="text-zinc-600" />
                  <div className="text-sm text-zinc-500">You&apos;re all caught up.</div>
                  <div className="max-w-[15rem] text-[11px] text-zinc-600">
                    Reviews and approvals that need you will show up here.
                  </div>
                </div>
              ) : (
                <ul className="divide-y divide-white/[0.04]">
                  {pendingApprovals > 0 && (
                    <li>
                      <Link
                        href="/computeruse"
                        onClick={() => setOpen(false)}
                        className="flex items-center gap-3 px-4 py-3 transition-colors hover:bg-white/[0.04]"
                      >
                        <span className="grid h-8 w-8 shrink-0 place-items-center rounded-lg border border-amber-500/25 bg-amber-500/[0.08] text-amber-300">
                          <MonitorCog size={15} />
                        </span>
                        <span className="min-w-0 flex-1">
                          <span className="block text-sm font-medium text-zinc-100">
                            {pendingApprovals} computer-use approval
                            {pendingApprovals === 1 ? "" : "s"}
                          </span>
                          <span className="block text-[11px] text-zinc-500">
                            A sensitive action is waiting for your OK.
                          </span>
                        </span>
                        <ArrowRight size={13} className="shrink-0 text-zinc-600" />
                      </Link>
                    </li>
                  )}

                  {reviews.map((e) => {
                    const summary =
                      (e.payload?.summary as string | undefined) ||
                      (e.payload?.risk ? `risk: ${String(e.payload.risk)}` : "") ||
                      "Changes are ready for your review.";
                    return (
                      <li key={e.id}>
                        <Link
                          href="/kanban"
                          onClick={() => setOpen(false)}
                          className="flex items-center gap-3 px-4 py-3 transition-colors hover:bg-white/[0.04]"
                        >
                          <span className="grid h-8 w-8 shrink-0 place-items-center rounded-lg border border-accent/25 bg-accent/[0.08] text-accent-soft">
                            <GitBranch size={15} />
                          </span>
                          <span className="min-w-0 flex-1">
                            <span className="block text-sm font-medium text-zinc-100">
                              Review requested
                            </span>
                            <span className="block truncate text-[11px] text-zinc-500">
                              {summary}
                            </span>
                            <span className="mt-0.5 block font-mono text-[10px] text-zinc-600">
                              {e.session_id ? shortId(e.session_id) : "—"} · {clockTime(e.ts)}
                            </span>
                          </span>
                          <ArrowRight size={13} className="shrink-0 text-zinc-600" />
                        </Link>
                      </li>
                    );
                  })}
                </ul>
              )}
            </div>

            {count > 0 && (
              <footer className="border-t hairline px-4 py-2">
                <Link
                  href="/kanban"
                  onClick={() => setOpen(false)}
                  className="flex items-center justify-center gap-1.5 text-[11px] font-medium text-accent-soft transition-colors hover:text-accent"
                >
                  Open the review board <ArrowRight size={12} />
                </Link>
              </footer>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
