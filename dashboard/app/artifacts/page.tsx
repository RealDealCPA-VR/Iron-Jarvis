"use client";

/**
 * Artifacts — the code and mini-apps agents build (v1.95.0).
 *
 * This page used to list the artifact STORE, which in practice holds only
 * generated media (images/video/audio). It requested each one as text, so
 * selecting a 65 MB video shipped ~155 MB of replacement characters into a
 * wrapping <pre> and froze the tab on the spinner. Generated media has a real
 * home — the Creative gallery — so this page now shows what had no home at all:
 * the scripts agents write with run_code, which used to die with the session
 * workspace. Browse them, read the source, and run them again.
 */

import { useState } from "react";
import {
  Code2,
  Play,
  Terminal,
  Clock,
  CheckCircle2,
  XCircle,
  Image as ImageIcon,
} from "lucide-react";
import Link from "next/link";
import { useApi } from "@/lib/useApi";
import { post, del, ApiError } from "@/lib/api";
import {
  Card,
  Spinner,
  OfflineHint,
  Empty,
  SkeletonRows,
  Badge,
  ConfirmButton,
  ErrorNote,
} from "@/components/ui";
import { PageHeader } from "@/components/PageHeader";
import { PageShell, Reveal } from "@/components/motion";

interface CodeArtifact {
  id: string;
  name: string;
  language: string;
  description: string;
  origin: string;
  session_id: string | null;
  project_id: string | null;
  run_count: number;
  last_exit_code: number | null;
  last_run_at: string | null;
  updated_at: string | null;
  size: number;
}

interface CodeArtifactDetail extends CodeArtifact {
  source: string;
  last_output: string;
}

interface RunResult {
  ok: boolean;
  exit_code: number;
  output: string;
  cwd: string;
  artifact: CodeArtifactDetail;
}

/** Exit status as a glanceable pill. `null` = saved but never run. */
function RunStatus({ code }: { code: number | null }) {
  if (code === null) return <Badge value="never run" tone="slate" />;
  if (code === 0)
    return (
      <span className="inline-flex items-center gap-1 text-xs text-emerald-400">
        <CheckCircle2 size={13} /> exit 0
      </span>
    );
  return (
    <span className="inline-flex items-center gap-1 text-xs text-rose-400">
      <XCircle size={13} /> exit {code}
    </span>
  );
}

function when(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "—" : d.toLocaleString();
}

