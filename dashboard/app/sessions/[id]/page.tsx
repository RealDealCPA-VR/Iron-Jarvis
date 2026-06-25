"use client";

import { use } from "react";
import Link from "next/link";
import { ArrowLeft, FileText, Gauge, ListTree, Wrench } from "lucide-react";
import { useApi } from "@/lib/useApi";
import type { SessionDetail, Evaluation, Review } from "@/lib/types";
import {
  Card,
  Badge,
  Stat,
  StatusDot,
  Spinner,
  OfflineHint,
  Empty,
  SkeletonRows,
} from "@/components/ui";
import { PageHeader } from "@/components/PageHeader";
import { ReviewPanel } from "@/components/ReviewPanel";
import { TracesPanel } from "@/components/TracesPanel";
import { PageShell, Reveal } from "@/components/motion";
import { pct, num, clockTime, shortId } from "@/lib/format";

export default function SessionDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const detail = useApi<SessionDetail>(`/sessions/${id}`);
  const evaluation = useApi<Evaluation>(`/sessions/${id}/evaluation`);
  const review = useApi<Review>(`/sessions/${id}/review`);

  const offline = detail.error && detail.error.status === 0;
  const notFound = detail.error && detail.error.status === 404;

  const session = detail.data?.session;
  const runs = detail.data?.transcript.runs ?? [];
  const tools = detail.data?.transcript.tools ?? [];

  return (
    <PageShell>
      <Reveal>
        <PageHeader
          title="Session"
          subtitle={id}
          actions={
            <Link
              href="/sessions"
              className="flex items-center gap-1.5 rounded-lg border border-white/10 bg-white/[0.03] px-3 py-1.5 text-sm text-zinc-300 transition-colors hover:border-white/20 hover:text-zinc-100"
            >
              <ArrowLeft size={14} /> All sessions
            </Link>
          }
        />
      </Reveal>

      {offline && (
        <Reveal>
          <OfflineHint />
        </Reveal>
      )}
      {notFound && (
        <Reveal>
          <Card>
            <Empty>Session not found.</Empty>
          </Card>
        </Reveal>
      )}

      {detail.loading && !detail.data ? (
        <Reveal>
          <Card title="Loading session" icon={<FileText size={15} />}>
            <SkeletonRows rows={5} />
          </Card>
        </Reveal>
      ) : session ? (
        <>
          <Reveal>
            <Card
              title="Summary"
              icon={<FileText size={15} />}
              right={<Badge value={session.status} />}
            >
              <div className="mb-4 flex items-start gap-2.5 text-[15px] text-zinc-100">
                <StatusDot status={session.status} className="mt-1.5" />
                <span>{session.task}</span>
              </div>
              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
                <Meta label="Agent" value={session.agent_type} />
                <Meta
                  label="Provider / model"
                  value={
                    <span className="font-mono text-xs">
                      {session.provider} / {session.model}
                    </span>
                  }
                />
                <Meta label="Created" value={clockTime(session.created_at)} />
                <Meta label="Finished" value={clockTime(session.finished_at)} />
                <Meta
                  label="Workspace"
                  value={
                    <span className="break-all font-mono text-xs text-zinc-400">
                      {session.workspace_path}
                    </span>
                  }
                />
              </div>
              {session.summary && (
                <div className="mt-4 rounded-xl border border-white/[0.05] bg-white/[0.02] px-3 py-2.5 text-sm text-zinc-300">
                  {session.summary}
                </div>
              )}
            </Card>
          </Reveal>

          {/* Evaluation */}
          <Reveal>
            <Card title="Evaluation" icon={<Gauge size={15} />}>
              {evaluation.error && evaluation.error.status === 404 ? (
                <Empty icon={<Gauge size={22} />}>No evaluation recorded for this session.</Empty>
              ) : evaluation.loading && !evaluation.data ? (
                <SkeletonRows rows={2} />
              ) : evaluation.data ? (
                <div className="grid grid-cols-2 gap-4 md:grid-cols-5">
                  <Stat label="Completion" value={pct(evaluation.data.completion)} accent />
                  <Stat label="Tool success" value={pct(evaluation.data.tool_success_rate)} />
                  <Stat label="Tool calls" value={evaluation.data.tool_calls} />
                  <Stat label="Steps" value={evaluation.data.step_count} />
                  <Stat label="Latency" value={`${num(evaluation.data.latency_s)}s`} />
                </div>
              ) : (
                <Empty>No evaluation.</Empty>
              )}
            </Card>
          </Reveal>

          {/* Review (only when present) */}
          {review.data && !(review.error && review.error.status === 404) && (
            <Reveal>
              <ReviewPanel
                sessionId={id}
                review={review.data}
                onAction={() => {
                  review.reload();
                  detail.reload();
                }}
              />
            </Reveal>
          )}

          {/* Transcript: agent runs */}
          <Reveal>
            <Card title={`Agent runs · ${runs.length}`} icon={<ListTree size={15} />}>
              {runs.length === 0 ? (
                <Empty>No agent runs.</Empty>
              ) : (
                <div className="-mx-1 overflow-x-auto">
                  <table className="w-full text-left text-sm">
                    <thead>
                      <tr className="border-b hairline text-[11px] uppercase tracking-[0.1em] text-zinc-500">
                        <th className="px-2 py-2.5 font-medium">Run</th>
                        <th className="px-2 py-2.5 font-medium">Agent</th>
                        <th className="px-2 py-2.5 font-medium">State</th>
                        <th className="px-2 py-2.5 font-medium">Steps</th>
                        <th className="px-2 py-2.5 font-medium">Parent</th>
                        <th className="px-2 py-2.5 font-medium">Result</th>
                      </tr>
                    </thead>
                    <tbody>
                      {runs.map((r) => (
                        <tr
                          key={r.id}
                          className="border-b border-white/[0.04] align-top last:border-0 hover:bg-white/[0.02]"
                        >
                          <td className="px-2 py-2.5 font-mono text-[11px] text-zinc-500">
                            {shortId(r.id)}
                          </td>
                          <td className="px-2 py-2.5 text-zinc-300">{r.agent_type}</td>
                          <td className="px-2 py-2.5">
                            <Badge value={r.state} />
                          </td>
                          <td className="px-2 py-2.5 text-zinc-400">{r.steps}</td>
                          <td className="px-2 py-2.5 font-mono text-[11px] text-zinc-600">
                            {r.parent_id ? shortId(r.parent_id) : "—"}
                          </td>
                          <td className="max-w-sm px-2 py-2.5 text-zinc-400">
                            <span className="line-clamp-2">{r.result || "—"}</span>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </Card>
          </Reveal>

          {/* Transcript: tool invocations */}
          <Reveal>
            <Card title={`Tool invocations · ${tools.length}`} icon={<Wrench size={15} />}>
              {tools.length === 0 ? (
                <Empty>No tool invocations.</Empty>
              ) : (
                <div className="-mx-1 overflow-x-auto">
                  <table className="w-full text-left text-sm">
                    <thead>
                      <tr className="border-b hairline text-[11px] uppercase tracking-[0.1em] text-zinc-500">
                        <th className="px-2 py-2.5 font-medium">Tool</th>
                        <th className="px-2 py-2.5 font-medium">Verdict</th>
                        <th className="px-2 py-2.5 font-medium">OK</th>
                        <th className="px-2 py-2.5 font-medium">Output</th>
                      </tr>
                    </thead>
                    <tbody>
                      {tools.map((t) => (
                        <tr
                          key={t.id}
                          className="border-b border-white/[0.04] align-top last:border-0 hover:bg-white/[0.02]"
                        >
                          <td className="px-2 py-2.5 font-mono text-zinc-200">{t.tool}</td>
                          <td className="px-2 py-2.5">
                            <Badge value={t.verdict} />
                          </td>
                          <td className="px-2 py-2.5">
                            <Badge value={t.ok ? "ok" : "failed"} />
                          </td>
                          <td className="max-w-md px-2 py-2.5">
                            <span className="line-clamp-2 font-mono text-xs text-zinc-400">
                              {t.output || "—"}
                            </span>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </Card>
          </Reveal>

          <Reveal>
            <TracesPanel sessionId={id} />
          </Reveal>
        </>
      ) : null}
    </PageShell>
  );
}

function Meta({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div>
      <div className="text-[11px] font-medium uppercase tracking-[0.1em] text-zinc-500">
        {label}
      </div>
      <div className="mt-1 text-sm text-zinc-200">{value}</div>
    </div>
  );
}
