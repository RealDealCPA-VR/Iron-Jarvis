"use client";

import { ChevronLeft, ChevronRight } from "lucide-react";
import { REPLY_ACTION_BTN } from "@/components/chat/replyActions";

/**
 * "‹ 2 / 3 ›" — flip between the versions kept at one fork (v1.325.0; the
 * idea is assistant-ui's branch picker, MIT — no code taken). An edited
 * question or another answer keeps the old ending; this is how the user gets
 * back to it. `pos` is 0-based (lib/branches.branchInfo), the label 1-based.
 *
 * Renders nothing for a single version. Both arrows are disabled at their
 * end and while a turn is running (`disabled`) — switching mid-turn would
 * pull the conversation out from under the reply being written.
 */
export function BranchPicker({
  pos,
  count,
  onSwitch,
  disabled = false,
}: {
  pos: number;
  count: number;
  onSwitch: (to: number) => void;
  disabled?: boolean;
}) {
  if (!Number.isInteger(count) || count < 2) return null;
  const at = Math.min(Math.max(0, Math.trunc(pos) || 0), count - 1);
  // Calm chat W1-4: the same 28px ghost squares as the rest of the row under
  // a message (an end arrow is dimmed and ignores the pointer).
  const btn = `${REPLY_ACTION_BTN} disabled:pointer-events-none`;
  return (
    <span
      data-testid="branch-picker"
      role="group"
      aria-label={`Version ${at + 1} of ${count}`}
      className="inline-flex select-none items-center text-[12px] tabular-nums text-zinc-500"
    >
      <button
        type="button"
        aria-label="Previous version"
        title={disabled ? "Wait for the reply to finish" : "Previous version"}
        disabled={disabled || at <= 0}
        onClick={() => onSwitch(at - 1)}
        className={btn}
      >
        <ChevronLeft size={12} aria-hidden="true" />
      </button>
      <span aria-hidden="true">
        {at + 1} / {count}
      </span>
      <button
        type="button"
        aria-label="Next version"
        title={disabled ? "Wait for the reply to finish" : "Next version"}
        disabled={disabled || at >= count - 1}
        onClick={() => onSwitch(at + 1)}
        className={btn}
      >
        <ChevronRight size={12} aria-hidden="true" />
      </button>
    </span>
  );
}
