"use client";

/**
 * v1.352.0 (readiness audit G19): what a FINISHED helper-agent run did, in
 * the settled row's opened body (components/chat/AgentRunRow AgentRunSettled,
 * through its `children`).
 *
 * The row used to open onto only why the work was handed over and the plan.
 * It now also says, in WorkLine's own quiet grey rows (no box, no border):
 *
 *   ⚠ <the route note, when the run's model failed over or was refused>
 *   ✓ Searched your chats 83 times          (lib/runWords, the ledger's counts)
 *   ✕ One memory lookup failed
 *   ▣ Ran on Opus 5.5 · cloud              (v1.352.0 integrate: where, G21)
 *   ▣ in 2026-10-18 Quarterly · Open folder (POST /documents/open, G11)
 *
 * Every fact comes from the stamp finalize saved on the hand-off message
 * (lib/agentRun), so a reopened chat shows the same lines and nothing is
 * fetched to draw them. A stamp from before v1.352.0 carries none of these
 * facts and the body renders nothing new. Raw tool ids stay in each row's
 * title (the record), never in its words.
 *
 * v1.358.0 (wave 8, lane 8C): THE run's one receipt. The reply under the row
 * no longer repeats these facts in a boxed card (components/chat/
 * RunResultCard `withRow`), so the step-by-step log the card used to fold
 * ("What it did") folds here, under the action lines ("Step by step"), read
 * off the reply's own saved receipt (`activity`, which the chat page hands
 * in: lib/runReceipt pairRunReceipts) — the same components/chat/RunActivity
 * piece, in the same words.
 */

import { useMemo, useState } from "react";
import { AlertTriangle, Check, Cpu, FolderOpen } from "lucide-react";
import { post, ApiError } from "@/lib/api";
import { WorkRow } from "@/components/chat/WorkLine";
import { runActionLines } from "@/lib/runWords";
import { friendlyModelName } from "@/lib/friendlyModelName";
import { answeredModelName } from "@/lib/answeredModel";
import { providerDisplay } from "@/lib/onboarding";
import { replyWhere } from "@/components/chat/TurnReceipt";
import { runActivity, RunActivityFold } from "@/components/chat/RunActivity";
import type { AgentRunStamp } from "@/lib/agentRun";

/** The model the run was served by, in the words the reply's receipt uses
 *  (the name the id spells; the demo model by its own name), or "". */
export function runModelName(run: Pick<AgentRunStamp, "provider" | "model">): string {
  if (run.provider === "mock") return providerDisplay("mock");
  const model = (run.model ?? "").trim();
  // v1.359.0 re-review: the reply receipt's rule, so a run on a CLI's
  // default model reads "Ran on Claude Code", never "Ran on subscription".
  if (model) return answeredModelName({ provider: run.provider ?? "", model }, []) ?? friendlyModelName(model);
  return providerDisplay(run.provider ?? "");
}

/** The last segment of a folder path, either separator ("" for none). */
function folderName(p: string): string {
  const parts = p.split(/[\\/]/).filter(Boolean);
  return parts.length ? parts[parts.length - 1] : p;
}

/** The app's own folder for a run that was given none
 *  (`<home>/workspaces/session_<id>`, agents/orchestrator.create_session). */
const RUN_OWN_FOLDER = /[\\/]workspaces[\\/]session_[A-Za-z0-9]+[\\/]?$/;

/** v1.352.0 re-review (G19): how the folder reads in the row. A folder the
 *  user chose reads by its name ("2026-10-17 S-corp"); the app's own folder
 *  for a run is named by the run's internal id ("session_8f3a…"), an
 *  engine-room word, so it reads "its own work folder". The full path stays
 *  in the title either way. */
export function runFolderWords(p: string): { name: string; own: boolean } {
  return RUN_OWN_FOLDER.test(p) ? { name: "its own work folder", own: true } : { name: folderName(p), own: false };
}

function Dot() {
  return <span aria-hidden="true" className="h-[3px] w-[3px] shrink-0 rounded-full bg-zinc-600" />;
}

