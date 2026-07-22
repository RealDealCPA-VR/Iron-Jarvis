"use client";

// The project screens INSIDE the chat module: with a project active, the
// conversation column can flip to Tasks / Board / Media right in place —
// Projects has no page of its own in daily use. Mirrors the old hub's wiring
// (visibility-paused scoped polls, shared reviews) with the same components.

import { useEffect, useState } from "react";
import Link from "next/link";
import { Images, SquareKanban } from "lucide-react";
import { API_BASE, ijToken } from "@/lib/api";
import { useApi, usePolledApi } from "@/lib/useApi";
import { useReviews } from "@/lib/useReviews";
import type { SessionView } from "@/lib/types";
import { Card, Empty, SkeletonRows } from "@/components/ui";
import { KanbanBoard } from "@/components/kanban/KanbanBoard";
import { ProjectTasks } from "@/components/project/ProjectTasks";

export type ProjectSurfaceView = "tasks" | "board" | "media";

function useDocumentVisible(): boolean {
  const [visible, setVisible] = useState(true);
  useEffect(() => {
    const onChange = () => setVisible(!document.hidden);
    onChange();
    document.addEventListener("visibilitychange", onChange);
    return () => document.removeEventListener("visibilitychange", onChange);
  }, []);
  return visible;
}

interface MediaItem {
  name: string;
  media: "image" | "video" | "audio" | null;
  filename: string;
  url: string;
}

function mediaSrc(url: string): string {
  const t = ijToken();
  const sep = url.includes("?") ? "&" : "?";
  return `${API_BASE}${url}${t ? `${sep}token=${encodeURIComponent(t)}` : ""}`;
}

function SurfaceTasks({ projectId, hasRoot }: { projectId: string; hasRoot: boolean }) {
  const detail = useApi<{ sessions: SessionView[] }>(
    `/projects/${encodeURIComponent(projectId)}`,
  );
  return (
    <ProjectTasks
      projectId={projectId}
      hasRoot={hasRoot}
      sessions={detail.data?.sessions ?? []}
      reloadSessions={detail.reload}
    />
  );
}

function SurfaceBoard({ projectId }: { projectId: string }) {
  const visible = useDocumentVisible();
  const { data, error, reload } = usePolledApi<{ sessions: SessionView[] }>(
    visible ? `/sessions?project_id=${encodeURIComponent(projectId)}` : null,
    4000,
  );
  const sessions = data?.sessions;
  const reviewsState = useReviews(sessions);
  const mine = (sessions ?? []).filter((s) => s.project_id === projectId);
  if (error && error.status === 0 && mine.length === 0)
    return (
      <Card title="Board" icon={<SquareKanban size={15} />}>
        <p className="py-2 text-sm text-zinc-500">
          Board unavailable — the daemon looks offline.
        </p>
      </Card>
    );
  if (mine.length === 0)
    return (
      <Card title="Board" icon={<SquareKanban size={15} />}>
        <Empty icon={<SquareKanban size={22} />}>
          No sessions in this project yet — run a task from the Tasks tab.
        </Empty>
      </Card>
    );
  return (
    <KanbanBoard
      sessions={mine}
      reviews={reviewsState.reviews}
      reload={() => {
        reload();
        reviewsState.reload();
      }}
      projectId={projectId}
    />
  );
}

function SurfaceMedia({ projectId }: { projectId: string }) {
  const { data, loading, error } = useApi<{ items: MediaItem[] }>(
    `/creative/items?project_id=${encodeURIComponent(projectId)}&limit=200`,
  );
  const items = data?.items ?? [];
  return (
    <Card
      title={items.length ? `Media · ${items.length}` : "Media"}
      icon={<Images size={15} />}
    >
      {loading && !data ? (
        <SkeletonRows rows={3} />
      ) : error && error.status === 0 ? (
        <p className="py-2 text-sm text-zinc-500">
          Media unavailable — the daemon looks offline.
        </p>
      ) : items.length === 0 ? (
        <Empty icon={<Images size={22} />}>
          No media in this project yet — generate something in Creative while
          this project is active, and it lands here.
        </Empty>
      ) : (
        <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-3 md:grid-cols-4">
          {items.map((m) => (
            <Link
              key={m.name}
              href="/creative"
              title={m.filename}
              className="group relative block overflow-hidden rounded-xl border border-white/[0.06] bg-ink-900/60"
            >
              {m.media === "image" ? (
                // eslint-disable-next-line @next/next/no-img-element
                <img
                  src={mediaSrc(m.url)}
                  alt={m.filename}
                  className="aspect-square w-full object-cover transition-transform group-hover:scale-[1.03]"
                />
              ) : (
                <div className="grid aspect-square w-full place-items-center text-[11px] text-zinc-500">
                  {m.media ?? "file"}
                </div>
              )}
            </Link>
          ))}
        </div>
      )}
    </Card>
  );
}

/** One project surface, selected by `view`, rendered in the chat column. */
export function ProjectSurface({
  projectId,
  hasRoot,
  view,
}: {
  projectId: string;
  hasRoot: boolean;
  view: ProjectSurfaceView;
}) {
  if (view === "tasks") return <SurfaceTasks projectId={projectId} hasRoot={hasRoot} />;
  if (view === "board") return <SurfaceBoard projectId={projectId} />;
  return <SurfaceMedia projectId={projectId} />;
}
