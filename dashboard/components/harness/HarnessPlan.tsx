"use client";

/**
 * THE TASK LIST UNDER THE REPLY (v1.374.0, IJ harness part 2 Phase 3).
 *
 * While a Build turn works, the plan it keeps with `plan_update` (Claude
 * Code's TodoWrite, Codex's update_plan) is drawn right under the live reply
 * as the chat's own checklist (components/chat/PlanChecklist): an empty
 * circle for a step still waiting, an arrow for the one it is on, a check for
 * one done. When the turn ends the list FOLDS into the reply: one quiet line
 * ("Plan · 3 of 4 done") that opens to the checklist as the turn left it,
 * settled (lib/planChecklist settlePlan — nothing is left working). The side
 * Plan pane is unchanged.
 *
 * The chat's pieces only: the checklist's rows, its words ("3 of 4 done") and
 * its settle rule. No box, no border; the reading column's own grey.
 */

import { useId, useState } from "react";
import { ChevronRight, ListChecks } from "lucide-react";
import { PlanChecklist } from "@/components/chat/PlanChecklist";
import { planCountWords, type PlanView } from "@/lib/planChecklist";

/** The running turn's plan, under the live reply. */
export function LivePlan({ plan }: { plan: PlanView | null }) {
  if (!plan) return null;
  return (
    <div data-testid="harness-live-plan" className="mt-1">
      <PlanChecklist plan={plan} />
    </div>
  );
}

/** A settled reply's plan, folded to one line until it is opened. */
export function SettledPlan({ plan }: { plan: PlanView | null }) {
  const [open, setOpen] = useState(false);
  const bodyId = useId();
  if (!plan) return null;
  return (
    <div data-testid="harness-reply-plan" className="mt-2">
      <button
        type="button"
        aria-expanded={open}
        aria-controls={bodyId}
        onClick={() => setOpen((v) => !v)}
        className="flex min-w-0 items-center gap-1.5 text-[13px] leading-5 text-zinc-500 transition-colors hover:text-zinc-300"
      >
        <ChevronRight size={13} aria-hidden className={`shrink-0 transition-transform ${open ? "rotate-90" : ""}`} />
        <ListChecks size={13} aria-hidden className="shrink-0" />
        <span>Plan</span>
        <span aria-hidden>·</span>
        <span data-testid="harness-reply-plan-count" className="tabular-nums">
          {planCountWords(plan)}
        </span>
      </button>
      {open ? (
        <div id={bodyId} className="mt-1 pl-[19px]">
          <PlanChecklist plan={plan} />
        </div>
      ) : null}
    </div>
  );
}
