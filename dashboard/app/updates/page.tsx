"use client";

import { useState } from "react";
import {
  DownloadCloud,
  GitCommitHorizontal,
  RefreshCw,
  Rocket,
  TriangleAlert,
  Terminal,
} from "lucide-react";
import { post, ApiError } from "@/lib/api";
import { useApi } from "@/lib/useApi";
import {
  Card,
  StatusDot,
  Badge,
  OfflineHint,
  SkeletonRows,
  ErrorNote,
  SuccessNote,
  SectionLabel,
  LoaderInline,
} from "@/components/ui";
import { PageHeader } from "@/components/PageHeader";
import { PageShell, Reveal } from "@/components/motion";

interface UpdateStatus {
  available: boolean;
  behind?: number;
  current?: string | null;
  remote?: string | null;
  branch?: string | null;
  clean?: boolean;
  reason?: string;
}

interface ApplyLogEntry {
  step: string;
  cmd: string;
  returncode: number;
  ok: boolean;
  stdout: string;
  stderr: string;
}

interface ApplyResult {
  ok: boolean;
  reason?: string;
  restart_required?: boolean;
  log?: ApplyLogEntry[];
}

/** "not a source checkout" is reported as available:false with this reason text. */
function isSourceCheckout(data: UpdateStatus | null): boolean {
  return !!data && !(data.reason ?? "").toLowerCase().includes("not a source checkout");
}

function ShaPill({ label, sha }: { label: string; sha?: string | null }) {
  return (
    <div className="space-y-1">
      <SectionLabel>{label}</SectionLabel>
      <div className="inline-flex items-center gap-1.5 rounded-lg border border-white/[0.06] bg-white/[0.02] px-2.5 py-1 font-mono text-[12px] text-zinc-300">
        <GitCommitHorizontal size={13} className="text-accent-soft/70" />
        {sha ?? "—"}
      </div>
    </div>
  );
}

