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
 */

import { useState } from "react";
import { AlertTriangle, Check, Cpu, FolderOpen } from "lucide-react";
import { post, ApiError } from "@/lib/api";
import { WorkRow } from "@/components/chat/WorkLine";
import { runActionLines } from "@/lib/runWords";
import { friendlyModelName } from "@/lib/friendlyModelName";
import { providerDisplay } from "@/lib/onboarding";
import { replyWhere } from "@/components/chat/TurnReceipt";
import type { AgentRunStamp } from "@/lib/agentRun";

/** The model the run was served by, in the words the reply's receipt uses
 *  (the name the id spells; the demo model by its own name), or "". */
export function runModelName(run: Pick<AgentRunStamp, "provider" | "model">): string {
  if (run.provider === "mock") return providerDisplay("mock");
  const model = (run.model ?? "").trim();
  if (model) return friendlyModelName(model);
  return providerDisplay(run.provider ?? "");
}

/** The last segment of a folder path, either separator ("" for none). */
function folderName(p: string): string {
  const parts = p.split(/[\\/]/).filter(Boolean);
  return parts.length ? parts[parts.length - 1] : p;
}

function Dot() {
  return <span aria-hidden="true" className="h-[3px] w-[3px] shrink-0 rounded-full bg-zinc-600" />;
}

export function AgentRunReceipt({ run }: { run: AgentRunStamp }) {
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

  if (!run.routeNote && !lines.length && !model && !folder) return null;
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
            in {folderName(folder)}
          </span>
          <Dot />
          <button
            type="button"
            data-testid="agent-run-open-folder"
            onClick={() => void openFolder()}
            disabled={opening}
            aria-label={`Open the folder ${folderName(folder)}`}
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
