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
 *
 * v1.358.0 (wave 8, lane 8C): ONE RECEIPT PER RUN. When the run was handed
 * over from chat, its settled hand-off row (components/chat/AgentRunRow +
 * AgentRunReceipt) already says how it ended, what it did, on what model,
 * where, and links "Open run" — so the reply under that row no longer draws a
 * second boxed card saying it again in other words ("answered by" beside
 * "Ran on", "Full transcript" beside "Open run", "Task complete" beside the
 * row's verdict). With `withRow` this component draws only what the row does
 * not carry, as quiet grey lines (no box): the files the run made or changed,
 * the honest note when it fell short, the errors folded, and Try again /
 * Revert / Stop. A reply with no such row (an older stamp, a run started
 * elsewhere) keeps the full card. The success word everywhere is the shared
 * status table's (lib/statusWords): "Done", never "Task complete".
 */

import { useId, useState, type ReactNode } from "react";
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
import { statusWords } from "@/lib/statusWords";
import { runActivity, RunActivityFold } from "@/components/chat/RunActivity";
import { friendlyModelName } from "@/lib/friendlyModelName";
import { answeredModelName } from "@/lib/answeredModel";
import { useModels } from "@/lib/useModels";
import { providerDisplay } from "@/lib/onboarding";
import { replyWhere, secondsText } from "@/components/chat/TurnReceipt";

export interface RunResult {
  found: boolean;
  session_id: string;
  status: string;
  task: string;
  summary: string;
  steps: number;
  // v1.359.0 integrate: `name` on a connected app's tool row ("Gmail ›
  // search messages", agents/outcome._tool_row); absent for other tools.
  tools_used: { tool: string; count: number; name?: string }[];
  tools_failed: { tool: string; count: number; name?: string }[];
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
 *  status is NOT "Done" when the ledger says the job fell short.
 *
 *  v1.358.0 (lane 8C): the words are the shared status table's
 *  (lib/statusWords), the same every badge, the settled row and the Sessions
 *  list say: "Done" (was "Task complete"), "Failed" (was "Task failed"),
 *  "Stopped", "Needs you", "Part failed" (lane 8A's table words). An ask that expired
 *  unanswered IS "needs you" (the note below says how many calls). Two say
 *  more than any table word: "Stopped at its step limit" and "Finished — but
 *  nothing ran" (a run that only TALKED about the work earns no "Done"). */
export function resultHeadline(result: RunResult): string {
  const end = runEnd(result);
  if (end === "stopped") return statusWords("cancelled").word;
  if (end === "step_limit") return "Stopped at its step limit";
  if (end === "failed") return statusWords("failed").word;
  if ((result.unanswered_asks ?? 0) > 0) return statusWords("completed", "needs_you").word;
  if (result.outcome === "needs_you" || result.outcome === "completed_with_failures") {
    return statusWords("completed", result.outcome).word;
  }
  if (!result.tools_used.length) return "Finished — but nothing ran";
  return statusWords("completed").word;
}

/** v1.358.0 (lane 8C): the note under a run that finished SHORT of the job,
 *  in plain words (no "session", no "worklist"): how many calls were never
 *  approved, or that some changes failed, and what the user can do. */
export function shortRunNote(result: Pick<RunResult, "unanswered_asks" | "outcome">): string {
  const asks = result.unanswered_asks ?? 0;
  if (asks > 0) {
    return `${asks === 1 ? "One call" : `${asks} calls`} waited for your OK and the ask${
      asks === 1 ? "" : "s"
    } ran out unanswered, so ${asks === 1 ? "it was" : "they were"} not run. Open the run to try again, or allow the tool and try again.`;
  }
  if (result.outcome === "needs_you") {
    return "The run waited for your OK and the asks ran out unanswered, so those calls were not run. Open the run to try again, or allow the tool and try again.";
  }
  return "Some of the changes this run tried failed. The errors are below, and the run's page lists what is left.";
}

/** v1.358.0 (lane 8C): the honest note for a run that finished without
 *  running a single tool (it described the work instead of doing it). */
export const NOTHING_RAN_NOTE =
  "No tools ran and no files changed — this run described the work rather than doing it. Ask again more concretely, or check that the tools it needed were available.";

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
  // v1.360.0 (lane 10B fix): the catalog the reply's receipt names the
  // "fleet" pick from (its machine, "DGX Spark"), read through the one
  // /models reader (cached, shared; an empty list until it answers).
  const { models } = useModels();
  const parts: ReactNode[] = [];
  const provider = (result.provider ?? "").trim();
  const model = (result.model ?? "").trim();
  // v1.359.0 re-review: the reply receipt's own rule (lib/answeredModel), so
  // a run on a CLI's default model reads "answered by Claude Code", never
  // "answered by subscription".
  const who =
    provider === "mock"
      ? providerDisplay("mock")
      : model
        ? answeredModelName({ provider, model }, models) ?? friendlyModelName(model)
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
  // v1.352.0 integrate (G21 on the helper agent's card): WHERE the run's
  // model ran, in the reply receipt's own words and rule (TurnReceipt
  // replyWhere: "local" or "cloud", only when the receipt says, never for the
  // demo model). The receipt carries no endpoint label, so no "on <label>".
  const where = replyWhere({ provider, model, on_this_network: runOnNetwork(result) });
  if (who && where?.tail) {
    parts.push(
      <span data-testid="run-result-where" title={where.title}>
        {where.tail}
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

/** The receipt's `on_this_network`, a real boolean only (null/absent = the
 *  receipt did not say, so the card says nothing about where). */
function runOnNetwork(result: RunResult): boolean | undefined {
  return typeof result.on_this_network === "boolean" ? result.on_this_network : undefined;
}

/** The receipt's action log (`activity`): since v1.358.0 one helper in
 *  components/chat/RunActivity, shared with the settled row. */
export { runActivity };

/** v1.358.0 (lane 8C): the files a run made or changed, as ONE quiet line
 *  under the reply ("Made brief.md, notes.md"), in the work line's verbs and
 *  its dotted file underline. No label in capitals, no chips, no box. */
function FileLine({
  verb,
  icon: Icon,
  items,
  total,
  testId,
}: {
  verb: string;
  icon: typeof FilePlus2;
  items: string[];
  total?: number;
  testId: string;
}) {
  if (!items.length) return null;
  const hidden = Math.max(0, (total ?? items.length) - items.length);
  return (
    <div data-testid={testId} className="flex min-w-0 items-start gap-2 text-[13px] leading-5 text-zinc-500">
      <Icon size={14} aria-hidden="true" className="mt-[3px] shrink-0" />
      <span className="min-w-0 break-words">
        {verb}{" "}
        {items.map((f, i) => (
          <span key={f}>
            {i > 0 ? ", " : ""}
            <span
              title={f}
              className="text-zinc-300 underline decoration-zinc-500 decoration-dotted underline-offset-[3px]"
            >
              {f}
            </span>
          </span>
        ))}
        {hidden > 0 ? ` and ${hidden} more` : ""}
      </span>
    </div>
  );
}

/** The calm ghost press the reply's own row uses (no border, fills on
 *  hover; only keyboard focus draws a ring). */
const REST_BUTTON =
  "inline-flex h-7 items-center gap-1.5 rounded-lg px-2 text-[12px] text-zinc-400 transition-colors " +
  "hover:bg-white/[0.06] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/50 " +
  "disabled:opacity-40 motion-reduce:transition-none";

export function RunResultCard({
  result,
  onRetry,
  onReverted,
  withRow = false,
}: {
  result: RunResult;
  /** Re-run the same task (POST /sessions/{id}/rerun happens upstream). */
  onRetry?: () => void;
  /** Called after a successful revert so the caller can refresh. */
  onReverted?: (summary: string) => void;
  /** v1.358.0 (lane 8C): the run's settled hand-off row sits right above this
   *  reply and already carries its receipt (how it ended, what it did, on
   *  what model, where, "Open run"). Draw only what that row does not say,
   *  as quiet lines: no box, no headline, no second "answered by". */
  withRow?: boolean;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const errorsId = useId();

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
  // v1.352.0 integrate (G18 -> G19): what it did, step by step, in the
  // mission screen's words (the receipt's `activity`), folded.
  const activity = runActivity(result);

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

  const revertTitle = `Undo the ${result.revertable} reversible action${
    result.revertable === 1 ? "" : "s"
  } this task took`;

  if (withRow) {
    // v1.358.0 (lane 8C): ONE receipt. The row above says how the run ended,
    // what it did, the model, the folder and "Open run"; this says only the
    // rest, in the calm chat's quiet lines.
    const hasActions = !!onRetry || canRevert || result.status === "active";
    return (
      <div data-testid="run-result-rest" data-end={end} className="mt-1.5 grid max-w-[640px] gap-1.5">
        {/* The honesty case: a run that finished without touching a single
            tool TALKED about the work. Said in words, never boxed. */}
        {didNothing && !endedEarly && (
          <p
            data-testid="run-result-note"
            className="flex min-w-0 items-start gap-2 text-[13px] leading-5 text-tone-warn"
          >
            <AlertTriangle size={14} aria-hidden="true" className="mt-[3px] shrink-0" />
            <span className="min-w-0">{NOTHING_RAN_NOTE}</span>
          </p>
        )}
        {/* It ENDED, but calls it needed were never answered, or changes
            failed: what is left for the user, never a green tick. */}
        {short && (
          <p
            data-testid="run-result-note"
            className="flex min-w-0 items-start gap-2 text-[13px] leading-5 text-tone-warn"
          >
            <AlertTriangle size={14} aria-hidden="true" className="mt-[3px] shrink-0" />
            <span className="min-w-0">{shortRunNote(result)}</span>
          </p>
        )}
        <FileLine
          verb="Made"
          icon={FilePlus2}
          items={result.files_created}
          total={result.files_created_total}
          testId="run-result-files-made"
        />
        <FileLine
          verb="Changed"
          icon={FilePen}
          items={result.files_changed}
          total={result.files_changed_total}
          testId="run-result-files-changed"
        />
        {/* What each failed step SAID, one press away (the row above already
            counts the failures in words). A native disclosure per error. */}
        {result.errors.length > 0 && (
          <div>
            <button
              type="button"
              data-testid="run-result-errors-toggle"
              aria-expanded={open}
              aria-controls={open ? errorsId : undefined}
              onClick={() => setOpen((v) => !v)}
              className="flex items-center gap-1.5 text-[12px] text-zinc-500 transition-colors hover:text-zinc-300 focus-visible:text-zinc-300 focus-visible:outline-none motion-reduce:transition-none"
            >
              {open ? <ChevronDown size={13} aria-hidden="true" /> : <ChevronRight size={13} aria-hidden="true" />}
              {`What went wrong (${result.errors.length})`}
            </button>
            {open && (
              <div id={errorsId} className="mt-1.5 grid gap-1">
                {result.errors.map((e, i) => (
                  <details key={i} data-testid="run-result-error">
                    <summary title={e.tool} className="cursor-pointer text-[12px] leading-5 text-tone-danger">
                      {stepFailedWords(e.tool)}
                    </summary>
                    <div
                      data-testid="run-result-error-raw"
                      className="mt-0.5 whitespace-pre-wrap break-words pl-4 text-[12px] leading-5 text-zinc-500"
                    >
                      {e.error}
                    </div>
                  </details>
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
        {err && (
          <div role="alert" className="text-[12px] text-tone-danger">
            {err}
          </div>
        )}
        {hasActions && (
          <div data-testid="run-result-actions" className="-ml-2 flex flex-wrap items-center gap-0.5">
            {onRetry && (
              <button type="button" onClick={onRetry} className={`${REST_BUTTON} hover:text-zinc-200`}>
                <RotateCcw size={12} aria-hidden="true" /> Try again
              </button>
            )}
            {canRevert && (
              <button
                type="button"
                onClick={() => void revert()}
                disabled={busy !== null}
                title={revertTitle}
                className={`${REST_BUTTON} hover:text-tone-warn`}
              >
                <Undo2 size={12} aria-hidden="true" />{" "}
                {busy === "revert" ? "Reverting…" : `Revert (${result.revertable})`}
              </button>
            )}
            {result.status === "active" && (
              <button
                type="button"
                onClick={() => void cancel()}
                disabled={busy !== null}
                className={`${REST_BUTTON} hover:text-tone-danger`}
              >
                <Ban size={12} aria-hidden="true" /> Stop
              </button>
            )}
          </div>
        )}
      </div>
    );
  }

  return (
    <div
      data-testid="run-result-card"
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
            {NOTHING_RAN_NOTE}
          </div>
        )}
        {/* The other honesty case (v1.227.0): the run ENDED, but calls it
            needed were never answered, or mutating calls failed. Say what is
            left for the user instead of a green tick. */}
        {short && (
          <div className="rounded-lg border border-tone-warn/25 bg-tone-warn/[0.06] px-2.5 py-2 text-[12px] leading-relaxed text-tone-warn">
            {shortRunNote(result)}
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

        {/* v1.352.0 integrate (G18 -> G19): the receipt's own action log —
            "Builder saved brief.md", "needed your OK to run a command" —
            folded behind one quiet toggle, as plain grey rows (no box). An
            older result with no log shows nothing new. (v1.358.0: the one
            fold components/chat/RunActivity, shared with the settled row.) */}
        <RunActivityFold lines={activity} />

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
            title={revertTitle}
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
        {/* v1.358.0 (lane 8C): the settled row's words for the same link. */}
        <a
          href={`/sessions/${result.session_id}`}
          data-testid="run-result-open"
          className="ml-auto text-[12px] text-zinc-500 transition-colors hover:text-zinc-300"
        >
          Open run
        </a>
      </div>
    </div>
  );
}
