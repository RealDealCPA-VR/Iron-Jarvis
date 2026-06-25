"use client";

import { SquareKanban, Info } from "lucide-react";
import { usePolledApi } from "@/lib/useApi";
import { useReviews } from "@/lib/useReviews";
import type { SessionView } from "@/lib/types";
import { OfflineHint, Empty, Skeleton } from "@/components/ui";
import { PageHeader } from "@/components/PageHeader";
import { PageShell, Reveal } from "@/components/motion";
import { KanbanBoard } from "@/components/kanban/KanbanBoard";
import { LANES } from "@/lib/kanban";

export default function KanbanPage() {
  const { data, error, loading, reload } = usePolledApi<{ sessions: SessionView[] }>(
    "/sessions",
    4000,
  );
  const sessions = data?.sessions;
  const reviewsState = useReviews(sessions);

  const offline = error && error.status === 0;
  const list = sessions ?? [];

  function refreshAll() {
    reload();
    reviewsState.reload();
  }

  return (
    <PageShell>
      <Reveal>
        <PageHeader
          title="Kanban"
          subtitle="Live session lifecycle — drag a card from In Review onto Completed to approve, or onto Failed to reject."
          actions={
            <span className="hidden items-center gap-2 rounded-lg border border-white/10 bg-white/[0.03] px-3 py-1.5 text-xs text-zinc-400 sm:flex">
              <Info size={13} className="text-accent-soft/70" />
              {list.length} session{list.length === 1 ? "" : "s"}
            </span>
          }
        />
      </Reveal>

      {offline && (
        <Reveal>
          <OfflineHint />
        </Reveal>
      )}

      {loading && !data ? (
        <Reveal>
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-4">
            {LANES.map((lane) => (
              <div key={lane.id} className="space-y-3">
                <Skeleton className="h-6 w-32" />
                <div className="space-y-2.5 rounded-2xl border border-white/[0.04] bg-ink-900/40 p-2.5">
                  <Skeleton className="h-24 w-full" />
                  <Skeleton className="h-24 w-full" />
                </div>
              </div>
            ))}
          </div>
        </Reveal>
      ) : !offline && list.length === 0 ? (
        <Reveal>
          <div className="card-surface">
            <Empty icon={<SquareKanban size={26} />}>
              No sessions yet. Create one on the Sessions page to populate the board.
            </Empty>
          </div>
        </Reveal>
      ) : (
        <Reveal>
          <KanbanBoard
            sessions={list}
            reviews={reviewsState.reviews}
            reload={refreshAll}
          />
        </Reveal>
      )}
    </PageShell>
  );
}
