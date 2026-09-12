"use client";

/**
 * "This folder could be one summary sheet" (v1.251.0, C-04).
 *
 * THE DEFECT THIS FIXES IS NOT A MISSING TOOL. `batch_documents` has existed
 * since v1.133.0: it reads a whole folder one document at a time (so the
 * context window is never the limit), extracts structured facts per file, and
 * synthesizes ONE xlsx/docx from the extractions. Almost nobody has ever run
 * it, because `tools/autoselect` deliberately keeps it OUT of auto-arming on
 * cost grounds — about one model call per document — and leaves it "one click
 * away in the '+' menu". A click nobody knows about never happens.
 *
 * So this card is that click, made visible, with the two facts a person needs
 * BEFORE spending money: how many documents, and roughly how many model calls.
 * Pressing it runs the batch (the user's own decision, asked for explicitly),
 * and the files tick by as they are read — because the alternative is a silent
 * screen for several minutes, which is indistinguishable from a hang and is
 * this app's most-reported complaint.
 *
 * HONESTY RULES it inherits from the rest of chat:
 *  - the count is what the batch will REALLY process (the daemon answers it
 *    from the pipeline's own sweep), and files it will skip are named with the
 *    reason — a count that quietly omits files reads as complete;
 *  - the estimate is described as an estimate, never as a price;
 *  - the cap is stated when the folder is bigger than one run;
 *  - a failure says what happened; nothing claims a sheet that was not written.
 *
 * Style follows PreflightNote/DraftCard: the app's own accent, one compact
 * block above the composer, no new visual language.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { CheckCircle2, FileStack, Loader2, Sparkles, X } from "lucide-react";

import { ApiError, post } from "@/lib/api";
import type { IJEvent } from "@/lib/types";

/** One document's progress, as the daemon reports it. */
export interface BatchProgress {
  index: number;
  total: number;
  name: string;
  status: "extracted" | "cached" | "failed" | string;
}

export interface BatchPreview {
  folder: string;
  count: number;
  estimate_calls: number;
  cap: number;
  truncated: boolean;
  files: { name: string; path: string }[];
  skipped: { file: string; reason: string }[];
}

/**
 * The per-file progress carried by `batch.file_done` for THIS folder, newest
 * last. Reads the rolling event window (useEvents re-delivers it every render,
 * so this derives rather than accumulates — no id bookkeeping needed) and
 * filters by folder, so two conversations batching different folders never
 * show each other's files.
 */
export function batchProgressFor(events: IJEvent[], folder: string): BatchProgress[] {
  if (!folder) return [];
  const out: BatchProgress[] = [];
  const seen = new Set<number>();
  for (const e of events) {
    if (e.type !== "batch.file_done") continue;
    const p = e.payload || {};
    if (String(p.folder ?? "") !== folder) continue;
    const index = Number(p.index ?? 0);
    if (!index || seen.has(index)) continue;
    seen.add(index);
    out.push({
      index,
      total: Number(p.total ?? 0),
      name: String(p.name ?? ""),
      status: String(p.status ?? ""),
    });
  }
  return out.sort((a, b) => a.index - b.index);
}

/** "about 13 model calls" — plain words, and honest that it is a shape. */
function estimateWords(calls: number): string {
  return `about ${calls} model call${calls === 1 ? "" : "s"}`;
}

