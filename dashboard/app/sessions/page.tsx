"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  Boxes,
  Plus,
  ArrowUpRight,
  Search,
  ChevronUp,
  ChevronDown,
  Square,
} from "lucide-react";
import { usePolledApi } from "@/lib/useApi";
import { post, del, ApiError } from "@/lib/api";
import type { SessionView } from "@/lib/types";
import {
  Card,
  Badge,
  StatusDot,
  OfflineHint,
  Empty,
  MockChip,
  SkeletonRows,
  ConfirmButton,
  SuccessNote,
  ErrorNote,
  LoaderInline,
} from "@/components/ui";
import { PageHeader } from "@/components/PageHeader";
import { NewSessionForm } from "@/components/NewSessionForm";
import { PageShell, Reveal } from "@/components/motion";
import { timeAgo, shortId } from "@/lib/format";

const ACTIVE = new Set(["active", "running", "pending"]);

export default function SessionsPage() {
  const { data, error, loading, reload } = usePolledApi<{
    sessions: SessionView[];
  }>("/sessions", 4000);

  const offline = error && error.status === 0;
  const sessions = useMemo(() => data?.sessions ?? [], [data]);

  // Toolbar state (all client-side over the already-fetched list).
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [agentFilter, setAgentFilter] = useState("");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");

  // Row + maintenance action state.
  const [stoppingId, setStoppingId] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  // Auto-dismiss the maintenance "toast".
  useEffect(() => {
    if (!notice) return;
    const t = setTimeout(() => setNotice(null), 5000);
    return () => clearTimeout(t);
  }, [notice]);

  const statusOptions = useMemo(
    () => Array.from(new Set(sessions.map((s) => s.status))).sort(),
    [sessions],
  );
  const agentOptions = useMemo(
    () => Array.from(new Set(sessions.map((s) => s.agent_type))).sort(),
    [sessions],
  );

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    let rows = sessions;
    if (q)
      rows = rows.filter(
        (s) => s.task.toLowerCase().includes(q) || s.id.toLowerCase().includes(q),
      );
    if (statusFilter) rows = rows.filter((s) => s.status === statusFilter);
    if (agentFilter) rows = rows.filter((s) => s.agent_type === agentFilter);
    return [...rows].sort((a, b) => {
      const da = new Date(a.created_at).getTime();
      const db = new Date(b.created_at).getTime();
      return sortDir === "asc" ? da - db : db - da;
    });
  }, [sessions, query, statusFilter, agentFilter, sortDir]);

  async function stopSession(id: string) {
    setStoppingId(id);
    setActionError(null);
    try {
      await post(`/sessions/${id}/cancel`);
      reload();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setStoppingId(null);
    }
  }

  async function deleteSession(id: string) {
    setActionError(null);
    try {
      await del(`/sessions/${id}`);
      reload();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : String(err));
    }
  }

  async function pruneWorktrees() {
    setActionError(null);
    setNotice(null);
    try {
      const res = await post<{ pruned: string[] }>("/worktrees/prune");
      const n = res?.pruned?.length ?? 0;
      setNotice(
        n === 0
          ? "No orphaned worktrees to prune."
          : `Pruned ${n} orphaned worktree${n === 1 ? "" : "s"}.`,
      );
      reload();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : String(err));
    }
  }

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
              right={
                <ConfirmButton
                  label="Prune orphaned worktrees"
                  confirmLabel="Prune now?"
                  onConfirm={pruneWorktrees}
                  title="Garbage-collect worktrees left behind by failed/missing sessions"
                  className="!text-zinc-400 hover:!text-accent-soft"
                />
              }
            >
              {/* Toolbar: search + filters */}
              {sessions.length > 0 && (
                <div className="mb-3 flex flex-wrap items-center gap-2">
                  <div className="relative min-w-[180px] flex-1">
                    <Search
                      size={14}
                      className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-zinc-600"
                    />
                    <input
                      value={query}
                      onChange={(e) => setQuery(e.target.value)}
                      placeholder="Search task or id…"
                      className="w-full rounded-lg border border-white/[0.08] bg-ink-900/80 py-1.5 pl-8 pr-3 text-sm text-zinc-100 outline-none transition-colors placeholder:text-zinc-600 focus:border-accent/60"
                    />
                  </div>
                  <select
                    aria-label="Filter by status"
                    value={statusFilter}
                    onChange={(e) => setStatusFilter(e.target.value)}
                    className="rounded-lg border border-white/[0.08] bg-ink-900/80 px-2.5 py-1.5 text-sm text-zinc-300 outline-none focus:border-accent/60"
                  >
                    <option value="">All statuses</option>
                    {statusOptions.map((s) => (
                      <option key={s} value={s}>
                        {s}
                      </option>
                    ))}
                  </select>
                  <select
                    aria-label="Filter by agent"
                    value={agentFilter}
                    onChange={(e) => setAgentFilter(e.target.value)}
                    className="rounded-lg border border-white/[0.08] bg-ink-900/80 px-2.5 py-1.5 text-sm text-zinc-300 outline-none focus:border-accent/60"
                  >
                    <option value="">All agents</option>
                    {agentOptions.map((a) => (
                      <option key={a} value={a}>
                        {a}
                      </option>
                    ))}
                  </select>
                </div>
              )}

              {notice && (
                <div className="mb-3">
                  <SuccessNote>{notice}</SuccessNote>
                </div>
              )}
              {actionError && (
                <div className="mb-3">
                  <ErrorNote>{actionError}</ErrorNote>
                </div>
              )}

              {loading && !data ? (
                <SkeletonRows rows={6} />
              ) : sessions.length === 0 ? (
                <Empty icon={<Boxes size={26} />}>
                  No sessions yet — create one on the left to get started.
                </Empty>
              ) : visible.length === 0 ? (
                <Empty icon={<Search size={24} />}>
                  No sessions match your filters.
                </Empty>
              ) : (
                <div className="-mx-1 overflow-x-auto">
                  <table className="w-full text-left text-sm">
                    <thead>
                      <tr className="border-b hairline text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                        <th className="px-2 py-2.5 font-medium">Task</th>
                        <th className="px-2 py-2.5 font-medium">Agent</th>
                        <th className="px-2 py-2.5 font-medium">Status</th>
                        <th className="px-2 py-2.5 font-medium">
                          <button
                            type="button"
                            onClick={() =>
                              setSortDir((d) => (d === "asc" ? "desc" : "asc"))
                            }
                            className="inline-flex items-center gap-1 uppercase tracking-[0.1em] transition-colors hover:text-zinc-300"
                            title="Sort by creation time"
                          >
                            Created
                            {sortDir === "asc" ? (
                              <ChevronUp size={12} />
                            ) : (
                              <ChevronDown size={12} />
                            )}
                          </button>
                        </th>
                        <th className="px-2 py-2.5 text-right font-medium">Actions</th>
                      </tr>
                    </thead>
                    <tbody>
                      {visible.map((s) => {
                        const active = ACTIVE.has(s.status.toLowerCase());
                        return (
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
                              <span className="flex items-center gap-2 pl-4">
                                <span className="font-mono text-[11px] text-zinc-600">
                                  {shortId(s.id)}
                                </span>
                                {s.provider === "mock" && <MockChip />}
                              </span>
                            </td>
                            <td className="px-2 py-2.5 text-zinc-400">{s.agent_type}</td>
                            <td className="px-2 py-2.5">
                              <Badge value={s.status} />
                            </td>
                            <td className="px-2 py-2.5 text-zinc-500">
                              {timeAgo(s.created_at)}
                            </td>
                            <td className="px-2 py-2.5">
                              <div className="flex items-center justify-end gap-1.5">
                                {active && (
                                  <button
                                    type="button"
                                    onClick={() => stopSession(s.id)}
                                    disabled={stoppingId === s.id}
                                    title="Stop this running session"
                                    className="inline-flex items-center gap-1.5 rounded-lg border border-white/10 px-2.5 py-1 text-xs font-medium text-zinc-400 transition-colors hover:border-amber-500/40 hover:text-amber-300 disabled:opacity-50"
                                  >
                                    {stoppingId === s.id ? (
                                      <LoaderInline label="Stopping…" />
                                    ) : (
                                      <>
                                        <Square size={12} /> Stop
                                      </>
                                    )}
                                  </button>
                                )}
                                <ConfirmButton onConfirm={() => deleteSession(s.id)} />
                              </div>
                            </td>
                          </tr>
                        );
                      })}
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
