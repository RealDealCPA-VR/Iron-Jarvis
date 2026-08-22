"use client";

// The project screens INSIDE the chat module: with a project active, the
// conversation column can flip to Tasks / Board / Media right in place —
// Projects has no page of its own in daily use. Mirrors the old hub's wiring
// (visibility-paused scoped polls, shared reviews) with the same components.

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { Images, SquareKanban } from "lucide-react";
import { API_BASE, get, ijToken } from "@/lib/api";
import { useApi, usePolledApi } from "@/lib/useApi";
import { useEvents } from "@/lib/useEvents";
import { useReviews } from "@/lib/useReviews";
import type { SessionView } from "@/lib/types";
import { Card, Empty, SkeletonRows } from "@/components/ui";
import { ApprovalCard } from "@/components/chat/ApprovalCard";
import { KanbanBoard } from "@/components/kanban/KanbanBoard";
import { ProjectTasks } from "@/components/project/ProjectTasks";
import { ProjectSchedules } from "@/components/project/ProjectSchedules";

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

/* ------------------------------------------------- mid-run approvals (P15) */

/** One PAUSED run in this project, waiting on the user's decision. */
export interface ProjectApproval {
  id: string;
  sessionId: string;
  tool: string;
  args?: Record<string, unknown>;
}

/**
 * The Projects half of the v1.189.0 mid-run ask. The runtime pauses an
 * ask-tier tool call and publishes `approval.requested` tagged with the
 * SESSION id; until now the chat page was the only renderer, scoped to the
 * one session chat itself was awaiting — so a Projects task's ask reached
 * nobody and died 300s later as a timeout-deny, which is strictly worse than
 * the instant honest denial it replaced.
 *
 * Membership is ASKED, never assumed: the event payload carries no
 * project_id, and a task session is usually newer than anything this page has
 * polled, so an unknown session is resolved once via
 * `GET /sessions/{id}` → `{session, transcript}` (NESTED — the twice-shipped
 * bug) and cached. If that lookup FAILS the answer is UNKNOWN, not "not
 * ours": the approval is un-seen so a later scan retries, and no card is
 * rendered, because showing another project's pause here would be a lie about
 * whose work is paused.
 */
export function useProjectApprovals(projectId: string): ProjectApproval[] {
  const { events } = useEvents(100);
  const [pending, setPending] = useState<ProjectApproval[]>([]);
  // approval ids already routed (so a re-scan of the same buffer is a no-op),
  // ids already resolved (so an in-flight membership lookup cannot resurrect a
  // dead card), and session id → belongs-to-this-project.
  const seen = useRef<Set<string>>(new Set());
  const resolved = useRef<Set<string>>(new Set());
  const member = useRef<Map<string, boolean>>(new Map());
  // The project the rendered state belongs to. A scan run is superseded on
  // EVERY WebSocket frame — `useEvents` does `setEvents(prev => [data, ...prev])`
  // and so hands back a NEW array each time — so a per-run "cancelled" flag
  // cannot mean "throw this answer away"; only a PROJECT switch may.
  const active = useRef<string>(projectId);

  useEffect(() => {
    active.current = projectId;
    seen.current = new Set();
    resolved.current = new Set();
    member.current = new Map();
    setPending([]);
  }, [projectId]);

  useEffect(() => {
    // OLDEST FIRST: a request and its resolution can land in one batch, and
    // applying them newest-first would leave a dead card on screen.
    const batch = [...events].reverse();
    void (async () => {
      for (const e of batch) {
        // A PROJECT SWITCH ends this run here, at the TOP — not at the render
        // gate below. Everything after this point writes into refs the
        // [projectId] effect has just REPLACED for the new project, and
        // `seen.current.add(aid)` a few lines down is unrecoverable: it stamps
        // the old run's ids into the NEW project's fresh `seen` set, and the new
        // project's own run then `continue`s past them forever — its card never
        // renders, the run pauses 300s and times out into a deny. Bailing whole
        // is safe precisely because a switch clears seen/resolved/member/pending
        // and the new run re-scans the ENTIRE batch from scratch; `active`
        // changes ONLY on a project switch, never on an unrelated frame, so this
        // is not the per-run cancellation the note below rules out.
        if (active.current !== projectId) return;
        if (e.type === "approval.resolved") {
          const rid = String(e.payload?.approval_id ?? "");
          if (!rid) continue;
          resolved.current.add(rid);
          setPending((prev) => prev.filter((a) => a.id !== rid));
          continue;
        }
        if (e.type !== "approval.requested") continue;
        const aid = String(e.payload?.approval_id ?? "");
        const sid = e.session_id ?? "";
        if (!aid || !sid || seen.current.has(aid)) continue;
        seen.current.add(aid);
        let mine = member.current.get(sid);
        if (mine === undefined) {
          try {
            const r = await get<{ session?: { project_id?: string | null } }>(
              `/sessions/${encodeURIComponent(sid)}`,
            );
            mine = (r.session?.project_id ?? "") === projectId;
            // The verdict was computed against THIS run's `projectId`, so it
            // may only enter the cache while that is still the project on
            // screen. The [projectId] effect CLEARS this map on a switch, so a
            // superseded run writing here lands its answer in the NEW project's
            // map, under a session id the new project never asked about — and
            // both directions bite: a session of the new project cached "not
            // ours" swallows its next ask (silent 300s pause → timeout-deny),
            // and a session of the old one cached "ours" would render a foreign
            // project's pause here. Skipping the write costs one re-lookup.
            if (active.current === projectId) member.current.set(sid, mine);
          } catch {
            seen.current.delete(aid); // unknown — retry on the next scan
            continue;
          }
        }
        // NOT gated on a per-run cancellation. `seen` is stamped BEFORE the
        // awaited lookup, so a run that bailed here because an unrelated frame
        // re-ran the effect would leave the id seen-but-unrouted and NO later
        // scan could recover it — the ask reaches nobody, the run pauses
        // silently for 300s and times out into a deny, which is exactly the
        // outcome P15 exists to prevent. A superseded run may safely finish
        // its own work instead: the write is idempotent (deduped by id and by
        // `resolved`), and the only thing that truly invalidates it is a
        // PROJECT switch, which `active` — not `cancelled` — answers.
        if (!mine || resolved.current.has(aid) || active.current !== projectId)
          continue;
        const tool = String(e.payload?.tool ?? "");
        const args = (e.payload?.args ?? undefined) as
          | Record<string, unknown>
          | undefined;
        setPending((prev) =>
          prev.some((p) => p.id === aid)
            ? prev
            : [...prev, { id: aid, sessionId: sid, tool, args }],
        );
      }
    })();
  }, [events, projectId]);

  return pending;
}

