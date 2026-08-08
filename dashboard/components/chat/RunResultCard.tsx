"use client";

/**
 * What the agent ACTUALLY did (v1.149.0).
 *
 * The report: "agents must stop only describing what they intend to do."
 * Everything below comes from `GET /sessions/{id}/result`, which reads the tool
 * ledger and the undo journal — files created/changed are journaled mutations,
 * not sentences parsed out of the reply. So when a model writes "I've saved
 * that to notes.md" and no write was journaled, this card shows no file, and
 * the contradiction is visible instead of taken on trust.
 *
 * The failure lane is the other half: a failed run gets Retry / Revert / Cancel
 * rather than a red status and a shrug. Revert is offered ONLY when the ledger
 * says something is genuinely reversible.
 */

import { useState } from "react";
import {
  CheckCircle2,
  XCircle,
  FilePlus2,
  FilePen,
  Wrench,
  AlertTriangle,
  RotateCcw,
  Undo2,
  Ban,
  ChevronDown,
  ChevronRight,
} from "lucide-react";
import { post, ApiError } from "@/lib/api";

export interface RunResult {
  found: boolean;
  session_id: string;
  status: string;
  task: string;
  summary: string;
  steps: number;
  tools_used: { tool: string; count: number }[];
  tools_failed: { tool: string; count: number }[];
  files_created: string[];
  files_changed: string[];
  files_created_total?: number;
  files_changed_total?: number;
  errors: { tool: string; error: string }[];
  revertable: number;
  reverted?: number;
  duration_s: number | null;
}

const FAILED = new Set(["failed", "cancelled"]);

function Row({
  icon,
  label,
  items,
  total,
}: {
  icon: React.ReactNode;
  label: string;
  items: string[];
  total?: number;
}) {
  if (!items.length) return null;
  const hidden = Math.max(0, (total ?? items.length) - items.length);
  return (
    <div className="flex gap-2">
      <span className="mt-0.5 shrink-0 text-zinc-500">{icon}</span>
      <div className="min-w-0">
        <div className="text-[11px] uppercase tracking-[0.1em] text-zinc-500">
          {label} · {total ?? items.length}
        </div>
        <div className="mt-0.5 flex flex-wrap gap-x-2 gap-y-1">
          {items.map((f) => (
            <code
              key={f}
              className="max-w-full truncate rounded bg-white/[0.04] px-1.5 py-0.5 font-mono text-[11.5px] text-zinc-300"
            >
              {f}
            </code>
          ))}
          {hidden > 0 && (
            <span className="text-[11.5px] text-zinc-500">+{hidden} more</span>
          )}
        </div>
      </div>
    </div>
  );
}

