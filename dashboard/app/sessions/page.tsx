"use client";

import Link from "next/link";
import { Boxes, Plus, ArrowUpRight } from "lucide-react";
import { usePolledApi } from "@/lib/useApi";
import type { SessionView } from "@/lib/types";
import { Card, Badge, StatusDot, OfflineHint, Empty, SkeletonRows } from "@/components/ui";
import { PageHeader } from "@/components/PageHeader";
import { NewSessionForm } from "@/components/NewSessionForm";
import { PageShell, Reveal } from "@/components/motion";
import { timeAgo, shortId } from "@/lib/format";

export default function SessionsPage() {
  const { data, error, loading, reload } = usePolledApi<{
    sessions: SessionView[];
  }>("/sessions", 4000);

  const offline = error && error.status === 0;
  const sessions = data?.sessions ?? [];

  return (
    <PageShell>
      <Reveal>
        <PageHeader title="Sessions" subtitle="Run agents and inspect past sessions." />
      </Reveal>

      {offline && (
        <Reveal>
          <OfflineHint />
        </Reveal>
      )}

      <Reveal>
        <div className="grid gap-6 lg:grid-cols-3">
          <div className="lg:col-span-1">
            <Card title="New session" icon={<Plus size={15} />}>
              <NewSessionForm onCreated={reload} />
            </Card>
          </div>

          <div className="lg:col-span-2">
            <Card
              title={`All sessions${sessions.length ? ` · ${sessions.length}` : ""}`}
              icon={<Boxes size={15} />}
            >
              {loading && !data ? (
                <SkeletonRows rows={6} />
              ) : sessions.length === 0 ? (
                <Empty icon={<Boxes size={26} />}>
                  No sessions yet — create one on the left to get started.
                </Empty>
              ) : (
                <div className="-mx-1 overflow-x-auto">
                  <table className="w-full text-left text-sm">
                    <thead>
                      <tr className="border-b hairline text-[11px] uppercase tracking-[0.1em] text-zinc-500">
                        <th className="px-2 py-2.5 font-medium">Task</th>
                        <th className="px-2 py-2.5 font-medium">Agent</th>
                        <th className="px-2 py-2.5 font-medium">Status</th>
                        <th className="px-2 py-2.5 font-medium">Created</th>
                      </tr>
                    </thead>
                    <tbody>
                      {sessions.map((s) => (
                        <tr
                          key={s.id}
                          className="group border-b border-white/[0.04] transition-colors last:border-0 hover:bg-white/[0.03]"
                        >
                          <td className="px-2 py-2.5">
                            <Link
                              href={`/sessions/${s.id}`}
                              className="flex items-center gap-2"
                              title={s.task}
                            >
                              <StatusDot status={s.status} />
                              <span className="block max-w-md truncate text-zinc-100 transition-colors group-hover:text-accent-soft">
                                {s.task}
                              </span>
                              <ArrowUpRight
                                size={13}
                                className="shrink-0 text-zinc-600 opacity-0 transition-opacity group-hover:opacity-100"
                              />
                            </Link>
                            <span className="pl-4 font-mono text-[11px] text-zinc-600">
                              {shortId(s.id)}
                            </span>
                          </td>
                          <td className="px-2 py-2.5 text-zinc-400">{s.agent_type}</td>
                          <td className="px-2 py-2.5">
                            <Badge value={s.status} />
                          </td>
                          <td className="px-2 py-2.5 text-zinc-500">{timeAgo(s.created_at)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </Card>
          </div>
        </div>
      </Reveal>
    </PageShell>
  );
}