/** The paused-run asks for this project. Renders NOTHING when there are none —
 *  the same card and the same `POST /chat/approvals/{id}` route chat answers
 *  with (the approvals registry is shared platform-side, so one answer path
 *  serves both lanes). The card disappears on `approval.resolved`, whoever
 *  answered and however it ended (including the 300s timeout-deny). */
export function ProjectApprovals({ projectId }: { projectId: string }) {
  const pending = useProjectApprovals(projectId);
  if (pending.length === 0) return null;
  return (
    <div className="space-y-2" data-testid="project-approvals">
      {pending.map((a) => (
        <div key={a.id} className="space-y-1">
          <p className="text-[11px] text-zinc-500">
            A task in this project is paused —{" "}
            <Link
              href={`/sessions/${encodeURIComponent(a.sessionId)}`}
              className="text-accent-soft hover:underline"
            >
              open the session
            </Link>
          </p>
          {/* No `onConversation`: there is no composer here to arm. The
              runtime widens the RUN's own allow-set server-side, so the
              button still means what it says for this task. */}
          <ApprovalCard
            approval={{ id: a.id, callId: "", tool: a.tool, args: a.args }}
          />
        </div>
      ))}
    </div>
  );
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
    <div className="space-y-4">
      <ProjectTasks
        projectId={projectId}
        hasRoot={hasRoot}
        sessions={detail.data?.sessions ?? []}
        reloadSessions={detail.reload}
      />
      {/* v1.169.0: the project's heartbeat — task schedules that run in here,
          with next fire + last outcome. Renders nothing when there are none. */}
      <ProjectSchedules projectId={projectId} />
    </div>
  );
}

function SurfaceBoard({ projectId }: { projectId: string }) {
  const visible = useDocumentVisible();
  const { data, error, loading, reload } = usePolledApi<{ sessions: SessionView[] }>(
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
  // "No sessions" is a CLAIM and may only be made once a response has landed.
  // `data` is null until the first /sessions round-trip resolves, so the old
  // `mine.length === 0` asserted an empty project on every activation of this
  // surface — the same honesty rule workflows/page.tsx states verbatim:
  // loading or errored means UNKNOWN, not "you have nothing".
  // An error is CLAIMED only when there is one. `useDocumentVisible` nulls the
  // path whenever the tab is hidden, and `useApi` then leaves data null, error
  // null and loading false — so gating the sentence on `!loading` alone
  // fabricated an HTTP failure that never happened (and does the same for one
  // commit on return-to-visible). Trading "you have nothing" for "the daemon
  // errored" is the same lie wearing a different coat.
  if (!data)
    return (
      <Card title="Board" icon={<SquareKanban size={15} />}>
        {loading || !error ? (
          <SkeletonRows rows={3} />
        ) : (
          <p className="py-2 text-sm text-zinc-500">
            Board unavailable — the daemon returned an error (HTTP {error.status}).
          </p>
        )}
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
      ) : !data ? (
        // Same unknown-guard as the Board, and for the same reason: `items`
        // is `data?.items ?? []`, so a 500/404 used to fall straight through
        // to "No media in this project yet" — a claim about the project made
        // from a failed request. No response in hand means UNKNOWN.
        error ? (
          <p className="py-2 text-sm text-zinc-500">
            Media unavailable — the daemon returned an error (HTTP {error.status}).
          </p>
        ) : (
          <SkeletonRows rows={3} />
        )
      ) : items.length === 0 ? (
        <Empty icon={<Images size={22} />}>
          No media in this project yet — media generated in this project&apos;s
          chat, or by a project task, lands here.
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
  return (
    <div className="space-y-4">
      {/* A paused task's ask outranks the surface it interrupted — it rides
          EVERY view, because the run keeps waiting whichever tab you're on. */}
      <ProjectApprovals projectId={projectId} />
      {view === "tasks" ? (
        <SurfaceTasks projectId={projectId} hasRoot={hasRoot} />
      ) : view === "board" ? (
        <SurfaceBoard projectId={projectId} />
      ) : (
        <SurfaceMedia projectId={projectId} />
      )}
    </div>
  );
}
