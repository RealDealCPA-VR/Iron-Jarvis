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

import { useState, type ReactNode } from "react";
import {
  CheckCircle2,
  CircleStop,
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
import type { SessionOutcome } from "@/lib/types";
import { atStepLimit } from "@/lib/agentRun";
import { runActionLines, stepFailedWords } from "@/lib/runWords";
import { friendlyModelName } from "@/lib/friendlyModelName";
import { providerDisplay } from "@/lib/onboarding";
import { secondsText } from "@/components/chat/TurnReceipt";

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
  /** The same files as ABSOLUTE paths (v1.155.0) — the lists above are
   *  workspace-relative, which reads well in the card but cannot be opened or
   *  downloaded. An agent session's workspace is a folder no user would guess,
   *  so this is what the preview rail and the download link use. */
  documents?: string[];
  files_created_total?: number;
  files_changed_total?: number;
  errors: { tool: string; error: string }[];
  revertable: number;
  reverted?: number;
  duration_s: number | null;
  /** How the run ENDED (v1.227.0) — `needs_you` when an ask for this session
   *  expired unanswered, `completed_with_failures` when a mutating call
   *  failed. Absent on an older daemon; the headline then falls back to the
   *  pre-v1.227.0 wording. */
  outcome?: SessionOutcome | null;
  /** Asks for this session that resolved by TIMEOUT (v1.227.0). The measured
   *  case: 24 rename calls "completed" with every one of them never approved,
   *  and this card said "Task complete". */
  unanswered_asks?: number;
  // v1.351.0 (G18) — the receipt's who / on what / where / what it did.
  // v1.352.0 (G19): the card names the model and says the route note. On
  // every result:
  /** The served provider and model the run's row recorded. */
  provider?: string;
  model?: string;
  /** The roster name a door recorded (a custom agent's "custom:<name>"),
   *  null when none was; `agent_type` is the base type. */
  agent_name?: string | null;
  agent_type?: string;
  // Only from GET /sessions/{id}/result (`session_result(receipt=True)`):
  /** One sentence when the run's model failed over or was refused, else "". */
  route_note?: string;
  /** Did the run's model stay on this PC / the user's network? null = no
   *  provider to judge. */
  on_this_network?: boolean | null;
  /** The newest 20 plain action lines, oldest first (the mission screen's
   *  words); tone "ok" | "warn" | "ask". */
  activity?: { at: string; who: string; session_id: string; text: string; tone: string }[];
}

/**
 * v1.352.0 (G16): how the run ENDED, in one of four looks. A run the user
 * STOPPED is not a failure: it reads "Stopped" with the neutral border and a
 * quiet stop icon (the mission screen's word for the same state), where it
 * used to arrive as a red "Task failed" over "Session cancelled by the
 * user.". A run that ran out of steps reads "Stopped at its step limit" in
 * the warn tone: the job is unfinished, but nothing broke. Only a FAILED run
 * (and not at its step limit) is red.
 */
export type RunEnd = "failed" | "stopped" | "step_limit" | "other";

export function runEnd(result: Pick<RunResult, "status" | "summary">): RunEnd {
  if (result.status === "cancelled") return "stopped";
  if (result.status === "failed") return atStepLimit(result.summary) ? "step_limit" : "failed";
  return "other";
}

/** The headline, as a pure function so the words are testable. A "completed"
 *  status is NOT "Task complete" when the ledger says the job fell short. */
export function resultHeadline(result: RunResult): string {
  const end = runEnd(result);
  if (end === "stopped") return "Stopped";
  if (end === "step_limit") return "Stopped at its step limit";
  if (end === "failed") return "Task failed";
  const asks = result.unanswered_asks ?? 0;
  if (asks > 0) {
    return `Finished — ${asks} call${asks === 1 ? " was" : "s were"} never approved`;
  }
  if (result.outcome === "needs_you") return "Finished — needs you";
  if (result.outcome === "completed_with_failures") return "Finished with failures";
  if (!result.tools_used.length) return "Finished — but nothing ran";
  return "Task complete";
}

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
              className="max-w-full truncate rounded bg-white/[0.04] px-1.5 py-0.5 font-mono text-[12px] text-zinc-300"
            >
              {f}
            </code>
          ))}
          {hidden > 0 && (
            <span className="text-[12px] text-zinc-500">+{hidden} more</span>
          )}
        </div>
      </div>
    </div>
  );
}

/**
 * v1.352.0 (G19): the line under the headline, "answered by Opus 5.5 ·
 * 2 min 5 s · 6 steps · 83 tool calls". Who served the run comes first, in
 * the name the reply's own receipt uses (lib/friendlyModelName; the provider
 * and the raw id stay in its title); the length reads like every other
 * duration in the chat (TurnReceipt secondsText: "10 min 57 s", never
 * "656.8s"). A run that did not finish "ran on" its model. An older result
 * with no provider says nothing about who, exactly as before.
 */
