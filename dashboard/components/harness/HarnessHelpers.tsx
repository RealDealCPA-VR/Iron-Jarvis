"use client";

/**
 * v1.372.0 (IJ harness part 2, Phase 1): a Build session's HELPER AGENTS, one
 * row each, built from the chat's own helper row (components/chat/
 * AgentRunRow) and work line (WorkLine) — never new styles.
 *
 *   waiting to start  one quiet line: "The researcher is waiting to start ·
 *                     3 helpers are already working" (the session runs at
 *                     most 3 at once; the daemon says when one waits).
 *   working           AgentRunLive: "The researcher is working · …", open to
 *                     what it was asked and its steps as they happen (the
 *                     live work line counts them). An ask of its own is the
 *                     chat's approval card, answered right here (POST
 *                     /chat/approvals/{id}, "Allow for this run").
 *   finished          AgentRunSettled: "The researcher worked for 42 s · 6
 *                     steps", "Open run", and opened, what it was asked, its
 *                     report and its folded steps.
 *
 * The rows read ONLY the `helper` frames (live) and the reply's saved
 * `helpers` (settled): lib/buildHelpers. Nothing here polls: the turn's own
 * stream carries the progress, so no reload is needed to see it.
 *
 * v1.374.0 (part 2, Phase 3): a helper whose run PLANS its work (the
 * decomposed lane) shows that plan as the chat's checklist (PlanChecklist,
 * through AgentRunLive's `plan` and the settled row's stamp), ticking off as
 * the run moves on — the daemon sends its own copy on the row's frames.
 *
 * v1.394.0 (the model team): each row also says the MODEL the helper ran on
 * ("On qwen3:8b"), in the Timeline's words (lib/buildHelpers
 * `workerModelName`) — a team member runs on its own model, not the
 * session's, so the row names both the member and its model.
 */

import { Bot, X } from "lucide-react";
import { AgentRunLive, AgentRunSettled } from "@/components/chat/AgentRunRow";
import { WorkLine, WorkRow } from "@/components/chat/WorkLine";
import { ApprovalCard } from "@/components/chat/ApprovalCard";
import {
  askedLine,
  decodeSavedHelpers,
  helperName,
  helperStamp,
  settledFromLive,
  workerModelName,
  type HelperView,
  type SavedHelper,
} from "@/lib/buildHelpers";
import type { CatalogRow } from "@/lib/answeredModel";

/** v1.394.0: the model the helper ran on, one quiet line ("" = not known). */
function HelperModel({ provider, model, models }: { provider?: string; model?: string; models: readonly CatalogRow[] }) {
  const words = workerModelName(provider ?? "", model ?? "", models);
  return words ? (
    <p data-testid="helper-model" className="text-[12px] leading-5 text-zinc-500" title={[provider, model].filter(Boolean).join(" · ")}>
      On {words}
    </p>
  ) : null;
}

/** v1.395.0 (the long-jobs plan's Phase 6): why the team router gave this
 *  member the piece — "local: good record for reading", "Codex: the local box
 *  is busy" — or why nothing started ("waited: …"), one quiet line. */
function HelperReason({ reason }: { reason?: string }) {
  return reason ? (
    <p
      data-testid="helper-reason"
      // v1.395.0 review: a member's or a model's long name wraps at a phone's
      // width instead of pushing the row sideways.
      className="break-words text-[12px] leading-5 text-zinc-500"
      title="Why Iron Jarvis gave this piece to this team member"
    >
      Why: {reason}
    </p>
  ) : null;
}

const NO_MODELS: readonly CatalogRow[] = [];

/** The words that say what a helper was asked, in the row's own grey. */
function Asked({ task }: { task: string }) {
  const line = askedLine(task);
  return line ? (
    <p data-testid="helper-asked" className="text-[13px] leading-5 text-zinc-500">
      {line}
    </p>
  ) : null;
}

/** A finished helper: the chat's settled run row; opened, what it was asked,
 *  its report and its steps. A helper refused before it had a session (no
 *  "Open run" to offer) is one failed work row with the reason. */
export function SettledHelperRow({ helper, models = NO_MODELS }: { helper: SavedHelper; models?: readonly CatalogRow[] }) {
  const name = helperName(helper.agent);
  if (!helper.session) {
    // Never started: refused (a remote agent, a pinned model), or stopped
    // while it waited for a free slot.
    const stopped = helper.state === "stopped";
    return (
      <div data-testid="helper-row" data-state={helper.state} data-helper={helper.id}>
        <WorkRow
          icon={X}
          failed={!stopped}
          title={stopped ? `${name} was stopped before it started` : `${name} could not start`}
          detail={stopped ? null : helper.result || null}
        />
        {/* v1.395.0: a role nobody could take says it WAITED, and why. */}
        <div className="pl-[22px]">
          <HelperReason reason={helper.reason} />
        </div>
      </div>
    );
  }
  return (
    <div data-testid="helper-row" data-state={helper.state} data-helper={helper.id}>
      <AgentRunSettled name={name} run={helperStamp(helper)} why={askedLine(helper.task) || null}>
        {helper.result ? (
          <p data-testid="helper-result" className="whitespace-pre-wrap text-[13px] leading-5 text-zinc-400">
            {helper.result}
          </p>
        ) : null}
        {helper.work.length ? (
          <WorkLine
            steps={helper.work}
            timing={
              helper.startedAt !== null && helper.endedAt !== null
                ? { startedAt: helper.startedAt, firstTokenAt: null, endedAt: helper.endedAt }
                : null
            }
          />
        ) : null}
      </AgentRunSettled>
      {/* v1.394.0: the model it ran on, always in sight (a team member's is
          its own, not the session's) — outside the folded body. */}
      <div className="pl-[22px]">
        <HelperModel provider={helper.provider} model={helper.model} models={models} />
        <HelperReason reason={helper.reason} />
      </div>
    </div>
  );
}

