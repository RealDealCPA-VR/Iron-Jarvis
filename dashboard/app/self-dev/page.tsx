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
import { PageGrid } from "@/components/PageGrid";

/**
 * v1.314.0 (UX wave 2): where the page sends people to switch it on. The
 * Self-development switch lives in Settings' collapsed Advanced section, and
 * `?focus=advanced` opens that section (app/settings/page.tsx) — a link to
 * the top of Settings left the user hunting for a switch with another name.
 */
const SETTINGS_ADVANCED = "/settings?focus=advanced";

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
        <PageGrid cols={3}>
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
                    {/* v1.314.0: plain state words (were available / blocked /
                        disabled). */}
                    <Badge
                      value={data.available ? "Ready" : data.enabled ? "Not ready" : "Off"}
                      tone={data.available ? "green" : data.enabled ? "amber" : "slate"}
                    />
                  </div>

                  <div className="space-y-1">
                    <SectionLabel>{data.enabled ? "Repo it works on" : "Repo it would edit"}</SectionLabel>
                    <div className="break-all rounded-xl border border-white/[0.05] bg-white/[0.02] px-3 py-2 font-mono text-[11px] text-zinc-400">
                      {data.repo_root ?? "— not found —"}
                    </div>
                  </div>

                  {/* v1.314.0: the daemon's own reason string (a config-key
                      sentence) is a record, so it stays — ONCE, one click
                      down. It used to print here AND as the action card's
                      first line. */}
                  <details className="text-[12px] text-zinc-500">
                    <summary className="cursor-pointer select-none text-zinc-400 hover:text-zinc-200">
                      Technical details
                    </summary>
                    <p className="mt-1.5 break-words font-mono text-[11px] text-zinc-400">{data.reason}</p>
                  </details>
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
              <NotReadyCard data={data ?? null} />
            )}
          </div>
        </PageGrid>
      </Reveal>
    </PageShell>
  );
}

/**
 * v1.314.0 (UX wave 2): why Maintainer cannot start, in words. Three cases,
 * each with its own title — "Not available yet" read as "coming in a future
 * release" when the feature is simply switched off:
 *  - switched off → "Turned off", the switch's name and where it is, and that
 *    it applies after a restart (the setting is restart:true);
 *  - on, but the repo was not found → the repo-root setting;
 *  - no status from the daemon → say so (the offline notice is above).
 * The config keys stay, AFTER the sentence, for whoever edits config.toml.
 */
function NotReadyCard({ data }: { data: SelfDevStatus | null }) {
  if (!data) {
    return (
      <Card title="Waiting for Iron Jarvis" icon={<FolderGit2 size={15} />}>
        <p className="text-sm text-zinc-400">
          Self-development needs Iron Jarvis to report its status before it can start.
        </p>
      </Card>
    );
  }
  const off = !data.enabled;
  const noRepo = data.enabled && !data.repo_root;
  return (
    <Card
      title={off ? "Turned off" : noRepo ? "Can’t find the Iron Jarvis code" : "Not ready"}
      icon={<FolderGit2 size={15} />}
    >
      <div className="space-y-4">
        <p className="text-sm leading-relaxed text-zinc-300">
          {off ? (
            <>
              Self-development is off. Turn on <strong>Self-development</strong> in Settings →
              Advanced, then restart Iron Jarvis (Settings → Maintenance → Restart daemon) so it
              takes effect.
            </>
          ) : noRepo ? (
            <>
              Self-development is on, but Iron Jarvis can’t find its own source code. Set{" "}
              <strong>Self-development repo root</strong> in Settings → Advanced to a copy of the
              Iron Jarvis repo, then restart Iron Jarvis so it takes effect.
            </>
          ) : (
            <>
              Self-development is on but can’t start yet. The reason is under Status → Technical
              details.
            </>
          )}
        </p>
        {/* The shared warning notice (v1.313.0): its border, tint, body ink
            and code chip are themed for the light Marks. v1.314.0: the
            config key comes AFTER the sentence, for whoever edits
            config.toml by hand. */}
        {(off || noRepo) && (
          <div className="notice-warn notice-warn-body rounded-xl border px-4 py-3 text-[12px] leading-relaxed">
            In config.toml this is{" "}
            <code className="notice-warn-code rounded px-1.5 py-0.5 font-mono text-[11px]">
              {off ? "self_dev_enabled" : "self_dev_root"}
            </code>
            .
          </div>
        )}
        <Link
          href={SETTINGS_ADVANCED}
          className="inline-flex items-center gap-1.5 rounded-xl border border-accent/30 bg-accent/[0.08] px-3 py-2 text-xs font-medium text-accent-soft transition-colors hover:bg-accent/[0.14]"
        >
          <SettingsIcon size={14} /> Open Settings → Advanced <ArrowRight size={13} />
        </Link>
      </div>
    </Card>
  );
}