export function RunMeta({ result }: { result: RunResult }) {
  const parts: ReactNode[] = [];
  const provider = (result.provider ?? "").trim();
  const model = (result.model ?? "").trim();
  const who =
    provider === "mock"
      ? providerDisplay("mock")
      : model
        ? friendlyModelName(model)
        : providerDisplay(provider);
  if (who) {
    const title = [providerDisplay(provider), model].filter(Boolean).join(" · ");
    parts.push(
      <span data-testid="run-result-model" title={title || undefined}>
        {result.status === "completed" ? "answered by" : "ran on"}{" "}
        <span className="text-zinc-400">{who}</span>
      </span>,
    );
  }
  const dur =
    typeof result.duration_s === "number" ? secondsText(result.duration_s * 1000) : null;
  if (dur) parts.push(dur);
  if (result.steps > 0) parts.push(`${result.steps} step${result.steps === 1 ? "" : "s"}`);
  const calls = result.tools_used.reduce((n, t) => n + t.count, 0);
  if (calls > 0) parts.push(`${calls} tool call${calls === 1 ? "" : "s"}`);
  if (!parts.length) return null;
  return (
    <div data-testid="run-result-meta" className="mt-0.5 text-[12px] text-zinc-500">
      {parts.map((p, i) => (
        <span key={i}>
          {i > 0 ? " · " : ""}
          {p}
        </span>
      ))}
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

  const end = runEnd(result);
  const failed = end === "failed";
  const stopped = end === "stopped";
  const atLimit = end === "step_limit";
  // v1.352.0 (G16): a run that ended early (stopped, or at its step limit)
  // is neither the red failure nor a "finished" run the notes below judge.
  const endedEarly = failed || stopped || atLimit;
  const didNothing = !result.tools_used.length;
  const canRevert = result.revertable > 0;
  // Finished, but short of the job (v1.227.0): amber, never the green tick.
  const short =
    !endedEarly &&
    ((result.unanswered_asks ?? 0) > 0 ||
      result.outcome === "needs_you" ||
      result.outcome === "completed_with_failures");

  // v1.352.0 (G19): the run's work in words, counted per tool.
  const actions = runActionLines(result.tools_used, result.tools_failed);

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
      className={`max-w-[640px] rounded-xl border ${
        failed ? "border-tone-danger/25 bg-tone-danger/[0.04]" : "border-white/[0.08] bg-white/[0.02]"
      }`}
    >
      <div className="flex items-start gap-2.5 border-b border-white/[0.05] px-3.5 py-2.5">
        <span
          data-testid="run-result-icon"
          data-end={end}
          className={`mt-0.5 shrink-0 ${
            failed
              ? "text-tone-danger"
              : stopped
                ? "text-zinc-500"
                : short || atLimit
                  ? "text-tone-warn"
                  : "text-tone-success"
          }`}
        >
          {failed ? (
            <XCircle size={15} />
          ) : stopped ? (
            <CircleStop size={15} />
          ) : short || atLimit ? (
            <AlertTriangle size={15} />
          ) : (
            <CheckCircle2 size={15} />
          )}
        </span>
        <div className="min-w-0 flex-1">
          <div className="text-[13px] font-medium text-zinc-200" data-testid="run-result-headline">
            {resultHeadline(result)}
          </div>
          <RunMeta result={result} />
          {/* v1.352.0 (G19): the run's model failed over or was refused, in
              the mission screen's own sentence, under the headline. */}
          {result.route_note ? (
            <div
              data-testid="run-result-route-note"
              className="mt-0.5 text-[12px] leading-relaxed text-tone-warn"
            >
              {result.route_note}
            </div>
          ) : null}
        </div>
      </div>

      <div className="space-y-2.5 px-3.5 py-3">
        {/* The honesty case this card exists for: a run that completed without
            touching a single tool is an agent that TALKED about the work. */}
        {didNothing && !endedEarly && (
          <div className="rounded-lg border border-tone-warn/25 bg-tone-warn/[0.06] px-2.5 py-2 text-[12px] leading-relaxed text-tone-warn">
            No tools ran and no files changed — this turn described the work
            rather than doing it. Ask again more concretely, or check that the
            tools it needed were available.
          </div>
        )}
        {/* The other honesty case (v1.227.0): the run ENDED, but calls it
            needed were never answered, or mutating calls failed. Say what is
            left for the user instead of a green tick. */}
        {short && (
          <div className="rounded-lg border border-tone-warn/25 bg-tone-warn/[0.06] px-2.5 py-2 text-[12px] leading-relaxed text-tone-warn">
            {(result.unanswered_asks ?? 0) > 0 || result.outcome === "needs_you"
              ? "The run paused for your approval and the asks expired unanswered — those calls were not run. Open the session to re-run the failed items, or grant the tool and try again."
              : "Some of the changes this run tried failed — check the errors below and the worklist on the session page."}
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

        {actions.length > 0 && (
          <div className="flex gap-2">
            <span className="mt-0.5 shrink-0 text-zinc-500">
              <Wrench size={13} />
            </span>
            <div className="min-w-0">
              <div className="text-[11px] uppercase tracking-[0.1em] text-zinc-500">
                Tools
              </div>
              {/* v1.352.0 (G19): what the steps DID, in words (lib/runWords:
                  "Searched your chats 83 times"), the calls that failed
                  counted apart ("One memory lookup failed"); the tool's own
                  id stays in the chip's title, the record of what ran. */}
              <div className="mt-0.5 flex flex-wrap gap-x-2 gap-y-1">
                {actions.map((a, i) => (
                  <span
                    key={`${a.tool}-${a.failed ? "x" : "ok"}-${i}`}
                    data-testid="run-result-tool"
                    data-failed={a.failed ? "true" : undefined}
                    className={`rounded px-1.5 py-0.5 text-[12px] ${
                      a.failed
                        ? "bg-tone-danger/10 text-tone-danger"
                        : "bg-white/[0.04] text-zinc-300"
                    }`}
                    title={a.tool}
                  >
                    {a.text}
                  </span>
                ))}
              </div>
            </div>
          </div>
        )}

        {result.errors.length > 0 && (
          <div>
            <button
              type="button"
              onClick={() => setOpen((v) => !v)}
              className="flex items-center gap-1.5 text-[12px] text-tone-danger/90 transition-colors hover:text-tone-danger"
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
                    data-testid="run-result-error"
                    className="rounded-lg border border-tone-danger/20 bg-tone-danger/[0.05] px-2.5 py-1.5"
                  >
                    {/* v1.352.0 (G19): the step in words first ("Memory
                        lookup failed"); what the tool said stays one press
                        away, folded (a native disclosure: Enter / Space open
                        it, a screen reader hears it); its id in the title. */}
                    <details>
                      <summary title={e.tool} className="cursor-pointer text-[12px] text-tone-danger">
                        {stepFailedWords(e.tool)}
                      </summary>
                      <div
                        data-testid="run-result-error-raw"
                        className="mt-0.5 whitespace-pre-wrap break-words text-[12px] text-zinc-400"
                      >
                        {e.error}
                      </div>
                    </details>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {(result.reverted ?? 0) > 0 && (
          <div className="text-[12px] text-zinc-500">
            {result.reverted} action{result.reverted === 1 ? "" : "s"} from this task
            {result.reverted === 1 ? " has" : " have"} already been reverted.
          </div>
        )}

        {note && <div className="text-[12px] text-tone-success">{note}</div>}
        {err && <div className="text-[12px] text-tone-danger">{err}</div>}
      </div>

      <div className="flex flex-wrap items-center gap-1.5 border-t border-white/[0.05] px-3.5 py-2">
        {onRetry && (
          <button
            type="button"
            onClick={onRetry}
            className="inline-flex items-center gap-1.5 rounded-lg border border-white/10 px-2.5 py-1 text-[12px] font-medium text-zinc-300 transition-colors hover:border-accent/40 hover:text-accent-soft"
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
            className="inline-flex items-center gap-1.5 rounded-lg border border-white/10 px-2.5 py-1 text-[12px] font-medium text-zinc-300 transition-colors hover:border-tone-warn/40 hover:text-tone-warn disabled:opacity-40"
          >
            <Undo2 size={12} /> {busy === "revert" ? "Reverting…" : `Revert (${result.revertable})`}
          </button>
        )}
        {result.status === "active" && (
          <button
            type="button"
            onClick={() => void cancel()}
            disabled={busy !== null}
            className="inline-flex items-center gap-1.5 rounded-lg border border-white/10 px-2.5 py-1 text-[12px] font-medium text-zinc-300 transition-colors hover:border-tone-danger/40 hover:text-tone-danger disabled:opacity-40"
          >
            <Ban size={12} /> Stop
          </button>
        )}
        <a
          href={`/sessions/${result.session_id}`}
          className="ml-auto text-[12px] text-zinc-500 transition-colors hover:text-zinc-300"
        >
          Full transcript →
        </a>
      </div>
    </div>
  );
}