/** A helper while the turn runs. */
export function LiveHelperRow({ helper, models = NO_MODELS }: { helper: HelperView; models?: readonly CatalogRow[] }) {
  const settled = settledFromLive(helper);
  if (settled) return <SettledHelperRow helper={settled} models={models} />;
  const name = helperName(helper.agent);
  if (helper.state === "queued" || (helper.state === "starting" && !helper.session)) {
    const waiting = helper.state === "queued";
    return (
      <div data-testid="helper-row" data-state={helper.state} data-helper={helper.id}>
        <WorkRow
          icon={Bot}
          running={!waiting}
          title={waiting ? `${name} is waiting to start` : `${name} is starting`}
          detail={waiting ? helper.note || null : askedLine(helper.task) || null}
        />
        <div className="pl-[22px]">
          <HelperReason reason={helper.reason} />
        </div>
      </div>
    );
  }
  const asking = helper.state === "waiting" && helper.ask;
  return (
    <div data-testid="helper-row" data-state={helper.state} data-helper={helper.id}>
      <AgentRunLive
        session={helper.session || helper.id}
        handOff={{ escalatedTo: name }}
        since={helper.since}
        progress={stepsSoFar(helper.steps)}
        tools={helper.tools}
        phaseTitle={asking ? "Waiting for your OK" : helper.phase ? phaseWords(helper.phase) : null}
        phaseDetail={asking ? null : helper.phaseDetail || null}
        // v1.374.0: the run's plan, ticking off as it works (the chat's
        // checklist, AgentRunLive's own `plan`) — absent for most helpers.
        plan={helper.plan ?? null}
      >
        <Asked task={helper.task} />
        <HelperModel provider={helper.provider} model={helper.model} models={models} />
        <HelperReason reason={helper.reason} />
        {asking && helper.ask ? (
          <div data-testid="helper-ask">
            <ApprovalCard
              scope="run"
              approval={{
                id: helper.ask.id,
                callId: "",
                tool: helper.ask.tool,
                args: helper.ask.args,
                timeoutS: 0,
                ...(helper.ask.count ? { count: helper.ask.count, examples: helper.ask.examples } : {}),
                ...(helper.ask.costNote ? { costNote: helper.ask.costNote } : {}),
                // v1.381.0: an ask answered once, every time (the daemon
                // reads a wider answer as once) — the card says so.
                ...(helper.ask.onceOnly ? { onceOnly: true } : {}),
                ...(helper.ask.onceReason ? { onceReason: helper.ask.onceReason } : {}),
              }}
            />
          </div>
        ) : null}
      </AgentRunLive>
    </div>
  );
}

/** The live step count, as the row's newest progress line: the header reads
 *  "The researcher is working · 3 steps so far" while no phase says more. */
function stepsSoFar(steps: number): string[] {
  if (steps <= 0) return [];
  return [steps === 1 ? "1 step so far" : `${steps} steps so far`];
}

/** The run's own lifecycle words (RunSink.phase names). */
function phaseWords(phase: string): string {
  switch (phase) {
    case "planning":
      return "Planning the work";
    case "verifying":
      return "Checking its work";
    case "assembling":
      return "Putting the answer together";
    default:
      return "Working";
  }
}

/** The turn's helpers while it runs (call order). */
export function LiveHelperRows({ helpers, models = NO_MODELS }: { helpers: readonly HelperView[]; models?: readonly CatalogRow[] }) {
  if (!helpers.length) return null;
  return (
    <div data-testid="harness-helpers" className="grid gap-2">
      {helpers.map((h) => (
        <LiveHelperRow key={h.id} helper={h} models={models} />
      ))}
    </div>
  );
}

/** A reply's saved helpers (the line's `helpers`). */
export function SavedHelperRows({ raw, models = NO_MODELS }: { raw: unknown; models?: readonly CatalogRow[] }) {
  const helpers = decodeSavedHelpers(raw);
  if (!helpers.length) return null;
  return (
    <div data-testid="harness-helpers" className="mb-2 grid gap-2">
      {helpers.map((h) => (
        <SettledHelperRow key={h.id} helper={h} models={models} />
      ))}
    </div>
  );
}