export function RunResultCard({
  result,
  onRetry,
  onReverted,
}: {
  result: RunResult;
  /** Re-run the same task (POST /sessions/{id}/rerun happens upstream). */
  onRetry?: () => void;
  /** Called after a successful revert so the caller can refresh. */
  onReverted?: (summary: string) => void;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [open, setOpen] = useState(false);

  const failed = FAILED.has(result.status);
  const didNothing = !result.tools_used.length;
  const canRevert = result.revertable > 0;

  async function revert() {
    setBusy("revert");
    setErr(null);
    try {
      const r = await post<{
        reverted: { action_id: string }[];
        skipped: { reason: string }[];
        considered: number;
      }>(`/sessions/${result.session_id}/revert`, {});
      // Honest either way: a partial revert says how many, and WHY the rest
      // stayed (usually "the file changed since", which is a refusal to
      // clobber newer work, not a failure).
      const msg =
        r.skipped.length === 0
          ? `Reverted ${r.reverted.length} action${r.reverted.length === 1 ? "" : "s"}.`
          : `Reverted ${r.reverted.length} of ${r.considered}. ${r.skipped.length} left alone — ${r.skipped[0]?.reason ?? "not safely reversible"}`;
      setNote(msg);
      onReverted?.(msg);
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  async function cancel() {
    setBusy("cancel");
    setErr(null);
    try {
      await post(`/sessions/${result.session_id}/cancel`, {});
      setNote("Stopped.");
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  return (
    <div
      className={`ml-11 max-w-[640px] rounded-xl border ${
        failed ? "border-rose-500/25 bg-rose-500/[0.04]" : "border-white/[0.08] bg-white/[0.02]"
      }`}
    >
      <div className="flex items-start gap-2.5 border-b border-white/[0.05] px-3.5 py-2.5">
        <span className={`mt-0.5 shrink-0 ${failed ? "text-rose-400" : "text-emerald-400"}`}>
          {failed ? <XCircle size={15} /> : <CheckCircle2 size={15} />}
        </span>
        <div className="min-w-0 flex-1">
          <div className="text-[13px] font-medium text-zinc-200">
            {failed ? "Task failed" : didNothing ? "Finished — but nothing ran" : "Task complete"}
          </div>
          <div className="mt-0.5 text-[11.5px] text-zinc-500">
            {result.steps > 0 && `${result.steps} step${result.steps === 1 ? "" : "s"}`}
            {result.duration_s != null && ` · ${result.duration_s.toFixed(1)}s`}
            {result.tools_used.length > 0 &&
              ` · ${result.tools_used.reduce((n, t) => n + t.count, 0)} tool call${
                result.tools_used.reduce((n, t) => n + t.count, 0) === 1 ? "" : "s"
              }`}
          </div>
        </div>
      </div>

      <div className="space-y-2.5 px-3.5 py-3">
        {/* The honesty case this card exists for: a run that completed without
            touching a single tool is an agent that TALKED about the work. */}
        {didNothing && !failed && (
          <div className="rounded-lg border border-amber-500/25 bg-amber-500/[0.06] px-2.5 py-2 text-[12px] leading-relaxed text-amber-300">
            No tools ran and no files changed — this turn described the work
            rather than doing it. Ask again more concretely, or check that the
            tools it needed were available.
          </div>
        )}

        <Row
          icon={<FilePlus2 size={13} />}
          label="Files created"
          items={result.files_created}
          total={result.files_created_total}
        />
        <Row
          icon={<FilePen size={13} />}
          label="Files changed"
          items={result.files_changed}
          total={result.files_changed_total}
        />

        {result.tools_used.length > 0 && (
          <div className="flex gap-2">
            <span className="mt-0.5 shrink-0 text-zinc-500">
              <Wrench size={13} />
            </span>
            <div className="min-w-0">
              <div className="text-[11px] uppercase tracking-[0.1em] text-zinc-500">
                Tools
              </div>
              <div className="mt-0.5 flex flex-wrap gap-x-2 gap-y-1">
                {result.tools_used.map((t) => {
                  const bad = result.tools_failed.find((f) => f.tool === t.tool);
                  return (
                    <span
                      key={t.tool}
                      className={`rounded px-1.5 py-0.5 font-mono text-[11.5px] ${
                        bad
                          ? "bg-rose-500/10 text-rose-300"
                          : "bg-white/[0.04] text-zinc-300"
                      }`}
                      title={bad ? `${bad.count} failed` : undefined}
                    >
                      {t.tool}
                      {t.count > 1 && ` ×${t.count}`}
                      {bad && ` · ${bad.count} failed`}
                    </span>
                  );
                })}
              </div>
            </div>
          </div>
        )}

        {result.errors.length > 0 && (
          <div>
            <button
              type="button"
              onClick={() => setOpen((v) => !v)}
              className="flex items-center gap-1.5 text-[12px] text-rose-300 transition-colors hover:text-rose-200"
            >
              {open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
              <AlertTriangle size={13} />
              {result.errors.length} error{result.errors.length === 1 ? "" : "s"}
            </button>
            {open && (
              <div className="mt-1.5 space-y-1.5">
                {result.errors.map((e, i) => (
                  <div
                    key={i}
                    className="rounded-lg border border-rose-500/20 bg-rose-500/[0.05] px-2.5 py-1.5"
                  >
                    <div className="font-mono text-[11px] text-rose-300">{e.tool}</div>
                    <div className="mt-0.5 whitespace-pre-wrap break-words text-[11.5px] text-zinc-400">
                      {e.error}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {(result.reverted ?? 0) > 0 && (
          <div className="text-[11.5px] text-zinc-500">
            {result.reverted} action{result.reverted === 1 ? "" : "s"} from this task
            {result.reverted === 1 ? " has" : " have"} already been reverted.
          </div>
        )}

        {note && <div className="text-[12px] text-emerald-300">{note}</div>}
        {err && <div className="text-[12px] text-rose-300">{err}</div>}
      </div>

      <div className="flex flex-wrap items-center gap-1.5 border-t border-white/[0.05] px-3.5 py-2">
        {onRetry && (
          <button
            type="button"
            onClick={onRetry}
            className="inline-flex items-center gap-1.5 rounded-lg border border-white/10 px-2.5 py-1 text-[11.5px] font-medium text-zinc-300 transition-colors hover:border-accent/40 hover:text-accent-soft"
          >
            <RotateCcw size={12} /> Try again
          </button>
        )}
        {canRevert && (
          <button
            type="button"
            onClick={() => void revert()}
            disabled={busy !== null}
            title={`Undo the ${result.revertable} reversible action${
              result.revertable === 1 ? "" : "s"
            } this task took`}
            className="inline-flex items-center gap-1.5 rounded-lg border border-white/10 px-2.5 py-1 text-[11.5px] font-medium text-zinc-300 transition-colors hover:border-amber-400/40 hover:text-amber-300 disabled:opacity-40"
          >
            <Undo2 size={12} /> {busy === "revert" ? "Reverting…" : `Revert (${result.revertable})`}
          </button>
        )}
        {result.status === "active" && (
          <button
            type="button"
            onClick={() => void cancel()}
            disabled={busy !== null}
            className="inline-flex items-center gap-1.5 rounded-lg border border-white/10 px-2.5 py-1 text-[11.5px] font-medium text-zinc-300 transition-colors hover:border-rose-400/40 hover:text-rose-300 disabled:opacity-40"
          >
            <Ban size={12} /> Stop
          </button>
        )}
        <a
          href={`/sessions/${result.session_id}`}
          className="ml-auto text-[11.5px] text-zinc-500 transition-colors hover:text-zinc-300"
        >
          Full transcript →
        </a>
      </div>
    </div>
  );
}