export default function UpdatesPage() {
  const { data, error, loading, reload } = useApi<UpdateStatus>("/update/check");
  const offline = error && error.status === 0;

  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<ApplyResult | null>(null);
  const [applyError, setApplyError] = useState<string | null>(null);

  const sourceCheckout = isSourceCheckout(data);
  const available = !!data?.available;

  async function applyUpdate() {
    setBusy(true);
    setApplyError(null);
    setResult(null);
    try {
      const res = await post<ApplyResult>("/update/apply", { build_dashboard: true });
      setResult(res);
    } catch (err) {
      setApplyError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <PageShell>
      <Reveal>
        <PageHeader
          title="Updates"
          subtitle="Check for and apply updates pushed to the Iron Jarvis repo. Applying pulls the new source (git), re-syncs Python deps, and rebuilds the dashboard — then you restart to load it."
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
            <Card
              title="Status"
              icon={<DownloadCloud size={15} />}
              right={
                <button
                  type="button"
                  onClick={reload}
                  className="inline-flex items-center gap-1.5 rounded-lg border border-white/10 px-2 py-1 text-[11px] text-zinc-400 transition-colors hover:border-accent/30 hover:text-accent-soft"
                  title="Re-check"
                >
                  <RefreshCw size={12} /> Re-check
                </button>
              }
            >
              {loading && !data ? (
                <SkeletonRows rows={3} />
              ) : data ? (
                !sourceCheckout ? (
                  <div className="space-y-3">
                    <div className="flex items-center gap-2 text-sm text-zinc-300">
                      <StatusDot status="idle" /> Not a source checkout
                    </div>
                    <p className="text-[13px] leading-relaxed text-zinc-500">
                      Iron Jarvis is running from an installed package, not a git
                      checkout — there&apos;s nothing to pull. Run it from a clone of
                      the repo (uv) to enable self-update.
                    </p>
                  </div>
                ) : (
                  <div className="space-y-4">
                    <div className="flex items-center justify-between">
                      <span className="flex items-center gap-2 text-sm text-zinc-300">
                        <StatusDot status={available ? "pending" : "ok"} />
                        Repository
                      </span>
                      <Badge
                        value={
                          available
                            ? `${data.behind ?? 0} behind`
                            : data.clean === false
                              ? "dirty"
                              : "up to date"
                        }
                        tone={available ? "amber" : data.clean === false ? "red" : "green"}
                      />
                    </div>

                    <div className="flex flex-wrap gap-x-6 gap-y-3">
                      <ShaPill label="Current" sha={data.current} />
                      <ShaPill label="Remote" sha={data.remote} />
                    </div>

                    <div className="space-y-1">
                      <SectionLabel>Branch</SectionLabel>
                      <div className="font-mono text-[12px] text-zinc-400">
                        {data.branch ?? "—"}
                      </div>
                    </div>

                    <div className="space-y-1">
                      <SectionLabel>Detail</SectionLabel>
                      <p className="text-sm text-zinc-400">{data.reason}</p>
                    </div>
                  </div>
                )
              ) : error ? (
                <ErrorNote>{error.message}</ErrorNote>
              ) : (
                <p className="text-sm text-zinc-500">Status unavailable.</p>
              )}
            </Card>
          </div>

          {/* Action + log */}
          <div className="lg:col-span-2 space-y-6">
            <Card title="Apply update" icon={<Rocket size={15} />}>
              {!sourceCheckout && data ? (
                <p className="text-sm text-zinc-500">
                  Nothing to apply — this instance isn&apos;t a git checkout.
                </p>
              ) : (
                <div className="space-y-4">
                  <p className="flex items-start gap-2 text-[13px] leading-relaxed text-zinc-400">
                    <TriangleAlert size={15} className="mt-0.5 shrink-0 text-amber-300/80" />
                    Applying runs{" "}
                    <code className="rounded bg-black/40 px-1 py-px font-mono text-[11px] text-zinc-300">
                      git pull --ff-only
                    </code>{" "}
                    →{" "}
                    <code className="rounded bg-black/40 px-1 py-px font-mono text-[11px] text-zinc-300">
                      uv sync
                    </code>{" "}
                    →{" "}
                    <code className="rounded bg-black/40 px-1 py-px font-mono text-[11px] text-zinc-300">
                      pnpm build
                    </code>
                    . It refuses if the working tree has uncommitted changes.
                  </p>

                  <button
                    type="button"
                    onClick={applyUpdate}
                    disabled={busy || !available}
                    className="btn-accent w-full"
                    title={
                      available
                        ? "Pull and rebuild"
                        : "No update available (or the tree is dirty)"
                    }
                  >
                    {busy ? (
                      <LoaderInline label="Updating… (pull + sync + build)" />
                    ) : (
                      <>
                        <DownloadCloud size={14} />{" "}
                        {available ? `Apply update (${data?.behind ?? 0} commits)` : "Up to date"}
                      </>
                    )}
                  </button>

                  {applyError && <ErrorNote>{applyError}</ErrorNote>}

                  {result && (
                    <div className="space-y-3">
                      {result.ok ? (
                        <SuccessNote>{result.reason ?? "Update complete."}</SuccessNote>
                      ) : (
                        <ErrorNote>{result.reason ?? "Update failed."}</ErrorNote>
                      )}

                      {result.restart_required && (
                        <div className="flex items-start gap-2.5 rounded-xl border border-amber-500/25 bg-amber-500/[0.07] px-3.5 py-3 text-[13px] text-amber-100/90">
                          <RefreshCw size={15} className="mt-0.5 shrink-0 text-amber-300" />
                          <div>
                            <div className="font-semibold text-amber-200">Restart required.</div>
                            <div className="mt-0.5 text-amber-100/70">
                              The files on disk are updated, but the daemon (and the
                              dashboard you&apos;re viewing) are still running the old
                              code. Restart{" "}
                              <code className="rounded bg-black/40 px-1 py-px font-mono text-[11px]">
                                ironjarvis serve
                              </code>{" "}
                              (and the dashboard) to load it.
                            </div>
                          </div>
                        </div>
                      )}

                      {result.log && result.log.length > 0 && (
                        <div className="space-y-1.5">
                          <SectionLabel>Build log</SectionLabel>
                          <div className="space-y-2 rounded-xl border border-white/[0.06] bg-black/40 p-3 font-mono text-[11px]">
                            {result.log.map((e, i) => (
                              <div key={i} className="space-y-1">
                                <div
                                  className={`flex items-center gap-2 ${
                                    e.ok ? "text-emerald-300" : "text-rose-300"
                                  }`}
                                >
                                  <Terminal size={12} className="shrink-0" />
                                  <span className="font-semibold">{e.step}</span>
                                  <span className="text-zinc-600">rc={e.returncode}</span>
                                </div>
                                {(e.stdout || e.stderr) && (
                                  <pre className="whitespace-pre-wrap break-all pl-5 text-zinc-500">
                                    {(e.stderr || e.stdout).slice(0, 1200)}
                                  </pre>
                                )}
                              </div>
                            ))}
                          </div>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              )}
            </Card>
          </div>
        </div>
      </Reveal>
    </PageShell>
  );
}
