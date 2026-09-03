"use client";

/**
 * Artifacts — the code and mini-apps agents build (v1.95.0; gallery v1.96.0).
 *
 * This page used to list the artifact STORE, which in practice holds only
 * generated media (images/video/audio). It requested each one as text, so
 * selecting a 65 MB video shipped ~155 MB of replacement characters into a
 * wrapping <pre> and froze the tab on the spinner. Generated media has a real
 * home — the Creative gallery — so this page now shows what had no home at all:
 * the scripts agents write with run_code, which used to die with the session
 * workspace. Browse them as a GALLERY headlined by each script's use case,
 * read the source, and run them again.
 */

import { useState } from "react";
import {
  ArrowLeft,
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
  const [removeError, setRemoveError] = useState<string | null>(null);

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
      // BOTH views hold now-stale metadata: the tile's run count/exit status and
      // THIS card's "last run" line. Reloading only the list left the detail
      // header showing the previous run's time and exit code next to fresh
      // output — quietly wrong, and exactly the kind of thing that erodes trust.
      list.reload();
      detail.reload();
    } catch (e) {
      setRunError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setRunning(false);
    }
  }

  async function remove(id: string) {
    // v1.226.0: this was the one truly uncaught mutation on the dashboard — a
    // failed delete surfaced as an unhandled rejection and NOTHING on screen.
    setRemoveError(null);
    try {
      await del(`/code-artifacts/${encodeURIComponent(id)}`);
    } catch (e) {
      setRemoveError(e instanceof ApiError ? e.message : String(e));
      return;
    }
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

      {/* GALLERY — one tile per script, headlined by its USE CASE. The name is
          often `run_<epoch>`, so leading with purpose is what makes this
          browsable at a glance. */}
      {!selected && (
        <Reveal>
          {list.loading && !list.data ? (
            <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
              {Array.from({ length: 6 }).map((_, i) => (
                <div
                  key={i}
                  className="h-36 animate-pulse rounded-2xl border border-white/[0.06] bg-white/[0.02]"
                />
              ))}
            </div>
          ) : items.length === 0 ? (
            <Card title="Scripts · 0" icon={<Code2 size={15} />}>
              <Empty icon={<Code2 size={22} />}>
                No saved scripts yet. When an agent uses <code>run_code</code> to solve
                something, it lands here automatically — with what it was for.
              </Empty>
            </Card>
          ) : (
            <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
              {items.map((a) => (
                <button
                  key={a.id}
                  onClick={() => select(a.id)}
                  className="group flex h-full flex-col rounded-2xl border border-white/[0.06] bg-white/[0.02] p-4 text-left transition-colors hover:border-accent/30 hover:bg-accent/[0.05]"
                >
                  <div className="flex items-start justify-between gap-2">
                    <span className="inline-flex items-center gap-1.5 rounded-md bg-white/[0.04] px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wide text-zinc-400">
                      <Code2 size={11} /> {a.language}
                    </span>
                    <RunStatus code={a.last_exit_code} />
                  </div>

                  {/* THE USE CASE — the reason this tile exists. */}
                  <p className="mt-3 line-clamp-3 text-sm leading-relaxed text-zinc-200">
                    {a.description || (
                      <span className="italic text-zinc-500">
                        No stated purpose — open to read the code.
                      </span>
                    )}
                  </p>

                  <div className="mt-auto pt-3">
                    <p className="truncate font-mono text-[11px] text-zinc-500">{a.name}</p>
                    <div className="mt-1.5 flex items-center gap-3 text-[11px] text-zinc-500">
                      <span className="inline-flex items-center gap-1">
                        <Play size={11} /> {a.run_count} run{a.run_count === 1 ? "" : "s"}
                      </span>
                      <span className="inline-flex items-center gap-1">
                        <Clock size={11} /> {when(a.last_run_at)}
                      </span>
                    </div>
                  </div>
                </button>
              ))}
            </div>
          )}

          {/* The media that used to (fail to) render here has a working home. */}
          <Link
            href="/creative"
            className="mt-4 flex items-center gap-2 rounded-xl border border-white/[0.06] px-3 py-2.5 text-xs text-zinc-400 transition-colors hover:border-white/10 hover:text-zinc-200"
          >
            <ImageIcon size={14} />
            Generated images, video and audio live in the Creative gallery →
          </Link>
        </Reveal>
      )}

      {selected && (
        <Reveal>
          <button
            onClick={() => {
              setSelected(null);
              setRun(null);
              setRunError(null);
            }}
            className="mb-4 inline-flex items-center gap-1.5 text-xs text-zinc-400 transition-colors hover:text-zinc-200"
          >
            <ArrowLeft size={14} /> All scripts
          </button>
          <div className="space-y-4">
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
              {removeError && (
                <div className="mb-3">
                  <ErrorNote>Could not delete this script: {removeError}</ErrorNote>
                </div>
              )}
              {detail.loading && !detail.data ? (
                <Spinner />
              ) : detail.data ? (
                <div className="space-y-3">
                  {/* The use case again, up top — the tile's headline carries
                      over so the detail view answers "what is this?" first. */}
                  <p className="text-sm leading-relaxed text-zinc-300">
                    {detail.data.description || (
                      <span className="italic text-zinc-500">
                        No stated purpose was recorded for this script.
                      </span>
                    )}
                  </p>
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
        </Reveal>
      )}
    </PageShell>
  );
}
