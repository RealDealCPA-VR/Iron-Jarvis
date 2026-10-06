"use client";

/**
 * A project world's Board tab (v1.304.0) — the project's own Kanban.
 *
 * The same composition as the project page's `ProjectBoard` (which is private
 * to that route file — a Next page module may not export extra components):
 * a scoped, visibility-paused /sessions poll feeding `KanbanBoard` with the
 * project id. MOUNTED ONLY WHILE THE TAB IS OPEN — `WorldTabs` renders it
 * conditionally — so a world nobody is looking at the board of never polls
 * /sessions every 4 s.
 */

import { SquareKanban } from "lucide-react";
import { usePolledApi } from "@/lib/useApi";
import { useDocumentVisible } from "@/lib/useDocumentVisible";
import { useReviews } from "@/lib/useReviews";
import { KanbanBoard } from "@/components/kanban/KanbanBoard";
import { Empty, SkeletonRows } from "@/components/ui";
import type { SessionView } from "@/lib/types";

export function WorldBoard({ projectId }: { projectId: string }) {
  const visible = useDocumentVisible();
  const { data, error, loading, reload } = usePolledApi<{ sessions: SessionView[] }>(
    visible ? `/sessions?project_id=${encodeURIComponent(projectId)}` : null,
    4000,
  );
  const sessions = data?.sessions;
  const reviewsState = useReviews(sessions);
  const list = sessions ?? [];
  const mine = list.filter((s) => s.project_id === projectId);

  if (error && error.status === 0 && list.length === 0)
    return (
      <p data-testid="world-board" className="py-2 text-sm text-zinc-500">
        Board unavailable — the daemon looks offline.
      </p>
    );
  // Same honesty rule as the project page: "nothing here" is only said once
  // an answer has landed; loading or errored is not "you have nothing".
  if (!data)
    return (
      <div data-testid="world-board">
        {loading || !error ? (
          <SkeletonRows rows={3} />
        ) : (
          <p className="py-2 text-sm text-zinc-500">
            Board unavailable — the daemon returned an error (HTTP {error.status}).
          </p>
        )}
      </div>
    );
  if (mine.length === 0)
    return (
      <div data-testid="world-board">
        <Empty icon={<SquareKanban size={22} />}>
          No work on this project&apos;s board yet — give the team a task.
        </Empty>
      </div>
    );
  return (
    <div data-testid="world-board" className="overflow-x-auto">
      <KanbanBoard
        sessions={list}
        reviews={reviewsState.reviews}
        reload={() => {
          reload();
          reviewsState.reload();
        }}
        projectId={projectId}
      />
    </div>
  );
}

export default WorldBoard;