export function AgentRunReceipt({
  run,
  activity,
}: {
  run: AgentRunStamp;
  /** v1.358.0 (lane 8C): the run's step-by-step log, the reply's saved
   *  receipt `activity` (raw; checked by runActivity). Absent: no fold. */
  activity?: unknown;
}) {
  const [openErr, setOpenErr] = useState<string | null>(null);
  const [opening, setOpening] = useState(false);
  const tools = run.tools ?? [];
  const lines = runActionLines(
    tools,
    tools.map((t) => ({ tool: t.tool, count: t.failed })),
  );
  const model = runModelName(run);
  const modelTitle = [providerDisplay(run.provider ?? ""), run.model].filter(Boolean).join(" · ");
  const folder = run.workspacePath ?? "";
  const folderWords = runFolderWords(folder);
  // v1.352.0 integrate (G21 on the helper agent): where its model ran, in the
  // reply receipt's words and rule ("local" / "cloud"; nothing for the demo
  // model or a stamp that never said).
  const where = replyWhere({
    provider: run.provider ?? "",
    model: run.model,
    on_this_network: run.onThisNetwork,
  });

  async function openFolder() {
    if (!folder || opening) return;
    setOpening(true);
    setOpenErr(null);
    try {
      await post("/documents/open", { path: folder });
    } catch (e) {
      setOpenErr(e instanceof ApiError ? e.message : String(e));
    } finally {
      setOpening(false);
    }
  }

  // v1.358.0 (lane 8C): what it did, step by step (read once per log).
  const steps = useMemo(() => runActivity({ activity }), [activity]);

  if (!run.routeNote && !lines.length && !steps.length && !model && !folder) return null;
  return (
    <div data-testid="agent-run-receipt" className="grid gap-1">
      {run.routeNote ? (
        <p
          data-testid="agent-run-route-note"
          className="flex min-w-0 items-start gap-2 text-[13px] leading-5 text-tone-warn"
        >
          <AlertTriangle size={14} aria-hidden="true" className="mt-[3px] shrink-0" />
          <span className="min-w-0">{run.routeNote}</span>
        </p>
      ) : null}
      {lines.length ? (
        <div data-testid="agent-run-actions" className="grid gap-1">
          {lines.map((l, i) => (
            <WorkRow
              key={`${l.tool}-${l.failed ? "x" : "ok"}-${i}`}
              icon={Check}
              title={l.text}
              failed={l.failed}
              hint={l.tool}
            />
          ))}
        </div>
      ) : null}
      {steps.length ? (
        <RunActivityFold
          lines={steps}
          under="row"
          toggleTestId="agent-run-steps-toggle"
          listTestId="agent-run-activity"
        />
      ) : null}
      {model ? (
        <div data-testid="agent-run-model" title={modelTitle || undefined}>
          <WorkRow
            icon={Cpu}
            title={`Ran on ${model}`}
            detail={where?.tail ?? null}
            detailTitle={where?.title}
          />
        </div>
      ) : null}
      {folder ? (
        <div
          data-testid="agent-run-folder"
          className="flex min-w-0 items-center gap-2 text-[13px] leading-5 text-zinc-500"
        >
          <FolderOpen size={14} aria-hidden="true" className="shrink-0" />
          <span title={folder} className="min-w-0 truncate text-zinc-400">
            in {folderWords.name}
          </span>
          <Dot />
          <button
            type="button"
            data-testid="agent-run-open-folder"
            onClick={() => void openFolder()}
            disabled={opening}
            aria-label={folderWords.own ? "Open its own work folder" : `Open the folder ${folderWords.name}`}
            className="shrink-0 text-zinc-500 underline decoration-zinc-600 decoration-dotted underline-offset-[3px] transition-colors hover:text-zinc-300 focus-visible:text-zinc-300 focus-visible:decoration-solid focus-visible:outline-none disabled:opacity-60 motion-reduce:transition-none"
          >
            Open folder
          </button>
        </div>
      ) : null}
      {openErr ? (
        <p role="alert" data-testid="agent-run-folder-error" className="text-[12px] text-tone-danger">
          {openErr}
        </p>
      ) : null}
    </div>
  );
}
