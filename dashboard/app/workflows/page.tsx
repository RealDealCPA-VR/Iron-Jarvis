"use client";

import { useEffect } from "react";
import { History } from "lucide-react";
import { useApi } from "@/lib/useApi";
import { useEvents } from "@/lib/useEvents";
import type { WorkflowRun } from "@/lib/types";
import { Card, Badge, Empty, SkeletonRows } from "@/components/ui";
import { PageHeader } from "@/components/PageHeader";
import { PageShell, Reveal } from "@/components/motion";
import WorkflowCanvas from "@/components/workflow/WorkflowCanvas";
import { timeAgo } from "@/lib/format";

export default function WorkflowsPage() {
  return (
    <PageShell>
      <Reveal>
        <PageHeader
          title="Workflows"
          subtitle="Wire agents into a visual, multi-step workflow, then run it."
        />
      </Reveal>
      <Reveal>
        <WorkflowCanvas />
      </Reveal>
      <Reveal>
        <RunHistory />
      </Reveal>
    </PageShell>
  );
}

/** Count the sessions a run spawned (`session_ids_json` is a JSON array string). */
function sessionCount(r: WorkflowRun): number {
  try {
    const arr = JSON.parse(String(r.session_ids_json ?? "[]"));
    return Array.isArray(arr) ? arr.length : 0;
  } catch {
    return 0;
  }
}

/** Best-available timestamp (the daemon record uses `started_at`). */
function runTimestamp(r: WorkflowRun): string | null {
  const raw = (r.started_at ?? r.created_at ?? r.finished_at) as
    | string
    | null
    | undefined;
  return raw ?? null;
}

function RunHistory() {
  const { data, error, loading, reload } = useApi<{ runs: WorkflowRun[] }>(
    "/workflows/runs",
  );

  // Refetch the moment a workflow finishes (the engine emits workflow.completed).
  const { events } = useEvents(50);
  const lastCompleted = events.find((e) => e.type === "workflow.completed");
  useEffect(() => {
    if (lastCompleted) reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lastCompleted?.id]);

  const offline = error && error.status === 0;
  const runs = data?.runs ?? [];
  // Newest first (records carry a started_at timestamp).
  const ordered = [...runs].sort((a, b) => {
    const ta = new Date(runTimestamp(a) ?? 0).getTime();
    const tb = new Date(runTimestamp(b) ?? 0).getTime();
    return tb - ta;
  });

  return (
    <Card
      title={`Run history${runs.length ? ` · ${runs.length}` : ""}`}
      icon={<History size={15} />}
    >
      {loading && !data ? (
        <SkeletonRows rows={4} />
      ) : offline ? (
        <Empty icon={<History size={22} />}>
          Daemon offline — run history is unavailable.
        </Empty>
      ) : ordered.length === 0 ? (
        <Empty icon={<History size={22} />}>
          No workflow runs yet. Run a workflow above to see it here.
        </Empty>
      ) : (
        <div className="-mx-1 overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="border-b hairline text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                <th className="px-2 py-2.5 font-medium">Workflow</th>
                <th className="px-2 py-2.5 font-medium">Status</th>
                <th className="px-2 py-2.5 font-medium">Sessions</th>
                <th className="px-2 py-2.5 font-medium">When</th>
              </tr>
            </thead>
            <tbody>
              {ordered.map((r, i) => {
                const ts = runTimestamp(r);
                const n = sessionCount(r);
                return (
                  <tr
                    key={r.id ?? `${r.workflow_name}-${i}`}
                    className="border-b border-white/[0.04] last:border-0 hover:bg-white/[0.02]"
                  >
                    <td className="px-2 py-2.5 text-zinc-100">
                      {r.workflow_name || "—"}
                    </td>
                    <td className="px-2 py-2.5">
                      <Badge value={r.status || "unknown"} />
                    </td>
                    <td className="px-2 py-2.5 text-zinc-400">
                      {n} session{n === 1 ? "" : "s"}
                    </td>
                    <td className="px-2 py-2.5 text-zinc-500">
                      {ts ? timeAgo(ts) : "—"}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}
