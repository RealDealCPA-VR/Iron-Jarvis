"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import {
  GitBranch,
  ShieldCheck,
  Play,
  FolderGit2,
  Settings as SettingsIcon,
  ArrowRight,
} from "lucide-react";
import { post, ApiError } from "@/lib/api";
import { useApi } from "@/lib/useApi";
import type { SessionView } from "@/lib/types";
import {
  Card,
  StatusDot,
  Badge,
  OfflineHint,
  SkeletonRows,
  ErrorNote,
  SectionLabel,
  LoaderInline,
} from "@/components/ui";
import { PageHeader } from "@/components/PageHeader";
import { PageShell, Reveal } from "@/components/motion";

interface SelfDevStatus {
  enabled: boolean;
  repo_root: string | null;
  available: boolean;
  reason: string;
}

export default function SelfDevPage() {
  const router = useRouter();
  const { data, error, loading } = useApi<SelfDevStatus>("/self-dev");
  const offline = error && error.status === 0;

  const [task, setTask] = useState("");
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  async function start(e: React.FormEvent) {
    e.preventDefault();
    if (!task.trim()) return;
    setBusy(true);
    setFormError(null);
    try {
      const session = await post<SessionView>("/sessions", {
        task: task.trim(),
        agent_type: "maintainer",
        self_dev: true,
        wait: false,
      });
      setTask("");
      if (session?.id) router.push(`/sessions/${session.id}`);
    } catch (err) {
      setFormError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <PageShell>
      <Reveal>
        <PageHeader
          title="Self-development"
          subtitle="Let Iron Jarvis improve its own source. A Maintainer agent works on a throwaway git worktree of this repo — every change is review-gated and never merges on its own."
        />
      </Reveal>

      {offline && (
        <Reveal>
          <OfflineHint />
        </Reveal>
      )}

      <Reveal>
        <div className="grid gap-6 lg:grid-cols-3">
          {/* Status */}
          <div className="lg:col-span-1">
            <Card title="Status" icon={<GitBranch size={15} />}>
              {loading && !data ? (
                <SkeletonRows rows={3} />
              ) : data ? (
                <div className="space-y-4">
                  <div className="flex items-center justify-between">
                    <span className="flex items-center gap-2 text-sm text-zinc-300">
                      <StatusDot status={data.available ? "ok" : data.enabled ? "pending" : "idle"} />
                      Self-development
                    </span>
                    <Badge
                      value={data.available ? "available" : data.enabled ? "blocked" : "disabled"}
                      tone={data.available ? "green" : data.enabled ? "amber" : "slate"}
                    />
                  </div>

                  <div className="space-y-1">
                    <SectionLabel>Repo root</SectionLabel>
                    <div className="break-all rounded-xl border border-white/[0.05] bg-white/[0.02] px-3 py-2 font-mono text-[11px] text-zinc-400">
                      {data.repo_root ?? "— not found —"}
                    </div>
                  </div>

                  <div className="space-y-1">
                    <SectionLabel>Reason</SectionLabel>
                    <p className="text-sm text-zinc-400">{data.reason}</p>
                  </div>
                </div>
              ) : (
                <p className="text-sm text-zinc-500">Status unavailable.</p>
              )}
            </Card>
          </div>

          {/* Action */}
          <div className="lg:col-span-2">
            {data?.available ? (
              <Card title="Start a Maintainer" icon={<FolderGit2 size={15} />}>
                <form onSubmit={start} className="space-y-3.5">
                  <div>
                    <label className="mb-1.5 block text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                      Task
                    </label>
                    <textarea
                      value={task}
                      onChange={(e) => setTask(e.target.value)}
                      rows={4}
                      placeholder="e.g. Fix the flaky test in tests/test_scheduler.py and tidy its imports."
                      className="field resize-y"
                    />
                  </div>
                  <p className="flex items-start gap-2 text-[11px] leading-relaxed text-zinc-500">
                    <ShieldCheck size={14} className="mt-0.5 shrink-0 text-accent-soft/80" />
                    The Maintainer edits and tests on an isolated worktree. Its changes land as a
                    review you approve on the{" "}
                    <Link href="/kanban" className="text-accent-soft hover:text-accent">
                      Kanban board
                    </Link>{" "}
                    — nothing is merged automatically.
                  </p>
                  <button
                    type="submit"
                    disabled={busy || !task.trim()}
                    className="btn-accent w-full"
                  >
                    {busy ? (
                      <LoaderInline label="Starting…" />
                    ) : (
                      <>
                        <Play size={14} /> Start Maintainer
                      </>
                    )}
                  </button>
                  {formError && <ErrorNote>{formError}</ErrorNote>}
                </form>
              </Card>
            ) : (
              <Card title="Not available yet" icon={<FolderGit2 size={15} />}>
                <div className="space-y-4">
                  <p className="text-sm text-zinc-400">
                    {data
                      ? data.reason
                      : "Self-development needs the daemon to report its status."}
                  </p>
                  <div className="rounded-xl border border-amber-500/20 bg-amber-500/[0.06] px-4 py-3 text-[13px] leading-relaxed text-amber-100/80">
                    To turn this on, enable{" "}
                    <code className="rounded bg-black/40 px-1.5 py-0.5 font-mono text-[11px] text-amber-100/90">
                      self_dev_enabled
                    </code>{" "}
                    {data && data.enabled && !data.repo_root ? (
                      <>
                        and point{" "}
                        <code className="rounded bg-black/40 px-1.5 py-0.5 font-mono text-[11px] text-amber-100/90">
                          self_dev_root
                        </code>{" "}
                        at a checkout of this repo
                      </>
                    ) : (
                      "in your configuration"
                    )}
                    . You can do that in{" "}
                    <Link href="/settings" className="font-medium text-accent-soft underline">
                      Settings
                    </Link>
                    .
                  </div>
                  <Link
                    href="/settings"
                    className="inline-flex items-center gap-1.5 rounded-xl border border-accent/30 bg-accent/[0.08] px-3 py-2 text-xs font-medium text-accent-soft transition-colors hover:bg-accent/[0.14]"
                  >
                    <SettingsIcon size={14} /> Open Settings <ArrowRight size={13} />
                  </Link>
                </div>
              </Card>
            )}
          </div>
        </div>
      </Reveal>
    </PageShell>
  );
}
