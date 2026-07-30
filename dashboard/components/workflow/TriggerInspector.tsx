"use client";

// The trigger node's inspector (v1.122.0) — the missing on-ramp. "When should
// this run?" used to have three answers scattered across three surfaces
// (manual on the canvas, cron on /schedules, webhook/event on /reflex) with
// no link between them; clicking the trigger now offers all three in place.

import Link from "next/link";
import { CalendarClock, Play, SlidersHorizontal, Webhook, X } from "lucide-react";

const TILE =
  "flex items-start gap-2.5 rounded-xl border border-white/[0.08] px-3 py-2.5 transition-colors";

/** A tile that is a live link only once the def is SAVED — schedules and
 * reflex rules fire saved workflows by name, so an unsaved deep link would
 * prefill a target that doesn't exist and fail only at fire time. */
function MaybeLink({
  saved,
  href,
  children,
}: {
  saved: boolean;
  href: string;
  children: React.ReactNode;
}) {
  if (!saved)
    return <div className={`${TILE} cursor-not-allowed opacity-45`}>{children}</div>;
  return (
    <Link href={href} className={`${TILE} hover:border-accent/40 hover:bg-white/[0.03]`}>
      {children}
    </Link>
  );
}

export function TriggerInspector({
  workflowName,
  saved,
  onClose,
}: {
  workflowName: string;
  /** Whether the def exists server-side — schedules/reflexes fire SAVED
   *  workflows by name, so the deep links are honest only after a save. */
  saved: boolean;
  onClose: () => void;
}) {
  const name = workflowName.trim();
  return (
    <div className="card-surface absolute right-3 top-3 z-20 flex w-[300px] flex-col overflow-hidden">
      <header className="flex items-center justify-between gap-3 border-b hairline px-4 py-3">
        <h3 className="flex items-center gap-2 text-[13px] font-semibold text-zinc-200">
          <SlidersHorizontal size={14} className="text-accent-soft/80" />
          When should this run?
        </h3>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close trigger options"
          className="rounded-lg border border-white/10 p-1 text-zinc-500 transition-colors hover:border-white/20 hover:text-zinc-200"
        >
          <X size={14} />
        </button>
      </header>

      <div className="space-y-2 p-4">
        <div className="flex items-start gap-2.5 rounded-xl border border-accent/25 bg-accent/[0.06] px-3 py-2.5">
          <Play size={14} className="mt-0.5 shrink-0 text-accent-soft" />
          <div>
            <div className="text-[12.5px] font-medium text-zinc-200">When you run it</div>
            <p className="mt-0.5 text-[11px] leading-snug text-zinc-500">
              The Run workflow button, or Run once from a chat card. Always on.
            </p>
          </div>
        </div>

        <MaybeLink
          saved={saved}
          href={`/schedules?workflow=${encodeURIComponent(name)}`}
        >
          <CalendarClock size={14} className="mt-0.5 shrink-0 text-zinc-400" />
          <div>
            <div className="text-[12.5px] font-medium text-zinc-200">On a schedule</div>
            <p className="mt-0.5 text-[11px] leading-snug text-zinc-500">
              Every morning, weekdays at 4pm… — results go to your destinations.
            </p>
          </div>
        </MaybeLink>

        <MaybeLink saved={saved} href={`/reflex?workflow=${encodeURIComponent(name)}`}>
          <Webhook size={14} className="mt-0.5 shrink-0 text-zinc-400" />
          <div>
            <div className="text-[12.5px] font-medium text-zinc-200">
              When something happens
            </div>
            <p className="mt-0.5 text-[11px] leading-snug text-zinc-500">
              A webhook, an inbound message, a calendar event. The signal's text
              reaches steps as {"{{Trigger}}"}.
            </p>
          </div>
        </MaybeLink>

        {!saved && (
          <p className="pt-1 text-[11px] leading-snug text-amber-300/80">
            Save the workflow first — schedules and signals fire the SAVED
            “{name || "workflow"}” by name.
          </p>
        )}
      </div>
    </div>
  );
}