export function BatchSuggestCard({
  preview,
  events,
  instructions,
  workspaceDir,
  sessionId = "chat",
  onDone,
  onDismiss,
}: {
  /** The daemon's answer for this folder, or null to render nothing. */
  preview: BatchPreview | null;
  events: IJEvent[];
  /** What the sheet should cover — the user's own words when they typed some. */
  instructions?: string;
  /** Where the sheet lands: the conversation's folder (v1.244.0). */
  workspaceDir?: string | null;
  sessionId?: string;
  /** Called with the ABSOLUTE deliverable paths so they join the Files rail. */
  onDone?: (createdPaths: string[], report: Record<string, unknown>) => void;
  onDismiss?: () => void;
}) {
  const [running, setRunning] = useState(false);
  const [done, setDone] = useState<string[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const startedRef = useRef(false);

  const folder = preview?.folder ?? "";
  const progress = useMemo(() => batchProgressFor(events, folder), [events, folder]);
  const latest = progress.at(-1);
  const failedSoFar = progress.filter((p) => p.status === "failed").length;

  // A folder swap retires the previous run's state: the card must never show
  // one folder's progress under another folder's numbers.
  useEffect(() => {
    setRunning(false);
    setDone(null);
    setError(null);
    startedRef.current = false;
  }, [folder]);

  if (!preview || preview.count <= 0) return null;

  async function run() {
    if (startedRef.current) return; // one click, one batch
    startedRef.current = true;
    setRunning(true);
    setError(null);
    try {
      const res = await post<{
        output: string;
        report: Record<string, unknown>;
        created_paths: string[];
      }>("/documents/batch", {
        folder: preview!.folder,
        instructions: (instructions || "").trim(),
        output: "xlsx",
        max_files: preview!.cap,
        workspace_dir: workspaceDir || "",
        session_id: sessionId,
      });
      const made = res.created_paths || [];
      setDone(made);
      onDone?.(made, res.report || {});
    } catch (e) {
      // An honest failure, and the card stays so it can be retried.
      setError(e instanceof ApiError ? e.message : String(e));
      startedRef.current = false;
    } finally {
      setRunning(false);
    }
  }

  const noun = `${preview.count} document${preview.count === 1 ? "" : "s"}`;

  return (
    <div
      data-testid="batch-suggest-card"
      className="rounded-xl border border-accent/20 bg-accent/[0.04] px-3 py-2.5"
    >
      <div className="flex items-start gap-2.5">
        <FileStack size={15} className="mt-0.5 shrink-0 text-accent-soft" />
        <div className="min-w-0 flex-1">
          {done ? (
            <p className="text-[12.5px] leading-snug text-zinc-200">
              <CheckCircle2 size={13} className="mr-1 inline text-emerald-400" />
              {done.length > 0
                ? `Summary sheet made from ${noun} — it's in this chat's files.`
                : `Read ${noun}, but no sheet was written.`}
            </p>
          ) : (
            <>
              <p className="text-[12.5px] leading-snug text-zinc-200">
                This folder has {noun}. I can read them all and make one summary
                sheet — {estimateWords(preview.estimate_calls)}.
              </p>
              {preview.truncated && (
                <p className="mt-0.5 text-[11.5px] leading-snug text-amber-300">
                  It will do the first {preview.cap} this time; the rest are
                  listed as skipped so nothing goes missing quietly.
                </p>
              )}
              {preview.skipped.length > 0 && !preview.truncated && (
                <p
                  className="mt-0.5 line-clamp-2 text-[11.5px] leading-snug text-zinc-500"
                  title={preview.skipped
                    .map((s) => `${s.file.split(/[\\/]/).pop()}: ${s.reason}`)
                    .join("\n")}
                >
                  {preview.skipped.length} item
                  {preview.skipped.length === 1 ? "" : "s"} in the folder can&apos;t be
                  read as documents — they&apos;ll be named in the sheet, not skipped
                  silently.
                </p>
              )}
            </>
          )}

          {running && (
            <div className="mt-1.5" data-testid="batch-progress">
              <p className="flex items-center gap-1.5 text-[11.5px] text-zinc-400">
                <Loader2 size={12} className="animate-spin text-accent-soft" />
                {latest
                  ? `Reading ${latest.index} of ${latest.total || preview.count}: ${latest.name}`
                  : `Starting on ${noun}…`}
                {failedSoFar > 0 && (
                  <span className="text-amber-300">
                    · {failedSoFar} couldn&apos;t be read
                  </span>
                )}
              </p>
              <div
                className="mt-1 h-1 w-full overflow-hidden rounded-full bg-white/[0.06]"
                role="progressbar"
                aria-valuemin={0}
                aria-valuemax={latest?.total || preview.count}
                aria-valuenow={progress.length}
              >
                <div
                  className="h-full rounded-full bg-accent/70 transition-[width] duration-300"
                  style={{
                    width: `${Math.round(
                      (progress.length / (latest?.total || preview.count || 1)) * 100,
                    )}%`,
                  }}
                />
              </div>
            </div>
          )}

          {error && (
            <p className="mt-1.5 text-[11.5px] leading-snug text-rose-300">{error}</p>
          )}
        </div>

        {!running && !done && (
          <div className="flex shrink-0 items-center gap-1">
            <button
              type="button"
              onClick={() => void run()}
              className="btn-ghost px-2.5 py-1.5 text-[12px]"
            >
              <Sparkles size={13} /> Make the summary sheet
            </button>
            {onDismiss && (
              <button
                type="button"
                onClick={onDismiss}
                aria-label="Not now"
                title="Not now"
                className="grid h-6 w-6 place-items-center rounded-md text-zinc-500 transition-colors hover:bg-white/[0.06] hover:text-zinc-200"
              >
                <X size={12} />
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
