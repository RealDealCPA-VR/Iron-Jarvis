"use client";

// The trigger node's inspector (v1.122.0) — the missing on-ramp. "When should
// this run?" used to have three answers scattered across three surfaces
// (manual on the canvas, cron on /schedules, webhook/event on /reflex) with
// no link between them; clicking the trigger now offers all three in place.

import Link from "next/link";
import { CalendarClock, Play, SlidersHorizontal, Webhook, X } from "lucide-react";
import { WF_ICON_GHOST, WF_POPOVER } from "./calm";

// v1.329.0 (calm chat wave 9, K3): the three answers are quiet rows that
// fill on hover, not bordered tiles.
const TILE = "flex items-start gap-2.5 rounded-lg px-3 py-2.5 transition-colors";

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
    <Link href={href} className={`${TILE} hover:bg-white/[0.05]`}>
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
    <div className={`${WF_POPOVER} absolute right-3 top-3 z-20 flex w-[300px] max-w-[calc(100%-1.5rem)] flex-col overflow-hidden`}>
      <header className="flex items-center justify-between gap-3 border-b hairline px-4 py-2.5">
        <h3 className="flex items-center gap-2 text-[13px] font-medium text-zinc-200">
          <SlidersHorizontal size={14} className="text-zinc-500" />
          When should this run?
        </h3>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close trigger options"
          className={WF_ICON_GHOST}
        >
          <X size={14} />
        </button>
      </header>

      <div className="space-y-1 p-2">
        <div className="flex items-start gap-2.5 rounded-lg bg-accent/[0.06] px-3 py-2.5">
          <Play size={14} className="mt-0.5 shrink-0 text-accent-soft" />
          <div>
            <div className="text-[13px] font-medium text-zinc-200">When you run it</div>
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
            <div className="text-[13px] font-medium text-zinc-200">On a schedule</div>
            <p className="mt-0.5 text-[11px] leading-snug text-zinc-500">
              Every morning, weekdays at 4pm, and more. Results go to your destinations.
            </p>
          </div>
        </MaybeLink>

        <MaybeLink saved={saved} href={`/reflex?workflow=${encodeURIComponent(name)}`}>
          <Webhook size={14} className="mt-0.5 shrink-0 text-zinc-400" />
          <div>
            <div className="text-[13px] font-medium text-zinc-200">
              When something happens
            </div>
            <p className="mt-0.5 text-[11px] leading-snug text-zinc-500">
              A webhook, an inbound message, a calendar event. The signal&apos;s text
              reaches steps as {"{{Trigger}}"}.
            </p>
          </div>
        </MaybeLink>

        {!saved && (
          <p className="px-3 pb-1 pt-1 text-[12px] leading-snug text-tone-warn">
            Save the workflow first. Schedules and signals fire the SAVED
            “{name || "workflow"}” by name.
          </p>
        )}
      </div>
    </div>
  );
}