export default function ArtifactsPage() {
  const list = useApi<{ artifacts: CodeArtifact[]; count: number }>("/code-artifacts");
  const [selected, setSelected] = useState<string | null>(null);
  const detail = useApi<CodeArtifactDetail>(
    selected ? `/code-artifacts/${encodeURIComponent(selected)}` : null,
    [selected],
  );

  const [running, setRunning] = useState(false);
  const [run, setRun] = useState<RunResult | null>(null);
  const [runError, setRunError] = useState<string | null>(null);

  const offline = list.error && list.error.status === 0;
  const items = list.data?.artifacts ?? [];

  function select(id: string) {
    setSelected(id);
    setRun(null); // a previous script's output must never sit under a new one
    setRunError(null);
  }

  async function runIt(id: string) {
    setRunning(true);
    setRunError(null);
    try {
      setRun(await post<RunResult>(`/code-artifacts/${encodeURIComponent(id)}/run`, {}));
      list.reload(); // run_count / last exit changed
    } catch (e) {
      setRunError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setRunning(false);
    }
  }

  async function remove(id: string) {
    await del(`/code-artifacts/${encodeURIComponent(id)}`);
    if (selected === id) {
      setSelected(null);
      setRun(null);
    }
    list.reload();
  }

  return (
    <PageShell>
      <Reveal>
        <PageHeader
          title="Artifacts"
          subtitle="The code agents write to get things done — kept, readable, and runnable again long after the session that produced it is gone."
        />
      </Reveal>
      {offline && (
        <Reveal>
          <OfflineHint />
        </Reveal>
      )}

      <Reveal>
        <div className="grid gap-6 lg:grid-cols-3">
          <div className="lg:col-span-1 space-y-4">
            <Card title={`Scripts · ${items.length}`} icon={<Code2 size={15} />}>
              {list.loading && !list.data ? (
                <SkeletonRows rows={5} />
              ) : items.length === 0 ? (
                <Empty icon={<Code2 size={22} />}>
                  No saved scripts yet. When an agent uses <code>run_code</code> to solve
                  something, it lands here automatically.
                </Empty>
              ) : (
                <ul className="space-y-1">
                  {items.map((a) => (
                    <li key={a.id}>
                      <button
                        onClick={() => select(a.id)}
                        className={`w-full rounded-xl border px-3 py-2.5 text-left transition-colors ${
                          selected === a.id
                            ? "border-accent/30 bg-accent/[0.08]"
                            : "border-transparent hover:border-white/10 hover:bg-white/[0.04]"
                        }`}
                      >
                        <div className="flex items-center justify-between gap-2">
                          <span className="truncate text-sm text-zinc-200">{a.name}</span>
                          <span className="shrink-0 font-mono text-[10px] uppercase text-zinc-500">
                            {a.language}
                          </span>
                        </div>
                        <div className="mt-1 flex items-center gap-3 text-[11px] text-zinc-500">
                          <RunStatus code={a.last_exit_code} />
                          <span>
                            {a.run_count} run{a.run_count === 1 ? "" : "s"}
                          </span>
                        </div>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </Card>

            {/* The media that used to (fail to) render here has a working home. */}
            <Link
              href="/creative"
              className="flex items-center gap-2 rounded-xl border border-white/[0.06] px-3 py-2.5 text-xs text-zinc-400 transition-colors hover:border-white/10 hover:text-zinc-200"
            >
              <ImageIcon size={14} />
              Generated images, video and audio live in the Creative gallery →
            </Link>
          </div>

          <div className="lg:col-span-2 space-y-4">
            <Card
              title={detail.data?.name || "Script"}
              icon={<Code2 size={15} />}
              right={
                detail.data ? (
                  <div className="flex items-center gap-3">
                    <span className="font-mono text-xs text-zinc-500">
                      {detail.data.language} · {detail.data.size} bytes
                    </span>
                    <button
                      onClick={() => runIt(detail.data!.id)}
                      disabled={running}
                      className="inline-flex items-center gap-1.5 rounded-lg border border-accent/30 bg-accent/[0.08] px-2.5 py-1 text-xs font-medium text-accent-soft transition-colors hover:bg-accent/[0.14] disabled:opacity-50"
                    >
                      <Play size={13} />
                      {running ? "Running…" : "Run again"}
                    </button>
                    <ConfirmButton
                      label="Delete"
                      onConfirm={() => remove(detail.data!.id)}
                      title="Forget this script (files it created are left alone)"
                    />
                  </div>
                ) : null
              }
            >
              {!selected ? (
                <Empty icon={<Code2 size={22} />}>
                  Select a script to read its source and run it again.
                </Empty>
              ) : detail.loading && !detail.data ? (
                <Spinner />
              ) : detail.data ? (
                <div className="space-y-3">
                  {detail.data.description && (
                    <p className="text-sm text-zinc-400">{detail.data.description}</p>
                  )}
                  <div className="flex flex-wrap items-center gap-3 text-[11px] text-zinc-500">
                    <span className="inline-flex items-center gap-1">
                      <Clock size={12} /> last run {when(detail.data.last_run_at)}
                    </span>
                    <RunStatus code={detail.data.last_exit_code} />
                    <Badge value={detail.data.origin} tone="slate" />
                  </div>
                  <pre className="max-h-[45vh] overflow-auto rounded-xl border border-white/[0.06] bg-ink-950 p-4 text-xs leading-relaxed text-zinc-300">
                    {detail.data.source}
                  </pre>
                </div>
              ) : (
                <Empty>Could not load this script.</Empty>
              )}
            </Card>

            {(run || runError || (detail.data?.last_output && !run)) && (
              <Card title="Output" icon={<Terminal size={15} />}>
                {runError ? (
                  <ErrorNote>{runError}</ErrorNote>
                ) : (
                  <>
                    {run && (
                      <div className="mb-2 flex items-center gap-3 text-xs">
                        <RunStatus code={run.exit_code} />
                        <span className="truncate font-mono text-[11px] text-zinc-500">
                          {run.cwd}
                        </span>
                      </div>
                    )}
                    {!run && (
                      <p className="mb-2 text-[11px] text-zinc-500">
                        From the previous run — press Run again for fresh output.
                      </p>
                    )}
                    <pre className="max-h-[35vh] overflow-auto whitespace-pre-wrap rounded-xl border border-white/[0.06] bg-ink-950 p-4 text-xs leading-relaxed text-zinc-300">
                      {(run ? run.output : detail.data?.last_output) || "(no output)"}
                    </pre>
                  </>
                )}
              </Card>
            )}
          </div>
        </div>
      </Reveal>
    </PageShell>
  );
}
